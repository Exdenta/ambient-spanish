# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `ambient-lookup`, a Rust tool the assistant sends its draft to. It returns only the words that have Spanish equivalents, so the vocabulary never enters the assistant's context. The installer builds it with `cargo`, so Rust is now required.
- One vocabulary with a weekly regime chosen at setup (5, 10, 20 or a custom number of new words per week, `configure --words-per-week`). New words are added automatically each week, and the hover mod underlines every one.
- Vocabulary level packs A0–C1 (`references/levels/lexicon.tsv`, about 4,500 words). A1–C1 are graded from ELELex textbook frequencies (CC BY-NC-SA 4.0); A0 is hand-picked.
- `ambient_state.py levels` lists the packs. `ambient_state.py vocab` builds a personal curriculum from a level, Anki or plain word lists, words to drop, and words to learn first, keeping usage history.
- `/ambient-spanish-setup` guided setup for Claude Code, and `scripts/install.py` for linking the skills into Claude Code or Codex, the hover mod, and the run-every-reply rule, installed together in one step (`--scope local` keeps the rule to one project).
- `ambient-spanish-vocab` skill for placement checks and vocabulary changes.
- `ambient-spanish-hover` Claude Code mod: underlines vocabulary words in replies and shows the English in a row above the prompt on hover.
- The mod only redraws paragraphs that contain a vocabulary word (headings, quotes, links, bold, italics and strikethrough included). Paragraphs without one, fenced code (with its language, so it is highlighted) and tables are drawn by Claude Code itself, so replies keep their usual look.
- README, license and CI workflow.

### Changed
- Removed the per-reply density cap (`--known-per-reply`) and the manifest the assistant used to read; state schema v6 migrates v5 state automatically. The hover mod now reloads the vocabulary file every 30 seconds, so weekly additions appear without restarting.
- Removed the "learning" tier and the bracketed English after new words; hover replaces it. `context` and `status` no longer report `learning`, and `init`/`configure` take `--words-per-week` instead of `--cadence-days`/`--batch-size`.
- The shipped curriculum starts with a pre-known core, and every word is checked against an `es-ES` blocklist. A state created against it now starts with that core (the first 321 entries) known; before, nothing set it and the core was taught 10 words a week.
- The assistant now sends the full reply as the last message after `record`.
- Spanish appears only in the Claude Code terminal CLI, where the hover mod can show translations. With `CLAUDE_CODE_ENTRYPOINT` set to anything other than `cli` (the desktop app, IDE extensions), `context` returns `active: false` with reason `non_cli_client`. Codex leaves the variable unset and is unaffected.
- `ambient_state.py lookup` takes the draft on stdin and runs the `context` check and `ambient-lookup` in one call, so each reply costs two tool calls instead of three. SKILL.md and the run-every-reply rule use it; re-run `install.py` to refresh the rule. `context` remains for troubleshooting.
- `record` no longer needs a decision id from `context`: it checks the used words against today's vocabulary, and `context` no longer writes the state on every reply. State schema v7 drops `pending_decisions`; a v6 state migrates automatically and keeps a `state.json.schema-v6.backup`. `--decision` is still accepted and ignored.
- `ambient-lookup`: phrases no longer join across lines or list items; phrase entries that start with "to" also match without it ("bear in mind", "caught up"); only verbs take `-ed`, `-ing` and irregular forms, and only nouns and verbs take plurals, so "evening" no longer matches "even" and "boxing" no longer matches "box".
- Hover mod 0.2.0: verbs are matched by their generated regular forms instead of a stem plus any ending, words that are also English (the vocabulary's glosses, function words, and look-alikes such as `probe` and `actual`) are never underlined, and `ñ` is no longer folded into `n`. Before, words like "come", "more" and "page" were underlined in English text.
- Hover mod 0.3.0: the band above the prompt keeps each reply's words separately, keyed by message, and redraws only when the words it would show change. Before, more than 80 distinct words on screen made every redraw evict and re-add words, so redraws never stopped; a streamed half-word could also stay in the band.
- `ambient-lookup` skips URLs of any scheme, `www.` addresses, emails, paths, filenames, dotted names, snake_case and camelCase identifiers, command-line flags, `@handles` and `#123`, and handles double-backtick and unterminated inline code. Excluded spans are walked once, so a large draft no longer takes quadratic time.

## Earlier

- Every known term is substituted by default, with a new batch of 3 words unlocking every 3 days.
- A CEFR A1 core is seeded as pre-known vocabulary.
- Calendar-paced skill with per-reply exposure tracking.
