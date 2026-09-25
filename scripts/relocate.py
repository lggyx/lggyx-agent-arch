#!/usr/bin/env python3
"""按人工确认后的清单执行归一化：迁入真源 + 软链回各处。

这是「存量整理」流程的最后一步，**唯一会改动文件的脚本**。

安全设计（缺一不可）：
  1. **默认 dry-run** —— 不加 --apply 绝不碰任何文件，
     先让用户看清"将要发生什么"。
  2. **清单必须已确认** —— 读 classify.py 产出的 review.json，
     只处理 category 字段被人工改过的项；未确认的一律跳过。
  3. **禁止覆盖** —— 目标已存在时中止并报告，绝不静默覆盖
     （用户可能已经手动整理过一部分）。
  4. **可回滚** —— 每次操作记入 migration-log/，
     并生成 undo.sh 还原脚本。
  5. **专有内容保护** —— category == third_party 的项只登记不迁移。

三种处理方式：
  personal    → 移到仓库 skills/ 真源，原位置建软链指回去
  third_party → 写入 registry.yaml（来源 URL + commit hash + 版本），不动内容
  official    → 不动，只记录
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 默认仓库真源位置，可用 --repo 覆盖
DEFAULT_REPO = Path.home() / "skill-repo"
REGISTRY_NAME = "registry.yaml"


def sh(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """执行命令，返回 (退出码, 输出)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=cwd, timeout=120)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def git_commit_of(path: Path) -> str:
    """取目录所在 git 仓库的当前 commit hash（用于版本跟踪）。"""
    code, out = sh(["git", "-C", str(path), "rev-parse", "HEAD"])
    return out if code == 0 else ""


def git_remote_of(path: Path) -> str:
    """取目录所在 git 仓库的 remote 地址（用于来源 URL）。

    注意：这个地址可能是**用户自己的私有仓库**（例如整个
    技能仓库就是一个 git 仓库，里面的每个 skill 都会返回
    同一个仓库地址）。这种值不是"上游来源"，写进 registry.yaml
    会泄露私有仓库地址。

    因此调用方必须用 sanitize_remote() 过滤，只有明确指向
    第三方上游的地址才保留。
    """
    code, out = sh(["git", "-C", str(path), "remote", "get-url", "origin"])
    return out if code == 0 else ""


def sanitize_remote(remote: str, skill_path: Path) -> str:
    """判断 git remote 是否为真正的"上游来源"，否则返回空串。

    判定规则：
      - 远程地址指向的仓库**包含**该 skill 目录本身，说明 skill 只是
        这个仓库的一部分（用户自己的仓库）→ 不是上游，返回空
      - 其他情况（fork 出来的独立仓库）才认为是上游来源
    """
    if not remote:
        return ""

    # 取仓库根目录：如果 skill 就在仓库根的 skills/ 下，
    # 说明 remote 是用户自己的仓库，不是上游
    code, top = sh(["git", "-C", str(skill_path), "rev-parse", "--show-toplevel"])
    if code == 0 and top:
        try:
            rel = Path(skill_path).resolve().relative_to(Path(top).resolve())
            # skill 位于 <repo>/skills/<name> —— 典型"用户自己仓库"结构
            if len(rel.parts) == 2 and rel.parts[0] == "skills":
                return ""
        except (ValueError, OSError):
            pass
    return remote


def is_confirmed(item: dict) -> bool:
    """判断清单项是否已被人工确认。

    确认标志：item 里有 confirmed: true。
    classify.py 产出的清单默认没有这个字段，
    必须由用户手动加上——这是有意的摩擦，防止误执行。
    """
    return item.get("confirmed") is True


