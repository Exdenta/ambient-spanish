# Contributing

Thanks for taking a look. Small, focused changes are easiest to review.

## Setup

No dependencies beyond Python 3.11+.

```bash
git clone https://github.com/Exdenta/ambient-spanish
cd ambient-spanish
python3 -m unittest discover -s tests -v
```

CI runs the same command on Python 3.11 and 3.13.

For the hover mod, see [its README](mods/ambient-spanish-hover/README.md#develop).

## Changing the curriculum

`references/curriculum.json` is an ordered list; position matters.

- Every word must pass the Peninsular-Spanish blocklist in `tests/test_ambient_state.py`.
- Pre-known words go at the front of the list, and `baseline_known_count` must rise by the same amount. Otherwise the learning window shifts over words people already know.
- Don't reorder existing entries without a migration note in `references/state-contract.md`; saved progress is positional.

## Changing state handling

`scripts/ambient_state.py` is the only writer of `state.json`. If you change the schema, update `references/state-contract.md` and add a test for migrating an older file.

## Pull requests

- Branch from `main`, keep the diff to one concern.
- Add or update tests for behaviour changes.
- Use conventional commit prefixes (`feat`, `fix`, `docs`, `chore`), as in the existing history.
- Add a line under "Unreleased" in [CHANGELOG.md](CHANGELOG.md) for anything a user would notice.
