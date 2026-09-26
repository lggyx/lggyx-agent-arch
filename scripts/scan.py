#!/usr/bin/env python3
"""扫描本机所有 Agent 的 skill 安装位置，产出结构化盘点清单。

这是「存量整理」流程的第一步：在决定怎么整理之前，先搞清楚现状。

设计要点：
  1. **只读，绝不改动任何文件** —— 扫描阶段不允许有副作用，
     否则用户在不了解现状时就已被改动。
  2. **发现重复副本** —— 同一个技能被多处安装是最常见的问题，
     清单里要显式标出，让用户看到"我装了三个 pptx"。
  3. **输出机器可读的 JSON** —— 后续 classify.py 直接消费，
     不再重复扫描。
  4. **Agent 位置可扩展** —— 已知位置写在 AGENTS_SKILL_DIRS，
     用户可在配置里追加自己的路径。

用法：
    python3 scripts/scan.py                    # 扫描并打印摘要
    python3 scripts/scan.py -o inventory.json  # 完整清单写文件
    python3 scripts/scan.py --json             # 只输出 JSON（供管道）
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# ─── 已知 Agent 的 skill 安装位置 ────────────────────────────
# key 是 Agent 名，value 是相对 HOME 的路径（支持 ~ 展开）。
# 只收集"真的存在"的目录；不存在的跳过，不报错。
AGENTS_SKILL_DIRS: dict[str, list[str]] = {
    "hermes":   ["~/.hermes/skills", "~/.hermes/skills.local"],
    "claude":   ["~/.claude/skills"],
    "opencode": ["~/.config/opencode/skills", "~/.opencode/skills"],
    "pi":       ["~/.pi/skills"],
    "cursor":   ["~/.cursor/skills"],
    "codex":    ["~/.codex/skills"],
    "gemini":   ["~/.gemini/skills"],
    "goose":    ["~/.config/goose/skills"],
    # StepCode：全局技能目录；~/.agents/skills 已由 EXTRA_SKILL_DIRS 覆盖
    "stepcode": ["~/.stepcode/agent/skills"],
}

# 额外的通用位置：有些工具不按上面命名，但会把 skill 放这里
EXTRA_SKILL_DIRS = [
    "~/skills",
    "~/.agents/skills",
    "~/.local/share/agents/skills",
]

# ─── Agent 配置里的 external_dirs（用户真源，最重要） ────────────
# Hermes 等 Agent 支持在配置里指定额外的 skill 目录
# （skills.external_dirs），用户自己的技能仓库就是这样被加载的。
# 这类位置是"用户真源"，优先级最高——分类时必须优先识别。
AGENT_CONFIG_FILES: dict[str, list[str]] = {
    "hermes": ["~/.hermes/config.yaml", "~/.hermes/config.yml"],
    "claude": ["~/.claude/config.json", "~/.claude/settings.json"],
    "opencode": ["~/.config/opencode/opencode.json"],
    "codex": ["~/.codex/config.yaml", "~/.codex/config.yml"],
    "stepcode": ["~/.stepcode/agent/settings.json"],
}

# ─── 配置格式为 JSON 的 Agent ────────────────────────────
# 实测踩到的坑：Hermes 等用 YAML 的 skills.external_dirs:，
# StepCode 的 settings.json 用**根级 skills 数组**——语义相同
# （被 Agent 加载的 skill 目录 = 用户真源），但格式完全不同。
# 行解析在 JSON 上一个路径都抓不到，用户的整个真源会被漏扫。
JSON_SKILLS_AGENTS = {"stepcode"}


def read_external_dirs(agent: str) -> list[Path]:
    """从 Agent 配置里读 skills.external_dirs / skills 数组。

    按 Agent 分两种格式：
      - YAML（hermes 等）：skills.external_dirs: 列表
      - JSON（stepcode）：settings.json 的根级 skills 数组
    找不到配置、解析失败都返回空列表，不报错
    （没配 external_dirs 是正常情况）。
    """
    paths: list[Path] = []
    for rel in AGENT_CONFIG_FILES.get(agent, []):
        cfg = Path(rel).expanduser()
        if not cfg.is_file():
            continue
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if agent in JSON_SKILLS_AGENTS:
            paths.extend(_paths_from_json_skills(text, cfg.parent))
        else:
            paths.extend(_paths_from_yaml_external_dirs(text))
    return paths


def _paths_from_yaml_external_dirs(text: str) -> list[Path]:
    """YAML 配置的 external_dirs 行解析（只认绝对路径）。

    只做轻量的行解析，不引入 YAML 依赖——external_dirs 是简单的
    列表结构，逐行抓 `- /path/to/dir` 就够了。
    """
    paths: list[Path] = []
    # 找到 external_dirs: 之后，收集紧随其后的列表项
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("external_dirs"):
            in_block = True
            # 可能是 `external_dirs: [/a, /b]` 的单行形式
            if "[" in stripped:
                inner = stripped.split("[", 1)[1].split("]", 1)[0]
                for part in inner.split(","):
                    part = part.strip().strip("'\"")
                    if part.startswith("/"):
                        paths.append(Path(part))
                in_block = False
            continue
        if in_block:
            m = re.match(r"^-\s+(.+?)\s*$", stripped)
            if m:
                val = m.group(1).strip().strip("'\"")
                if val.startswith("/"):
                    paths.append(Path(val))
            elif stripped and not stripped.startswith("#"):
                # 遇到非列表项、非空行，说明列表结束
                if not stripped.startswith("-"):
                    in_block = False
    return paths


def _paths_from_json_skills(text: str, cfg_dir: Path) -> list[Path]:
    """JSON 配置（StepCode settings.json）的根级 skills 数组。

    相对路径相对**配置文件所在目录**解析——StepCode 的全局配置
    在 ~/.stepcode/agent/settings.json，相对它解析。
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # 配置写坏了不能让扫描崩——扫描的定位是只读摸底
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get("skills")
    if not isinstance(entries, list):
        return []
    return normalize_skill_entries(entries, cfg_dir)


