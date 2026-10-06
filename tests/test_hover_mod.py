from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "mods" / "ambient-spanish-hover"
MANIFEST = MOD / ".claude-plugin" / "plugin.json"
# The version the mod's code was last released under, and a hash of that code.
FINGERPRINT = Path(__file__).with_name("hover_mod_fingerprint.json")


def code_fingerprint() -> str:
    """Hash of every file under hooks/ that ships, tests excluded."""
    digest = hashlib.sha256()
    for path in sorted((MOD / "hooks").rglob("*")):
        if path.is_file() and ".test." not in path.name:
            digest.update(path.relative_to(MOD).as_posix().encode("utf-8") + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


class HoverModVersionTests(unittest.TestCase):
    def test_code_changes_come_with_a_version_bump(self) -> None:
        # A GitHub install runs a cached copy that `claude plugin update`
        # replaces only when the version moves.
        version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
        recorded = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
        current = code_fingerprint()
        if current == recorded["sha256"] and version == recorded["version"]:
            return
        self.assertNotEqual(
            recorded["version"],
            version,
            "The hover mod's code changed: bump `version` in "
            f"{MANIFEST.relative_to(ROOT)}, then update {FINGERPRINT.name}",
        )
        self.fail(
            f"Record the new release in tests/{FINGERPRINT.name}: "
            + json.dumps({"version": version, "sha256": current})
        )


if __name__ == "__main__":
    unittest.main()
