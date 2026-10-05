from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install.py"


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name)
        self.skills = self.home / ".claude" / "skills"
        self.claude_md = self.home / ".claude" / "CLAUDE.md"

    def run_install(self, *args: str, ok: bool = True) -> dict:
        result = subprocess.run(
            [sys.executable, str(INSTALL), "--home", str(self.home), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0 if ok else 1, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def results(self, report: dict) -> list[str]:
        return [step["result"] for step in report["steps"]]

    def test_links_both_skills_and_is_idempotent(self) -> None:
        self.assertEqual(["linked", "linked"], self.results(self.run_install("--claude")))
        self.assertEqual(ROOT, (self.skills / "ambient-spanish").resolve())
        self.assertEqual(
            ROOT / "skills" / "ambient-spanish-vocab",
            (self.skills / "ambient-spanish-vocab").resolve(),
        )
        self.assertEqual(["unchanged", "unchanged"], self.results(self.run_install("--claude")))

    def test_an_existing_install_is_a_conflict_until_replaced(self) -> None:
        (self.skills / "ambient-spanish").mkdir(parents=True)
        report = self.run_install("--claude", ok=False)
        self.assertEqual("conflict", report["steps"][0]["result"])
        self.assertFalse((self.skills / "ambient-spanish").is_symlink())
        report = self.run_install("--claude", "--replace")
        self.assertEqual("linked", report["steps"][0]["result"])
        self.assertTrue((self.skills / "ambient-spanish.old").is_dir())
        self.assertTrue((self.skills / "ambient-spanish").is_symlink())

    def test_a_checkout_cloned_into_the_skills_dir_counts_as_installed(self) -> None:
        checkout = self.skills / "ambient-spanish"
        (checkout / "scripts").mkdir(parents=True)
        (checkout / "scripts" / "install.py").write_text(
            INSTALL.read_text(encoding="utf-8"), encoding="utf-8"
        )
        result = subprocess.run(
            [sys.executable, str(checkout / "scripts" / "install.py"), "--home", str(self.home), "--claude"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual("unchanged", json.loads(result.stdout)["steps"][0]["result"])
        self.assertFalse(checkout.is_symlink())

    def test_rule_is_added_once_and_removed_without_touching_other_text(self) -> None:
        self.claude_md.parent.mkdir(parents=True)
        self.claude_md.write_text("# Mine\n\nKeep this.\n", encoding="utf-8")
        self.assertEqual("added", self.run_install("--claude", "--rule")["steps"][-1]["result"])
        text = self.claude_md.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Mine\n\nKeep this.\n\n<!-- ambient-spanish:begin -->"))
        self.assertIn(str(self.skills / "ambient-spanish" / "scripts" / "ambient_state.py"), text)
        self.assertEqual("unchanged", self.run_install("--claude", "--rule")["steps"][-1]["result"])
        self.assertEqual(1, self.claude_md.read_text(encoding="utf-8").count("ambient-spanish:begin"))
        self.assertEqual(
            "removed", self.run_install("--claude", "--remove-rule")["steps"][-1]["result"]
        )
        self.assertEqual("# Mine\n\nKeep this.\n", self.claude_md.read_text(encoding="utf-8"))

    def test_codex_uses_its_own_skills_dir_and_agents_file(self) -> None:
        self.run_install("--codex", "--rule")
        self.assertTrue((self.home / ".codex" / "skills" / "ambient-spanish").is_symlink())
        self.assertIn(
            "ambient-spanish:begin",
            (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8"),
        )
        self.assertFalse(self.claude_md.exists())

    def test_dry_run_changes_nothing(self) -> None:
        report = self.run_install("--claude", "--rule", "--dry-run")
        self.assertEqual(["linked", "linked", "added"], self.results(report))
        self.assertFalse(self.skills.exists())
        self.assertFalse(self.claude_md.exists())


class SkillFileTests(unittest.TestCase):
    """Every shipped skill needs a name and a description to be discoverable."""

    SKILL_FILES = {
        "ambient-spanish": ROOT / "SKILL.md",
        "ambient-spanish-vocab": ROOT / "skills" / "ambient-spanish-vocab" / "SKILL.md",
        "ambient-spanish-setup": ROOT / ".claude" / "skills" / "ambient-spanish-setup" / "SKILL.md",
    }

    def frontmatter(self, path: Path) -> dict[str, str]:
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), path)
        block = text[4 : text.index("\n---\n", 4)]
        fields = {}
        for line in block.splitlines():
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        return fields

    def test_each_skill_has_its_name_and_a_description(self) -> None:
        for name, path in self.SKILL_FILES.items():
            fields = self.frontmatter(path)
            self.assertEqual(name, fields.get("name"), path)
            self.assertGreater(len(fields.get("description", "")), 40, path)

    def test_installer_links_the_shipped_skills(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(ROOT / "scripts"))
        import install

        for name, source in install.SKILLS.items():
            self.assertEqual(self.SKILL_FILES[name].parent, source)


if __name__ == "__main__":
    unittest.main()
