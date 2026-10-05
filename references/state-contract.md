# State contract v6

Read this only when maintaining, migrating, or troubleshooting persistent state.

## Authority

`scripts/ambient_state.py` is the transition authority. Do not edit live state by hand when a command can perform the change.

The default state file is `~/.codex/state/ambient-spanish/state.json`. `AMBIENT_SPANISH_STATE` and `--state` override it. State lives outside the installed skill so reinstalling or updating the skill does not erase progress.

## Curriculum source

Every command resolves the curriculum in this order:
1. `--curriculum`
2. `AMBIENT_SPANISH_CURRICULUM`
3. the personal `curriculum.json` beside the state file
4. the shipped `references/curriculum.json`

`status` reports the one in use under `curriculum` as `{path, source, size, build}`. `source` is one of `flag`, `env`, `user` or `shipped`.

`vocab` is the only writer of the personal curriculum. It builds it from the graded lexicon `references/levels/lexicon.tsv` (CEFR A0–C1) and the user's word lists: known terms first, then the queue of words to add. A rebuild is one locked transition:

- `baseline_known_count` becomes the known count and `start_date` becomes today, so batch 0 is the head of the queue.
- Usage history stays attached to curriculum ids:
  - A used term whose Spanish form moved to a new id has its history moved, or merged if the new id already has some.
  - A used term the new build left out is appended to the known set, so every `progress.terms` id still exists.
- `pending_decisions` is cleared, because those decisions were issued against the old curriculum.
- The previous personal curriculum and state are copied to `*.previous`. Then `vocab` writes the curriculum, then `curriculum.meta.json` (a build summary), then the state, then the vocabulary file, each atomically.
  - A crash between the curriculum and state writes can leave a state that fails validation against the new curriculum. Re-running `vocab`, or restoring the `.previous` pair, repairs it.

Do not delete the personal curriculum by hand. Its baseline would then index the shipped curriculum and shift the weekly window. Rebuild instead.

## Semantics

- `schema_version`: Exact persisted contract version. Unknown versions fail closed.
- `config.start_date`: Local date on which batch 0 begins.
- `config.cadence_days`: Calendar days per batch. Default: 3.
- `config.batch_size`: New terms unlocked per batch. Default: 3.
- `config.baseline_known_count`: Leading curriculum items treated as already known at `start_date` and therefore never added as a batch. Batch 0 starts at this offset. Set by migration to the number of terms already introduced under the previous schema, and adjustable with `configure --baseline-known`.

  This is how pre-existing knowledge enters the system. For one user, `vocab` builds a personal curriculum with the known words at the front and sets the count itself. For the shipped curriculum, put the words every user already knows at the **front** of `references/curriculum.json` and set `baseline_known_count` to cover them. Because tiers are index-based, prepending `n` items to the curriculum without adding `n` to `baseline_known_count` silently shifts the weekly window backwards over words the user already knows — always change the two together.
- `config.timezone`: IANA timezone used for day boundaries. Default: `Europe/Madrid`.
- `config.dialect`: Output dialect hint. Default: `es-ES`.
- `config.paused`: Stops substitution without deleting progress.
- `progress.last_exposure_at`: Latest recorded use. It provides monotonic-clock protection but does not cap or gate anything.
- `progress.pending_decisions`: One-time reservations returned by `context` and consumed by `record`. Each holds `scope: "known_all"`, and `record` recomputes the permitted set (the whole vocabulary) from the tiers of the decision's own local date, so no id list is stored. A decision issued before v6 may still hold a `term_ids` list; it stays valid and `record` honours that list as written.
- `progress.terms`: Observational usage history. `use_count` and `introduced_at` never affect tiers or unlocking.

## Vocabulary derivation

The vocabulary is a pure function of the local date, not of usage. There is one tier; `config.batch_size` is the learner's words per week and `config.cadence_days` is 7:

```
batch_index = (today - start_date) // cadence_days      # negative before start_date
vocabulary  = curriculum[: baseline_known_count + (batch_index + 1) * batch_size]
```

Consequences: the first weekly batch joins on `start_date` and a new one every `cadence_days` after, whether or not any term was ever used; no term is ever glossed in a reply (the hover mod shows the English); and `progress.terms` is **not** required to form a curriculum prefix.

## Lookup and the vocabulary file

The agent does not read the vocabulary. Per reply it pipes its draft to the Rust binary `ambient-lookup`, which returns the English phrases that match with Spanish candidates. `context` therefore returns, instead of any term list:

```
"lookup": {"command": <abs path of ambient-lookup>, "vocabulary": <abs path of vocabulary.txt>},
"vocabulary_count": <terms in the vocabulary today>
```

