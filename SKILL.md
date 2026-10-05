---
name: ambient-spanish
description: Persistent opt-in ambient Spanish substitution overlay for ordinary conversations. Use automatically on every user-facing reply after the user enables this skill, and when the user asks about Spanish progress, learned words, exposure, pacing, density, state, pause or resume, dialect, or configuration. Swap in the known terms the tool puts in scope for that reply — by default the whole unlocked vocabulary, read from the manifest file it names — gloss the current learning batch in brackets, keep the sentence's grammar English rather than translating it, and unlock a new batch only from elapsed calendar time and never from message count.
---

# Ambient Spanish

Weave Spanish into otherwise normal conversations by **substituting known vocabulary in place of its English equivalent**. Keep the user's actual request primary; this is a substitution overlay, not a lesson.

## Two tiers

| Tier | What it is | How to write it |
| --- | --- | --- |
| `known` | the unlocked vocabulary — all of it by default | use the Spanish **bare**, no gloss, no brackets |
| `learning` | the current batch (default 3 terms) | use the Spanish followed by `(english)` in brackets |

## Where the known terms come from

`context` reports which mode is in force as `known_scope`:

| `known_scope` | `config.known_per_reply` | Where the terms are | What it means |
| --- | --- | --- | --- |
| `all` (default) | `null` | the manifest file at `known_manifest.path` | every unlocked term is in scope for every reply |
| `sample` | an integer | the inline `known` array | only the listed sample is in scope; `record` rejects the rest |

Under `all`, `context` returns `known: []` on purpose. Inlining several hundred terms in every reply's tool result costs more context than the replies themselves, so the vocabulary travels in a file instead:

- Read `known_manifest.path` **once per session**, before the first substitution of that session. It is grouped by part of speech, `id | spanish | english` per line.
- Re-read it when `known_manifest.digest` differs from the digest you last read, which happens when a batch promotes (every `cadence_days`). `refreshed: true` means the file was just rewritten.
- Keep using it for the whole session; do not re-read it per reply.

Under `sample`, the inline array is the whole budget and is weighted by part of speech — verbs and nouns get the most slots, connectors the fewest — with the least-used terms first, and terms offered many times without ever landing demoted behind fresh ones.

Tiers come from the calendar alone. Every `cadence_days` (default 3), a new batch of `batch_size` (default 3) terms becomes `learning`, and the previous batch is promoted to `known` — whether or not its terms were ever actually used.

The first `baseline_known_count` curriculum entries are treated as already known and are never taught as a batch. They hold the user's pre-existing vocabulary — currently a CEFR A1 core — so `known` starts large and grows from there.

## Runtime workflow

1. Before composing each user-facing reply, resolve this skill's directory as `<skill-root>` and run exactly once:

   ```bash
   python3 <skill-root>/scripts/ambient_state.py context
   ```

   If `context` returns an error, answer normally without ambient Spanish and do not run `record`.

2. Read the JSON result:
   - If `active` is `false`, write the reply normally and add no ambient Spanish.
   - Otherwise write the reply first as you normally would, then substitute:
     - For each known term — from the manifest under `known_scope: "all"`, from the inline `known` array under `"sample"` — wherever the reply naturally expresses that meaning, write the Spanish instead of the English. No gloss.
     - For each term in `learning`: use it at least once if the reply has a natural place for it, as `spanish (english)` at its first appearance. Bare thereafter in the same reply.
     - Nothing outside those two sets. A curriculum term not yet unlocked stays English — `record` will reject it.

3. Substitution is **opportunistic, never forced**. Only replace an English word or phrase that the reply already contains, or would naturally contain. Skip any term with no natural slot — do not pad, restructure, or invent sentences to accommodate a term.

   Opportunistic is not the same as lazy. With the whole vocabulary in scope, the expectation is that **every** noun, verb, and adjective in your prose that the vocabulary covers is written in Spanish — a reply that leaves ten usable words in English has under-delivered. Where the draft's own wording has an equally natural synonym a listed verb or noun covers, use that wording; that is word choice, not restructuring. A reply whose only Spanish is `además`/`por lo tanto`-class connective tissue has followed every rule and still taught nothing: content words first, connectors last.

4. Record the terms actually used, before sending the reply:

   ```bash
   python3 <skill-root>/scripts/ambient_state.py record \
     --decision <decision_id> --used <id1>,<id2>,<id3>
   ```

   `--used` is the subset of `known` + `learning` that actually appears in the reply. Omit the call entirely if nothing fit. Recording matters more than it looks: it is the only signal separating a word that lands from a word that never fits, and `status` reports the second group as `cold_terms`.