def normalize_skill_entries(entries: list, cfg_dir: Path) -> list[Path]:
    """把 skills 数组元素解析成真实路径（StepCode 的路径规则）。

    规则来自 StepCode settings 文档：
      - 绝对路径与 ~ 直接支持
      - 相对路径相对配置文件所在目录
      - 支持 glob；`!pattern` / `-path` 为排除，`+path` 为强制包含
    排除模式要先生效再做 glob——否则"包含全部再排除一个"
    的写法会把排除项也扫进去。
    """
    includes: list[tuple[str, bool]] = []   # (模式, 是否强制包含)
    excludes: list[str] = []
    for raw in entries:
        if not isinstance(raw, str):
            continue
        pat = raw.strip()
        if not pat:
            continue
        if pat.startswith(("!", "-")):
            excludes.append(pat[1:])
            continue
        force = pat.startswith("+")
        if force:
            pat = pat[1:]
        includes.append((pat, force))

    def resolve(pat: str) -> str:
        expanded = Path(pat).expanduser()
        if not expanded.is_absolute():
            expanded = cfg_dir / expanded
        return str(expanded)

    paths: list[Path] = []
    for pat, _force in includes:
        resolved = resolve(pat)
        if any(ch in resolved for ch in "*?["):
            paths.extend(Path(m) for m in glob.glob(resolved))
        else:
            paths.append(Path(resolved))

    if excludes:
        ex_patterns = [resolve(ex) for ex in excludes]
        kept = []
        for p in paths:
            if any(fnmatch.fnmatch(str(p), ex) for ex in ex_patterns):
                continue
            kept.append(p)
        paths = kept
    return paths

# frontmatter 字段：name 与 description 是 agentskills.io 规范要求的
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---", re.DOTALL)
FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")


def parse_frontmatter(skill_md: Path) -> dict:
    """从 SKILL.md 提取 YAML frontmatter。

    不引入 PyYAML 依赖（用户机器上未必有），只做简单的
    `key: value` 解析——足够覆盖 name/description/license 这些字段。
    解析失败时返回空 dict，由调用方标为"无 frontmatter"。
    """
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"_error": f"读取失败: {exc}"}

    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}

    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        fm = FIELD_RE.match(line.strip())
        if fm:
            key, val = fm.group(1), fm.group(2).strip().strip('"').strip("'")
            fields[key] = val
    return fields


def dir_fingerprint(path: Path) -> str:
    """对目录内容做轻量指纹，用于判断两个副本是否真的相同。

    只统计「文件名 + 大小」，不读全文——既快又足以发现
    "同一技能的两个不同版本"。若大小集合完全一致，视为同一版本。
    """
    parts = []
    try:
        for p in sorted(path.rglob("*")):
            if p.is_file() and not any(
                part in p.parts for part in ("node_modules", "__pycache__", ".git")
            ):
                try:
                    parts.append(f"{p.relative_to(path)}:{p.stat().st_size}")
                except OSError:
                    continue
    except OSError:
        return ""
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