The binary resolves from `AMBIENT_LOOKUP_BIN`, then `<repo>/rust/ambient-lookup/target/release/ambient-lookup`. If it does not exist, an active `context` fails with `ambient-lookup binary not found: run python3 scripts/install.py` (which builds it with cargo). An inactive context (paused, before the start date) returns `lookup: null` and needs no binary. `status` never needs it and reports `lookup_binary` (a path or `null`).

`context` still writes the whole vocabulary to `vocabulary.txt` beside the state file, as `id | spanish | english` lines grouped by kind, and the lookup binary reads it. The hover mod reads it too, and that is the only reason it is a file the mod can rely on. It is a derived artifact: deleting it is safe and the next `context` rewrites it. It is rewritten only when its content changes (when a weekly batch lands), so readers can cache it.

## Invariants

1. Message count never changes tiers, promotion, or unlocking.
2. A reply may draw on any term of the vocabulary of the day its decision was issued. There is no density cap and no probabilistic exposure gate.
3. No term is glossed in a reply. The hover mod shows the English.
4. Tier membership is derived solely from `start_date`, `cadence_days`, `batch_size`, and `baseline_known_count`.
5. Missed time never causes a multi-batch catch-up: elapsed days advance `batch_index`, so skipped batches are promoted straight to `known` rather than queued.
6. Mutations use a lock plus atomic replacement.
7. Unknown schema versions and curriculum identifiers fail closed.
8. Test-time clock overrides require `AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE=1`; ordinary runtime uses the system clock.
9. `last_exposure_at` must equal the newest `terms[*].last_used_at`.
10. Exact integers reject booleans and numeric lookalikes.
11. A `record` transition requires the unexpired decision id issued for that reply, and `--used` must be a subset of the permitted set (the vocabulary of the decision's day, or its legacy `term_ids`).

Mutating commands return `write_durability: confirmed` after both the file and parent directory sync. If the replacement is visible but the filesystem cannot confirm the directory sync, they return `write_durability: uncertain` while keeping `ok: true`; this avoids claiming that an already-visible transition failed.

## Decision lifetime

Decisions expire after 24 hours. Replies that legitimately used no Spanish leave their reservation unconsumed, so at `MAX_PENDING_DECISIONS` (128) `context` evicts the oldest reservation rather than failing closed. Eviction only invalidates a stale anti-replay token; it never touches term history.

## Migration rule

Never silently reinterpret an existing field. A breaking change requires a new `schema_version` and an explicit migration that preserves the original file until the migrated state validates.

Schema v1 migrates to v2 in memory (adds `config.exposure_percent: 50`, renames `progress.last_any_insertion_at` to `progress.last_exposure_at`, initializes `progress.pending_decisions`, and validates that introduced items form a curriculum prefix on distinct increasing local dates), then v2 migrates to v3.

The v2 to v3 migration converts one-item-per-reply pacing into batch pacing. It drops `config.exposure_percent` and `progress.last_new_term_at`, adds `config.batch_size: 3`, sets `config.baseline_known_count` to the number of terms already introduced, resets `config.start_date` to the migration's local date so batch 0 begins immediately, and clears `pending_decisions` because the v2 single-term decision shape is incompatible. Term history is preserved verbatim. The original document is written to `state.json.schema-v<n>.backup` (where `<n>` is the version actually found on disk) before live state is replaced.

Every already-introduced term becomes `known` — unglossed from the first v3 reply — on the grounds that it was taught under the previous regime. The first v3 `learning` batch is the next `batch_size` unseen curriculum items.

The v3 to v4 migration adds `config.known_per_reply: 12` and clears `pending_decisions`, because a v3 decision reserved an unbounded active set and honouring one would let a single reply ignore the new budget. Config, tiers, and term history are otherwise untouched.

The v4 to v5 migration makes the budget nullable and adds `progress.offers`. A v4 state keeps its integer `known_per_reply` — an existing density setting is a user choice, not a default to overwrite — so only a fresh state starts uncapped. Offers are seeded from term history (`count = use_count`, `last_offered_at = last_used_at`) so a term already in rotation is not treated as never offered, which would otherwise put every used term ahead of untried ones on the first v5 sample. This is the first migration that keeps `pending_decisions`: the v4 `term_ids` decision shape stays valid in v5 alongside the new scoped shape, so an in-flight reply can still record.

The v5 to v6 migration removes the density cap and its bookkeeping, because the agent now looks words up instead of receiving a sample: it drops `config.known_per_reply` and `progress.offers` and clears `pending_decisions` (each was issued under the old scope rules; a reply in flight simply records nothing). Term history, pacing and everything else are untouched. The original is first preserved as `state.json.schema-v5.backup`, like every earlier migration. A v4 or older state migrates through each step in order, so a v4 integer budget is discarded at the last one.
