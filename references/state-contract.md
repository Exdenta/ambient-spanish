# State contract v2

Read this only when maintaining, migrating, or troubleshooting persistent state.

## Authority

`scripts/ambient_state.py` is the transition authority. Do not edit live state by hand when a command can perform the change.

The default state file is `~/.codex/state/ambient-spanish/state.json`. `AMBIENT_SPANISH_STATE` and `--state` override it. State lives outside the installed skill so reinstalling or updating the skill does not erase progress.

## Semantics

- `schema_version`: Exact persisted contract version. Unknown versions fail closed.
- `config.start_date`: Local date from which curriculum eligibility is calculated.
- `config.cadence_days`: Minimum elapsed calendar days between new items. Default: 7.
- `config.exposure_percent`: Target percentage of eligible replies that receive one ambient item. Default: 50.
- `config.timezone`: IANA timezone used for day boundaries. Default: `Europe/Madrid`.
- `config.dialect`: Output dialect hint. Default: `es-ES`.
- `config.paused`: Stops suggestions without deleting progress.
- `progress.last_exposure_at`: Latest recorded ambient use. It provides monotonic-clock protection but does not impose a daily cap.
- `progress.last_new_term_at`: Enforces the real-time new-item cadence even when eligible items have accumulated.
- `progress.pending_decisions`: One-time exposure reservations returned by `context` and consumed by `record`.
- `progress.terms`: Introduced terms and their timestamps. `use_count` is observational only and never unlocks content.

## Invariants

1. Message count never changes eligibility, review gaps, or introduction cadence.
2. Each `context` result contains zero or one focus; one reply can never receive multiple ambient items.
3. A term can be reviewed only after it has been introduced.
4. A new term must be both curriculum-eligible and cadence-eligible.
5. Missed time never causes a multi-item catch-up response.
6. Mutations use a lock plus atomic replacement.
7. Unknown schema versions and curriculum identifiers fail closed.
8. Test-time clock overrides require `AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE=1`; ordinary runtime uses the system clock.
9. Enforcement timestamps must equal the latest corresponding term-history timestamps.
10. Exact integers reject booleans and numeric lookalikes.
11. Exposure selection is an independent Bernoulli decision per eligible reply. It never changes curriculum eligibility.
12. A record transition requires the unexpired decision id returned for that exact selected exposure.

Mutating commands return `write_durability: confirmed` after both the file and parent directory sync. If the replacement is visible but the filesystem cannot confirm the directory sync, they return `write_durability: uncertain` while keeping `ok: true`; this avoids claiming that an already-visible transition failed.

## Calendar behavior

The first curriculum item is eligible on `start_date`. Each later item becomes curriculum-eligible after another `cadence_days` interval. Actual introduction also requires at least `cadence_days` since the preceding introduction, so a backlog can never cause rapid catch-up.

On each reply, the exposure gate selects a candidate with probability `exposure_percent / 100`. A selected reply introduces a ready new item first, otherwise reviews a due item, otherwise reuses the least-used unlocked item. Sending additional messages can create more practice exposures, but cannot make new vocabulary eligible earlier.

## Migration rule

Never silently reinterpret an existing field. A breaking change requires a new `schema_version` and an explicit migration that preserves the original file until the migrated state validates.

Schema v1 migrates automatically to v2. The migration adds `config.exposure_percent: 50`, renames `progress.last_any_insertion_at` to `progress.last_exposure_at`, initializes `progress.pending_decisions`, validates that introduced items form a curriculum prefix on distinct increasing local dates, and preserves the original as `state.json.schema-v1.backup` before replacing live state. It does not reinterpret old introductions using the currently configured cadence, because cadence may legitimately have changed.

At most 128 unconsumed decisions may be reserved at once. Reaching that safety bound fails visibly without deleting any unexpired reservation; expired reservations are removed first.
