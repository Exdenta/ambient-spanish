# State contract v1

Read this only when maintaining, migrating, or troubleshooting persistent state.

## Authority

`scripts/ambient_state.py` is the transition authority. Do not edit live state by hand when a command can perform the change.

The default state file is `~/.codex/state/ambient-spanish/state.json`. `AMBIENT_SPANISH_STATE` and `--state` override it. State lives outside the installed skill so reinstalling or updating the skill does not erase progress.

## Semantics

- `schema_version`: Exact persisted contract version. Unknown versions fail closed.
- `config.start_date`: Local date from which curriculum eligibility is calculated.
- `config.cadence_days`: Minimum elapsed calendar days between new items. Default: 7.
- `config.timezone`: IANA timezone used for day boundaries. Default: `Europe/Madrid`.
- `config.dialect`: Output dialect hint. Default: `es-ES`.
- `config.paused`: Stops suggestions without deleting progress.
- `progress.last_any_insertion_at`: Enforces at most one ambient insertion per local day.
- `progress.last_new_term_at`: Enforces the real-time new-item cadence even when eligible items have accumulated.
- `progress.terms`: Introduced terms and their timestamps. `use_count` is observational only and never unlocks content.

## Invariants

1. Message count never changes eligibility, review gaps, or introduction cadence.
2. One successful `record` transition is allowed per local day.
3. A term can be reviewed only after it has been introduced.
4. A new term must be both curriculum-eligible and cadence-eligible.
5. Missed time never causes a multi-item catch-up response.
6. Mutations use a lock plus atomic replacement.
7. Unknown schema versions and curriculum identifiers fail closed.
8. Test-time clock overrides require `AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE=1`; ordinary runtime uses the system clock.
9. Enforcement timestamps must equal the latest corresponding term-history timestamps.
10. Exact integers reject booleans and numeric lookalikes.

Mutating commands return `write_durability: confirmed` after both the file and parent directory sync. If the replacement is visible but the filesystem cannot confirm the directory sync, they return `write_durability: uncertain` while keeping `ok: true`; this avoids claiming that an already-visible transition failed.

## Calendar behavior

The first curriculum item is eligible on `start_date`. Each later item becomes eligible after another `cadence_days` interval. Actual introduction can be later because review takes priority and the daily cap still applies.

Reviews become due from elapsed days since the term was last used. The intervals widen with the term's real-world age. Sending additional messages cannot make a review or new item due earlier.

## Migration rule

Never silently reinterpret an existing field. A breaking change requires a new `schema_version` and an explicit migration that preserves the original file until the migrated state validates.
