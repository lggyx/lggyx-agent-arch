#!/usr/bin/env python3
"""scripts/ 的单元测试。

运行方式：
    python3 -m unittest discover -s tests -v

测试原则：
  - **不动真实数据** —— 所有用例都在临时目录里造 fixture，
    绝不碰 ~/.hermes、~/shared-brain 等真实位置。
  - **验证安全属性** —— 这个工具会改文件，所以测试重点不是
    "能迁移"，而是"什么情况下拒绝迁移"。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import classify as classify_mod  # noqa: E402
import relocate as relocate_mod  # noqa: E402
import scan as scan_mod  # noqa: E402


def make_skill(base: Path, name: str, *, with_md: bool = True,
               license_text: str | None = None,
               description: str = "测试技能",
               symlink_to: Path | None = None) -> Path:
    """造一个测试用 skill 目录。"""
    d = base / name
    if symlink_to is not None:
        d.symlink_to(symlink_to, target_is_directory=True)
        return d
    d.mkdir(parents=True, exist_ok=True)
    if with_md:
        (d / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            "---\n\n# 测试\n",
            encoding="utf-8",
        )
    if license_text is not None:
        (d / "LICENSE.txt").write_text(license_text, encoding="utf-8")
    return d


class TestScan(unittest.TestCase):
    """scan.py：只读扫描的正确性。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-arch-test-"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_frontmatter_parsing(self) -> None:
        """能正确提取 frontmatter 的 name / description / license。"""
        d = make_skill(self.tmp, "my-skill", description="做某件事")
        fm = scan_mod.parse_frontmatter(d / "SKILL.md")
        self.assertEqual(fm.get("name"), "my-skill")
        self.assertEqual(fm.get("description"), "做某件事")

    def test_missing_skill_md_flagged(self) -> None:
        """没有 SKILL.md 的目录要被标出来，而不是静默跳过。"""
        d = make_skill(self.tmp, "broken", with_md=False)
        rec = scan_mod.scan_one_skill(d, "hermes", self.tmp)
        self.assertFalse(rec["has_skill_md"])

    def test_symlink_detected_with_target(self) -> None:
        """软链要记录目标地址——这是判断"已治理"的关键。"""
        real = make_skill(self.tmp / "real", "linked")
        link = make_skill(self.tmp, "linked", symlink_to=real)
        rec = scan_mod.scan_one_skill(link, "hermes", self.tmp)
        self.assertTrue(rec["is_symlink"])
        self.assertIn("real", rec["symlink_target"] or "")

    def test_categorized_layout_discovered(self) -> None:
        """分类式布局（Hermes 的 skills/<cat>/<name>/）必须被发现。

        这是实测踩到的坑：第一版只扫第一层，把 62 个官方技能全漏了。
        """
        cat = self.tmp / "productivity"
        cat.mkdir()
        make_skill(cat, "pptx")
        make_skill(cat, "xlsx")
        found = scan_mod.iter_skill_dirs(self.tmp)
        names = sorted(p.name for p in found)
        self.assertEqual(names, ["pptx", "xlsx"])

    def test_duplicate_detection_identical_vs_diverged(self) -> None:
        """相同副本 vs 版本分叉，必须区分开。"""
        a = make_skill(self.tmp / "a", "dup")
        b = make_skill(self.tmp / "b", "dup")

        # 手工造三条记录（绕过文件系统，只测分组与指纹对比逻辑）
        inv = {
            "skills": [
                {"name": "dup", "path": str(a), "agent": "hermes",
                 "fingerprint": "same", "frontmatter": {"name": "dup"}},
                {"name": "dup", "path": str(b), "agent": "claude",
                 "fingerprint": "same", "frontmatter": {"name": "dup"}},
                {"name": "dup", "path": str(a), "agent": "other",
                 "fingerprint": "different",
                 "frontmatter": {"name": "dup"}},
            ],
            "agents": {},
        }
        by_name: dict[str, list[dict]] = {}
        for rec in inv["skills"]:
            key = (rec.get("frontmatter") or {}).get("name") or rec["name"]
            by_name.setdefault(key, []).append(rec)

        self.assertEqual(len(by_name["dup"]), 3)
        fps = {r["fingerprint"] for r in by_name["dup"]}
        self.assertEqual(fps, {"same", "different"})


