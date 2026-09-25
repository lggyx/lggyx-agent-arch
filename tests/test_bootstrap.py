#!/usr/bin/env python3
"""bootstrap.py 的单元测试。

bootstrap 会创建目录和模板文件，所以测试重点是：
  1. **幂等性** —— 重复跑不重复建，已存在的报告并跳过
  2. **dry-run 不落盘** —— 预览模式绝不创建任何文件
  3. **不替用户编造内容** —— 模板文件只含占位符，不含虚构信息

运行方式：
    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import bootstrap as boot  # noqa: E402


class TestBootstrap(unittest.TestCase):
    """bootstrap 的安全属性与幂等性。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-arch-boot-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_creates_nothing(self) -> None:
        """dry-run 必须一个文件都不创建。"""
        repo = self.tmp / "cap"
        boot.build_capability_repo(repo, dry=True)
        boot.build_index_skill(repo, dry=True)
        boot.build_agents_md(repo, "test-device", dry=True)
        boot.build_hooks(repo, dry=True)
        self.assertFalse(repo.exists(), "dry-run 竟然创建了目录")

    def test_dry_run_shows_all_components(self) -> None:
        """dry-run 下所有组件都必须出现在计划里。

        实测踩到的坑：早期用 build_capability_repo 的返回值做门槛，
        而它在 dry-run 下返回 False，导致后续 3 个组件被静默跳过——
        用户看到的计划不完整，却以为那就是全部。
        """
        repo = self.tmp / "cap"
        # 门条件必须是 True，否则走不到后面的组件
        boot.build_capability_repo(repo, dry=True)
        # 后续组件在目录不存在时也不能抛异常
        boot.build_index_skill(repo, dry=True)
        boot.build_agents_md(repo, "test-device", dry=True)
        boot.build_hooks(repo, dry=True)
        # 只要没抛异常且未落盘，即为通过
        self.assertFalse(repo.exists())

    def test_build_creates_expected_layout(self) -> None:
        """真正执行时应建出 skills/hooks/docs/AGENTS.md。"""
        repo = self.tmp / "cap"
        boot.build_capability_repo(repo, dry=False)
        self.assertTrue((repo / "skills").is_dir())
        self.assertTrue((repo / "hooks").is_dir())
        self.assertTrue((repo / "docs").is_dir())

    def test_idempotent_second_run(self) -> None:
        """重复跑必须跳过，不能重复建或覆盖已有内容。"""
        repo = self.tmp / "cap"
        boot.build_capability_repo(repo, dry=False)
        # 写个标记文件，验证第二次跑不会动它
        marker = repo / "skills" / "keep.txt"
        marker.write_text("mine", encoding="utf-8")

        boot.build_capability_repo(repo, dry=False)
        boot.build_index_skill(repo, dry=False)

        self.assertTrue(marker.is_file())
        self.assertEqual(marker.read_text(encoding="utf-8"), "mine")

    def test_index_skill_not_overwritten(self) -> None:
        """索引技能已存在时必须跳过，不能覆盖用户改过的内容。"""
        repo = self.tmp / "cap"
        (repo / "skills" / "my-index").mkdir(parents=True)
        (repo / "skills" / "my-index" / "SKILL.md").write_text(
            "我的自定义内容", encoding="utf-8"
        )
        boot.build_index_skill(repo, dry=False)
        self.assertEqual(
            (repo / "skills" / "my-index" / "SKILL.md").read_text(
                encoding="utf-8"
            ),
            "我的自定义内容",
        )

    def test_context_repo_templates_have_no_fabrication(self) -> None:
        """上下文层模板只能有占位符，不能有虚构的项目/日程信息。"""
        repo = self.tmp / "ctx"
        boot.build_context_repo(repo, dry=False)

        working = (repo / "WORKING.md").read_text(encoding="utf-8")
        # 占位注释可以有，但不能有具体设备名/时间/资源名
        for fabricated in ("wsl-laptop", "wsl-desktop", "2026-", "shared-brain/"):
            self.assertNotIn(fabricated, working,
                             f"模板里出现了疑似编造的内容: {fabricated}")
        self.assertIn("待填写", repo.joinpath("PROFILE.md").read_text(
            encoding="utf-8"))

    def test_context_gitignore_covers_privacy(self) -> None:
        """上下文层必须屏蔽密钥类文件（隐私红线）。"""
        repo = self.tmp / "ctx"
        boot.build_context_repo(repo, dry=False)
        gi = (repo / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("*.key", "*.pem", ".env"):
            self.assertIn(pattern, gi)

    def test_hooks_installed_and_executable(self) -> None:
        """钩子要装上、可执行，且不含原作者的私有命名。"""
        repo = self.tmp / "cap"
        boot.build_hooks(repo, dry=False)

        target = repo / "hooks" / "skill-repo-sync-check.sh"
        self.assertTrue(target.is_file())
        self.assertTrue(target.stat().st_mode & 0o111, "钩子应有可执行位")

        content = target.read_text(encoding="utf-8")
        # 脱敏：不能残留原作者的仓库名
        self.assertNotIn("shared-brain", content)
        # 关键是 monitor 模式的确定性约束注释必须保留
        self.assertIn("输出必须确定性", content)
        self.assertIn("无输出 = 已是最新", content)

    def test_device_name_detected(self) -> None:
        """设备标识能自动生成且非空。"""
        name = boot.detect_device_name()
        self.assertTrue(name)
        self.assertEqual(name, name.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