def plan_relocations(items: list[dict], repo: Path) -> list[dict]:
    """把确认后的清单转成可执行的操作计划。"""
    plan: list[dict] = []

    for it in items:
        if not is_confirmed(it):
            continue

        cat = it.get("category")
        name = it["name"]
        src = Path(it["path"])
        dest = repo / "skills" / name

        if cat == "personal":
            op = {
                "action": "relocate",
                "name": name,
                "src": str(src),
                "dest": str(dest),
                "symlink_at": str(src),
                "problems": [],
            }
            # 预检顺序有讲究：必须先判"已在仓库内"，
            # 否则 dest 就是 src 本身，会误报"目标已存在"。
            # 源在仓库内 = 已经是真源，无需移动。
            try:
                already = src.resolve().parent == (repo / "skills").resolve()
            except OSError:
                already = False
            if already:
                op["action"] = "already_in_repo"
                plan.append(op)
                continue

            if not src.is_dir():
                op["problems"].append(f"源目录不存在: {src}")
            if dest.exists():
                op["problems"].append(
                    f"目标已存在（不会覆盖）: {dest}"
                )
            if src.is_symlink():
                op["problems"].append(
                    f"源已是软链 → {os.readlink(src)}，可能已治理过"
                )
            plan.append(op)

        elif cat == "third_party":
            # 来源 URL 优先取 classify 阶段从文档里识别出的 GitHub 仓库
            # （那是真正的上游）；git remote 要过 sanitize_remote，
            # 因为 remote 很可能指向用户自己的私有仓库。
            detected = (
                (it.get("signals") or {}).get("github_repos") or []
            )
            remote = sanitize_remote(git_remote_of(src), src)
            entry = {
                "action": "register",
                "name": name,
                "path": str(src),
                "url": (detected[0] if detected else "") or remote,
                "commit": git_commit_of(src),
                "notes": (it.get("reasons") or [""])[0],
            }
            # 没有任何来源信息时要显式提醒，不能留空让用户以为没问题
            if not entry["url"] and not entry["commit"]:
                entry["needs_manual_url"] = True
            plan.append(entry)

        elif cat == "official":
            plan.append({
                "action": "keep",
                "name": name,
                "path": str(src),
                "reason": "官方自带，保持原样",
            })

    return plan


def apply_plan(plan: list[dict], repo: Path, logdir: Path) -> list[str]:
    """执行计划，返回 undo 命令列表。"""
    undo: list[str] = []
    (repo / "skills").mkdir(parents=True, exist_ok=True)
    # 日志目录必须先建：undo.sh 要写进去，
    # 而 migrate 的调用方只保证 repo 存在，不保证 logdir 已建。
    logdir.mkdir(parents=True, exist_ok=True)

    for op in plan:
        name, src, dest = op["name"], Path(op["src"]), Path(op["dest"])

        if op["action"] == "already_in_repo":
            print(f"  · {name}: 已在仓库真源内，跳过")
            continue

        if op["action"] != "relocate":
            continue

        if op["problems"]:
            print(f"  ✗ {name}: " + "; ".join(op["problems"]))
            continue

        # 1. 移动目录到仓库真源
        #    用 shutil.move 而非 git mv：不要求仓库已 commit，
        #    也不要求 src 在同一个 git 仓库里。
        try:
            shutil.move(str(src), str(dest))
        except (OSError, shutil.Error) as exc:
            print(f"  ✗ {name}: 移动失败 {exc}")
            continue

        # 2. 原位置建软链指回真源
        try:
            os.symlink(str(dest), str(src))
        except OSError as exc:
            # 软链失败必须回滚移动，否则技能"消失"了
            print(f"  ✗ {name}: 建软链失败 {exc}，正在回滚移动")
            shutil.move(str(dest), str(src))
            continue

        print(f"  ✓ {name}: 已迁入 {dest}，原位置改为软链")
        undo.append(f"rm {src} && mv {dest} {src}")

    if undo:
        undo_path = logdir / "undo.sh"
        undo_path.write_text(
            "#!/usr/bin/env bash\n"
            "# 生成于 " + datetime.now().isoformat(timespec="seconds") + "\n"
            "# 撤销本次迁移：删软链，把真源移回原位置\n"
            "set -e\n" + "\n".join(undo) + "\n",
            encoding="utf-8",
        )
        undo_path.chmod(0o755)
        print(f"\n回滚脚本: {undo_path}")

    return undo


