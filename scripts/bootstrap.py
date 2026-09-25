#!/usr/bin/env python3
"""交互式引导：从零搭建属于你自己的「六部」架构。

这是用户 clone 本仓库后应该跑的第一个脚本。它不替你决定任何事，
而是**问清楚你的现状，再按蓝图逐组件搭建**。

与 scan/classify/relocate 的关系：
  - `bootstrap.py` 负责「从零建架构」——新用户入口
  - `scan/classify/relocate` 负责「整理存量 skill」——建好真源后的填充动作
  两者可以独立使用，但一起用才是完整流程。

设计原则：
  1. **所有操作可跳过** —— 用户只想建一部分架构是完全合理的
  2. **破坏性操作前必须 dry-run 预览** —— 复用 relocate.py 的安全设计
  3. **绝不擅自生成内容** —— 模板文件由用户确认后才写入
  4. **幂等** —— 重复跑不会重复建，已存在的组件会报告并跳过

用法：
    python3 scripts/bootstrap.py            # 交互式引导
    python3 scripts/bootstrap.py --yes      # 全部用默认值，只预览不执行
    python3 scripts/bootstrap.py --dry-run  # 只打印将做什么，不提问
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ─── 架构默认配置（可由用户覆盖） ────────────────────
DEFAULTS = {
    "capability_repo": "~/skill-repo",   # 能力层仓库（六部）
    "context_repo": "~/life-os",           # 上下文层仓库（识海）
    "device_name": "",                     # 设备标识，空则自动生成
}

HOME = Path.home()

# --yes / --dry-run 时置 True：跳过所有提问，全部用默认值。
# 做成模块级而不是局部变量，因为 _yes() 需要读它。
SKIP_PROMPT = False


def sh(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """执行命令，返回 (退出码, 输出)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           cwd=cwd, timeout=60)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def ask(prompt: str, default: str = "") -> str:
    """交互提问。带默认值时直接回车即接受。"""
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        sys.exit(1)
    return ans or default


def confirm(prompt: str, default: bool = False) -> bool:
    """是/否确认。"""
    hint = "Y/n" if default else "y/N"
    try:
        ans = input(f"{prompt} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        sys.exit(1)
    if not ans:
        return default
    return ans in ("y", "yes", "是", "好", "对")


def _yes(prompt: str, default: bool = True) -> bool:
    """组件级确认：--yes/--dry-run 模式下直接用默认值，不提问。

    这样 CI 或快速查看时能一次看完整个计划，
    而交互模式下仍由用户逐项决定。
    """
    if SKIP_PROMPT:
        print(f"{prompt} → {'是（默认）' if default else '否（默认）'}")
        return default
    return confirm(prompt, default=default)


def detect_device_name() -> str:
    """生成设备标识：主机名 + 平台，如 wsl-laptop。"""
    import platform
    host = platform.node().split(".")[0] or "unknown"
    if "microsoft" in platform.release().lower():
        return f"wsl-{host}".lower()
    return host.lower()


def banner(title: str) -> None:
    print()
    print("=" * 66)
    print(f"  {title}")
    print("=" * 66)


# ─── 组件构建函数 ────────────────────────────────

def build_capability_repo(repo: Path, dry: bool) -> bool:
    """组件 1：能力层仓库（六部）。"""
    skills = repo / "skills"
    if skills.is_dir():
        print(f"  · 已存在，跳过: {repo}")
        return True

    print(f"  将创建: {repo}/")
    print("    skills/        技能真源")
    print("    hooks/         同步与推送钩子")
    print("    docs/          设备登记等")
    print("    AGENTS.md      治理规则")
    if dry:
        return False

    skills.mkdir(parents=True, exist_ok=True)
    (repo / "hooks").mkdir(exist_ok=True)
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "mcp").mkdir(exist_ok=True)
    print(f"  ✓ 已创建 {repo}")
    return True


def build_context_repo(repo: Path, dry: bool) -> bool:
    """组件 3：上下文层仓库（识海）。"""
    if (repo / "WORKING.md").is_file():
        print(f"  · 已存在，跳过: {repo}")
        return True

    print(f"  将创建: {repo}/")
    print("    WORKING.md     占用登记（防多设备冲突）")
    print("    PROJECTS/      项目进度")
    print("    PROFILE.md     个人信息")
    if dry:
        return False

    repo.mkdir(parents=True, exist_ok=True)
    (repo / "PROJECTS").mkdir(exist_ok=True)

    # WORKING.md 只给空模板，内容由用户自己填——不替用户编造项目信息
    (repo / "WORKING.md").write_text(
        "# 占用登记\n\n"
        "> 修改共享资源（技能/项目）前先读这里。出现他人登记且 TTL 未过 → 停下问用户。\n"
        "> 开工登记一行，完工撤销。\n\n"
        "<!-- 格式：\n"
        "- 设备: <设备名>\n"
        "  资源: skill-repo/<技能名>\n"
        "  开始: <YYYY-MM-DD HH:MM>\n"
        "  TTL: 2h\n"
        "-->\n",
        encoding="utf-8",
    )
    (repo / "PROFILE.md").write_text(
        "# 个人信息\n\n（待填写：身份、偏好、常用工具）\n", encoding="utf-8"
    )
    (repo / ".gitignore").write_text(
        "# 隐私红线：证件/卡号/token 永不入 git\n"
        "*.key\n*.pem\nid_rsa*\n.env\ncredentials*\n",
        encoding="utf-8",
    )
    print(f"  ✓ 已创建 {repo}")
    return True


