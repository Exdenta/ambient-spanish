#!/usr/bin/env python3
"""Maintainer tool: build `references/levels/lexicon.tsv` from ELELex.

Not used at runtime; `ambient_state.py` only reads the lexicon this writes.

    python3 scripts/build_lexicon.py candidates --elelex ELELex.tsv --out todo.tsv
    # gloss todo.tsv into a TSV with columns: spanish, english, kind, keep, note
    python3 scripts/build_lexicon.py build --elelex ELELex.tsv --glosses glossed.tsv

ELELex (CEFRLex project, UCLouvain) counts how often each lemma appears in
Spanish-as-a-foreign-language textbooks at each CEFR level, A1 to C1. A word is
placed at the first level by which it has appeared in at least `EXPOSURE_DOCS`
textbook documents, lower levels included: a learner has met it often enough
to know it by then. "First level it appears at all" is too noisy; one textbook
using a rare word at A1 would make it A1.

The existing lexicon doubles as the gloss cache, so a rebuild only needs
glosses for words it has not seen. Words a glosser rejected (`keep` 0) go to
`excluded.tsv` with the reason and are never offered again. A0 is not an
ELELex level: it comes from the hand-picked `a0.txt`.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LEVELS_DIR = ROOT / "references" / "levels"
LEXICON_PATH = LEVELS_DIR / "lexicon.tsv"
EXCLUDED_PATH = LEVELS_DIR / "excluded.tsv"
A0_PATH = LEVELS_DIR / "a0.txt"
CURRICULUM_PATH = ROOT / "references" / "curriculum.json"
ELELEX_URL = "https://cental.uclouvain.be/cefrlex/static/resources/es/ELELex.tsv"

LEVELS = ("A0", "A1", "A2", "B1", "B2", "C1")
ELELEX_LEVELS = ("a1", "a2", "b1", "b2", "c1")
KINDS = ("verb", "noun", "adjective", "adverb", "phrase", "connector")
EXPOSURE_DOCS = 5
LEXICON_FIELDS = ("level", "id", "spanish", "english", "kind", "usage")
GLOSS_FIELDS = ("spanish", "english", "kind", "keep", "note")
# Curated entries ELELex does not grade fall back to the band the curriculum
# placed them in: the A1 core, the everyday pre-known block, then the rest.
CURATED_BANDS = ((109, "A1"), (321, "A2"))
CURATED_FALLBACK = "B2"
CONTRACTIONS = ((re.compile(r"\ba el\b"), "al"), (re.compile(r"\bde el\b"), "del"))


def normalize(word: str) -> str:
    text = unicodedata.normalize("NFC", word).strip().lower().replace("_", " ")
    text = " ".join(text.split())
    for pattern, replacement in CONTRACTIONS:
        text = pattern.sub(replacement, text)
    return text


def slug(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", stripped).strip("-")


def is_content(word: str, tag: str) -> bool:
    """Nouns, verbs, adjectives, adverbs, interjections, and multiword units.

    Single function words (articles, pronouns, prepositions, conjunctions,
    determiners) are never substituted, and proper nouns are not vocabulary.
    """
    if tag.startswith("NP"):
        return False
    if " " in word:
        return True
    return tag == "I" or tag.startswith(("NC", "AQ", "VM", "RG"))


def level_of(docs: list[float]) -> str | None:
    seen = 0.0
    for level, count in zip(ELELEX_LEVELS, docs):
        seen += count
        if seen >= EXPOSURE_DOCS:
            return level.upper()
    return None


def read_elelex(path: Path) -> dict[str, dict[str, Any]]:
    """Aggregate ELELex rows per normalized word, keeping content rows apart."""
    words: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            word = normalize(row["word"])
            tag = row["tag"].strip()
            if not word:
                continue
            entry = words.setdefault(
                word,
                {
                    "docs": [0.0] * len(ELELEX_LEVELS),
                    "content_docs": [0.0] * len(ELELEX_LEVELS),
                    "freq": 0.0,
                    "tags": Counter(),
                },
            )
            docs = [float(row[f"nb_doc@{level}"] or 0) for level in ELELEX_LEVELS]
            freq = float(row["total_freq@total"] or 0)
            entry["docs"] = [a + b for a, b in zip(entry["docs"], docs)]
            if is_content(word, tag):
                entry["content_docs"] = [
                    a + b for a, b in zip(entry["content_docs"], docs)
                ]
                entry["freq"] += freq
                entry["tags"][tag] += freq
    return words


def read_a0() -> list[str]:
    lines = A0_PATH.read_text(encoding="utf-8").splitlines()
    return [normalize(line) for line in lines if line.strip() and not line.lstrip().startswith("#")]


def read_curated() -> list[dict[str, str]]:
    return json.loads(CURRICULUM_PATH.read_text(encoding="utf-8"))


def read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE))


def write_tsv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    for row in rows:
        for field in fields:
            value = row.get(field, "")
            if "\t" in value or "\n" in value:
                raise SystemExit(f"{path.name}: tab or newline in {field} of {row}")
    lines = ["\t".join(fields)]
    lines.extend("\t".join(row.get(field, "") for field in fields) for row in rows)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_elelex(path: Path, download: bool) -> Path:
    if path.exists():
        return path
    if not download:
        raise SystemExit(f"{path} not found; pass --download to fetch {ELELEX_URL}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(ELELEX_URL) as response:  # noqa: S310 - fixed URL
        path.write_bytes(response.read())
    return path


def curated_level(index: int, word: str, elelex: dict[str, dict[str, Any]], a0: set[str]) -> str:
    """The earlier of ELELex's level and the curriculum band the word was placed in.

    ELELex barely counts function words, numerals and multiword connectors
    ("como", "dos", "por lo tanto"), so for those its level is noise or
    missing. The curated band caps it: ELELex can only move a word earlier.
    """
    if word in a0:
        return "A0"
    band = next((level for bound, level in CURATED_BANDS if index < bound), CURATED_FALLBACK)
    entry = elelex.get(word)
    # Curated entries include connectors and function-like words the content
    # filter skips, so every reading of the word counts here.
    graded = level_of(entry["docs"]) if entry is not None else None
    if graded is None:
        return band
    return min(graded, band, key=LEVELS.index)


def cmd_candidates(args: argparse.Namespace) -> None:
    elelex = read_elelex(ensure_elelex(Path(args.elelex), args.download))
    curated = {normalize(item["spanish"]) for item in read_curated()}
    known = {normalize(row["spanish"]) for row in read_tsv(LEXICON_PATH)}
    excluded = {normalize(row["spanish"]) for row in read_tsv(EXCLUDED_PATH)}
    skip = curated | known | excluded
    rows: list[dict[str, str]] = []
    a0 = read_a0()
    for word in a0:
        if word not in skip:
            tags = elelex.get(word, {}).get("tags") or Counter()
            rows.append({"spanish": word, "tag": _top_tag(tags), "level": "A0", "freq": "0"})
            skip.add(word)
    for word, entry in elelex.items():
        level = level_of(entry["content_docs"])
        if level is None or word in skip:
            continue
        rows.append(
            {
                "spanish": word,
                "tag": _top_tag(entry["tags"]),
                "level": level,
                "freq": f"{entry['freq']:.2f}",
            }
        )
    rows.sort(key=lambda row: (LEVELS.index(row["level"]), -float(row["freq"]), row["spanish"]))
    write_tsv(Path(args.out), ("spanish", "tag", "level", "freq"), rows)
    print(json.dumps({"candidates": len(rows), "out": args.out}))


def _top_tag(tags: Counter) -> str:
    return tags.most_common(1)[0][0] if tags else ""


def cmd_build(args: argparse.Namespace) -> None:
    elelex = read_elelex(ensure_elelex(Path(args.elelex), args.download))
    a0_order = read_a0()
    a0 = set(a0_order)
    curated = read_curated()

    glosses: dict[str, dict[str, str]] = {
        normalize(row["spanish"]): {"english": row["english"], "kind": row["kind"]}
        for row in read_tsv(LEXICON_PATH)
    }
    excluded: dict[str, str] = {
        normalize(row["spanish"]): row["reason"] for row in read_tsv(EXCLUDED_PATH)
    }
    for gloss_path in args.glosses:
        for row in read_tsv(Path(gloss_path)):
            word = normalize(row["spanish"])
            if row.get("keep", "1").strip() == "0":
                excluded[word] = row.get("note", "").strip() or "rejected"
                glosses.pop(word, None)
                continue
            kind = row["kind"].strip()
            if kind not in KINDS:
                raise SystemExit(f"{gloss_path}: unknown kind {kind!r} for {word}")
            english = row["english"].strip()
            if not english:
                raise SystemExit(f"{gloss_path}: empty english for {word}")
            glosses[word] = {"english": english, "kind": kind}
            excluded.pop(word, None)

    entries: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(curated):
        word = normalize(item["spanish"])
        entries[word] = {
            "level": curated_level(index, word, elelex, a0),
            "id": item["id"],
            "spanish": item["spanish"],
            "english": item["english"],
            "kind": item["kind"],
            "usage": item["usage"],
            "rank": (0, index),
        }
    missing: list[str] = []
    for position, word in enumerate(a0_order):
        if word in entries:
            entries[word]["level"] = "A0"
            entries[word]["rank"] = (-1, position)
            continue
        if word not in glosses:
            missing.append(word)
            continue
        entries[word] = {"level": "A0", "spanish": word, "usage": "", "rank": (-1, position), **glosses[word]}
    for word, entry in elelex.items():
        level = level_of(entry["content_docs"])
        if level is None or word in entries or word in excluded:
            continue
        if word not in glosses:
            missing.append(word)
            continue
        entries[word] = {
            "level": level,
            "spanish": word,
            "usage": "",
            "rank": (1, -entry["freq"]),
            **glosses[word],
        }

    ordered = sorted(
        entries.values(),
        key=lambda row: (LEVELS.index(row["level"]), row["rank"], row["spanish"]),
    )
    taken: dict[str, str] = {
        row["id"]: row["spanish"] for row in ordered if row.get("id")
    }
    for row in ordered:
        if row.get("id"):
            continue
        base = slug(row["spanish"])
        candidate = base
        if candidate in taken:
            candidate = f"{base}-{row['kind']}"
        suffix = 2
        while candidate in taken:
            candidate = f"{base}-{row['kind']}-{suffix}"
            suffix += 1
        row["id"] = candidate
        taken[candidate] = row["spanish"]

    write_tsv(LEXICON_PATH, LEXICON_FIELDS, ordered)
    write_tsv(
        EXCLUDED_PATH,
        ("spanish", "reason"),
        [{"spanish": word, "reason": reason} for word, reason in sorted(excluded.items())],
    )
    counts = Counter(row["level"] for row in ordered)
    summary = {
        "lexicon": str(LEXICON_PATH.relative_to(ROOT)),
        "levels": {level: counts.get(level, 0) for level in LEVELS},
        "total": len(ordered),
        "excluded": len(excluded),
        "missing_glosses": len(missing),
    }
    print(json.dumps(summary, ensure_ascii=False))
    if missing:
        print("missing glosses: " + ", ".join(missing[:40]), file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("candidates", cmd_candidates), ("build", cmd_build)):
        command = sub.add_parser(name)
        command.add_argument("--elelex", required=True, help="Path to ELELex.tsv")
        command.add_argument("--download", action="store_true", help="Fetch ELELex if missing")
        command.set_defaults(handler=handler)
        if name == "candidates":
            command.add_argument("--out", required=True)
        else:
            command.add_argument("--glosses", nargs="*", default=[])
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
