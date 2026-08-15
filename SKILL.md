---
name: ambient-spanish
description: Persistent opt-in ambient Spanish learning overlay for ordinary conversations. Use automatically on every user-facing reply after the user enables this skill, and when the user asks about Spanish progress, learned words, pacing, state, pause or resume, dialect, or configuration. Introduce and review at most one Spanish item on an eligible local-calendar day using durable state; never advance from message count.
---

# Ambient Spanish

Weave Spanish into otherwise normal conversations at a deliberately slow, real-world pace. Keep the user's actual request primary; this is a quiet overlay, not a lesson.

## Runtime workflow

1. Before composing the first user-facing reply in a task, resolve this skill's directory as `<skill-root>` and run:

   ```bash
   python3 <skill-root>/scripts/ambient_state.py context
   ```

2. Read the JSON result:
   - If `focus` is `null`, write the reply normally and add no ambient Spanish.
   - If `focus.action` is `introduce` or `review`, naturally use exactly the item in `focus.term.spanish` once when it fits the reply.
   - Follow `focus.gloss`: `required` means add a brief English meaning at first natural mention; `optional` means gloss only if clarity needs it; `omit` means do not gloss unless the term is ambiguous in context.

3. Never distort the reply to force the item. If it does not fit naturally, skip it and do not record it.

4. If the item was actually included, record it before sending the reply:

   ```bash
   python3 <skill-root>/scripts/ambient_state.py record \
     --term <focus.term.id> --kind <focus.action>
   ```

5. Treat `ok: true` as recorded. `write_durability: uncertain` means the transition is visible but the filesystem could not confirm crash durability; do not retry it. If recording returns `ok: false`, omit the planned insertion and answer normally. Never claim progress was saved when it was not.

Run `context` only once per task unless the task crosses a local calendar day or the user changes the skill configuration.

## Teaching rules

- Preserve the requested answer's accuracy, tone, and concision.
- Insert no more than one curriculum item in a reply and no more than one ambient item per local calendar day. The state script enforces the daily limit.
- Unlock at most one new item per configured number of elapsed calendar days. The default is seven days.
- Never accelerate because the user sends many messages, answers correctly, seems fluent, or asks many questions.
- Reuse due items according to elapsed time. Do not invent extra Spanish outside the returned item, except when Spanish is independently required by the user's request.
- Keep insertions short and natural. Do not add quizzes, exercises, grammar explanations, corrections, streak pressure, or lesson summaries unless explicitly requested.
- Never alter code, commands, paths, JSON, logs, errors, quotations, citations, generated artifacts, or other exact text to insert Spanish.
- Use Spain Spanish (`es-ES`) by default. Respect the configured dialect.
- Treat missed days quietly. Do not dump a backlog or introduce multiple items to catch up.
- Do not mention the learning system in ordinary replies.

## State and controls

State defaults to `~/.codex/state/ambient-spanish/state.json` and can be overridden with `AMBIENT_SPANISH_STATE` or `--state`. It is separate from the skill so updates do not erase progress.

Use these commands when the user asks:

```bash
python3 <skill-root>/scripts/ambient_state.py status
python3 <skill-root>/scripts/ambient_state.py configure --pause
python3 <skill-root>/scripts/ambient_state.py configure --resume
python3 <skill-root>/scripts/ambient_state.py configure --cadence-days 10
python3 <skill-root>/scripts/ambient_state.py configure --dialect es-ES
```

Do not reset or overwrite state unless the user explicitly requests it. For state semantics and migration rules, read [references/state-contract.md](references/state-contract.md).
