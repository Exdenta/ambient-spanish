---
name: ambient-spanish-setup
description: Install and configure ambient-spanish from this checkout. Links the skill into this assistant and/or Codex, asks the learner's Spanish level, imports a ready-made A0–C1 vocabulary pack or builds a custom one, and installs everything in one go: both skills, the run-every-reply rule and the hover-translation mod. Use when the user asks to set up, install, reinstall or onboard ambient-spanish, or runs /ambient-spanish-setup.
---

# Set up ambient-spanish

This skill runs inside a checkout of the ambient-spanish repo. Run every command from the repo root (`git rev-parse --show-toplevel`). `S=scripts/ambient_state.py` below.

Ask with AskUserQuestion when available, otherwise in plain text. Don't ask about anything you can check.

## 1. Check what is already there

```bash
python3 --version                                    # 3.11 or newer
python3 scripts/install.py --claude --codex --dry-run
ls -la "${AMBIENT_SPANISH_STATE:-$HOME/.codex/state/ambient-spanish/state.json}"
```

- **Python older than 3.11:** stop, and tell the user what to install.
- **The dry run reports a `conflict`:** an earlier install is in the way. If it's a directory, check whether it is a git clone with `git -C <path> remote -v`. Ask before re-running with `--replace`, which moves it to `<name>.old`; nothing is deleted.
- **A state file exists:** run `python3 $S status`.
  - If it loads, the learner has progress. Note `curriculum.source`, `known_count` and how many `used_terms` they have.
  - If it fails with a schema or field error, it was written by an older, incompatible version. Show the error and offer to move it aside to `state.json.legacy-<date>.backup`. Never delete it.

## 2. Ask

Run `python3 $S levels` first so the options can show word counts (`known_if_chosen`).

Make one AskUserQuestion call with these questions:
1. **Install for:** Claude Code (recommended) / Codex / Both.
2. **Spanish level:**
   - Beginner (A0–A1)
   - Elementary–intermediate (A2–B1)
   - Advanced (B2–C1)
   - Not sure (quick placement check)
3. **Vocabulary:**
   - Quick start: import the level pack as known words (recommended)
   - Build my own: placement check, add or remove words, choose what to learn
4. **New-word regime:** how many new words join the vocabulary each week.
   - 10 per week (recommended)
   - 5 per week (gentle)
   - 20 per week (fast)
   - My own number

Then ask the follow-ups (AskUserQuestion takes at most four questions per call, so these go in a second call):
- **Rule scope** (the run-every-reply rule the hover mod and the skill depend on):
  - Global: every project (recommended). Goes in `~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md`.
  - This project only. Goes in `./CLAUDE.local.md` / `./AGENTS.md`.
- **Custom regime:** if they chose "My own number", ask for the words per week.
- **Exact level:** if a band was picked, ask which of its two levels. Give each one's `description` and known-word count, for example "A2: 2,100 words known".
- **Existing progress:** if the learner has used words already, ask whether to keep today's known words and add the level on top (`--keep-known`), or start from the level alone.
- **Not sure:** do the placement check from `skills/ambient-spanish-vocab/SKILL.md` (step 3) to pick the level.

## 3. Install

```bash
python3 scripts/install.py --claude [--codex] [--scope local]
```

There are no separate installs: one run links both skills, adds the rule and, with `--claude`, installs the hover mod (needs the `claude` CLI; reported as `skipped` without it).

- `--claude` and `--codex` come from question 1.
- `--scope local` is for "This project only"; the default is global.
- The rule is a marked block that runs the skill on every reply. `--remove-rule` takes it out again.

Report any step whose result is `failed`, with its output. Don't retry blindly.

## 4. Vocabulary

**Quick start:**

```bash
python3 $S vocab --level <LEVEL> --words-per-week <N> [--keep-known] --dry-run   # preview
python3 $S vocab --level <LEVEL> --words-per-week <N> [--keep-known]
```

**Build my own:** read `skills/ambient-spanish-vocab/SKILL.md` now and follow its workflow from step 2, passing `--words-per-week <N>` on the final `vocab` run. Its placement check, word lists and learning targets all end in a `vocab` run.

**Density:** if `known_count` is over about 1,500 (B1 and up), ask about density:
- **Every known word, every reply.** This is the default. The assistant reads the word list once per session, at roughly 12 tokens per word.
- **A rotating sample of 60 per reply.** Lighter, and suggested for B2–C1. Set it with `python3 $S configure --known-per-reply 60`.

## 5. Finish

Run `python3 $S status` and report:
- what was installed, and where;
- the level and number of known words;
- the weekly regime (`words_per_week`), this week's new words from the `vocab` result's `new_this_week`, as `spanish (english)`, and `next_batch_date`.

Then tell the user what's next:
- **Hover mod and rule:** run `/reload-plugins` and start a new session.
- **Changing the vocabulary later:** ask for it, or use the `ambient-spanish-vocab` skill.
- **Pausing:** `python3 $S configure --pause`.