def write_registry(plan: list[dict], repo: Path) -> Path | None:
    """把 third_party 项写入 registry.yaml（手工可编辑的登记表）。"""
    regs = [op for op in plan if op["action"] == "register"]
    if not regs:
        return None

    path = repo / "registry.yaml"
    lines = [
        "# 第三方 Skill 登记表",
        "#",
        "# 本文件记录「不是自己写的」技能：只登记来源与版本，不迁内容。",
        "# 由 scripts/relocate.py 自动追加，可手工编辑。",
        "# 格式: name / url / commit / notes",
        "",
        "third_party:",
    ]
    for op in regs:
        lines += [
            f"  - name: {op['name']}",
            f"    url: \"{op['url']}\"",
            f"    commit: \"{op['commit']}\"",
            f"    path: \"{op['path']}\"",
            f"    notes: \"{op['notes']}\"",
        ]
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def print_dry_run(plan: list[dict], repo: Path) -> None:
    """打印将要执行的操作（dry-run）。"""
    reloc = [p for p in plan if p["action"] in ("relocate", "already_in_repo")]
    reg = [p for p in plan if p["action"] == "register"]
    keep = [p for p in plan if p["action"] == "keep"]

    print("=" * 68)
    print("迁移计划（dry-run，未改动任何文件）")
    print("=" * 68)

    if reloc:
        print(f"\n【将迁入真源】 {repo}/skills/  ({len(reloc)} 个)")
        for op in reloc:
            if op["action"] == "already_in_repo":
                print(f"  · {op['name']}: 已在真源内，跳过")
            else:
                print(f"  → {op['name']}")
                print(f"      {op['src']}")
                print(f"      ⇒ {op['dest']}（原位置建软链）")
                for p in op["problems"]:
                    print(f"      ✗ {p}")

    if reg:
        print(f"\n【将登记到 registry.yaml】 ({len(reg)} 个，不动内容)")
        for op in reg:
            print(f"  · {op['name']}")
            if op.get("needs_manual_url"):
                print("      ⚠ 未识别到来源，请手工补充 url 与 commit")
            else:
                print(f"      来源: {op['url'] or '(未识别)'}")
                print(f"      版本: {op['commit'][:12] or '(非 git 仓库)'}")

    if keep:
        print(f"\n【保持原样】 ({len(keep)} 个官方自带)")

    if not plan:
        print("\n没有已确认的操作。")
        print("请先在 review.json 中给要处理的项加上 \"confirmed\": true")
        return

    print("\n确认无误后加 --apply 执行。")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="按人工确认后的清单执行归一化（默认 dry-run）"
    )
    ap.add_argument("review", help="classify.py 产出并经人工确认的 review.json")
    ap.add_argument("--repo", default=str(DEFAULT_REPO),
                    help=f"仓库真源位置（默认 {DEFAULT_REPO}）")
    ap.add_argument("--apply", action="store_true",
                    help="真正执行（不加则只打印计划）")
    args = ap.parse_args()

    review_path = Path(args.review)
    if not review_path.is_file():
        print(f"错误: 找不到 {review_path}", file=sys.stderr)
        return 1

    review = json.loads(review_path.read_text(encoding="utf-8"))
    repo = Path(args.repo).expanduser()
    if not repo.is_dir():
        print(f"错误: 仓库不存在 {repo}（用 --repo 指定）", file=sys.stderr)
        return 1

    items = review.get("items", [])
    confirmed = [i for i in items if is_confirmed(i)]
    if not confirmed:
        print("清单中没有任何已确认的项。")
        print("请编辑 review.json，给要处理的项加 \"confirmed\": true")
        return 0

    plan = plan_relocations(items, repo)

    if not args.apply:
        print_dry_run(plan, repo)
        return 0

    logdir = repo / "migration-log"
    logdir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    print("=" * 68)
    print("执行迁移")
    print("=" * 68)
    undo = apply_plan(plan, repo, logdir)
    reg_path = write_registry(plan, repo)
    if reg_path:
        print(f"第三方登记表: {reg_path}")

    (logdir / f"{stamp}.json").write_text(
        json.dumps({"plan": plan, "undo": undo},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"操作日志: {logdir}/{stamp}.json")
    print("\n完成后建议: git add -A && git commit -m 'chore: 归一化 skill 真源'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
