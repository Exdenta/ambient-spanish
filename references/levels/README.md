# Level packs

`lexicon.tsv` lists about 4,500 Peninsular Spanish words graded from A0 to C1. `ambient_state.py vocab --level B1` makes every word up to B1 known in one step; `ambient_state.py levels` prints the sizes.

| Level | New words | Known if chosen |
| --- | --- | --- |
| A0 | 143 | 143 |
| A1 | 673 | 816 |
| A2 | 1,286 | 2,102 |
| B1 | 1,077 | 3,179 |
| B2 | 773 | 3,952 |
| C1 | 511 | 4,463 |

## Files

| File | What it holds |
| --- | --- |
| `lexicon.tsv` | `level id spanish english kind usage`, tab-separated, in teaching order within each level |
| `excluded.tsv` | words the build dropped, with the reason: proper nouns, Latin-American-only forms, fragments, variant spellings |
| `a0.txt` | the hand-picked A0 survival set |

## How levels are assigned

A1 to C1 come from [ELELex](https://cental.uclouvain.be/cefrlex/elelex/), part of the CEFRLex project at UCLouvain. ELELex counts how often each lemma appears in Spanish-as-a-foreign-language textbooks at each CEFR level.

A word goes to the first level by which it has appeared in at least 5 textbook documents, counting lower levels too. The simpler "first level it appears at all" rule lets one textbook's rare word land in A1.

Only content words are graded: nouns, verbs, adjectives, adverbs, interjections and multiword units. Function words and proper nouns are left out.

The 424 hand-written entries in `../curriculum.json` keep their ids, glosses and usage notes. Each one takes the earlier of two levels: ELELex's, and the block it sits in within the curriculum (A1 core, everyday A2, then B2). ELELex barely counts function words, numbers and multiword connectors, so for those the block decides.

A0 is not a CEFR level and ELELex has no data for it, so `a0.txt` was picked by hand.

English glosses and parts of speech for words outside the curriculum were written for this project. A reviewer pass dropped:
- Latin-American-only forms (checked against the Peninsular blocklist in `tests/test_ambient_state.py`);
- proper nouns, inflected forms and tagging errors.

## Rebuilding

```bash
python3 scripts/build_lexicon.py candidates --elelex /tmp/ELELex.tsv --download --out todo.tsv
# gloss todo.tsv into: spanish  english  kind  keep  note   (keep 0 = drop, note = why)
python3 scripts/build_lexicon.py build --elelex /tmp/ELELex.tsv --glosses glossed.tsv
python3 -m unittest discover -s tests
```

The current lexicon doubles as the gloss cache, so a rebuild only needs glosses for words it has not seen. Don't commit the ELELex file itself.

## License

`lexicon.tsv` and `excluded.tsv` are derived from ELELex and are licensed, like ELELex, under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) (see `LICENSE`). You may share and adapt them for non-commercial use, with credit and under the same licence. The code in this repository stays under the MIT licence in the root `LICENSE`.

Credit: ELELex, CEFRLex project, CENTAL, Université catholique de Louvain, https://cental.uclouvain.be/cefrlex/elelex/.
