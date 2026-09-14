# State contract v3

Read this only when maintaining, migrating, or troubleshooting persistent state.

## Authority

`scripts/ambient_state.py` is the transition authority. Do not edit live state by hand when a command can perform the change.

The default state file is `~/.codex/state/ambient-spanish/state.json`. `AMBIENT_SPANISH_STATE` and `--state` override it. State lives outside the installed skill so reinstalling or updating the skill does not erase progress.

## Semantics

- `schema_version`: Exact persisted contract version. Unknown versions fail closed.
- `config.start_date`: Local date on which batch 0 begins.
- `config.cadence_days`: Calendar days per batch. Default: 3.
- `config.batch_size`: New terms unlocked per batch. Default: 3.
- `config.baseline_known_count`: Leading curriculum items treated as already known at `start_date` and therefore never taught as a batch. Batch 0's learning window starts at this offset. Set by migration to the number of terms already introduced under the previous schema, and adjustable with `configure --baseline-known`.

  This is how pre-existing knowledge enters the system: put the words the user already knows at the **front** of `references/curriculum.json` and set `baseline_known_count` to cover them. Because tiers are index-based, prepending `n` items to the curriculum without adding `n` to `baseline_known_count` silently shifts the learning window backwards over words the user already knows — always change the two together.
- `config.timezone`: IANA timezone used for day boundaries. Default: `Europe/Madrid`.
- `config.dialect`: Output dialect hint. Default: `es-ES`.
- `config.paused`: Stops substitution without deleting progress.
- `progress.last_exposure_at`: Latest recorded use. It provides monotonic-clock protection but does not cap or gate anything.
- `progress.pending_decisions`: One-time reservations returned by `context` and consumed by `record`. Each holds the full `term_ids` active set the reply was permitted to draw from.
- `progress.terms`: Observational usage history. `use_count` and `introduced_at` never affect tiers or unlocking.

## Tier derivation

Tiers are a pure function of the local date, not of usage:

```
batch_index    = (today - start_date) // cadence_days      # negative before start_date
learning_start = baseline_known_count + batch_index * batch_size
known          = curriculum[:learning_start]
learning       = curriculum[learning_start : learning_start + batch_size]
```

Consequences: a batch is promoted from `learning` to `known` on its date even if none of its terms were ever used; `known` terms carry no gloss; `learning` terms carry a bracketed English gloss; and `progress.terms` is therefore **not** required to form a curriculum prefix.

## Invariants

1. Message count never changes tiers, promotion, or unlocking.
2. Every reply may draw on the entire active set. There is no per-reply item cap and no probabilistic exposure gate.
3. Only the current batch is glossed. Promotion to `known` removes the gloss permanently.
4. Tier membership is derived solely from `start_date`, `cadence_days`, `batch_size`, and `baseline_known_count`.
5. Missed time never causes a multi-batch catch-up: elapsed days advance `batch_index`, so skipped batches are promoted straight to `known` rather than queued.
6. Mutations use a lock plus atomic replacement.
7. Unknown schema versions and curriculum identifiers fail closed.
8. Test-time clock overrides require `AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE=1`; ordinary runtime uses the system clock.
9. `last_exposure_at` must equal the newest `terms[*].last_used_at`.
10. Exact integers reject booleans and numeric lookalikes.
11. A `record` transition requires the unexpired decision id issued for that reply, and `--used` must be a subset of that decision's `term_ids`.

Mutating commands return `write_durability: confirmed` after both the file and parent directory sync. If the replacement is visible but the filesystem cannot confirm the directory sync, they return `write_durability: uncertain` while keeping `ok: true`; this avoids claiming that an already-visible transition failed.

## Decision lifetime

Decisions expire after 24 hours. Replies that legitimately used no Spanish leave their reservation unconsumed, so at `MAX_PENDING_DECISIONS` (128) `context` evicts the oldest reservation rather than failing closed. Eviction only invalidates a stale anti-replay token; it never touches term history.

## Migration rule

Never silently reinterpret an existing field. A breaking change requires a new `schema_version` and an explicit migration that preserves the original file until the migrated state validates.

Schema v1 migrates to v2 in memory (adds `config.exposure_percent: 50`, renames `progress.last_any_insertion_at` to `progress.last_exposure_at`, initializes `progress.pending_decisions`, and validates that introduced items form a curriculum prefix on distinct increasing local dates), then v2 migrates to v3.

The v2 to v3 migration converts one-item-per-reply pacing into batch pacing. It drops `config.exposure_percent` and `progress.last_new_term_at`, adds `config.batch_size: 3`, sets `config.baseline_known_count` to the number of terms already introduced, resets `config.start_date` to the migration's local date so batch 0 begins immediately, and clears `pending_decisions` because the v2 single-term decision shape is incompatible. Term history is preserved verbatim. The original document is written to `state.json.schema-v<n>.backup` (where `<n>` is the version actually found on disk) before live state is replaced.

Every already-introduced term becomes `known` — unglossed from the first v3 reply — on the grounds that it was taught under the previous regime. The first v3 `learning` batch is the next `batch_size` unseen curriculum items.
