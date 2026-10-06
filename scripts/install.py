#!/usr/bin/env python3
"""Install ambient-spanish from this checkout into Claude Code and/or Codex.

    python3 scripts/install.py --claude                  # skills + rule + hover mod
    python3 scripts/install.py --claude --codex --scope local
    python3 scripts/install.py --claude --remove-rule

One install does everything: it links both skills, adds the run-every-reply
rule and, for Claude Code, installs the hover mod. The rule goes in your global
instructions file, or with `--scope local` in this project only.

Skills are symlinked, not copied, so `git pull` in this checkout updates every
install. Re-running is safe: finished steps report `unchanged`. An existing
install that is not a link to this checkout is a `conflict` and is left alone
unless `--replace` is given, which moves it aside to `<name>.old`.

Learner state is not touched here; `scripts/ambient_state.py` owns it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SKILLS = {
    "ambient-spanish": ROOT,
    "ambient-spanish-vocab": ROOT / "skills" / "ambient-spanish-vocab",
}
TOOLS = {
    # tool: (skills dir, global instructions file), relative to the home directory
    "claude": (Path(".claude") / "skills", Path(".claude") / "CLAUDE.md"),
    "codex": (Path(".codex") / "skills", Path(".codex") / "AGENTS.md"),
}
MARKETPLACE = "ambient-spanish"
HOVER_PLUGIN = "ambient-spanish-hover@ambient-spanish"
LOCAL_INSTRUCTIONS = {
    # tool: per-project instructions file, relative to the project directory
    "claude": Path("CLAUDE.local.md"),
    "codex": Path("AGENTS.md"),
}
LOOKUP_CRATE = ROOT / "rust" / "ambient-lookup"
LOOKUP_BIN = LOOKUP_CRATE / "target" / "release" / "ambient-lookup"
SKIP_BUILD_ENV = "AMBIENT_SPANISH_SKIP_BUILD"
RULE_BEGIN = "<!-- ambient-spanish:begin -->"
RULE_END = "<!-- ambient-spanish:end -->"


def rule_block(skill_root: Path) -> str:
    return "\n".join(
        [
            RULE_BEGIN,
            "## Ambient Spanish",
            "",
            "Before every user-facing reply, follow the `ambient-spanish` skill: pipe your",
            f"draft to `python3 {skill_root}/scripts/ambient_state.py lookup` once,",
            "substitute the matches it returns, then `record` the ones you used. Pause",
            "with `ambient_state.py configure --pause`.",
            RULE_END,
        ]
    )


def link_skill(name: str, source: Path, skills_dir: Path, *, replace: bool, dry_run: bool) -> dict[str, Any]:
    target = skills_dir / name
    step = {"step": f"link {name}", "path": str(target), "source": str(source)}
    # A link to this checkout, or the checkout itself cloned straight into the
    # skills directory, is already installed.
    if (target.exists() or target.is_symlink()) and target.resolve() == source.resolve():
        return {**step, "result": "unchanged"}
    if target.exists() or target.is_symlink():
        if not replace:
            kind = "link to " + str(target.resolve()) if target.is_symlink() else "directory"
            return {**step, "result": "conflict", "found": kind}
        aside = target.with_name(name + ".old")
        if aside.exists() or aside.is_symlink():
            return {**step, "result": "conflict", "found": f"{aside} is in the way"}
        if not dry_run:
            target.rename(aside)
        step["moved_aside_to"] = str(aside)
    if not dry_run:
        skills_dir.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=True)
    return {**step, "result": "linked"}


def _write_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    temp = Path(handle.name)
    try:
        with handle:
            handle.write(text)
        if path.exists():
            shutil.copymode(path, temp)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def set_rule(path: Path, block: str | None, *, dry_run: bool) -> dict[str, Any]:
    """Insert, refresh, or (with `block=None`) remove the marked rule block.

    Damaged markers (an end before its begin, or a begin with no end) are
    reported as `failed` and the file is left alone. A file that removal leaves
    empty is deleted, since this installer was the only thing in it.
    """
    step = {"step": "remove rule" if block is None else "add rule", "path": str(path)}
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    begin, end = text.find(RULE_BEGIN), text.find(RULE_END)
    if (begin == -1) != (end == -1) or (begin != -1 and end < begin):
        problem = (
            "the begin marker has no end marker"
            if begin != -1 and end == -1
            else "the end marker has no begin marker"
            if begin == -1
            else "the end marker comes before the begin marker"
        )
        return {
            **step,
            "result": "failed",
            "output": f"{path}: {problem}; fix or delete the ambient-spanish block by hand and re-run",
        }
    if begin != -1:
        before, after = text[:begin].rstrip("\n"), text[end + len(RULE_END) :].lstrip("\n")
        if block is not None and text[begin : end + len(RULE_END)] == block:
            return {**step, "result": "unchanged"}
        parts = [part for part in (before, block, after) if part]
        updated = "\n\n".join(parts) + "\n" if parts else ""
        result = "removed" if block is None else "updated"
    elif block is None:
        return {**step, "result": "unchanged"}
    else:
        updated = (text.rstrip("\n") + "\n\n" if text.strip() else "") + block + "\n"
        result = "added"
    if not dry_run:
        if not updated.strip():
            path.unlink(missing_ok=True)
        else:
            _write_atomically(path, updated)
    return {**step, "result": result}


def install_hover(*, dry_run: bool, isolated: bool = False) -> dict[str, Any]:
    step = {"step": "install hover mod", "plugin": HOVER_PLUGIN}
    if isolated:
        # The plugin lands in the real Claude config whatever `--home` says.
        return {
            **step,
            "result": "skipped",
            "reason": f"--home or {SKIP_BUILD_ENV}=1 means an isolated install",
        }
    claude = shutil.which("claude")
    if claude is None:
        return {**step, "result": "skipped", "reason": "the claude CLI is not on PATH"}
    if dry_run:
        return {**step, "result": "would install"}
    listed = subprocess.run(
        [claude, "plugin", "marketplace", "list"], text=True, capture_output=True, check=False
    )
    if MARKETPLACE not in listed.stdout:
        added = subprocess.run(
            [claude, "plugin", "marketplace", "add", str(ROOT)],
            text=True,
            capture_output=True,
            check=False,
        )
        if added.returncode != 0:
            return {**step, "result": "failed", "output": (added.stdout + added.stderr).strip()}
    installed = subprocess.run(
        [claude, "plugin", "install", HOVER_PLUGIN], text=True, capture_output=True, check=False
    )
    output = (installed.stdout + installed.stderr).strip()
    if installed.returncode != 0 and "already" not in output.lower():
        return {**step, "result": "failed", "output": output}
    return {**step, "result": "installed", "next": "run /reload-plugins or start a new session"}


def _lookup_is_current() -> bool:
    if not LOOKUP_BIN.is_file():
        return False
    built = LOOKUP_BIN.stat().st_mtime
    sources = [
        LOOKUP_CRATE / "Cargo.toml",
        LOOKUP_CRATE / "Cargo.lock",
        *(LOOKUP_CRATE / "src").rglob("*"),
    ]
    return all(path.stat().st_mtime <= built for path in sources if path.is_file())


def build_lookup(*, dry_run: bool) -> dict[str, Any]:
    """Build the `ambient-lookup` binary the agent pipes each draft through."""
    step = {"step": "build ambient-lookup", "path": str(LOOKUP_BIN)}
    if os.environ.get(SKIP_BUILD_ENV) == "1":
        skipped = {**step, "result": "skipped", "reason": f"{SKIP_BUILD_ENV}=1"}
        if not LOOKUP_BIN.is_file():
            skipped["note"] = "no binary is built yet, so `ambient_state.py lookup` will fail until it is"
        return skipped
    if _lookup_is_current():
        return {**step, "result": "unchanged"}
    cargo = shutil.which("cargo")
    if cargo is None:
        return {
            **step,
            "result": "failed",
            "output": "cargo is not on PATH; install Rust from https://rustup.rs and re-run",
        }
    if dry_run:
        return {**step, "result": "would build"}
    built = subprocess.run(
        [cargo, "build", "--release", "--manifest-path", str(LOOKUP_CRATE / "Cargo.toml")],
        text=True,
        capture_output=True,
        check=False,
    )
    if built.returncode != 0:
        return {**step, "result": "failed", "output": (built.stdout + built.stderr).strip()[-2000:]}
    return {**step, "result": "built"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--claude", action="store_true", help="Install into Claude Code")
    parser.add_argument("--codex", action="store_true", help="Install into Codex")
    parser.add_argument(
        "--scope",
        choices=("global", "local"),
        default="global",
        help="Put the run-every-reply rule in your global instructions file (default) or in one project",
    )
    parser.add_argument("--project-dir", help="Project for --scope local (default: current directory)")
    parser.add_argument("--remove-rule", action="store_true", help="Remove the rule instead of installing")
    parser.add_argument("--replace", action="store_true", help="Move a conflicting install aside")
    parser.add_argument("--dry-run", action="store_true", help="Report without changing anything")
    parser.add_argument("--home", help="Home directory to install under (default: yours)")
    args = parser.parse_args(argv)
    tools = [tool for tool in TOOLS if getattr(args, tool)]
    if not tools:
        parser.error("choose at least one of --claude, --codex")

    home = Path(args.home).expanduser() if args.home else Path.home()
    steps: list[dict[str, Any]] = []
    for tool in tools:
        skills_dir, instructions = (home / part for part in TOOLS[tool])
        for name, source in () if args.remove_rule else SKILLS.items():
            steps.append(
                {"tool": tool, **link_skill(name, source, skills_dir, replace=args.replace, dry_run=args.dry_run)}
            )
        if args.scope == "local":
            project = Path(args.project_dir).expanduser() if args.project_dir else Path.cwd()
            instructions = project / LOCAL_INSTRUCTIONS[tool]
        block = None if args.remove_rule else rule_block(skills_dir / "ambient-spanish")
        steps.append({"tool": tool, **set_rule(instructions, block, dry_run=args.dry_run)})
    if not args.remove_rule:
        steps.append(build_lookup(dry_run=args.dry_run))
    if "claude" in tools and not args.remove_rule:
        isolated = bool(args.home) or os.environ.get(SKIP_BUILD_ENV) == "1"
        steps.append({"tool": "claude", **install_hover(dry_run=args.dry_run, isolated=isolated)})

    ok = all(step["result"] not in ("conflict", "failed") for step in steps)
    print(json.dumps({"ok": ok, "dry_run": args.dry_run, "steps": steps}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
