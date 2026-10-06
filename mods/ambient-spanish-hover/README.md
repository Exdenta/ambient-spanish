# ambient-spanish-hover

A Claude Code mod that underlines the Spanish words in a reply and shows their English in a row above the prompt when you hover one. Claude Code only; the skill itself still runs anywhere.

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
| `ui.render` on `AssistantMessage` | matches words against the skill's `vocabulary.txt`, draws each as its own hoverable `Text` |
| `ui.render` on `AbovePrompt` | one hidden `spanish = english` entry per word of the replies on screen (about 300 at most, whole oldest replies dropped first), revealed by the same hover `scope` |
| `turn.complete` | redraws the row once the turn is over |

The vocabulary is read from `~/.codex/state/ambient-spanish/vocabulary.txt`, which `ambient_state.py lookup` rewrites whenever the weekly additions land (the path follows `AMBIENT_SPANISH_STATE` when it is set). The mod re-reads it every 30 seconds, so new words appear without restarting. Every vocabulary word is underlined; there are no bracketed glosses to skip.

## Limits

- Hover needs a terminal that reports the pointer; the fullscreen layout is the documented case.
- The mod only draws when `CLAUDE_CODE_ENTRYPOINT` is `cli`; in the desktop app and IDE extensions Claude Code draws replies itself.
- Only paragraphs that contain a vocabulary word are redrawn by the mod (headings, blockquotes, links, bold, italics and strikethrough are reproduced; nested ordering and spacing may differ a little from Claude Code's own). Paragraphs without one, fenced code (never matched, drawn with its language so it is highlighted) and tables are drawn by Claude Code itself. Table words get no hover; the row above the prompt lists them as plain text instead.
- Each word is drawn as its own element (a `Text` nested in a `Text` cannot be hovered), so long paragraphs can wrap slightly differently.
- Verbs are matched against their generated regular forms (present, preterite, imperfect, future, conditional, subjunctive, gerund, participle, with -car/-gar/-zar/-ger/-gir spelling changes; reflexive verbs use the base verb). Irregular and stem-changing forms are missed. Any word that is also English (every word of a gloss and its simple inflections, common function words, and `ENGLISH_HOMOGRAPHS` in `hooks/register.tsx`) is never underlined, even when used as Spanish. `ñ` is kept distinct from `n`.

## Develop

```bash
claude plugin validate mods/ambient-spanish-hover
claude plugin test mods/ambient-spanish-hover
```

The tests stub `$.env` and `$.fs`, so they cover the drawn trees, not the pointer.
