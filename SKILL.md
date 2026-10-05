---
name: ambient-spanish
description: Persistent opt-in ambient Spanish substitution overlay for ordinary conversations. Use automatically on every user-facing reply after the user enables this skill, and when the user asks about Spanish progress, learned words, exposure, pacing, density, state, pause or resume, dialect, or configuration. Draft the reply in English, send the draft to the `ambient-lookup` tool, which returns the words that have Spanish equivalents in the learner's vocabulary (the assistant never loads the vocabulary itself), and write the final reply with them and no glosses or brackets (the hover mod shows translations), keep the sentence's grammar English rather than translating it, and add new words to the vocabulary weekly from elapsed calendar time, never from message count.
---

# Ambient Spanish

Weave Spanish into otherwise normal conversations by **substituting vocabulary words in place of their English equivalents**. Keep the user's actual request primary; this is a substitution overlay, not a lesson.

## One vocabulary

There is a single vocabulary. Write its Spanish **bare**: no gloss, no brackets, no English in parentheses. The hover mod underlines every vocabulary word and shows its English when hovered, so the reply never needs to explain one.

## You never see the vocabulary

The vocabulary has thousands of words and is deliberately kept out of your context. Instead you draft the reply in plain English, send the draft to `ambient-lookup`, and it returns only the words and phrases in your draft that have a Spanish equivalent. You then write the final reply using those.

The vocabulary grows from the calendar alone. The learner picks a regime — 5, 10 or 20 new words per week, or their own number (`words_per_week` in `context`, default 10) — and that many curriculum words join the vocabulary every 7 days, whether or not the previous ones were ever used. The first `baseline_known_count` curriculum entries are the learner's starting vocabulary, either the shipped curriculum's A1 core or a personal curriculum built by `vocab` from a CEFR level pack (A0–C1) and the user's own word lists.

## Runtime workflow

1. Before each user-facing reply, resolve this skill's directory as `<skill-root>` and run exactly once:

   ```bash
   python3 <skill-root>/scripts/ambient_state.py context
   ```

   If `context` returns an error, answer normally without ambient Spanish and do not run `record`. If `active` is `false`, write the reply normally.

2. Draft the reply in English, as you normally would.

3. Skip the lookup, and send the draft as is, when the reply is only code, a command, a path, a table of exact fields or a few words. Otherwise send the prose of the draft (leave out code blocks) to the lookup, using the `lookup.command` and `lookup.vocabulary` that `context` returned:

   ```bash
   <lookup.command> --vocab <lookup.vocabulary> <<'EOF'
   ...your draft...
   EOF
   ```

   It prints one line per distinct word or phrase of your draft that has Spanish in the vocabulary: `english: spanish | spanish`. These are dictionary forms, there only to confirm the words are in the vocabulary. You adapt them yourself (conjugation, gender, number) and choose the candidate that fits what you meant.

4. Write the final reply, substituting:
   - Only the words the lookup returned, and only where the sense fits what you meant. Pick the right candidate (`work` the verb is `trabajar`, the noun is `trabajo`) and skip a word whose candidates all miss.
   - Spanish bare, with no gloss or brackets; conjugate and agree it as the English sentence needs.
   - Substitution is **opportunistic, never forced**. Skip any term with no natural slot and never restructure a sentence to fit one. A reply whose only Spanish is connective tissue has under-delivered: content words first.

5. Record the words actually used, as the last tool call before the reply:

   ```bash
   python3 <skill-root>/scripts/ambient_state.py record \
     --decision <decision_id> --used <spanish1>,<spanish2>,<spanish3>
   ```

   `--used` lists the dictionary forms exactly as the lookup printed them (`abrir`, `ventana`). Omit the call if nothing fit. `record` rejects words outside the vocabulary.

6. Send the full reply as the final text message, after `record`. A message that only describes the reply leaves the user with nothing. Never add a sign-off after `record`.

7. Treat `ok: true` as recorded. Each `decision_id` is single-use. `write_durability: uncertain` means the transition is visible but the filesystem could not confirm crash durability; do not retry it. If recording returns `ok: false`, send the reply anyway and never claim progress was saved when it was not.

Run `context` once per reply.

## Teaching rules

