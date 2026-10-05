# ambient-spanish

Learn Spanish by reading your normal AI-assistant replies: the assistant swaps a few English words for Spanish ones you've already met, and introduces three new words every three days. Works as a skill for Claude Code and Codex.

## What a reply looks like

```
The build is listo. Let me buscar the failing test and evitar (to avoid) the old cache.
```

| Tier | What it is | How it appears |
| --- | --- | --- |
| `known` | words unlocked so far (321 at the start, growing over time) | bare Spanish, no gloss |
| `learning` | the current batch of 3 words | Spanish followed by `(english)` on first use in a reply |

Only words are swapped. The sentence stays English: articles, prepositions and verb structure are never translated, and code, commands, paths and quotations are never touched.

## How it works

Pacing is a pure function of the calendar, so chatting more never unlocks words faster.

```
learning_start = baseline_known_count + batch_index * batch_size
known          = curriculum[:learning_start]
learning       = curriculum[learning_start : learning_start + batch_size]
```

| Piece | Job |
| --- | --- |
| `SKILL.md` | the instructions the assistant follows on every reply |
| `scripts/ambient_state.py` | the only writer of state: `context` says which words are in scope, `record` logs which were used |
| `references/curriculum.json` | the ordered word list, Peninsular Spanish (`es-ES`) |
| `references/state-contract.md` | state schema and migration rules, for maintainers |
| `~/.codex/state/ambient-spanish/state.json` | your progress; kept outside the skill so updating the skill never resets it |
| `~/.codex/state/ambient-spanish/vocabulary.txt` | all known words as `id \| spanish \| english`, rewritten when a batch is promoted |

Each reply, the assistant runs `context` once, writes the reply with the words it was given, then runs `record` with the ones it used. `status` reports words that were offered but never fit (`cold_terms`).

## Install

Clone into the skills folder of the tool you use:

```bash
git clone https://github.com/Exdenta/ambient-spanish ~/.claude/skills/ambient-spanish   # Claude Code
git clone https://github.com/Exdenta/ambient-spanish ~/.codex/skills/ambient-spanish    # Codex
python3 ~/.claude/skills/ambient-spanish/scripts/ambient_state.py init
```

The skill triggers implicitly. To apply it to every reply without relying on the description match, add a short "run `ambient_state.py context` before each reply" block to your global `CLAUDE.md` or `AGENTS.md`.

Requires Python 3.11 or newer (CI runs 3.11 and 3.13).

## Controls

```bash
python3 scripts/ambient_state.py status
python3 scripts/ambient_state.py configure --pause        # or --resume
python3 scripts/ambient_state.py configure --known-per-reply 18    # density cap; `all` removes it
python3 scripts/ambient_state.py configure --cadence-days 3 --batch-size 3
python3 scripts/ambient_state.py configure --dialect es-ES
```

Don't reset or edit `state.json` by hand; `ambient_state.py` is the authority.

## Hover translations (Claude Code)

`mods/ambient-spanish-hover` underlines known Spanish words in Claude Code replies and shows their English in a row above the prompt when you hover one. See its [README](mods/ambient-spanish-hover/README.md) for install and limits.

## Development

```bash
python3 -m unittest discover -s tests -v
```

Any word added to `references/curriculum.json` must pass the Peninsular-Spanish blocklist in `tests/test_ambient_state.py`. When you add pre-known words, put them at the front of the curriculum and raise `baseline_known_count` by the same amount, or the learning window shifts over words you already know.

## License

MIT
