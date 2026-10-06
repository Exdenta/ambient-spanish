# ambient-spanish

[![test](https://github.com/Exdenta/ambient-spanish/actions/workflows/test.yml/badge.svg)](https://github.com/Exdenta/ambient-spanish/actions/workflows/test.yml)
[![code: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)
[![data: CC BY-NC-SA 4.0](https://img.shields.io/badge/data-CC%20BY--NC--SA%204.0-lightgrey.svg)](references/levels/LICENSE)

Learn Spanish while you work. ambient-spanish is a skill for Claude Code and Codex. Your assistant's normal English replies come back with Spanish words from your vocabulary mixed in, and a few new words join it every week.

![A Claude Code reply with known Spanish words underlined; hovering "cerrar" shows "cerrar = to close" above the prompt](docs/hover-demo.gif)

<sub>Every Spanish word is underlined; hover one to see its English (the [hover mod](#hover-translations-claude-code) is part of the install).</sub>

- **One vocabulary.** Its words appear in Spanish, always underlined, with the English on hover. Nothing is glossed in the text.
- **New words join weekly.** You pick the pace at setup: 5, 10 or 20 words a week, or your own number. They are added to the vocabulary automatically.
- **Only words change.** The grammar stays English. Code, commands, file paths and quotes are never touched.
- **The calendar sets the pace.** Chatting more doesn't add words faster.
- **Start at your level.** Ready-made word lists cover A0 to C1, or you can build your own.
- **Peninsular Spanish** (es-ES): *ordenador*, *móvil*, *coche*.

## Install

You need Python 3.11 or newer, [Rust](https://rustup.rs) (the installer builds the small `ambient-lookup` tool with `cargo`), and Claude Code or Codex.

### Guided setup (Claude Code)

```bash
git clone https://github.com/Exdenta/ambient-spanish ~/ambient-spanish
cd ~/ambient-spanish && claude
```

Then type `/ambient-spanish-setup`. It asks:
- where to install (Claude Code, Codex or both);
- your level, or runs a quick placement check;
- whether to start from that level's word list or build your own;
- whether the every-reply rule applies to every project or just this one.

Then it installs everything (skills, rule and hover mod) in one step and shows the first words you'll learn.

### Manual setup (Claude Code or Codex)

```bash
git clone https://github.com/Exdenta/ambient-spanish ~/ambient-spanish
cd ~/ambient-spanish
python3 scripts/install.py --claude                    # Codex: --codex
python3 scripts/ambient_state.py vocab --level A2     # your level, A0 to C1
```

| `install.py` flag | What it does |
| --- | --- |
| `--claude`, `--codex` | install for Claude Code or Codex: link the skills into `~/.claude/skills` or `~/.codex/skills`, add the every-reply rule, and (Claude Code) install the [hover mod](#hover-translations-claude-code) |
| `--scope local` | put the rule in this project's `CLAUDE.local.md` / `AGENTS.md` instead of your global file |
| `--remove-rule` | take the rule out again |
| `--replace` | move an existing install aside to `<name>.old` instead of stopping |
| `--dry-run` | show what would change, without changing it |

The skills are linked, not copied, so keep the clone where it is. Re-running the script is safe.

### Update and uninstall

To update, run `git pull` in the clone. Your progress lives in `~/.codex/state/ambient-spanish/`, outside the clone, so updates never reset it.

To uninstall:

```bash
python3 scripts/install.py --claude --remove-rule
rm ~/.claude/skills/ambient-spanish ~/.claude/skills/ambient-spanish-vocab   # removes the links, not the clone
claude plugin uninstall ambient-spanish-hover@ambient-spanish
```

Delete `~/.codex/state/ambient-spanish/` as well if you also want to drop your progress.

## Your vocabulary

Pick your level, and every word up to it counts as known from day one:

| Level | Known words | Covers |
| --- | --- | --- |
| A0 | 143 | greetings and a survival core |
| A1 | 816 | everyday basics: family, food, time, simple actions |
| A2 | 2,102 | routine tasks, shopping, work, travel |
| B1 | 3,179 | opinions, plans, news, most everyday topics |
| B2 | 3,952 | abstract topics, work and debate |
| C1 | 4,463 | nuanced, formal and specialised vocabulary |

Not sure where you are? `python3 scripts/ambient_state.py levels --sample 10` shows ten random words from each level.

A1 to C1 come from [ELELex](https://cental.uclouvain.be/cefrlex/elelex/), a lexicon of Spanish learner textbooks graded by CEFR level. A0 is hand-picked. [references/levels/README.md](references/levels/README.md) explains how words were assigned to levels.

To fine-tune the list, ask your assistant (the `ambient-spanish-vocab` skill handles it), or run `vocab` yourself:

```bash
python3 scripts/ambient_state.py vocab --level B1 --dry-run                     # preview only
python3 scripts/ambient_state.py vocab --level A2 --remove-known missed.txt --learn-first work.txt
python3 scripts/ambient_state.py vocab --keep-known --add-known anki-export.txt   # keep what you have, add more
```

Word lists have one word per line. Words outside the level lists also need `| english | kind`. Tabs work in place of `|`, so Anki exports can be used as they are.

Rebuilding keeps your usage history and restarts the weekly cycle from today. It also saves the previous files as `*.previous`.

## Settings

```bash
python3 scripts/ambient_state.py status                           # vocabulary size, words per week, next batch date
python3 scripts/ambient_state.py configure --pause                # or --resume
python3 scripts/ambient_state.py configure --words-per-week 10    # 5, 10, 20 or any number
```

In Claude Code, replies get Spanish only in the terminal CLI. When `CLAUDE_CODE_ENTRYPOINT` is set to anything else (the desktop app, Cowork, IDE extensions), `context` returns `active: false` with reason `non_cli_client`.

Every vocabulary word can appear in every reply, wherever it fits the sentence.

Don't edit `state.json` or `curriculum.json` by hand. `ambient_state.py` is the only thing that writes them.

## Hover translations (Claude Code)

The `ambient-spanish-hover` mod underlines every vocabulary word in replies. When you hover one, its English appears above the prompt, as in the demo above. `install.py --claude` and the guided setup install it.

Hover needs a terminal that reports the mouse pointer. See the [mod's README](mods/ambient-spanish-hover/README.md) for details and limits.

## How it works

For each reply, the assistant:
1. runs `ambient_state.py context` once;
2. drafts the reply in English and pipes the draft to `ambient-lookup`, a Rust tool that returns the words and phrases that have a Spanish equivalent in your vocabulary, so the assistant never loads the word list;
3. writes the final reply with those words;
4. runs `record` with the words it actually used.

Which words are in your vocabulary depends only on the date:

```
batch_index = (today - start_date) // 7
vocabulary  = curriculum[: baseline_known_count + (batch_index + 1) * words_per_week]
```

Change the pace later with `ambient_state.py configure --words-per-week N`.

| File | Role |
| --- | --- |
| `SKILL.md` | the instructions the assistant follows on every reply |
| `rust/ambient-lookup/` | the lookup tool: reads `vocabulary.txt` and finds the vocabulary words in a draft, in under a millisecond |
| `scripts/ambient_state.py` | the only writer of state: `context`, `record`, `status`, `configure`, `levels`, `vocab` |
| `references/curriculum.json` | the default word list, used until you run `vocab` |
| `references/levels/lexicon.tsv` | about 4,500 words graded A0–C1, which `vocab` builds your list from |
| `references/state-contract.md` | state schema and migration rules, for maintainers |
| `skills/ambient-spanish-vocab/` | the skill for levels, placement checks and word lists |
| `.claude/skills/ambient-spanish-setup/` | the guided setup, available when Claude Code is opened in the clone |
| `~/.codex/state/ambient-spanish/` | your progress (`state.json`), your word list (`curriculum.json`) and the vocabulary file (`vocabulary.txt`) that the assistant and the hover mod read |

## Contributing

Bug reports and word-list fixes are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and for the rules on changing the curriculum and level data. [CHANGELOG.md](CHANGELOG.md) lists what has changed.

## License

The code is under the [MIT licence](LICENSE).

The level data in `references/levels/` (`lexicon.tsv`, `excluded.tsv`) is derived from ELELex and is under [CC BY-NC-SA 4.0](references/levels/LICENSE). You may share and adapt it for non-commercial use, with credit and under the same licence.
