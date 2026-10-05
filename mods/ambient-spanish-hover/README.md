# ambient-spanish-hover

A Claude Code mod that underlines the Spanish words in a reply and shows their English in a row above the prompt when you hover one. Claude Code only; the skill itself still runs anywhere.

## Install

```bash
claude plugin marketplace add Exdenta/ambient-spanish      # or a local clone's path
claude plugin install ambient-spanish-hover@ambient-spanish
```

Then start a new session, or run `/reload-plugins`.

## How it works

| Piece | Job |
| --- | --- |
| `ui.render` on `AssistantMessage` | matches words against the skill's `vocabulary.txt`, draws each as its own hoverable `Text` |
| `ui.render` on `AbovePrompt` | one hidden `spanish = english` entry per recent word, revealed by the same hover `scope` |
| `turn.complete` | redraws the row once the turn is over |

The vocabulary is read from `~/.codex/state/ambient-spanish/vocabulary.txt`, which `ambient_state.py context` rewrites when a batch is promoted. Only known words are underlined; bracketed `learning` terms already carry their gloss.

## Limits

- Hover needs a terminal that reports the pointer; the fullscreen layout is the documented case.
- Replies with code fences, tables, headings, blockquotes or markdown links keep the engine's drawing and get no hover.
- Each word is drawn as its own element (a `Text` nested in a `Text` cannot be hovered), so long paragraphs can wrap slightly differently.
- Verbs are matched by stem plus a fixed ending list, and `ENGLISH_HOMOGRAPHS` in `hooks/register.tsx` skips words that are also English (`color`, `red`, `pan`, ...). Irregular forms are missed.

## Develop

```bash
claude plugin validate mods/ambient-spanish-hover
claude plugin test mods/ambient-spanish-hover
```

The tests stub `$.env` and `$.fs`, so they cover the drawn trees, not the pointer.
