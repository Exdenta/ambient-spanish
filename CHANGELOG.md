# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Vocabulary level packs A0–C1 (`references/levels/lexicon.tsv`, about 4,500 words). A1–C1 are graded from ELELex textbook frequencies (CC BY-NC-SA 4.0); A0 is hand-picked.
- `ambient_state.py levels` lists the packs. `ambient_state.py vocab` builds a personal curriculum from a level, Anki or plain word lists, words to drop, and words to learn first, keeping usage history.
- `/ambient-spanish-setup` guided setup for Claude Code, and `scripts/install.py` for linking the skills into Claude Code or Codex, the hover mod, and an optional run-every-reply rule.
- `ambient-spanish-vocab` skill for placement checks and vocabulary changes.
- `ambient-spanish-hover` Claude Code mod: underlines known Spanish words in replies and shows the English in a row above the prompt on hover.
- The mod redraws headings, quotes, links, rules and code lines so they stay hoverable.
- README, license and CI workflow.

### Changed
- Known terms per reply can be capped (`--known-per-reply`) and are served from a manifest file rather than inlined.
- The curriculum starts with 212 pre-known terms, and every word is checked against an `es-ES` blocklist.
- The assistant now sends the full reply as the last message after `record`.

## Earlier

- Every known term is substituted by default, with a new batch of 3 words unlocking every 3 days.
- A CEFR A1 core is seeded as pre-known vocabulary.
- Calendar-paced skill with per-reply exposure tracking.