def build_index_skill(repo: Path, dry: bool) -> bool:
    """组件 2：索引技能（最高优先级）。"""
    d = repo / "skills" / "my-index"
    if (d / "SKILL.md").is_file():
        print("  · 已存在，跳过: skills/my-index")
        return True

    print("  将创建: skills/my-index/SKILL.md（索引技能，最高优先级）")
    if dry:
        return False

    d.mkdir(parents=True, exist_ok=True)
    # 只写规则骨架，不替用户编技能清单——清单由 scan.py 盘点后生成
    (d / "SKILL.md").write_text(
        "---\n"
        "name: my-index\n"
        'description: "Load this skill BEFORE any other skill. 本仓库是唯一真源，'
        "优先级高于任何 Agent 自带技能。任务匹配技能时、或准备加载/修改任何技能前，"
        '先读本技能。"\n'
        "category: productivity\n"
        "---\n\n"
        "# 我的技能索引（最高优先级）\n\n"
        "## 规则：先查这里，再决定用哪个技能\n\n"
        "本仓库 `" + str(repo) + "` 是**唯一真源**。当任务匹配某个技能时：\n\n"
        "1. **先 `ls " + str(repo) + "/skills/`**，找到能力匹配的那个\n"
        "2. **本仓库与 Agent 自带技能冲突时，本仓库赢**\n"
        "3. **按能力匹配，不按名字匹配**\n\n"
        "## 同名陷阱表\n\n"
        "| 任务 | 正确选择 | 自带错误选择 | 差异 |\n"
        "|---|---|---|---|\n"
        "| （待补充） | | | |\n\n"
        "## 本仓库现有技能\n\n"
        "（运行 `python3 scripts/scan.py -o inventory.json` 后填入）\n",
        encoding="utf-8",
    )
    print("  ✓ 已创建 skills/my-index/SKILL.md")
    return True


def build_agents_md(repo: Path, device: str, dry: bool) -> bool:
    """组件 6：治理规则 AGENTS.md。"""
    f = repo / "AGENTS.md"
    if f.is_file():
        print("  · 已存在，跳过: AGENTS.md")
        return True

    print("  将创建: AGENTS.md（治理规则）")
    if dry:
        return False

    f.write_text(
        "# 跨 Agent 通用行为规范\n\n"
        "本文件会被多个 Agent 读取。只放**所有 Agent 都应遵守**的通用规则。\n\n"
        "## 诚实性（最高优先级）\n\n"
        "- 做不到就说做不到，阻塞点如实说明\n"
        "- 绝不用看起来合理的假输出冒充真实执行结果\n"
        "- 不确定时提问，不要猜测后交付错误方向\n\n"
        "## 本仓库的操作约定\n\n"
        "- 技能通过软链接被各 Agent 引用，**不要**拷贝到别处再改\n"
        "- 修改技能请直接改 `" + str(repo) + "/skills/` 下的源文件\n"
        "- **本仓库只存规则与脚本，不存素材**：图片/视频等大二进制\n"
        "  一律进 `~/work/<项目名>/`\n"
        "- 判断标准：换个项目还能复用 → 进仓库；只服务某个项目 → 进 workspace\n"
        "- 密钥只写本地密钥文件，`git status` 里不该出现任何 token\n\n"
        "## 提交与同步约定\n\n"
        "- 提交信息用中文，格式 `<类型>: <简述>`\n"
        "- 本设备 git 身份: `" + device + "`\n"
        "- 冲突时 `git pull --rebase` 后重推，**绝不 `git push --force`**\n",
        encoding="utf-8",
    )
    print("  ✓ 已创建 AGENTS.md")
    return True


def build_hooks(repo: Path, dry: bool) -> bool:
    """组件 4：同步钩子。"""
    hooks = repo / "hooks"
    target = hooks / "skill-repo-sync-check.sh"
    if target.is_file():
        print("  · 已存在，跳过: hooks/")
        return True

    src = Path(__file__).resolve().parent.parent / "hooks" / \
        "skill-repo-sync-check.sh"
    if not src.is_file():
        print(f"  ✗ 模板不存在: {src}")
        return False

    print("  将安装: hooks/skill-repo-sync-check.sh（远程更新检测）")
    if dry:
        return False

    hooks.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)
    target.chmod(0o755)
    print("  ✓ 已安装 hooks/")
    return True


