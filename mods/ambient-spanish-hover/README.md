# ambient-spanish-hover

A Claude Code mod that swaps safe English words in a reply for their Spanish, underlines every Spanish word and shows its English in a row above the prompt when you hover one. Claude Code only; the skill itself still runs anywhere.

## Install

```bash
claude plugin marketplace add Exdenta/ambient-spanish      # or a local clone's path
claude plugin install ambient-spanish-hover@ambient-spanish
```

Then start a new session, or run `/reload-plugins`.

## Update

Added from a local clone, Claude Code reads the mod straight from the clone: `git pull`, then `/reload-plugins`. Added from GitHub, it runs a cached copy:

```bash
claude plugin marketplace update ambient-spanish
claude plugin update ambient-spanish-hover@ambient-spanish
```

## How it works

| Piece | Job |
| --- | --- |
| `ui.render` on `AssistantMessage` | pipes the reply through `ambient-lookup --swap` first (below), then matches words against the skill's `vocabulary.txt`, draws each as its own hoverable `Text` |
| `ui.render` on `AbovePrompt` | one hidden `spanish = english` entry per word of the replies on screen (about 300 at most, whole oldest replies dropped first), revealed by the same hover `scope` |
| `turn.complete` | redraws the row once the turn is over |

The vocabulary is read from `~/.codex/state/ambient-spanish/vocabulary.txt`, which `ambient_state.py lookup` rewrites whenever the weekly additions land (the path follows `AMBIENT_SPANISH_STATE` when it is set, or a state moved with `--state`, which leaves `vocabulary.path` beside the default state). The mod re-reads it every 30 seconds, so new words appear without restarting. Every vocabulary word is underlined; there are no bracketed glosses to skip.

## Display-side swap

The mod runs each reply through `ambient-lookup --swap`, so the screen shows Spanish for words the assistant wrote in English. The transcript and the model keep the English, and no tool call is spent. The neighbouring words decide what part of speech an English word is; only the Spanish candidates of that kind are kept, and the word is swapped when one remains (or the tightest-glossed of up to three):

| Context | Swapped |
| --- | --- |
| after a determiner, preposition or adjective | a noun, or its regular plural ("the tool", "two songs") |
| after "to", a modal, "do not" or at the start of an imperative | the infinitive, "to" removed ("to open" → `abrir`, "can open" → `can abrir`) |
| after a subject pronoun or a singular noun | the regular present of a regular verb ("you add" → `añades`) |
| after "is", "seems", "not very"… | an adjective ("is perfect") |
| before a noun | only an adjective with no gender (`diferente`, `cada`) |
| anywhere | a conjunction or an adverb with one candidate ("because", "never") |

Never swapped: code, tables, double-quoted text, paths, hyphenated compounds, capitalised names mid-sentence, number words, glosses with a sense note such as `rock (music)`, and verbs whose stem might change or carry an accent (`empezar`, `enviar`). Two limits remain: the swap picks by neighbours, not meaning, so a one-candidate entry such as `path` → `camino` is swapped even when the sense is off, and an adjective in `-o` before a noun stays English because it would need agreement.

The binary is found through `AMBIENT_LOOKUP_BIN`, then `~/.codex/state/ambient-spanish/lookup.path` (written by `ambient_state.py` when the vocabulary file is). Without it replies show as written. Set `AMBIENT_SPANISH_SWAP=0` to turn the swap off.

## Limits


- Hover needs a terminal that reports the pointer; the fullscreen layout is the documented case.
- The mod only draws when `CLAUDE_CODE_ENTRYPOINT` is `cli` and the message is drawn for the terminal surface; in the desktop app, IDE extensions and the phone (including a CLI session mirrored there) Claude Code draws replies itself, so Spanish words are not underlined there.
- Only paragraphs that contain a vocabulary word are redrawn by the mod (headings, blockquotes, links, bold, italics and strikethrough are reproduced; nested ordering and spacing may differ a little from Claude Code's own). Paragraphs without one, fenced code (never matched, drawn with its language so it is highlighted) and tables are drawn by Claude Code itself. Table words get no hover; the row above the prompt lists them as plain text instead.
- Each word is drawn as its own element (a `Text` nested in a `Text` cannot be hovered), so long paragraphs can wrap slightly differently.
- Verbs are matched against their generated regular forms (present, preterite, imperfect, future, conditional, subjunctive, gerund, participle, with -car/-gar/-zar/-ger/-gir spelling changes; reflexive verbs use the base verb). Irregular and stem-changing forms are missed. Any word that is also English (every word of a gloss and its simple inflections, common function words, and `ENGLISH_HOMOGRAPHS` in `hooks/register.tsx`) is never underlined, even when used as Spanish. `ñ` is kept distinct from `n`.

## Develop

```bash
claude plugin validate mods/ambient-spanish-hover
claude plugin test mods/ambient-spanish-hover
```

The tests stub `$.env` and `$.fs`, so they cover the drawn trees, not the pointer.