def iter_skill_dirs(base: Path) -> list[Path]:
    """产出 base 下所有"看起来像 skill 目录"的路径。

    不同 Agent 的组织方式不同，必须都覆盖：
      - 平铺式：~/.claude/skills/<skill-name>/SKILL.md
      - 分类式：~/.hermes/skills/<category>/<skill-name>/SKILL.md
        （Hermes 自带技能按 productivity / creative 等分类组织）
      - 软链式：目录本身是软链，指向别处的真源

    策略：向下最多钻 3 层，凡是含 SKILL.md 的目录就算一个 skill；
    若整棵子树都没有 SKILL.md，则把直接子目录记为一条
    "无 SKILL.md" 记录（提醒用户这里有异常）。
    """
    max_depth = 3
    results: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(d.iterdir())
        except OSError:
            return

        subdirs = [c for c in children
                   if c.is_dir() and not c.name.startswith(".")
                   and c.name not in ("__pycache__", "node_modules")]

        for sub in subdirs:
            if (sub / "SKILL.md").is_file() or sub.is_symlink():
                results.append(sub)
            else:
                walk(sub, depth + 1)

    walk(base, 0)

    # 整棵子树都没找到 SKILL.md：把直接子目录记为异常项
    if not results:
        try:
            for child in base.iterdir():
                if child.is_dir() and not child.name.startswith("."):
                    results.append(child)
        except OSError:
            pass

    return results


def scan_one_skill(skill_dir: Path, agent: str, base: Path,
                   source_kind: str = "official_dir") -> dict:
    """扫描单个 skill 目录，返回一条记录。"""
    skill_md = skill_dir / "SKILL.md"
    record: dict = {
        "name": skill_dir.name,
        "path": str(skill_dir),
        "agent": agent,
        "source_kind": source_kind,
        "base_dir": str(base),
        "has_skill_md": skill_md.is_file(),
    }

    if record["has_skill_md"]:
        fm = parse_frontmatter(skill_md)
        record["frontmatter"] = {
            "name": fm.get("name", ""),
            "description": fm.get("description", ""),
            "license": fm.get("license", ""),
        }
        # frontmatter 里声明的 name 与目录名不一致时要标出：
        # 这通常是"复制时改了目录名"或"版本不同步"的信号
        declared = fm.get("name", "")
        if declared and declared != skill_dir.name:
            record["name_mismatch"] = declared
    else:
        record["frontmatter"] = None

    # 是否软链接（软链说明已有"真源"意识，是被治理过的）
    record["is_symlink"] = skill_dir.is_symlink()
    if record["is_symlink"]:
        try:
            record["symlink_target"] = os.readlink(skill_dir)
        except OSError:
            record["symlink_target"] = None

    # 内嵌 .git 说明是直接 clone 下来的第三方技能（强信号）
    record["has_embedded_git"] = (skill_dir / ".git").exists()

    # 体积与文件数（用于发现误提交的大素材）
    try:
        files = [p for p in skill_dir.rglob("*")
                 if p.is_file() and "node_modules" not in p.parts]
        record["file_count"] = len(files)
        record["size_bytes"] = sum(p.stat().st_size for p in files if p.exists())
    except OSError:
        record["file_count"] = 0
        record["size_bytes"] = 0

    record["fingerprint"] = dir_fingerprint(skill_dir)
    return record


def collect_dirs() -> list[tuple[str, Path, str]]:
    """汇总所有待扫描的 (agent名, 路径, 来源类型)，去重并过滤不存在的。

    来源类型三种，分类器会据此判断：
      official_dir —— Agent 安装目录内的位置（官方自带技能）
      external     —— 配置里的 external_dirs（用户真源，优先级最高）
      other        —— 其他通用位置
    """
    seen: set[Path] = set()
    result: list[tuple[str, Path, str]] = []

    def add(agent: str, path: Path, kind: str) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if not path.is_dir() or resolved in seen:
            return
        seen.add(resolved)
        result.append((agent, path, kind))

    # 1. external_dirs 优先（用户真源）
    for agent in AGENT_CONFIG_FILES:
        for ext in read_external_dirs(agent):
            add(agent, ext, "external")

    # 2. 各 Agent 的官方安装目录
    for agent, rels in AGENTS_SKILL_DIRS.items():
        for rel in rels:
            add(agent, Path(rel).expanduser(), "official_dir")

    # 3. 通用位置
    for rel in EXTRA_SKILL_DIRS:
        add("other", Path(rel).expanduser(), "other")

    return result