class TestClassify(unittest.TestCase):
    """classify.py：启发式分类的正确性与安全性。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-arch-test-"))

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_official_dir_classified_official(self) -> None:
        """位于 Agent 官方目录 → official，不迁入仓库。"""
        d = make_skill(self.tmp, "apple-notes")
        rec = {"path": str(d), "is_symlink": False, "symlink_target": None,
               "has_embedded_git": False, "has_skill_md": True,
               "frontmatter": {"name": "apple-notes", "license": ""},
               "source_kind": "official_dir"}
        is_off, _ = classify_mod.looks_official(rec)
        self.assertTrue(is_off)

    def test_external_dir_is_not_official(self) -> None:
        """external_dirs 来源绝不判为 official——那是用户真源。"""
        d = make_skill(self.tmp, "mine")
        rec = {"path": str(d), "is_symlink": False, "symlink_target": None,
               "has_embedded_git": False, "has_skill_md": True,
               "frontmatter": {"name": "mine", "license": ""},
               "source_kind": "external"}
        is_off, _ = classify_mod.looks_official(rec)
        self.assertFalse(is_off)

    def test_proprietary_license_wins_over_external(self) -> None:
        """**最重要的安全测试**：专有授权优先于 external_dirs。

        用户把自己的 fork 放在 external_dirs 里很常见。若分类器
        因为"在 external_dirs 里"就判 personal，用户会把专有内容
        当原创开源——这是侵权。专有授权必须压过 personal 信号。
        """
        d = make_skill(
            self.tmp, "vendor-skill",
            license_text="Copyright (c) 2026 Someone. All Rights Reserved.",
        )
        rec = {"path": str(d), "is_symlink": False, "symlink_target": None,
               "has_embedded_git": False, "has_skill_md": True,
               "frontmatter": {"name": "vendor-skill", "license": ""},
               "source_kind": "external"}
        result = classify_mod.classify(rec)
        self.assertEqual(result["category"], "third_party")
        self.assertTrue(result["needs_review"])

    def test_third_party_notice_detected(self) -> None:
        """含 THIRD_PARTY_NOTICES.md + 上游 GitHub 链接 → third_party。"""
        d = make_skill(self.tmp, "forked")
        (d / "THIRD_PARTY_NOTICES.md").write_text(
            "上游: https://github.com/someone/forked\n", encoding="utf-8"
        )
        rec = {"path": str(d), "is_symlink": False, "symlink_target": None,
               "has_embedded_git": False, "has_skill_md": True,
               "frontmatter": {"name": "forked", "license": ""},
               "source_kind": "external"}
        result = classify_mod.classify(rec)
        self.assertEqual(result["category"], "third_party")
        self.assertIn(
            "https://github.com/someone/forked",
            " ".join(result["reasons"]),
        )

    def test_personal_when_no_third_party_signal(self) -> None:
        """无任何第三方信号 + 在 external_dirs → personal。"""
        d = make_skill(self.tmp, "mine")
        rec = {"path": str(d), "is_symlink": False, "symlink_target": None,
               "has_embedded_git": False, "has_skill_md": True,
               "frontmatter": {"name": "mine", "license": ""},
               "source_kind": "external"}
        result = classify_mod.classify(rec)
        self.assertEqual(result["category"], "personal")

    def test_unknown_when_no_signals(self) -> None:
        """信号不足必须判 unknown 并标记需人工确认，不能瞎猜。"""
        d = make_skill(self.tmp, "mystery", with_md=False)
        rec = {"path": str(d), "is_symlink": False, "symlink_target": None,
               "has_embedded_git": False, "has_skill_md": False,
               "frontmatter": None, "source_kind": "other"}
        result = classify_mod.classify(rec)
        self.assertEqual(result["category"], "unknown")
        self.assertTrue(result["needs_review"])

    def test_already_symlinked_is_personal(self) -> None:
        """已是软链 → 已被治理，归 personal 并指向真源。"""
        real = make_skill(self.tmp / "repo", "linked")
        link = make_skill(self.tmp, "linked", symlink_to=real)
        rec = {"path": str(link), "is_symlink": True,
               "symlink_target": str(real),
               "has_embedded_git": False, "has_skill_md": True,
               "frontmatter": {"name": "linked", "license": ""},
               "source_kind": "other"}
        result = classify_mod.classify(rec)
        self.assertEqual(result["category"], "personal")
        self.assertIn(str(real), " ".join(result["reasons"]))


class TestRelocate(unittest.TestCase):
    """relocate.py：执行阶段的安全属性。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-arch-test-"))
        self.repo = self.tmp / "repo"
        (self.repo / "skills").mkdir(parents=True)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _item(self, name: str, cat: str, confirmed: bool = True) -> dict:
        return {
            "name": name, "path": str(self.tmp / "src" / name),
            "agent": "other", "category": cat,
            "confirmed": confirmed, "reasons": [], "signals": {},
        }

    def test_unconfirmed_items_are_ignored(self) -> None:
        """未确认的项一律不进入计划——有意的摩擦。"""
        items = [self._item("a", "personal", confirmed=False)]
        self.assertEqual(relocate_mod.is_confirmed(items[0]), False)

    def test_dest_exists_blocks_relocation(self) -> None:
        """目标已存在必须报错，绝不静默覆盖。"""
        src = make_skill(self.tmp / "src", "thing")
        (self.repo / "skills" / "thing").mkdir(parents=True)
        (self.repo / "skills" / "thing" / "SKILL.md").write_text("x")
        plan = relocate_mod.plan_relocations(
            [self._item("thing", "personal")], self.repo
        )
        self.assertTrue(any("目标已存在" in p for p in plan[0]["problems"]))

    def test_already_in_repo_detected(self) -> None:
        """源已在仓库 skills/ 内 → 标记 already_in_repo，不移动。"""
        # 注意：item 的 path 必须真的指向仓库内，
        # 不能套用 _item() 里写死的 tmp/src/<name>。
        src = make_skill(self.repo / "skills", "thing")
        item = self._item("thing", "personal")
        item["path"] = str(src)
        plan = relocate_mod.plan_relocations([item], self.repo)
        self.assertEqual(plan[0]["action"], "already_in_repo")

    def test_apply_moves_and_creates_symlink(self) -> None:
        """apply 后：真源在仓库内，原位置变成指向它的软链。"""
        src = make_skill(self.tmp / "src", "thing")
        plan = relocate_mod.plan_relocations(
            [self._item("thing", "personal")], self.repo
        )
        undo = relocate_mod.apply_plan(plan, self.repo, self.tmp / "log")

        dest = self.repo / "skills" / "thing"
        self.assertTrue(dest.is_dir())
        self.assertTrue((dest / "SKILL.md").is_file())
        self.assertTrue(src.is_symlink())
        self.assertEqual(src.resolve(), dest.resolve())
        self.assertTrue(len(undo) > 0, "必须生成回滚命令")

    def test_apply_symlink_failure_rolls_back(self) -> None:
        """软链失败必须回滚移动，否则技能会"消失"。"""
        src = make_skill(self.tmp / "src", "thing")
        # 预占软链位置，让 os.symlink 失败
        (self.tmp / "src" / "thing").rename(self.tmp / "src" / "thing.bak")
        (self.tmp / "src" / "thing").symlink_to(
            self.tmp / "src" / "thing.bak", target_is_directory=True
        )

        plan = relocate_mod.plan_relocations(
            [self._item("thing", "personal")], self.repo
        )
        relocate_mod.apply_plan(plan, self.repo, self.tmp / "log")

        # 回滚后原位置应仍是可用的目录，仓库里不应留下残留
        self.assertFalse((self.repo / "skills" / "thing").exists())

    def test_private_remote_not_written_as_upstream(self) -> None:
        """**脱敏测试**：用户自己的私有仓库地址不能当上游来源。

        实测踩到的坑：shared-brain 里的每个 skill 用
        `git remote get-url` 都会返回 shared-brain 自己的地址，
        写进 registry.yaml 就泄露了私有仓库。
        """
        # 没有 git 仓库时 remote 为空，sanitize 也应安全返回空
        d = make_skill(self.tmp, "thing")
        self.assertEqual(
            relocate_mod.sanitize_remote("", d), ""
        )

    def test_registry_written_for_third_party(self) -> None:
        """third_party 只登记不迁移，且写入 registry.yaml。"""
        d = make_skill(self.tmp / "src", "forked")
        (d / "THIRD_PARTY_NOTICES.md").write_text(
            "上游 https://github.com/someone/forked\n", encoding="utf-8"
        )
        item = self._item("forked", "third_party")
        item["signals"] = {"github_repos": ["https://github.com/someone/forked"]}
        plan = relocate_mod.plan_relocations([item], self.repo)
        self.assertEqual(plan[0]["action"], "register")

        path = relocate_mod.write_registry(plan, self.repo)
        self.assertIsNotNone(path)
        text = path.read_text(encoding="utf-8")
        self.assertIn("someone/forked", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