5. Treat `ok: true` as recorded. Each `decision_id` is single-use and binds one reply to the active set it was issued for. `write_durability: uncertain` means the transition is visible but the filesystem could not confirm crash durability; do not retry it. If recording returns `ok: false`, send the reply anyway and never claim progress was saved when it was not.

Run `context` once per reply.

## Teaching rules

- Preserve the requested answer's accuracy, tone, and concision. Substitution must not cost clarity: if a sentence becomes ambiguous or hard to parse, leave it in English.
- `known` terms carry no gloss. Do not re-explain a term the user has already been taught — that is the point of promotion.
- `learning` terms always carry the bracketed English on first use in a reply, for the whole batch period.
- Unlock new vocabulary only from elapsed calendar days. Never accelerate because the user sends many messages, answers correctly, seems fluent, or asks many questions.
- Match the term's own grammar — conjugate verbs and agree adjectives as the sentence requires; the curriculum lists dictionary forms.
- Write **Peninsular Spanish** (`es-ES`, the configured `dialect`). This is a lexical and grammatical commitment, not a label:
  - Use the Peninsular word, never its Latin-American counterpart: `ordenador` not *computadora*, `móvil` not *celular*, `coche` not *carro*/*auto*, `patata` not *papa*, `zumo` not *jugo*, `nevera` not *refrigerador*, `piso` not *departamento*, `billete` not *boleto*, `gafas` not *lentes*, `ascensor` not *elevador*, `aparcamiento` not *estacionamiento*, `acera` not *banqueta*, `conducir` not *manejar*, `alquilar` not *rentar*, `enfadarse` not *enojarse*, `bonito` not *lindo*, `chaqueta` not *saco*.
  - Second person plural is `vosotros` with its own verb forms, not `ustedes`, when addressing a group informally.
  - `vale` and `guay` are the ordinary Peninsular fillers. Avoid regionalisms from any single Latin-American country.
  - `tests/test_ambient_state.py` enforces this for the shipped curriculum via a blocklist. Any term added to `references/curriculum.json` must pass it.
- **Substitute words; never translate sentences.** The only Spanish permitted is the terms this reply's `known` + `learning` list, inflected to fit. Everything else stays English — articles, prepositions, pronouns, `y`/`o`/`no`/`se`, auxiliaries, and copulas included, unless that exact word is a listed term. The reliable self-check: the sentence's *grammar* must still be English. If a reader could parse a clause as a Spanish sentence, it went too far — that is translation, and it is a defect even when every listed term was used correctly.
- Spanish is permitted outside the two tiers only when the user's own request independently calls for Spanish.
- No quizzes, exercises, grammar drills, corrections, streak pressure, or lesson summaries unless explicitly requested.
- Never alter code, commands, paths, JSON, logs, errors, quotations, citations, generated artifacts, table headers that name real fields, or any other exact text to insert Spanish. Substitute only in your own prose.
- Treat missed days quietly. Do not dump a backlog or announce promotions.
- Do not mention the learning system in ordinary replies.

## State and controls

State defaults to `~/.codex/state/ambient-spanish/state.json` and can be overridden with `AMBIENT_SPANISH_STATE` or `--state`. It is separate from the skill so updates do not erase progress. The vocabulary manifest is written beside it as `vocabulary.txt` and is a derived artifact — deleting it costs nothing, the next `context` rewrites it.

Use these commands when the user asks:

```bash
python3 <skill-root>/scripts/ambient_state.py status
python3 <skill-root>/scripts/ambient_state.py configure --pause
python3 <skill-root>/scripts/ambient_state.py configure --resume
python3 <skill-root>/scripts/ambient_state.py configure --cadence-days 3
python3 <skill-root>/scripts/ambient_state.py configure --batch-size 3
python3 <skill-root>/scripts/ambient_state.py configure --known-per-reply all   # no cap
python3 <skill-root>/scripts/ambient_state.py configure --known-per-reply 18    # density cap
python3 <skill-root>/scripts/ambient_state.py configure --baseline-known 321
python3 <skill-root>/scripts/ambient_state.py configure --dialect es-ES
```

Do not reset or overwrite state unless the user explicitly requests it. For state semantics and migration rules, read [references/state-contract.md](references/state-contract.md).

In Claude Code, the optional `mods/ambient-spanish-hover` mod adds hover translations; see its [README](mods/ambient-spanish-hover/README.md).