def scan() -> dict:
    """执行扫描，返回完整清单。"""
    inventory: dict = {
        "agents": {},
        "skills": [],
        "duplicates": [],
        "summary": {},
    }

    for agent, base, source_kind in collect_dirs():
        entries = []
        # 用 iter_skill_dirs 兼容平铺式与分类式（Hermes）两种组织
        for skill_dir in iter_skill_dirs(base):
            rec = scan_one_skill(skill_dir, agent, base, source_kind)
            entries.append(rec)
            inventory["skills"].append(rec)

        inventory["agents"][agent] = {
            "base": str(base),
            "source_kind": source_kind,
            "skill_count": len(entries),
        }

    # 按 (frontmatter name 或目录名) 分组找重复
    by_name: dict[str, list[dict]] = {}
    for rec in inventory["skills"]:
        key = (rec.get("frontmatter") or {}).get("name") or rec["name"]
        by_name.setdefault(key, []).append(rec)

    for name, recs in sorted(by_name.items()):
        if len(recs) < 2:
            continue
        fingerprints = {r["fingerprint"] for r in recs if r["fingerprint"]}
        inventory["duplicates"].append({
            "name": name,
            "copies": [
                {"agent": r["agent"], "path": r["path"],
                 "fingerprint": r["fingerprint"]}
                for r in recs
            ],
            # 指纹全同 = 内容一致的真重复；有差异 = 版本已分叉
            "identical": len(fingerprints) == 1 and len(recs) > 1,
        })

    inventory["summary"] = {
        "total_skills": len(inventory["skills"]),
        "agents_scanned": len(inventory["agents"]),
        "duplicate_names": len(inventory["duplicates"]),
        "identical_duplicates": sum(
            1 for d in inventory["duplicates"] if d["identical"]
        ),
        "diverged_duplicates": sum(
            1 for d in inventory["duplicates"] if not d["identical"]
        ),
        "symlinked": sum(1 for s in inventory["skills"] if s["is_symlink"]),
        "without_skill_md": sum(
            1 for s in inventory["skills"] if not s["has_skill_md"]
        ),
        "embedded_git": sum(1 for s in inventory["skills"] if s["has_embedded_git"]),
    }
    return inventory


def print_summary(inv: dict) -> None:
    """人类可读的摘要——让用户一眼看清现状。"""
    s = inv["summary"]
    print("=" * 62)
    print("本机 Skill 盘点结果")
    print("=" * 62)
    print(f"扫描到 Agent 位置 : {s['agents_scanned']} 个")
    print(f"Skill 总数        : {s['total_skills']}")
    print(f"其中软链接        : {s['symlinked']}（已被治理过）")
    print(f"缺少 SKILL.md     : {s['without_skill_md']}")
    print(f"内嵌 .git         : {s['embedded_git']}（直接 clone 的第三方技能）")
    print()

    print("各 Agent 位置：")
    for agent, info in sorted(inv["agents"].items()):
        print(f"  {agent:<10} {info['skill_count']:>3} 个   {info['base']}")
    print()

    if inv["duplicates"]:
        print(f"发现 {s['duplicate_names']} 个重名技能"
              f"（内容完全相同的 {s['identical_duplicates']} 个，"
              f"版本已分叉的 {s['diverged_duplicates']} 个）：")
        for d in inv["duplicates"]:
            flag = "相同" if d["identical"] else "分叉"
            print(f"  [{flag}] {d['name']}  ×{len(d['copies'])}")
            for c in d["copies"]:
                print(f"         {c['agent']:<9} {c['path']}")
    else:
        print("未发现重名技能。")
    print()
    print("下一步：python3 scripts/classify.py <inventory.json>")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="扫描本机所有 Agent 的 skill 安装位置（只读，无副作用）"
    )
    ap.add_argument("-o", "--output", help="完整清单输出路径（JSON）")
    ap.add_argument("--json", action="store_true",
                    help="只向 stdout 输出 JSON，不打印摘要")
    args = ap.parse_args()

    inv = scan()

    if args.output:
        Path(args.output).write_text(
            json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not args.json:
            print(f"完整清单已写入: {args.output}\n")

    if args.json:
        print(json.dumps(inv, ensure_ascii=False, indent=2))
    else:
        print_summary(inv)

    return 0


if __name__ == "__main__":
    sys.exit(main())
