#!/usr/bin/env python3
"""对扫描出的 skill 做启发式预分类，生成人工确认清单。

这是「存量整理」流程的第二步。分类规则是**启发式**的，
一定会出错——所以本脚本**绝不自动改动任何文件**，
只输出一份待确认清单，由用户逐项确认后才进入迁移阶段。

四类归属（与 README 的治理模型对应）：

  1. official   —— Agent 官方自带技能（随发行版更新，不该迁走）
  2. third_party —— 从别处安装的第三方技能（登记 URL + hash，不迁内容）
  3. personal   —— 用户自己写的原创技能（迁入仓库真源）
  4. unknown    —— 信号不足，必须人工判断

启发式信号（按可靠性从高到低）：
  - 路径特征    ：在 Agent 官方 skills 目录内 = official
  - frontmatter ：license 字段、description 里的来源说明
  - 内嵌 .git   ：有 .git 说明是 clone 下来的（第三方强信号）
  - 软链        ：已经是软链 = 已被治理过，指向真源
  - 内容特征    ：含 THIRD_PARTY_NOTICES / 版权声明 / 上游 URL

用法：
    python3 scripts/classify.py inventory.json
    python3 scripts/classify.py inventory.json -o review.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ─── 官方自带的判定 ────────────────────────────────
# Hermes 自带技能集中在分类目录里，且属于 Agent 安装的一部分。
# 判定为 official 的 skill 不迁入仓库——用户升级 Agent 时它们会更新，
# 迁走反而造成版本冲突。
OFFICIAL_PATH_MARKERS = (
    "/.hermes/skills/",       # Hermes 自带（平铺或分类都在此树下）
    "/.claude/skills/",       # Claude Code 自带
    "/.config/opencode/",     # OpenCode 自带
    "/.cursor/skills/",       # Cursor 自带
    "/.stepcode/agent/skills/",  # StepCode 自带
)

# 但 shared-brain 这类"用户自己的仓库"即使放在 agent 目录下也不是 official。
# 通过软链判定：指向 agent 目录之外的 = 用户真源。
# 注意：这些标记用于匹配**路径段**（用 "/<name>/" 包裹），
# 不能裸用 in path —— 否则 /tmp/agent-arch-test-xxx/ 这种临时目录
# 会被误判成"用户仓库"（实测踩到的坑）。
KNOWN_USER_REPO_MARKERS = (
    "shared-brain", "agent-arch", "dotfiles", "my-skills", "my-agents",
)

# ─── 第三方信号 ────────────────────────────────
THIRD_PARTY_FILES = (
    "THIRD_PARTY_NOTICES.md", "THIRD_PARTY.md",
    "NOTICE", "NOTICE.md", "ACKNOWLEDGEMENTS.md",
    "LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING",
)
UPSTREAM_MARKERS = (
    "upstream", "provenance", "来源", "上游", "fork from",
    "based on", "adapted from", "原作者", "original author",
    "anthropic", "官方技能", "官方原版",
)
URL_RE = re.compile(r"https?://[^\s)\]\"'>]+")
GITHUB_RE = re.compile(r"https?://github\.com/[\w.-]+/[\w.-]+")

# 专有授权声明：出现这些词说明不是用户原创，不能当个人技能开源
PROPRIETARY_MARKERS = (
    "proprietary", "all rights reserved", "版权所有",
    "不得转载", "禁止转载", "confidential",
)


def path_has_segment(path: str, markers: tuple[str, ...]) -> str | None:
    """判断路径中是否含 markers 里的某个**完整路径段**。

    为什么不能用 `marker in path`：
      临时目录 /tmp/agent-arch-test-xxx/ 含子串 "agent-arch"，
      会被误判成"用户仓库"，导致官方技能被错误分类。
    按 "/" 切段后精确匹配才可靠。
    返回匹配到的段名，未命中返回 None。
    """
    parts = {p for p in path.split("/") if p}
    for m in markers:
        if m in parts:
            return m
    return None


def looks_official(rec: dict) -> tuple[bool, str]:
    """判定是否官方自带。返回 (是否, 理由)。

    判定依据按可靠性排序：
      1. source_kind == "official_dir" —— scan 阶段已经确认这个位置
         是 Agent 的安装目录（最可靠，不依赖路径字符串匹配）
      2. 路径含官方目录标记 —— 兜底，用于 scan 阶段漏标的情况
    软链到用户仓库的一律不算 official。
    """
    path = rec["path"]
    # 软链到用户仓库的，即使路径在 agent 目录内也不算 official
    if rec.get("is_symlink"):
        target = rec.get("symlink_target") or ""
        if path_has_segment(target, KNOWN_USER_REPO_MARKERS):
            return False, "软链指向用户仓库真源"

    # 最可靠信号：来自 Agent 配置里的 external_dirs = 用户真源
    if rec.get("source_kind") == "external":
        return False, "位于 Agent 配置的 external_dirs（用户真源）"

    # 名字像用户仓库的，即使位于官方目录也不算 official
    if path_has_segment(path, KNOWN_USER_REPO_MARKERS):
        return False, "位于官方目录但名字像用户仓库"

    # scan 阶段标记的官方安装目录
    if rec.get("source_kind") == "official_dir":
        return True, "位于 Agent 官方 skill 安装目录"

    # 兜底：路径字符串匹配
    if any(marker in path for marker in OFFICIAL_PATH_MARKERS):
        return True, "路径位于 Agent 官方 skill 目录"
    return False, ""


def find_third_party_signals(skill_dir: Path) -> dict:
    """在 skill 目录里找第三方信号。"""
    signals: dict = {
        "third_party_files": [],
        "urls": [],
        "github_repos": [],
        "proprietary": [],
    }

    # 只在文本文件里找，且限制数量避免读大文件
    text_exts = {".md", ".txt", ".py", ".js", ".ts", ".json", ".yaml",
                 ".yml", ".toml", ".cfg", ".ini", ".rst", ""}
    checked = 0
    for p in sorted(skill_dir.rglob("*")):
        if not p.is_file() or checked >= 40:
            continue
        if p.suffix.lower() not in text_exts:
            continue
        if any(part in p.parts for part in
               ("node_modules", "__pycache__", ".git")):
            continue
        checked += 1
        try:
            if p.stat().st_size > 400_000:      # 跳过超大文件
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # 授权文件本身就是第三方归属的强信号
        if p.name in THIRD_PARTY_FILES:
            signals["third_party_files"].append(p.name)
            # 从 LICENSE 文件里提取专有授权证据（前 300 字足够判断）
            head = text[:300]
            for marker in PROPRIETARY_MARKERS:
                if marker in head.lower():
                    signals["proprietary"].append(f"{p.name}: {marker}")
                    break

        low = text.lower()
        # upstream / 原作者等关键词说明内容来自别处
        for marker in UPSTREAM_MARKERS:
            if marker in low:
                signals.setdefault("upstream_mentions", []).append(
                    f"{p.name}: {marker}"
                )

        for url in URL_RE.findall(text):
            url = url.rstrip(".,;:)")
            signals["urls"].append(url)
            gh = GITHUB_RE.match(url)
            if gh:
                signals["github_repos"].append(gh.group(0))

    # 去重保序
    for key in ("urls", "github_repos", "proprietary"):
        seen, uniq = set(), []
        for v in signals[key]:
            if v not in seen:
                seen.add(v)
                uniq.append(v)
        signals[key] = uniq[:12]
    return signals


def classify(rec: dict) -> dict:
    """对单条 skill 记录做启发式分类。"""
    result = {
        # 用 .get 而非 [] 取 name：classify() 应能被单独调用
        # （如测试、或别的脚本直接喂记录），缺字段时给个可用默认值，
        # 而不是抛 KeyError。
        "name": rec.get("name") or Path(rec["path"]).name,
        "path": rec["path"],
        # .get 取 agent：分类逻辑本身不依赖它，缺了不该抛异常
        "agent": rec.get("agent", "unknown"),
        "category": "unknown",
        "confidence": "low",
        "reasons": [],
        "signals": {},
        "needs_review": True,
        "recommended_action": "",
    }

    path = Path(rec["path"])
    fm = rec.get("frontmatter") or {}

    # ── 1. 官方自带 ──
    is_off, why = looks_official(rec)
    if is_off:
        result["category"] = "official"
        result["confidence"] = "high"
        result["reasons"].append(why)
        result["recommended_action"] = "保持原样，不迁入仓库（随 Agent 升级而更新）"
        # 软链的官方技能仍可能是用户定制过的，提示一下
        if rec.get("is_symlink"):
            result["needs_review"] = True
            result["reasons"].append("但是它是软链，可能已被定制，请确认")
        else:
            result["needs_review"] = False
        return result

    # ── 2. 已被治理（软链） ──
    if rec.get("is_symlink"):
        target = rec.get("symlink_target") or ""
        result["category"] = "personal"
        result["confidence"] = "medium"
        result["reasons"].append(f"已是软链 → {target}")
        result["recommended_action"] = "已治理，确认真源是否在仓库内即可"
        return result

    # ── 3. 第三方信号（先于 external_dirs 判定）──
    # 关键顺序：即使技能在用户的 external_dirs 里，只要检测到
    # 专有授权或第三方声明，就必须归为 third_party——
    # 否则用户会把自己 fork 来的专有技能当原创开源，造成侵权。
    if path.is_dir():
        sig = find_third_party_signals(path)
        result["signals"] = sig

        if sig["proprietary"]:
            result["category"] = "third_party"
            result["confidence"] = "high"
            result["reasons"].append(
                f"检测到专有授权声明: {sig['proprietary'][0]}"
            )
            result["recommended_action"] = (
                "⚠ 专有内容，不可当作原创开源。保留在私有仓库自用，"
                "或登记来源后移除"
            )
            result["needs_review"] = True
            return result

        if sig["third_party_files"] and any(
            f.startswith(("THIRD_PARTY", "NOTICE", "ACKNOWLEDGE"))
            for f in sig["third_party_files"]
        ):
            result["category"] = "third_party"
            result["confidence"] = "high"
            result["reasons"].append(
                f"含第三方声明文件: "
                f"{', '.join(n for n in sig['third_party_files'] if not n.startswith('LICEN'))}"
            )
            result["recommended_action"] = (
                "登记来源 URL + commit hash，不迁内容（见 docs/治理模型.md）"
            )
            if sig["github_repos"]:
                result["reasons"].append(
                    f"疑似上游仓库: {sig['github_repos'][0]}"
                )
            return result

        if sig.get("upstream_mentions"):
            result["category"] = "third_party"
            result["confidence"] = "medium"
            result["reasons"].append(
                f"内容中声明了上游来源: {sig['upstream_mentions'][0]}"
            )
            result["recommended_action"] = (
                "人工确认：是 fork/改编还是仅引用了别人的项目？"
            )
            result["needs_review"] = True
            return result

        if rec.get("has_embedded_git"):
            result["category"] = "third_party"
            result["confidence"] = "medium"
            result["reasons"].append("目录内含 .git（直接 clone 的）")
            result["recommended_action"] = "登记来源 URL + commit hash"
            return result

        if sig["github_repos"] and not fm.get("license"):
            result["category"] = "third_party"
            result["confidence"] = "low"
            result["reasons"].append(
                f"文档中出现 GitHub 仓库链接: {sig['github_repos'][0]}"
            )
            result["recommended_action"] = "人工确认：是 fork 的还是引用了别人的项目？"
            return result

    # ── 4. 来自 external_dirs（用户真源）──
    # 第三方信号都没命中，才认定为用户自己的技能
    if rec.get("source_kind") == "external":
        result["category"] = "personal"
        result["confidence"] = "high"
        result["reasons"].append("来自 Agent 配置的 external_dirs（用户自己的技能仓库）")
        result["recommended_action"] = "确认真源在仓库内、各处软链指向正确即可"
        return result

    # ── 5. 有 license 字段但无第三方信号 ──
    if fm.get("license"):
        result["category"] = "personal"
        result["confidence"] = "medium"
        result["reasons"].append(f"frontmatter 声明 license: {fm['license']}")
        result["recommended_action"] = "人工确认是否原创，然后迁入仓库真源"
        return result

    # ── 6. 信号不足 ──
    result["category"] = "unknown"
    result["confidence"] = "low"
    result["reasons"].append("无明确信号，需人工判断")
    if not rec.get("has_skill_md"):
        result["reasons"].append("且缺少 SKILL.md")
    result["recommended_action"] = "人工判断：原创 / 第三方 / 官方"
    return result


def print_review(items: list[dict]) -> None:
    """打印人工确认清单。"""
    cats = {"personal": [], "third_party": [], "official": [], "unknown": []}
    for it in items:
        cats[it["category"]].append(it)

    labels = {
        "personal": "个人原创（建议迁入仓库真源）",
        "third_party": "第三方（登记来源，不迁内容）",
        "official": "官方自带（保持原样）",
        "unknown": "信号不足（必须人工判断）",
    }

    print("=" * 68)
    print("Skill 归属预分类 · 人工确认清单")
    print("=" * 68)
    for key in ("personal", "third_party", "official", "unknown"):
        lst = cats[key]
        if not lst:
            continue
        print(f"\n【{labels[key]}】 {len(lst)} 个")
        for it in lst:
            mark = "✎" if it["needs_review"] else " "
            print(f"  {mark} {it['name']}")
            print(f"      {it['path']}")
            for r in it["reasons"]:
                print(f"      · {r}")
            print(f"      → {it['recommended_action']}")

    print("\n" + "=" * 68)
    print("图例: ✎ = 需要你确认   (无标记 = 高置信度，可跳过)")
    print("\n确认后编辑 JSON 里的 category 字段（personal / third_party / official），")
    print("然后运行: python3 scripts/relocate.py review.json")
    print("⚠  在人工确认完成前，relocate.py 不会改动任何文件。")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="启发式预分类 skill 归属，生成人工确认清单"
    )
    ap.add_argument("inventory", help="scan.py 产出的 inventory.json")
    ap.add_argument("-o", "--output", default="review.json",
                    help="确认清单输出路径（默认 review.json）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    inv_path = Path(args.inventory)
    if not inv_path.is_file():
        print(f"错误: 找不到 {inv_path}", file=sys.stderr)
        print("先运行: python3 scripts/scan.py -o inventory.json",
              file=sys.stderr)
        return 1

    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    items = [classify(rec) for rec in inv.get("skills", [])]

    out = {
        "source_inventory": str(inv_path),
        "items": items,
        "summary": {
            "personal": sum(1 for i in items if i["category"] == "personal"),
            "third_party": sum(1 for i in items if i["category"] == "third_party"),
            "official": sum(1 for i in items if i["category"] == "official"),
            "unknown": sum(1 for i in items if i["category"] == "unknown"),
            "needs_review": sum(1 for i in items if i["needs_review"]),
        },
    }

    Path(args.output).write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print_review(items)
        print(f"\n清单已写入: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
