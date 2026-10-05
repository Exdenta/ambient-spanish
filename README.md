# ambient-spanish

[![test](https://github.com/Exdenta/ambient-spanish/actions/workflows/test.yml/badge.svg)](https://github.com/Exdenta/ambient-spanish/actions/workflows/test.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Learn Spanish by reading your normal AI-assistant replies. The assistant swaps a few English words for Spanish ones you've already met, and introduces three new words every three days. Works as a skill for Claude Code and Codex.

```
The build is listo. Let me buscar the failing test and evitar (to avoid) the old cache.
```

![A Claude Code reply with known Spanish words underlined; hovering "cerrar" shows "cerrar = to close" above the prompt](docs/hover-demo.gif)

<sub>Known words are underlined by the optional [hover mod](#hover-translations-claude-code); hover one to see its English. [MP4](docs/hover-demo.mp4)</sub>

| Tier | What it is | How it appears |
| --- | --- | --- |
| `known` | words unlocked so far (your level's words at the start, growing over time) | bare Spanish, no gloss |
| `learning` | the current batch of 3 words | Spanish followed by `(english)` on first use in a reply |

Only words are swapped. The sentence stays English: articles, prepositions and verb structure are never translated, and code, commands, paths and quotations are never touched.

## Install

Requires Python 3.11 or newer.

### Guided setup (Claude Code)

```bash
git clone https://github.com/Exdenta/ambient-spanish ~/ambient-spanish
cd ~/ambient-spanish && claude
```

Then run `/ambient-spanish-setup`. It asks:
- where to install it (Claude Code, Codex or both);
- your level, A0 to C1 (or gives you a quick placement check);
- whether to import that level's words as a quick start or build your own vocabulary;
- whether to add hover translations and the run-every-reply rule.

It then installs everything and shows the first words you'll learn.

### Manual setup

```bash
python3 scripts/install.py --claude            # or --codex; add --hover and/or --rule
python3 scripts/ambient_state.py levels        # level packs and their sizes
python3 scripts/ambient_state.py vocab --level A2
```

`install.py` links the skills into `~/.claude/skills` or `~/.codex/skills` rather than copying them, so `git pull` in the clone updates every install. Keep the clone where it is. The script is safe to re-run, and it leaves an existing install alone unless you pass `--replace`, which moves the old one to `<name>.old`.

- `--rule` adds a marked block to your global `CLAUDE.md` or `AGENTS.md` so the skill runs on every reply instead of relying on the description match. `--remove-rule` takes it out.
- `--hover` installs the [hover mod](#hover-translations-claude-code).

Your progress lives outside the clone and is not affected by updates.

## How it works

Pacing is a pure function of the calendar, so chatting more never unlocks words faster.

```
learning_start = baseline_known_count + batch_index * batch_size
known          = curriculum[:learning_start]
learning       = curriculum[learning_start : learning_start + batch_size]
```

Each reply, the assistant runs `context` once, writes the reply with the words it was given, then runs `record` with the ones it used. `status` reports words that were offered but never fit (`cold_terms`).

| Piece | Job |
| --- | --- |
| `SKILL.md` | the instructions the assistant follows on every reply |
| `scripts/ambient_state.py` | the only writer of state: `context` says which words are in scope, `record` logs which were used |
| `references/curriculum.json` | the default ordered word list, Peninsular Spanish (`es-ES`) |
| `references/levels/lexicon.tsv` | about 4,500 words graded A0–C1, which `vocab` builds personal word lists from |
| `references/state-contract.md` | state schema and migration rules, for maintainers |
| `skills/ambient-spanish-vocab/` | skill for choosing a level, importing word lists and picking what to learn |
| `.claude/skills/ambient-spanish-setup/` | the guided setup, available when Claude Code is opened in the clone |
| `~/.codex/state/ambient-spanish/state.json` | your progress; kept outside the skill so updating the skill never resets it |
| `~/.codex/state/ambient-spanish/curriculum.json` | your personal word list, written by `vocab`; replaces the default when present |
| `~/.codex/state/ambient-spanish/vocabulary.txt` | all known words as `id \| spanish \| english`, rewritten when a batch is promoted |

## Vocabulary levels

New users can start from what they already know. `vocab --level B1` marks every word up to B1 as known and teaches from there:

| Level | Known words | Roughly |
| --- | --- | --- |
| A0 | 143 | greetings and a survival core |
| A1 | 816 | everyday basics: family, food, time, simple actions |
| A2 | 2,102 | routine tasks, shopping, work, travel |
| B1 | 3,179 | opinions, plans, news, most everyday topics |
| B2 | 3,952 | abstract topics, work and debate |
| C1 | 4,463 | nuanced, formal and specialised vocabulary |

A1 to C1 come from [ELELex](https://cental.uclouvain.be/cefrlex/elelex/), a lexicon of Spanish learner textbooks graded by CEFR level. A0 is hand-picked. [references/levels/README.md](references/levels/README.md) explains how levels were assigned.

To fine-tune the list, use the `ambient-spanish-vocab` skill (or `vocab` directly). It can:
- run a placement check;
- mark words from an Anki export or a pasted list as known;
- take out words you don't know yet;
- queue words you want to learn first.

```bash
python3 scripts/ambient_state.py vocab --level A2 --remove-known missed.txt --learn-first work.txt
python3 scripts/ambient_state.py vocab --keep-known --add-known anki-export.txt
```

Word lists take one `spanish | english | kind` per line, with english and kind needed only for words outside the lexicon. Tabs work in place of `|`. Every rebuild keeps your usage history and saves the previous files as `*.previous`.

## Configuration

```bash
python3 scripts/ambient_state.py status
python3 scripts/ambient_state.py configure --pause        # or --resume
python3 scripts/ambient_state.py configure --known-per-reply 18    # density cap; `all` removes it
python3 scripts/ambient_state.py configure --cadence-days 3 --batch-size 3
python3 scripts/ambient_state.py configure --dialect es-ES
python3 scripts/ambient_state.py vocab --level B1 --dry-run   # preview a vocabulary rebuild
```

With a large known list (B1 and up), `--known-per-reply 60` offers a rotating sample per reply instead of the whole list.

Don't reset or edit `state.json` by hand; `ambient_state.py` is the authority.

## Hover translations (Claude Code)

`mods/ambient-spanish-hover` underlines known Spanish words in Claude Code replies and shows their English in a row above the prompt when you hover one. See its [README](mods/ambient-spanish-hover/README.md) for install and limits.

## Contributing

Bug reports and word-list fixes are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and the rules for changing the curriculum, and [CHANGELOG.md](CHANGELOG.md) for what's changed.

## License

Code: [MIT](LICENSE).

The level data in `references/levels/` (`lexicon.tsv`, `excluded.tsv`) is derived from ELELex and is under [CC BY-NC-SA 4.0](references/levels/LICENSE). You may share and adapt it for non-commercial use, with credit and under the same licence.