def config_git_identity(repo: Path, device: str, dry: bool) -> None:
    """组件 7 的一部分：设备专属 git 身份。"""
    if not (repo / ".git").is_dir():
        print(f"  · {repo} 还不是 git 仓库，先 git init 再配身份")
        if dry:
            return
        code, _ = sh(["git", "init", "-b", "main"], cwd=repo)
        if code != 0:
            print("  ✗ git init 失败")
            return

    code, cur = sh(["git", "-C", str(repo), "config", "user.name"])
    if code == 0 and cur:
        print(f"  · 已有身份: {cur}")
        return

    print(f"  将设置 git 身份: {device}")
    if dry:
        return
    sh(["git", "-C", str(repo), "config", "user.name", device])
    print("  ✓ 已设置 git 身份")


def show_external_dirs(repo: Path) -> None:
    """提示如何接入 Agent。"""
    banner("接入 Agent（手动一步）")
    print("架构已建好，最后把它接入你的 Agent。Hermes 示例：\n")
    print(f"  # ~/.hermes/config.yaml")
    print("  skills:")
    print("    external_dirs:")
    print(f"      - {repo / 'skills'}\n")
    print("其他 Agent（Claude Code / OpenCode / pi）请查各自文档的")
    print("外部技能目录配置项。\n")
    print("接入后运行一次 scan.py 盘点本机存量：")
    print("  python3 scripts/scan.py -o inventory.json")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="交互式引导：搭建属于你自己的技能治理架构"
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将做什么，不提问不执行")
    ap.add_argument("--yes", action="store_true",
                    help="全部用默认值，直接预览不执行（用于 CI 或快速查看）")
    args = ap.parse_args()
    # --yes 与 --dry-run 都走"不提问"路径；区别仅在提示语
    global SKIP_PROMPT
    SKIP_PROMPT = args.dry_run or args.yes
    dry = SKIP_PROMPT

    banner("搭建你的「六部」架构")
    print("本向导分 7 个组件搭建，每步都可跳过。")
    print("它会创建目录与模板文件，**绝不替你编造任何内容**。\n")

    # ── 配置 ──
    if SKIP_PROMPT:
        cap = Path(DEFAULTS["capability_repo"]).expanduser()
        ctx = Path(DEFAULTS["context_repo"]).expanduser()
        device = DEFAULTS["device_name"] or detect_device_name()
        print(f"能力层仓库: {cap}")
        print(f"上下文层仓库: {ctx}")
        print(f"本设备标识: {device}")
    else:
        cap = Path(ask("能力层仓库位置（六部：装技能）",
                       DEFAULTS["capability_repo"])).expanduser()
        ctx = Path(ask("上下文层仓库位置（识海：装进度/身份）",
                       DEFAULTS["context_repo"])).expanduser()
        device = ask("本设备标识",
                     DEFAULTS["device_name"] or detect_device_name())

    # ── 组件 1 ──
    banner("组件 1/7：唯一真源（skills/ 目录）")
    print("所有自己写的技能集中在一处，别处只放软链。")
    build_capability_repo(cap, dry)
    # 注意：不能用返回值决定是否继续后续组件。
    # dry-run 下 build_capability_repo 返回 False（未真正创建），
    # 若用它做门槛，组件 6/2/4 会被静默跳过，用户看不到完整计划。
    # 后续组件自己会处理"目录还不存在"的情况。
    ok = True

    # ── 组件 6 ──
    if ok and _yes("\n建立治理规则 AGENTS.md？", default=True):
        banner("组件 6/7：治理规则（AGENTS.md）")
        build_agents_md(cap, device, dry)

    # ── 组件 2 ──
    if ok and _yes("\n建立索引技能（让 Agent 认这份真源）？", default=True):
        banner("组件 2/7：索引技能（最高优先级）")
        build_index_skill(cap, dry)

    # ── 组件 4 ──
    if ok and _yes("\n安装同步钩子？", default=True):
        banner("组件 4/7：跨设备同步机制")
        build_hooks(cap, dry)
        config_git_identity(cap, device, dry)

    # ── 组件 3 ──
    if _yes("\n建立上下文层仓库（识海）？", default=True):
        banner("组件 3/7：上下文层（life-os / 识海）")
        build_context_repo(ctx, dry)

    # ── 收尾 ──
    if dry:
        banner("预览结束")
        print("以上是预览。去掉 --dry-run / --yes 后真正执行。")
        return 0

    banner("完成")
    print("架构骨架已就位。接下来：\n")
    print("1. 盘点本机存量 skill：")
    print("     python3 scripts/scan.py -o inventory.json")
    print("2. 预分类归属（人工确认后才能迁移）：")
    print("     python3 scripts/classify.py inventory.json -o review.json")
    print("3. 编辑 review.json，给要处理的项加 \"confirmed\": true")
    print("4. dry-run 预览，确认后 --apply：")
    print(f"     python3 scripts/relocate.py review.json --repo {cap}")
    print(f"     python3 scripts/relocate.py review.json --repo {cap} --apply\n")
    show_external_dirs(cap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
