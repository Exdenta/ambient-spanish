---
name: ambient-spanish-vocab
description: Build or change the ambient-spanish vocabulary. Covers picking a CEFR level pack (A0–C1) as already-known words, running a quick placement check, importing or pasting word lists (Anki, Duolingo, notes), marking words as not known yet, and choosing what to learn next. Use when the user asks to set or change their Spanish level, import, add or remove known words, build, rebuild or restore their vocabulary, or change what ambient-spanish teaches next.
---

# Build the ambient-spanish vocabulary

`ambient_state.py vocab` rebuilds the learner's personal curriculum: the words they already know, followed by the queue they will learn from. It writes `curriculum.json` beside the state file, and every other command picks that file up automatically. Only this command writes it; never edit it or `state.json` by hand.

`<root>` below is the ambient-spanish skill directory: `~/.claude/skills/ambient-spanish`, `~/.codex/skills/ambient-spanish`, or the repo checkout. All commands print JSON.

## What a build is made of

| Input | Effect |
| --- | --- |
| `--level A0..C1` | every lexicon word up to that level is known |
| `--level none` | start from nothing |
| `--keep-known` | start from the words known today and keep the current learning order |
| `--add-known FILE` | mark these words known |
| `--remove-known FILE` | take these out of known; they are learned soon |
| `--learn-first FILE` | learn these before anything else, in the order given |

Each list flag can be repeated. Give at least one of `--level`, `--keep-known` or `--add-known`.

The queue is built in this order: `learn-first`, then the removed words, then the kept queue, then every remaining lexicon word from the easiest level up.

A build restarts the calendar today, so the first `batch_size` words of the queue are being learned from now. Usage history survives:
- a used word that moved to a new id takes its history along;
- a used word the new build left out stays, as known.

Each build saves the previous files as `curriculum.json.previous` and `state.json.previous`.

The lexicon is `<root>/references/levels/lexicon.tsv`: about 4,500 Peninsular Spanish words graded A0–C1. A1–C1 come from ELELex textbook frequencies; A0 is a hand-picked survival set. `levels` prints each level's size.

## Word lists

Write lists to scratch files, one entry per line: `spanish | english | kind`.
- `english` and `kind` are only needed for words the lexicon lacks. `kind` is one of verb, noun, adjective, adverb, phrase, connector.
- A tab works in place of `|`, so an Anki or spreadsheet export can be passed as is.
- A JSON array of strings, or of `{spanish, english, kind}` objects, also works.
- `#` starts a comment.

Lookups ignore case, a leading article (`el coche`) and missing accents (`rapido`).

When `vocab` returns `unresolved`, those words are in no level pack. Supply their `english` and `kind` yourself, in dictionary form:
- infinitive verbs;
- masculine singular adjectives;
- nouns without an article;
- Peninsular Spanish only (`ordenador`, not *computadora*).

Then run it again.

## Workflow

1. **Read where they are.** Run `python3 <root>/scripts/ambient_state.py status`. Note `curriculum.source` (`shipped` or `user`), `curriculum.build.level`, `known_count` and `learning`.
2. **Find out what they want.** Ask only if it isn't already clear. The usual requests:
   - set or change the level;
   - a placement check;
   - add words they know;
   - drop words they don't;
   - choose words or a topic to learn next.
   For a learner with existing progress, ask whether to build on today's known words (`--keep-known`) or start from a level.
3. **Placement check** (when they don't know their level):
   - Run `levels --sample 12`.
   - Starting at A1, show that level's 12 Spanish words in chat, without the English, and ask which ones they don't know.
   - Stop at the first level where they know fewer than about 8 of the 12. Their level is the one before it, or `none` if that was A1.
   - Put the words they missed at or below that level in a `--remove-known` list.
   - Use plain text, not multiple-choice prompts, for the word lists.
4. **Collect lists.**
   - Save pasted words to a scratch file.
   - For topic targets (for example "words for my job" or "travel"), write 20–60 Peninsular Spanish lemmas with glosses. Prefer ones already in the lexicon (`grep` `lexicon.tsv`).
5. **Preview.** Run the build with `--dry-run`. Show `known_count`, the first `learning` batch and `next_up`, and confirm.
6. **Build.** Run the same command without `--dry-run`.
7. **Density.** With more than about 1,500 known words, the known list the assistant reads once per session gets long (roughly 12 tokens per word). Offer a rotating per-reply sample instead with `configure --known-per-reply 60`, and `configure --known-per-reply all` to undo it. Say which is in force.
8. **Report.** Give the level, known count, the words being learned now (`spanish (english)`), and the next batch date from `status`.

To undo the last build, ask first. Then move `curriculum.json.previous` and `state.json.previous` back over the live files, beside `state.json`.

## Examples

```bash
S=<root>/scripts/ambient_state.py
python3 $S levels --sample 12
python3 $S vocab --level B1 --dry-run
python3 $S vocab --level A2 --remove-known missed.txt --learn-first work-words.txt
python3 $S vocab --keep-known --add-known anki-export.txt
```
