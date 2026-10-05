#!/usr/bin/env python3
"""Install ambient-spanish from this checkout into Claude Code and/or Codex.

    python3 scripts/install.py --claude --hover --rule
    python3 scripts/install.py --codex --rule
    python3 scripts/install.py --claude --remove-rule

Skills are symlinked, not copied, so `git pull` in this checkout updates every
install. Re-running is safe: finished steps report `unchanged`. An existing
install that is not a link to this checkout is a `conflict` and is left alone
unless `--replace` is given, which moves it aside to `<name>.old`.

Learner state is not touched here; `scripts/ambient_state.py` owns it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
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
RULE_BEGIN = "<!-- ambient-spanish:begin -->"
RULE_END = "<!-- ambient-spanish:end -->"


def rule_block(skill_root: Path) -> str:
    return "\n".join(
        [
            RULE_BEGIN,
            "## Ambient Spanish",
            "",
            "Before every user-facing reply, follow the `ambient-spanish` skill: run",
            f"`python3 {skill_root}/scripts/ambient_state.py context` once, substitute the",
            "terms it returns, then `record` the ones you used. Pause with",
            "`ambient_state.py configure --pause`.",
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


def set_rule(path: Path, block: str | None, *, dry_run: bool) -> dict[str, Any]:
    """Insert, refresh, or (with `block=None`) remove the marked rule block."""
    step = {"step": "remove rule" if block is None else "add rule", "path": str(path)}
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    begin, end = text.find(RULE_BEGIN), text.find(RULE_END)
    if begin != -1 and end != -1:
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(updated, encoding="utf-8")
    return {**step, "result": result}


def install_hover(*, dry_run: bool) -> dict[str, Any]:
    step = {"step": "install hover mod", "plugin": HOVER_PLUGIN}
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--claude", action="store_true", help="Install into Claude Code")
    parser.add_argument("--codex", action="store_true", help="Install into Codex")
    parser.add_argument("--hover", action="store_true", help="Install the Claude Code hover mod")
    rule = parser.add_mutually_exclusive_group()
    rule.add_argument("--rule", action="store_true", help="Add the run-every-reply rule")
    rule.add_argument("--remove-rule", action="store_true", help="Remove that rule")
    parser.add_argument("--replace", action="store_true", help="Move a conflicting install aside")
    parser.add_argument("--dry-run", action="store_true", help="Report without changing anything")
    parser.add_argument("--home", help="Home directory to install under (default: yours)")
    args = parser.parse_args(argv)
    tools = [tool for tool in TOOLS if getattr(args, tool)]
    if not tools and not args.hover:
        parser.error("choose at least one of --claude, --codex, --hover")

    home = Path(args.home).expanduser() if args.home else Path.home()
    steps: list[dict[str, Any]] = []
    for tool in tools:
        skills_dir, instructions = (home / part for part in TOOLS[tool])
        for name, source in SKILLS.items():
            steps.append(
                {"tool": tool, **link_skill(name, source, skills_dir, replace=args.replace, dry_run=args.dry_run)}
            )
        if args.rule or args.remove_rule:
            block = None if args.remove_rule else rule_block(skills_dir / "ambient-spanish")
            steps.append({"tool": tool, **set_rule(instructions, block, dry_run=args.dry_run)})
    if args.hover:
        steps.append({"tool": "claude", **install_hover(dry_run=args.dry_run)})

    ok = all(step["result"] not in ("conflict", "failed") for step in steps)
    print(json.dumps({"ok": ok, "dry_run": args.dry_run, "steps": steps}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
