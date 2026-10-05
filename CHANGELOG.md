# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
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