- Preserve the requested answer's accuracy, tone, and concision. Substitution must not cost clarity: if a sentence becomes ambiguous or hard to parse, leave it in English.
- Never gloss a vocabulary term, in brackets or otherwise. Hovering is how the learner sees the English.
- Add new vocabulary only from elapsed calendar days. Never accelerate because the user sends many messages, answers correctly, seems fluent, or asks many questions.
- Match the term's own grammar — conjugate verbs and agree adjectives as the sentence requires; the curriculum lists dictionary forms.
- Write **Peninsular Spanish** (`es-ES`, the configured `dialect`). This is a lexical and grammatical commitment, not a label:
  - Use the Peninsular word, never its Latin-American counterpart: `ordenador` not *computadora*, `móvil` not *celular*, `coche` not *carro*/*auto*, `patata` not *papa*, `zumo` not *jugo*, `nevera` not *refrigerador*, `piso` not *departamento*, `billete` not *boleto*, `gafas` not *lentes*, `ascensor` not *elevador*, `aparcamiento` not *estacionamiento*, `acera` not *banqueta*, `conducir` not *manejar*, `alquilar` not *rentar*, `enfadarse` not *enojarse*, `bonito` not *lindo*, `chaqueta` not *saco*.
  - Second person plural is `vosotros` with its own verb forms, not `ustedes`, when addressing a group informally.
  - `vale` and `guay` are the ordinary Peninsular fillers. Avoid regionalisms from any single Latin-American country.
  - `tests/test_ambient_state.py` enforces this for the shipped curriculum via a blocklist. Any term added to `references/curriculum.json` must pass it.
- **Substitute words; never translate sentences.** The only Spanish permitted is the words the lookup returned for this reply, inflected to fit. Everything else stays English — articles, prepositions, pronouns, `y`/`o`/`no`/`se`, auxiliaries, and copulas included, unless that exact word is a listed term. The reliable self-check: the sentence's *grammar* must still be English. If a reader could parse a clause as a Spanish sentence, it went too far — that is translation, and it is a defect even when every listed term was used correctly.
- Spanish is permitted outside the lookup results only when the user's own request independently calls for Spanish.
- No quizzes, exercises, grammar drills, corrections, streak pressure, or lesson summaries unless explicitly requested.
- Never alter code, commands, paths, JSON, logs, errors, quotations, citations, generated artifacts, table headers that name real fields, or any other exact text to insert Spanish. Substitute only in your own prose.
- Treat missed days quietly. Do not dump a backlog or announce new words.
- Do not mention the learning system in ordinary replies.

## State and controls

State defaults to `~/.codex/state/ambient-spanish/state.json` and can be overridden with `AMBIENT_SPANISH_STATE` or `--state`. It is separate from the skill so updates do not erase progress. The vocabulary is written beside it as `vocabulary.txt`, a derived artifact that `ambient-lookup` and the hover mod read; deleting it costs nothing, the next `context` rewrites it. The `ambient-lookup` binary is built from `rust/ambient-lookup` by `scripts/install.py`.

Use these commands when the user asks:

```bash
python3 <skill-root>/scripts/ambient_state.py status
python3 <skill-root>/scripts/ambient_state.py configure --pause
python3 <skill-root>/scripts/ambient_state.py configure --resume
python3 <skill-root>/scripts/ambient_state.py configure --words-per-week 10   # 5, 10, 20 or any number
python3 <skill-root>/scripts/ambient_state.py configure --dialect es-ES
python3 <skill-root>/scripts/ambient_state.py levels                      # CEFR level packs and their sizes
python3 <skill-root>/scripts/ambient_state.py vocab --level B1 --dry-run  # preview a rebuild
```

To set the level, add or remove known words, or choose which words are added next, follow the `ambient-spanish-vocab` skill (`skills/ambient-spanish-vocab/SKILL.md` in this repo). `vocab` writes a personal `curriculum.json` beside the state, which then replaces the shipped curriculum. `status` reports which one is in use under `curriculum`. Use `configure --baseline-known` only with the shipped curriculum. See `references/state-contract.md` for how the baseline works.

Do not reset or overwrite state unless the user explicitly requests it. For state semantics and migration rules, read [references/state-contract.md](references/state-contract.md).

In Claude Code, the `mods/ambient-spanish-hover` mod (installed with the skill) underlines every vocabulary word and shows its English on hover; see its [README](mods/ambient-spanish-hover/README.md).
