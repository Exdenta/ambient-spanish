---
name: ambient-spanish
description: Persistent opt-in ambient Spanish substitution overlay for ordinary conversations. Use automatically on every user-facing reply after the user enables this skill, and when the user asks about Spanish progress, learned words, exposure, pacing, state, pause or resume, dialect, or configuration. Substitute every already-known term wherever its meaning occurs in the reply, gloss the current learning batch in brackets, and unlock a new batch only from elapsed calendar time and never from message count.
---

# Ambient Spanish

Weave Spanish into otherwise normal conversations by **substituting known vocabulary in place of its English equivalent**. Keep the user's actual request primary; this is a substitution overlay, not a lesson.

## Two tiers

| Tier | What it is | How to write it |
| --- | --- | --- |
| `known` | every term unlocked before the current batch | use the Spanish **bare**, no gloss, no brackets |
| `learning` | the current batch (default 3 terms) | use the Spanish followed by `(english)` in brackets |

Tiers come from the calendar alone. Every `cadence_days` (default 3), a new batch of `batch_size` (default 3) terms becomes `learning`, and the previous batch is promoted to `known` — whether or not its terms were ever actually used.

## Runtime workflow

1. Before composing each user-facing reply, resolve this skill's directory as `<skill-root>` and run exactly once:

   ```bash
   python3 <skill-root>/scripts/ambient_state.py context
   ```

   If `context` returns an error, answer normally without ambient Spanish and do not run `record`.

2. Read the JSON result:
   - If `active` is `false`, write the reply normally and add no ambient Spanish.
   - Otherwise write the reply first as you normally would, then substitute:
     - For each term in `known`: wherever the reply naturally expresses that meaning, write the Spanish instead of the English. No gloss.
     - For each term in `learning`: use it at least once if the reply has a natural place for it, as `spanish (english)` at its first appearance. Bare thereafter in the same reply.

3. Substitution is **opportunistic, never forced**. Only replace an English word or phrase that the reply already contains, or would naturally contain. Skip any term with no natural slot — do not pad, restructure, or invent sentences to accommodate a term. Each term's `usage` field says when it genuinely fits.

4. Record the terms actually used, before sending the reply:

   ```bash
   python3 <skill-root>/scripts/ambient_state.py record \
     --decision <decision_id> --used <id1>,<id2>,<id3>
   ```

   `--used` is the subset of `known` + `learning` that actually appears in the reply. Omit the call entirely if nothing fit.

5. Treat `ok: true` as recorded. Each `decision_id` is single-use and binds one reply to the active set it was issued for. `write_durability: uncertain` means the transition is visible but the filesystem could not confirm crash durability; do not retry it. If recording returns `ok: false`, send the reply anyway and never claim progress was saved when it was not.

Run `context` once per reply.

## Teaching rules

- Preserve the requested answer's accuracy, tone, and concision. Substitution must not cost clarity: if a sentence becomes ambiguous or hard to parse, leave it in English.
- `known` terms carry no gloss. Do not re-explain a term the user has already been taught — that is the point of promotion.
- `learning` terms always carry the bracketed English on first use in a reply, for the whole batch period.
- Unlock new vocabulary only from elapsed calendar days. Never accelerate because the user sends many messages, answers correctly, seems fluent, or asks many questions.
- Use Spain Spanish (`es-ES`) by default. Respect the configured dialect. Match the term's own grammar — conjugate verbs and agree adjectives as the sentence requires; the curriculum lists dictionary forms.
- Do not invent Spanish outside `known` + `learning`, except when Spanish is independently required by the user's request.
- No quizzes, exercises, grammar drills, corrections, streak pressure, or lesson summaries unless explicitly requested.
- Never alter code, commands, paths, JSON, logs, errors, quotations, citations, generated artifacts, table headers that name real fields, or any other exact text to insert Spanish. Substitute only in your own prose.
- Treat missed days quietly. Do not dump a backlog or announce promotions.
- Do not mention the learning system in ordinary replies.

## State and controls

State defaults to `~/.codex/state/ambient-spanish/state.json` and can be overridden with `AMBIENT_SPANISH_STATE` or `--state`. It is separate from the skill so updates do not erase progress.

Use these commands when the user asks:

```bash
python3 <skill-root>/scripts/ambient_state.py status
python3 <skill-root>/scripts/ambient_state.py configure --pause
python3 <skill-root>/scripts/ambient_state.py configure --resume
python3 <skill-root>/scripts/ambient_state.py configure --cadence-days 3
python3 <skill-root>/scripts/ambient_state.py configure --batch-size 3
python3 <skill-root>/scripts/ambient_state.py configure --dialect es-ES
```

Do not reset or overwrite state unless the user explicitly requests it. For state semantics and migration rules, read [references/state-contract.md](references/state-contract.md).
