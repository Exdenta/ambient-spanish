from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install.py"


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.home = Path(self.temp_dir.name)
        self.skills = self.home / ".claude" / "skills"
        self.claude_md = self.home / ".claude" / "CLAUDE.md"
        # No `claude` CLI on PATH, so the hover step is skipped instead of touching real plugins.
        self.env = {"PATH": str(self.home / "no-bin"), "AMBIENT_SPANISH_SKIP_BUILD": "1"}

    def run_install(self, *args: str, ok: bool = True) -> dict:
        result = subprocess.run(
            [sys.executable, str(INSTALL), "--home", str(self.home), *args],
            text=True,
            capture_output=True,
            check=False,
            env=self.env,
        )
        self.assertEqual(0 if ok else 1, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def results(self, report: dict) -> list[str]:
        return [step["result"] for step in report["steps"]]

    def test_links_both_skills_and_is_idempotent(self) -> None:
        self.assertEqual(
            ["linked", "linked", "added", "skipped", "skipped"], self.results(self.run_install("--claude"))
        )
        self.assertEqual(ROOT, (self.skills / "ambient-spanish").resolve())
        self.assertEqual(
            ROOT / "skills" / "ambient-spanish-vocab",
            (self.skills / "ambient-spanish-vocab").resolve(),
        )
        self.assertEqual(
            ["unchanged", "unchanged", "unchanged", "skipped", "skipped"], self.results(self.run_install("--claude"))
        )

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
            env=self.env,
        )
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertEqual("unchanged", json.loads(result.stdout)["steps"][0]["result"])
        self.assertFalse(checkout.is_symlink())

    def test_rule_is_added_once_and_removed_without_touching_other_text(self) -> None:
        self.claude_md.parent.mkdir(parents=True)
        self.claude_md.write_text("# Mine\n\nKeep this.\n", encoding="utf-8")
        self.assertEqual("added", self.run_install("--claude")["steps"][2]["result"])
        text = self.claude_md.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Mine\n\nKeep this.\n\n<!-- ambient-spanish:begin -->"))
        self.assertIn(str(self.skills / "ambient-spanish" / "scripts" / "ambient_state.py"), text)
        self.assertEqual("unchanged", self.run_install("--claude")["steps"][2]["result"])
        self.assertEqual(1, self.claude_md.read_text(encoding="utf-8").count("ambient-spanish:begin"))
        self.assertEqual(
            "removed", self.run_install("--claude", "--remove-rule")["steps"][-1]["result"]
        )
        self.assertEqual("# Mine\n\nKeep this.\n", self.claude_md.read_text(encoding="utf-8"))

    def test_codex_uses_its_own_skills_dir_and_agents_file(self) -> None:
        self.run_install("--codex")
        self.assertTrue((self.home / ".codex" / "skills" / "ambient-spanish").is_symlink())
        self.assertIn(
            "ambient-spanish:begin",
            (self.home / ".codex" / "AGENTS.md").read_text(encoding="utf-8"),
        )
        self.assertFalse(self.claude_md.exists())

    def test_local_scope_writes_the_rule_into_the_project_only(self) -> None:
        project = self.home / "project"
        project.mkdir()
        report = self.run_install("--claude", "--codex", "--scope", "local", "--project-dir", str(project))
        self.assertIn("ambient-spanish:begin", (project / "CLAUDE.local.md").read_text(encoding="utf-8"))
        self.assertIn("ambient-spanish:begin", (project / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertFalse(self.claude_md.exists())
        self.assertNotIn("conflict", self.results(report))

    def test_dry_run_changes_nothing(self) -> None:
        report = self.run_install("--claude", "--dry-run")
        self.assertEqual(["linked", "linked", "added", "skipped", "skipped"], self.results(report))
        self.assertFalse(self.skills.exists())
        self.assertFalse(self.claude_md.exists())


class BuildLookupTests(unittest.TestCase):
    """The step that builds the Rust `ambient-lookup` binary, with cargo faked out."""

    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(ROOT / "scripts"))
        import install

        self.install = install
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        crate = Path(temp.name) / "crate"
        (crate / "src").mkdir(parents=True)
        (crate / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        (crate / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        self.crate = crate
        patches = [
            mock.patch.object(install, "LOOKUP_CRATE", crate),
            mock.patch.object(install, "LOOKUP_BIN", crate / "target" / "release" / "ambient-lookup"),
            mock.patch.dict("os.environ", {}, clear=False),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("AMBIENT_SPANISH_SKIP_BUILD", None)

    def make_binary(self, *, newer: bool) -> Path:
        binary = self.install.LOOKUP_BIN
        binary.parent.mkdir(parents=True)
        binary.write_text("", encoding="utf-8")
        sources = [self.crate / "Cargo.toml", self.crate / "src" / "main.rs"]
        base = max(path.stat().st_mtime for path in sources)
        stamp = base + 10 if newer else base - 10
        os.utime(binary, (stamp, stamp))
        return binary

    def test_skip_env_skips(self) -> None:
        os.environ["AMBIENT_SPANISH_SKIP_BUILD"] = "1"
        self.assertEqual("skipped", self.install.build_lookup(dry_run=False)["result"])

    def test_missing_cargo_fails_with_the_rustup_hint(self) -> None:
        with mock.patch("shutil.which", return_value=None):
            step = self.install.build_lookup(dry_run=False)
        self.assertEqual("failed", step["result"])
        self.assertIn("https://rustup.rs", step["output"])

    def test_dry_run_reports_would_build_and_runs_nothing(self) -> None:
        with mock.patch("shutil.which", return_value="/usr/bin/cargo"), mock.patch(
            "subprocess.run"
        ) as run:
            step = self.install.build_lookup(dry_run=True)
        self.assertEqual("would build", step["result"])
        run.assert_not_called()

    def test_build_runs_cargo_release_on_the_crate_manifest(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch("shutil.which", return_value="/usr/bin/cargo"), mock.patch(
            "subprocess.run", return_value=completed
        ) as run:
            step = self.install.build_lookup(dry_run=False)
        self.assertEqual("built", step["result"])
        self.assertEqual(
            ["/usr/bin/cargo", "build", "--release", "--manifest-path", str(self.crate / "Cargo.toml")],
            run.call_args.args[0],
        )

    def test_a_failed_cargo_build_is_reported(self) -> None:
        completed = subprocess.CompletedProcess([], 101, "", "error: boom")
        with mock.patch("shutil.which", return_value="/usr/bin/cargo"), mock.patch(
            "subprocess.run", return_value=completed
        ):
            step = self.install.build_lookup(dry_run=False)
        self.assertEqual("failed", step["result"])
        self.assertIn("boom", step["output"])

    def test_a_binary_newer_than_every_source_is_unchanged(self) -> None:
        self.make_binary(newer=True)
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual("unchanged", self.install.build_lookup(dry_run=False)["result"])

    def test_a_binary_older_than_a_source_is_rebuilt(self) -> None:
        self.make_binary(newer=False)
        with mock.patch("shutil.which", return_value="/usr/bin/cargo"):
            self.assertEqual("would build", self.install.build_lookup(dry_run=True)["result"])

    def test_remove_rule_does_not_build(self) -> None:
        env = {"PATH": "/nonexistent"}
        result = subprocess.run(
            [sys.executable, str(INSTALL), "--home", self.crate.parent.as_posix(), "--claude", "--remove-rule"],
            text=True, capture_output=True, check=False, env=env,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        steps = json.loads(result.stdout)["steps"]
        self.assertNotIn("build ambient-lookup", [step["step"] for step in steps])


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
