# State contract v7

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

- `baseline_known_count` becomes the known count and `start_date` becomes today, so batch 0 is the head of the queue and the live vocabulary is the known count plus one batch. A `start_date` still in the future is kept, so a rebuild never unlocks batch 0 early.
- `--keep-known` keeps `baseline_known_count + max(batch_index, 0) * batch_size` leading terms as known (the batches before the current one). The rebuild then re-adds the current batch as batch 0, so repeating the same rebuild leaves the vocabulary size unchanged.
- Usage history stays attached to curriculum ids:
  - A used term whose Spanish form moved to a new id has its history moved, or merged if the new id already has some.
  - A used term the new build left out is appended to the known set, so every `progress.terms` id still exists.
- The previous personal curriculum and state are copied to `*.previous`. Then `vocab` writes the curriculum, then `curriculum.meta.json` (a build summary), then the state, then the vocabulary file (the live vocabulary, known count plus one batch), each atomically.
  - A crash between the curriculum and state writes can leave a state that fails validation against the new curriculum (for example, a used term the new curriculum lacks). Re-running `vocab` does not reliably repair this, because it first validates the state against the curriculum now on disk and fails the same way. Restore the `*.previous` curriculum and state pair, then re-run `vocab`.

Do not delete the personal curriculum by hand. Its baseline would then index the shipped curriculum and shift the weekly window. Rebuild instead.

## Semantics

- `schema_version`: Exact persisted contract version. Unknown versions fail closed.
- `config.start_date`: Local date on which batch 0 begins.
- `config.cadence_days`: Calendar days per batch. Default: 7 (one week).
- `config.batch_size`: New terms added per batch, the learner's words per week. Default: 10.
- `config.baseline_known_count`: Leading curriculum items treated as already known at `start_date` and therefore never added as a batch. Batch 0 starts at this offset. Set by migration to the number of terms already introduced under the previous schema, and adjustable with `configure --baseline-known`.

  This is how pre-existing knowledge enters the system. For one user, `vocab` builds a personal curriculum with the known words at the front and sets the count itself. For the shipped curriculum, the words every user already knows sit at the **front** of `references/curriculum.json`, and `SHIPPED_BASELINE_KNOWN` (321) covers them: a state created against the shipped curriculum, by any command or by `init` without `--baseline-known`, starts with that baseline. Any other curriculum starts at 0. Because the vocabulary is index-based, prepending `n` items to the curriculum without adding `n` to `baseline_known_count` silently shifts the weekly window backwards over words the user already knows — always change the two together.
- `config.timezone`: IANA timezone used for day boundaries. Default: `Europe/Madrid`.
- `config.dialect`: Output dialect hint. Default: `es-ES`.
- `config.paused`: Stops substitution without deleting progress.
- `progress.last_exposure_at`: Latest recorded use. `record` refuses to run with the clock before it. It does not cap or gate anything.
- `progress.terms`: Observational usage history. `use_count` and `introduced_at` never affect the vocabulary or unlocking.

## Vocabulary derivation

The vocabulary is a pure function of the local date, not of usage. There is one vocabulary list: the words the learner already knows plus every weekly addition so far. `config.batch_size` is the learner's words per week and `config.cadence_days` is 7:

```
batch_index = (today - start_date) // cadence_days      # negative before start_date
vocabulary  = curriculum[: baseline_known_count + (batch_index + 1) * batch_size]
```

Consequences: the first weekly batch joins on `start_date` and a new one every `cadence_days` after, whether or not any term was ever used; no term is ever glossed in a reply (the hover mod shows the English); and `progress.terms` is **not** required to form a curriculum prefix.

## Lookup and the vocabulary file

The agent does not read the vocabulary. Per reply it pipes its draft to `ambient_state.py lookup`, which runs everything `context` does and then the Rust binary `ambient-lookup` on the draft, and prints:

```
{"active": <bool>, "reason": <as in context>, "matches": ["english: spanish | spanish", ...]}
```

An inactive lookup, or an empty draft, returns no matches without running the binary. A binary that exits non-zero, or runs longer than 30 seconds, fails the command with `ambient-lookup failed: <its stderr>`. `lookup` writes the state only when `context` would (creating or migrating it).

`context` returns the same check without running the binary, plus, instead of any term list:

```
"lookup": {"command": <abs path of ambient-lookup>, "vocabulary": <abs path of vocabulary.txt>},
"vocabulary_count": <terms in the vocabulary today>
```

The binary resolves from `AMBIENT_LOOKUP_BIN`, then `<repo>/rust/ambient-lookup/target/release/ambient-lookup`. If it does not exist, an active `context` or `lookup` fails with `ambient-lookup binary not found: run python3 scripts/install.py` (which builds it with cargo). An inactive one (paused, outside the CLI, before the start date) needs no binary, and `context` returns `lookup: null`. `status` never needs it and reports `lookup_binary` (a path or `null`).

`context` and `lookup` write the whole vocabulary to `vocabulary.txt` beside the state file, as `id | spanish | english` lines grouped by kind, and the lookup binary reads it. The hover mod reads it too, and that is the only reason it is a file the mod can rely on. It is a derived artifact: deleting it is safe and the next `context` or `lookup` rewrites it. It is rewritten only when its content changes (when a weekly batch lands), so readers can cache it.

## `record --used`

`--used` takes the Spanish words as `ambient-lookup` prints them, or curriculum ids, comma-separated. Each token resolves in this order:

1. The exact Spanish form, case-folded and normalised through `_word_key` (surrounding punctuation ignored, so `Hola!` and `¿adiós?` work). `--used campana` is the Spanish word campana, not the id `campana`.
2. A curriculum id.
3. The accent-folded Spanish form, only if it matches exactly one entry.

A Spanish form shared by several entries (homographs such as `porque` conjunction and noun) keeps only the entries in today's vocabulary; it is an error only if none are (`Terms are not in today's vocabulary`). A token that matches nothing fails with `Unknown curriculum terms`.

`--decision` is accepted and ignored, so older instructions keep working. `context` returns no `decision_id` and writes nothing when the state is already at the current schema (it may refresh `vocabulary.txt`), so it reports `write_durability: not-written`.

## Invariants

1. Message count never changes the vocabulary or unlocking.
2. A reply may draw on any term of today's vocabulary. There is no density cap and no probabilistic exposure gate.
3. No term is glossed in a reply. The hover mod shows the English.
4. Vocabulary membership is derived solely from `start_date`, `cadence_days`, `batch_size`, and `baseline_known_count`.
5. Missed time never causes a multi-batch catch-up: elapsed days advance `batch_index`, so skipped batches simply join the vocabulary rather than queueing.
6. Mutations use a lock plus atomic replacement.
7. Unknown schema versions and curriculum identifiers fail closed.
8. Test-time clock overrides require `AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE=1`; ordinary runtime uses the system clock.
9. `last_exposure_at` must equal the newest `terms[*].last_used_at`.
10. Exact integers reject booleans and numeric lookalikes.
11. A `record` transition requires the resolved `--used` terms to lie in today's vocabulary and the clock not to be before `last_exposure_at`.

Mutating commands return `write_durability: confirmed` after both the file and parent directory sync. If the replacement is visible but the filesystem cannot confirm the directory sync, they return `write_durability: uncertain` while keeping `ok: true`; this avoids claiming that an already-visible transition failed.

## Migration rule

Never silently reinterpret an existing field. A breaking change requires a new `schema_version` and an explicit migration that preserves the original file until the migrated state validates.

Migrations run in order, each preserving the original file as a backup first. Schema v1 migrates to v2 in memory (adds `config.exposure_percent: 50`, renames `progress.last_any_insertion_at` to `progress.last_exposure_at`, initializes `progress.pending_decisions`, and validates that introduced items form a curriculum prefix on distinct increasing local dates), then v2 migrates to v3.

The v2 to v3 migration converts one-item-per-reply pacing into batch pacing. It drops `config.exposure_percent` and `progress.last_new_term_at`, adds `config.batch_size` at the default of 10, sets `config.baseline_known_count` to the number of terms already introduced, resets `config.start_date` to the migration's local date so batch 0 begins immediately, and clears `pending_decisions` because the v2 single-term decision shape is incompatible. Term history is preserved verbatim. The original document is written to `state.json.schema-v<n>.backup` (where `<n>` is the version actually found on disk) before live state is replaced.

Every already-introduced term joins the baseline on the grounds that it was taught under the previous regime; the first batch is the next `batch_size` unseen curriculum items. The migration also sets `cadence_days` to 7, since it restarts the calendar anyway.

The v3 to v4 migration adds `config.known_per_reply: 18` and clears `pending_decisions`, because a v3 decision reserved an unbounded active set and honouring one would let a single reply ignore the new budget. Config and term history are otherwise untouched.

The v4 to v5 migration makes the budget nullable and adds `progress.offers`. A v4 state keeps its integer `known_per_reply` — an existing density setting is a user choice, not a default to overwrite — so only a fresh state starts uncapped. Offers are seeded from term history (`count = use_count`, `last_offered_at = last_used_at`) so a term already in rotation is not treated as never offered, which would otherwise put every used term ahead of untried ones on the first v5 sample. The v4 `term_ids` decision shape stays valid in v5 alongside the new scoped shape; the v5 to v6 migration then clears all pending decisions.

The v5 to v6 migration removes the density cap and its bookkeeping, because the agent now looks words up instead of receiving a sample: it drops `config.known_per_reply` and `progress.offers` and clears `pending_decisions` (each was issued under the old scope rules; a reply in flight simply records nothing). Term history is untouched. The original is first preserved as `state.json.schema-v5.backup`, like every earlier migration. A v4 or older state migrates through each step in order, so a v4 integer budget is discarded at the last one.

The same migration moves pacing to weekly: `cadence_days` becomes 7 and `batch_size` becomes `max(1, round(batch_size * 7 / cadence_days))` (an old 3 words every 3 days becomes 7 per week). It rebases so today's vocabulary size does not change: the old size as of the migration's local date is computed with the old pacing, `baseline_known_count` becomes that size minus the new batch size (floored at 0; when the floor applies the vocabulary grows to one new batch), and `start_date` becomes the migration's local date. A `start_date` still in the future is left as is.

`configure --words-per-week N` rebases the same way, so the current vocabulary size neither jumps nor shrinks when the pace changes (unless `--baseline-known` is given in the same call, which then sets the baseline itself). `vocab --words-per-week` needs no rebase, since a rebuild sets the baseline from the known words.

The v6 to v7 migration drops `progress.pending_decisions`, because `record` now checks the used words against today's vocabulary instead of a reservation; term history is untouched. The original is first preserved as `state.json.schema-v6.backup`.
