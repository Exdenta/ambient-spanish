#!/usr/bin/env python3
"""Calendar-governed batch curriculum and vocabulary state for ambient Spanish."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import fcntl
except ImportError:  # pragma: no cover - Unix is the supported Codex runtime.
    fcntl = None


SCHEMA_VERSION = 6
LEGACY_SCHEMA_VERSIONS = (1, 2, 3, 4, 5)
DEFAULT_TIMEZONE = "Europe/Madrid"
DEFAULT_DIALECT = "es-ES"
DEFAULT_CADENCE_DAYS = 7  # one week between additions to the vocabulary
DEFAULT_BATCH_SIZE = 10  # new words per week
LEGACY_V4_KNOWN_PER_REPLY = 18  # v4 required a cap; only the migration path needs it.
MANIFEST_FILENAME = "vocabulary.txt"
LOOKUP_BIN_ENV = "AMBIENT_LOOKUP_BIN"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOOKUP_BIN = REPO_ROOT / "rust" / "ambient-lookup" / "target" / "release" / "ambient-lookup"
MANIFEST_KIND_ORDER = ("verb", "noun", "adjective", "adverb", "phrase", "connector")
DEFAULT_EXPOSURE_PERCENT = 50  # Legacy v2 field, retained only for migration.
MAX_PENDING_DECISIONS = 128
MAX_DECISION_AGE_SECONDS = 24 * 60 * 60
DEFAULT_STATE_PATH = Path.home() / ".codex" / "state" / "ambient-spanish" / "state.json"
DEFAULT_CURRICULUM_PATH = Path(__file__).resolve().parent.parent / "references" / "curriculum.json"
DEFAULT_LEXICON_PATH = (
    Path(__file__).resolve().parent.parent / "references" / "levels" / "lexicon.tsv"
)
# A personal curriculum written by `vocab` lives beside the state it indexes,
# so the two travel together and the shipped curriculum stays the default.
USER_CURRICULUM_FILENAME = "curriculum.json"
USER_CURRICULUM_META_FILENAME = "curriculum.meta.json"
LEVELS = ("A0", "A1", "A2", "B1", "B2", "C1")
LEVEL_DESCRIPTIONS = {
    "A0": "Absolute beginner: greetings and a survival core.",
    "A1": "Beginner: everyday basics such as family, food, time and simple actions.",
    "A2": "Elementary: routine tasks, shopping, work and travel.",
    "B1": "Intermediate: opinions, plans, news and most everyday topics.",
    "B2": "Upper intermediate: abstract topics, work and debate.",
    "C1": "Advanced: nuanced, formal and specialised vocabulary.",
}
LEXICON_FIELDS = ("level", "id", "spanish", "english", "kind", "usage")
KINDS = ("verb", "noun", "adjective", "adverb", "phrase", "connector")
KIND_ALIASES = {
    "v": "verb",
    "n": "noun",
    "adj": "adjective",
    "adv": "adverb",
    "expr": "phrase",
    "expression": "phrase",
    "interjection": "phrase",
    "conj": "connector",
    "conjunction": "connector",
}
# Lexicon rows without a hand-written usage note get the one for their kind.
KIND_USAGE = {
    "verb": "Conjugate to fit; use where the reply describes this action.",
    "noun": "Use where the reply names this thing.",
    "adjective": "Agree in gender and number; use where the reply describes this quality.",
    "adverb": "Use where the reply qualifies a statement this way.",
    "phrase": "Use as a set expression where the reply would say this.",
    "connector": "Use to link two ideas the way the English would.",
}
LEADING_ARTICLES = ("el ", "la ", "los ", "las ", "un ", "una ")


class StateError(RuntimeError):
    """Raised when state or a requested transition is invalid."""


def _words_per_week(value: int) -> int:
    if value < 1:
        raise StateError("words per week must be at least 1")
    return value


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _state_path(value: str | None) -> Path:
    raw = value or os.environ.get("AMBIENT_SPANISH_STATE")
    return Path(raw).expanduser().resolve() if raw else DEFAULT_STATE_PATH


def _curriculum_source(value: str | None, state_path: Path) -> tuple[Path, str]:
    """Resolve the curriculum: flag, then environment, then the user's, then shipped."""
    if value:
        return Path(value).expanduser().resolve(), "flag"
    raw = os.environ.get("AMBIENT_SPANISH_CURRICULUM")
    if raw:
        return Path(raw).expanduser().resolve(), "env"
    personal = state_path.parent / USER_CURRICULUM_FILENAME
    if personal.exists():
        return personal, "user"
    return DEFAULT_CURRICULUM_PATH, "shipped"


def _curriculum_path(value: str | None, state_path: Path) -> Path:
    return _curriculum_source(value, state_path)[0]


def _lexicon_path(value: str | None) -> Path:
    raw = value or os.environ.get("AMBIENT_SPANISH_LEXICON")
    return Path(raw).expanduser().resolve() if raw else DEFAULT_LEXICON_PATH


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise StateError(f"Unknown IANA timezone: {name}") from exc


def _parse_now(value: str | None, timezone_name: str) -> datetime:
    zone = _timezone(timezone_name)
    if value is None:
        return datetime.now(zone)
    if os.environ.get("AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE") != "1":
        raise StateError(
            "--now is disabled outside deterministic tests; use the real system clock"
        )
    return _parse_timestamp(value, timezone_name, field="datetime")


def _parse_timestamp(value: str, timezone_name: str, *, field: str) -> datetime:
    zone = _timezone(timezone_name)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise StateError(f"Invalid ISO {field}: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise StateError(f"Invalid {field}: {value}") from exc


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StateError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StateError(f"Invalid JSON in {path}: {exc}") from exc


def _atomic_write(path: Path, value: Any) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        durability_confirmed = True
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # Replacement has already committed. Some filesystems do not allow
            # directory fsync. Report uncertainty without falsely reporting the
            # visible state as failed.
            durability_confirmed = False
        return durability_confirmed
    finally:
        if temp_path.exists():
            temp_path.unlink()


@contextmanager
def _locked(state_path: Path) -> Iterator[None]:
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_curriculum(path: Path) -> list[dict[str, str]]:
    value = _read_json(path)
    if not isinstance(value, list) or not value:
        raise StateError("Curriculum must be a non-empty JSON array")
    required = {"id", "spanish", "english", "kind", "usage"}
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not required.issubset(item):
            raise StateError(f"Curriculum item {index} lacks required fields")
        normalized = {key: str(item[key]) for key in required}
        term_id = normalized["id"]
        if term_id in seen:
            raise StateError(f"Duplicate curriculum id: {term_id}")
        seen.add(term_id)
        result.append(normalized)
    return result


def _load_lexicon(path: Path) -> dict[str, list[dict[str, str]]]:
    """Level packs from the graded lexicon: the words new at each level, in teaching order."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise StateError(f"Lexicon not found: {path}") from exc
    if not lines or tuple(lines[0].split("\t")) != LEXICON_FIELDS:
        header = " ".join(LEXICON_FIELDS)
        raise StateError(f"Lexicon {path} must start with the tab-separated header: {header}")
    packs: dict[str, list[dict[str, str]]] = {level: [] for level in LEVELS}
    seen: set[str] = set()
    for number, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != len(LEXICON_FIELDS):
            raise StateError(f"Lexicon line {number} has {len(fields)} fields")
        row = dict(zip(LEXICON_FIELDS, fields))
        if row["level"] not in packs:
            raise StateError(f"Lexicon line {number} has unknown level {row['level']}")
        if row["kind"] not in KINDS:
            raise StateError(f"Lexicon line {number} has unknown kind {row['kind']}")
        if not row["id"] or not row["spanish"] or not row["english"]:
            raise StateError(f"Lexicon line {number} lacks an id, spanish or english")
        if row["id"] in seen:
            raise StateError(f"Duplicate lexicon id: {row['id']}")
        seen.add(row["id"])
        packs[row["level"]].append(
            {
                "id": row["id"],
                "spanish": row["spanish"],
                "english": row["english"],
                "kind": row["kind"],
                "usage": row["usage"] or KIND_USAGE[row["kind"]],
            }
        )
    return packs


def _word_key(text: str) -> str:
    """Case-, spacing- and punctuation-insensitive form of a Spanish entry."""
    folded = unicodedata.normalize("NFC", text).casefold().strip()
    folded = folded.strip("¿¡!?.,;:\"'«»“”()[]")
    return " ".join(folded.split())


def _accentless(text: str) -> str:
    """Drop accents but keep ñ, which is a different letter, not an accented n."""
    decomposed = unicodedata.normalize("NFD", text.replace("ñ", "\0"))
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return stripped.replace("\0", "ñ")


def _slug(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", stripped).strip("-") or "term"


def _read_word_list(path: Path) -> list[dict[str, str]]:
    """Entries from a user word list.

    Either a JSON array (of strings, or of objects with `spanish` and optional
    `english`, `kind`, `usage`, `id`), or text with one `spanish | english |
    kind` entry per line, where english and kind are optional, a tab may stand
    in for `|` (so an Anki or spreadsheet export works as is), and `#` starts
    a comment.
    """
    try:
        text = path.expanduser().read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise StateError(f"Word list not found: {path}") from exc
    entries: list[dict[str, str]] = []
    if text.lstrip().startswith("["):
        try:
            items = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StateError(f"Invalid JSON in {path}: {exc}") from exc
        for index, item in enumerate(items):
            if isinstance(item, str):
                item = {"spanish": item}
            if not isinstance(item, dict) or not isinstance(item.get("spanish"), str):
                raise StateError(f"{path}: item {index} needs a 'spanish' string")
            entries.append(
                {
                    key: str(item[key]).strip()
                    for key in ("spanish", "english", "kind", "usage", "id")
                    if item.get(key) not in (None, "")
                }
            )
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("\t" if "\t" in line else "|")]
            entry = {"spanish": parts[0]}
            for key, value in zip(("english", "kind"), parts[1:3]):
                if value:
                    entry[key] = value
            entries.append(entry)
    return [entry for entry in entries if _word_key(entry.get("spanish", ""))]


class VocabularyError(StateError):
    """A `vocab` request that cannot be built as given; `details` says why."""

    def __init__(self, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


class _Catalog:
    """Every term `vocab` can draw on, looked up by Spanish form or id."""

    def __init__(self) -> None:
        self.by_key: dict[str, dict[str, str]] = {}
        self.by_id: dict[str, dict[str, str]] = {}
        self.by_accentless: dict[str, list[str]] = {}

    def add(self, entry: dict[str, str]) -> None:
        key = _word_key(entry["spanish"])
        if key in self.by_key:
            return
        self.by_key[key] = entry
        self.by_id.setdefault(entry["id"], entry)
        self.by_accentless.setdefault(_accentless(key), []).append(key)

    def find(self, text: str) -> dict[str, str] | None:
        key = _word_key(text)
        candidates = [key] + [
            key[len(article) :] for article in LEADING_ARTICLES if key.startswith(article)
        ]
        for candidate in candidates:
            if candidate in self.by_key:
                return self.by_key[candidate]
        if key in self.by_id:
            return self.by_id[key]
        for candidate in candidates:
            matches = self.by_accentless.get(_accentless(candidate), [])
            if len(matches) == 1:
                return self.by_key[matches[0]]
        return None

    def unique_id(self, text: str) -> str:
        base = _slug(text)
        candidate, suffix = base, 2
        while candidate in self.by_id:
            candidate, suffix = f"{base}-{suffix}", suffix + 1
        return candidate


def _compose_vocabulary(
    packs: dict[str, list[dict[str, str]]],
    *,
    level: str | None,
    current: list[dict[str, str]],
    keep_known: list[dict[str, str]],
    keep_queue: list[dict[str, str]],
    add_known: list[dict[str, str]],
    remove_known: list[dict[str, str]],
    learn_first: list[dict[str, str]],
) -> dict[str, Any]:
    """Order a personal curriculum: everything known first, then the queue of words to add.

    Known: the kept known tier, the level packs up to `level`, and `add_known`,
    minus anything removed or queued to learn. Queue: `learn_first` in the
    order given, then the removed words, the kept queue, and every remaining
    lexicon word, easiest level first. Each Spanish form appears once.
    """
    catalog = _Catalog()
    for pack_level in LEVELS:
        for entry in packs[pack_level]:
            catalog.add(entry)
    for entry in current:
        catalog.add(entry)

    unresolved: list[dict[str, Any]] = []
    not_found: list[str] = []

    def resolve(raw: dict[str, str], source: str) -> dict[str, str] | None:
        found = catalog.find(raw["spanish"])
        if found is not None:
            return found
        if source == "remove-known":
            not_found.append(raw["spanish"])
            return None
        kind = KIND_ALIASES.get(raw.get("kind", "").lower(), raw.get("kind", "").lower())
        missing = [field for field in ("english", "kind") if not raw.get(field)]
        if missing or kind not in KINDS:
            unresolved.append(
                {
                    "spanish": raw["spanish"],
                    "list": source,
                    "problem": f"missing {', '.join(missing)}"
                    if missing
                    else f"unknown kind {raw['kind']}",
                }
            )
            return None
        term_id = raw.get("id") or catalog.unique_id(raw["spanish"])
        if term_id in catalog.by_id:
            term_id = catalog.unique_id(term_id)
        entry = {
            "id": term_id,
            "spanish": raw["spanish"].strip(),
            "english": raw["english"],
            "kind": kind,
            "usage": raw.get("usage") or KIND_USAGE[kind],
        }
        catalog.add(entry)
        return entry

    added = [entry for raw in add_known if (entry := resolve(raw, "add-known"))]
    learn = [entry for raw in learn_first if (entry := resolve(raw, "learn-first"))]
    removed = [entry for raw in remove_known if (entry := resolve(raw, "remove-known"))]
    if unresolved:
        raise VocabularyError(
            "Some words are in no level pack and lack an english gloss or kind; "
            "give them as `spanish | english | kind`",
            {"unresolved": unresolved},
        )
    added_keys = {_word_key(entry["spanish"]) for entry in added}
    dropped_keys = {_word_key(entry["spanish"]) for entry in removed + learn}
    conflicts = sorted(added_keys & dropped_keys)
    if conflicts:
        raise VocabularyError(
            "Words listed both as known and as unknown or to learn",
            {"conflicts": conflicts},
        )

    placed: set[str] = set()
    known: list[dict[str, str]] = []
    queue: list[dict[str, str]] = []

    def place(target: list[dict[str, str]], entry: dict[str, str]) -> None:
        key = _word_key(entry["spanish"])
        if key not in placed:
            placed.add(key)
            target.append(entry)

    base = list(keep_known)
    if level is not None:
        for pack_level in LEVELS[: LEVELS.index(level) + 1]:
            base.extend(packs[pack_level])
    for entry in base:
        if _word_key(entry["spanish"]) not in dropped_keys:
            place(known, entry)
    for entry in added:
        place(known, entry)
    for entry in learn + removed + keep_queue:
        place(queue, entry)
    for pack_level in LEVELS:
        for entry in packs[pack_level]:
            place(queue, entry)
    return {
        "known": known,
        "queue": queue,
        "added": added,
        "removed": removed,
        "learn_first": learn,
        "not_found": not_found,
    }


def _new_state(
    now: datetime,
    *,
    timezone_name: str,
    dialect: str,
    cadence_days: int,
    batch_size: int,
    baseline_known_count: int = 0,
    start_date: date | None = None,
) -> dict[str, Any]:
    if cadence_days < 1:
        raise StateError("cadence_days must be at least 1")
    if batch_size < 1:
        raise StateError("batch_size must be at least 1")
    if baseline_known_count < 0:
        raise StateError("baseline_known_count must not be negative")
    return {
        "schema_version": SCHEMA_VERSION,
        "config": {
            "timezone": timezone_name,
            "dialect": dialect,
            "cadence_days": cadence_days,
            "batch_size": batch_size,
            "baseline_known_count": baseline_known_count,
            "paused": False,
            "start_date": (start_date or now.date()).isoformat(),
        },
        "progress": {
            "last_exposure_at": None,
            "pending_decisions": {},
            "terms": {},
        },
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def _validate_legacy_state(
    state: Any,
    curriculum: list[dict[str, str]],
    *,
    version: int,
) -> dict[str, Any]:
    """Validate a schema v1 or v2 document. Used only on the migration path."""
    if version not in LEGACY_SCHEMA_VERSIONS:
        raise StateError(f"No legacy validator exists for state schema_version {version}")
    if not isinstance(state, dict):
        raise StateError("State root must be an object")
    if type(state.get("schema_version")) is not int or state.get("schema_version") != version:
        raise StateError(
            f"Unsupported state schema_version {state.get('schema_version')}; expected {version}"
        )
    config = state.get("config")
    progress = state.get("progress")
    if not isinstance(config, dict) or not isinstance(progress, dict):
        raise StateError("State requires config and progress objects")
    config_keys = ["timezone", "dialect", "cadence_days", "paused", "start_date"]
    if version == 2:
        config_keys.append("exposure_percent")
    for key in config_keys:
        if key not in config:
            raise StateError(f"State config is missing {key}")
    if not isinstance(config["timezone"], str):
        raise StateError("config.timezone must be a string")
    if not isinstance(config["dialect"], str) or not config["dialect"].strip():
        raise StateError("config.dialect must be a non-empty string")
    if not isinstance(config["start_date"], str):
        raise StateError("config.start_date must be a string")
    _timezone(config["timezone"])
    _parse_date(config["start_date"], "start_date")
    cadence = config["cadence_days"]
    if type(cadence) is not int or cadence < 1:
        raise StateError("config.cadence_days must be an integer of at least 1")
    if version == 2:
        exposure_percent = config["exposure_percent"]
        if (
            type(exposure_percent) is not int
            or exposure_percent < 0
            or exposure_percent > 100
        ):
            raise StateError("config.exposure_percent must be an integer from 0 to 100")
    if not isinstance(config["paused"], bool):
        raise StateError("config.paused must be a boolean")
    for field in ("created_at", "updated_at"):
        if not isinstance(state.get(field), str):
            raise StateError(f"State requires string {field}")
        _parse_timestamp(state[field], config["timezone"], field=field)

    exposure_field = "last_any_insertion_at" if version == 1 else "last_exposure_at"
    progress_keys = [exposure_field, "last_new_term_at", "terms"]
    if version == 2:
        progress_keys.append("pending_decisions")
    for key in progress_keys:
        if key not in progress:
            raise StateError(f"State progress is missing {key}")
    for field in (exposure_field, "last_new_term_at"):
        value = progress[field]
        if value is not None and not isinstance(value, str):
            raise StateError(f"progress.{field} must be a string or null")
        if value is not None:
            _parse_timestamp(value, config["timezone"], field=field)
    terms = progress.get("terms")
    if not isinstance(terms, dict):
        raise StateError("progress.terms must be an object")
    known_ids = {term["id"] for term in curriculum}
    unknown = sorted(set(terms) - known_ids)
    if unknown:
        raise StateError(f"State contains unknown curriculum ids: {', '.join(unknown)}")
    introduced_times: list[datetime] = []
    last_used_times: list[datetime] = []
    for term_id, term_state in terms.items():
        if not isinstance(term_state, dict):
            raise StateError(f"State term {term_id} must be an object")
        for field in ("introduced_at", "last_used_at", "use_count"):
            if field not in term_state:
                raise StateError(f"State term {term_id} is missing {field}")
        if not isinstance(term_state["introduced_at"], str) or not isinstance(
            term_state["last_used_at"], str
        ):
            raise StateError(f"State term {term_id} timestamps must be strings")
        introduced_at = _parse_timestamp(
            term_state["introduced_at"], config["timezone"], field="introduced_at"
        )
        last_used_at = _parse_timestamp(
            term_state["last_used_at"], config["timezone"], field="last_used_at"
        )
        if last_used_at.timestamp() < introduced_at.timestamp():
            raise StateError(f"State term {term_id} was used before introduction")
        if type(term_state["use_count"]) is not int or term_state["use_count"] < 1:
            raise StateError(f"State term {term_id} use_count must be at least 1")
        introduced_times.append(introduced_at)
        last_used_times.append(last_used_at)

    introduced_ids = set(terms)
    introduced_prefix = {term["id"] for term in curriculum[: len(introduced_ids)]}
    if introduced_ids != introduced_prefix:
        raise StateError("Introduced terms must form a curriculum prefix")
    if version == 1:
        start_date = _parse_date(config["start_date"], "start_date")
        previous_introduced_on: date | None = None
        for term in curriculum[: len(introduced_ids)]:
            introduced_on = _parse_timestamp(
                terms[term["id"]]["introduced_at"],
                config["timezone"],
                field="introduced_at",
            ).date()
            if introduced_on < start_date:
                raise StateError(f"Term {term['id']} predates start_date")
            if (
                previous_introduced_on is not None
                and introduced_on <= previous_introduced_on
            ):
                raise StateError(
                    "Schema v1 introductions must use distinct increasing local dates"
                )
            previous_introduced_on = introduced_on

    if version == 2:
        pending = progress["pending_decisions"]
        if not isinstance(pending, dict):
            raise StateError("progress.pending_decisions must be an object")
        for decision_id, decision in pending.items():
            if not isinstance(decision_id, str) or not decision_id:
                raise StateError("Pending decision ids must be non-empty strings")
            if not isinstance(decision, dict):
                raise StateError(f"Pending decision {decision_id} must be an object")
            if set(decision) != {"action", "term_id", "created_at"}:
                raise StateError(f"Pending decision {decision_id} has invalid fields")
            if decision["action"] not in ("introduce", "review"):
                raise StateError(f"Pending decision {decision_id} has invalid action")
            if decision["term_id"] not in known_ids:
                raise StateError(f"Pending decision {decision_id} has unknown term")
            if not isinstance(decision["created_at"], str):
                raise StateError(f"Pending decision {decision_id} requires created_at")
            _parse_timestamp(decision["created_at"], config["timezone"], field="created_at")
    if terms and progress[exposure_field] is None:
        raise StateError(f"Introduced terms require {exposure_field}")
    if terms and progress["last_new_term_at"] is None:
        raise StateError("Introduced terms require last_new_term_at")
    if not terms and (
        progress[exposure_field] is not None or progress["last_new_term_at"] is not None
    ):
        raise StateError("Empty term history requires null enforcement timestamps")
    if terms:
        last_exposure = _parse_timestamp(
            progress[exposure_field], config["timezone"], field=exposure_field
        )
        last_new = _parse_timestamp(
            progress["last_new_term_at"], config["timezone"], field="last_new_term_at"
        )
        latest_used = max(last_used_times, key=lambda value: value.timestamp())
        latest_introduced = max(introduced_times, key=lambda value: value.timestamp())
        if last_exposure.timestamp() != latest_used.timestamp():
            raise StateError(f"{exposure_field} must equal the latest term last_used_at")
        if last_new.timestamp() != latest_introduced.timestamp():
            raise StateError("last_new_term_at must equal the latest term introduced_at")
    return state


def _validate_state(
    state: Any, curriculum: list[dict[str, str]], *, version: int = SCHEMA_VERSION
) -> dict[str, Any]:
    """Validate a schema v3 to v6 document.

    v4 adds the required `config.known_per_reply` budget; v5 lets that budget be
    null (no cap), adds `progress.offers`, and allows a decision to reserve the
    whole vocabulary by scope instead of listing every id; v6 drops the budget
    and the offers, since the agent now looks words up instead of sampling.
    v3 to v5 are accepted only on the migration path. Unknown versions fail
    closed.
    """
    if version not in (3, 4, 5, SCHEMA_VERSION):
        raise StateError(f"No validator exists for state schema_version {version}")
    if not isinstance(state, dict):
        raise StateError("State root must be an object")
    if type(state.get("schema_version")) is not int or state.get("schema_version") != version:
        raise StateError(
            f"Unsupported state schema_version {state.get('schema_version')}; expected {version}"
        )
    config = state.get("config")
    progress = state.get("progress")
    if not isinstance(config, dict) or not isinstance(progress, dict):
        raise StateError("State requires config and progress objects")
    config_keys = [
        "timezone",
        "dialect",
        "cadence_days",
        "batch_size",
        "baseline_known_count",
        "paused",
        "start_date",
    ]
    if 4 <= version <= 5:
        config_keys.append("known_per_reply")
    for key in config_keys:
        if key not in config:
            raise StateError(f"State config is missing {key}")
    if not isinstance(config["timezone"], str):
        raise StateError("config.timezone must be a string")
    if not isinstance(config["dialect"], str) or not config["dialect"].strip():
        raise StateError("config.dialect must be a non-empty string")
    if not isinstance(config["start_date"], str):
        raise StateError("config.start_date must be a string")
    _timezone(config["timezone"])
    _parse_date(config["start_date"], "start_date")
    if type(config["cadence_days"]) is not int or config["cadence_days"] < 1:
        raise StateError("config.cadence_days must be an integer of at least 1")
    if type(config["batch_size"]) is not int or config["batch_size"] < 1:
        raise StateError("config.batch_size must be an integer of at least 1")
    if 4 <= version <= 5:
        budget = config["known_per_reply"]
        nullable = version == 5
        if budget is None and not nullable:
            raise StateError("config.known_per_reply must be an integer of at least 1")
        if budget is not None and (type(budget) is not int or budget < 1):
            raise StateError(
                "config.known_per_reply must be an integer of at least 1"
                + (" or null for no cap" if nullable else "")
            )
    baseline = config["baseline_known_count"]
    if type(baseline) is not int or baseline < 0:
        raise StateError("config.baseline_known_count must be a non-negative integer")
    if baseline > len(curriculum):
        raise StateError("config.baseline_known_count exceeds the curriculum size")
    if not isinstance(config["paused"], bool):
        raise StateError("config.paused must be a boolean")
    for field in ("created_at", "updated_at"):
        if not isinstance(state.get(field), str):
            raise StateError(f"State requires string {field}")
        _parse_timestamp(state[field], config["timezone"], field=field)

    progress_keys = ["last_exposure_at", "pending_decisions", "terms"]
    if version == 5:
        progress_keys.append("offers")
    for key in progress_keys:
        if key not in progress:
            raise StateError(f"State progress is missing {key}")
    exposure_value = progress["last_exposure_at"]
    if exposure_value is not None and not isinstance(exposure_value, str):
        raise StateError("progress.last_exposure_at must be a string or null")
    if exposure_value is not None:
        _parse_timestamp(exposure_value, config["timezone"], field="last_exposure_at")

    terms = progress["terms"]
    if not isinstance(terms, dict):
        raise StateError("progress.terms must be an object")
    known_ids = {term["id"] for term in curriculum}
    unknown = sorted(set(terms) - known_ids)
    if unknown:
        raise StateError(f"State contains unknown curriculum ids: {', '.join(unknown)}")
    last_used_times: list[datetime] = []
    for term_id, term_state in terms.items():
        if not isinstance(term_state, dict):
            raise StateError(f"State term {term_id} must be an object")
        for field in ("introduced_at", "last_used_at", "use_count"):
            if field not in term_state:
                raise StateError(f"State term {term_id} is missing {field}")
        if not isinstance(term_state["introduced_at"], str) or not isinstance(
            term_state["last_used_at"], str
        ):
            raise StateError(f"State term {term_id} timestamps must be strings")
        introduced_at = _parse_timestamp(
            term_state["introduced_at"], config["timezone"], field="introduced_at"
        )
        last_used_at = _parse_timestamp(
            term_state["last_used_at"], config["timezone"], field="last_used_at"
        )
        if last_used_at.timestamp() < introduced_at.timestamp():
            raise StateError(f"State term {term_id} was used before introduction")
        if type(term_state["use_count"]) is not int or term_state["use_count"] < 1:
            raise StateError(f"State term {term_id} use_count must be at least 1")
        last_used_times.append(last_used_at)

    if version == 5:
        offers = progress["offers"]
        if not isinstance(offers, dict):
            raise StateError("progress.offers must be an object")
        unknown_offers = sorted(set(offers) - known_ids)
        if unknown_offers:
            raise StateError(
                f"State offers contain unknown curriculum ids: {', '.join(unknown_offers)}"
            )
        for term_id, offer_state in offers.items():
            if not isinstance(offer_state, dict):
                raise StateError(f"State offer {term_id} must be an object")
            if set(offer_state) != {"count", "last_offered_at"}:
                raise StateError(f"State offer {term_id} has invalid fields")
            if type(offer_state["count"]) is not int or offer_state["count"] < 1:
                raise StateError(f"State offer {term_id} count must be at least 1")
            if not isinstance(offer_state["last_offered_at"], str):
                raise StateError(f"State offer {term_id} last_offered_at must be a string")
            _parse_timestamp(
                offer_state["last_offered_at"], config["timezone"], field="last_offered_at"
            )

    pending = progress["pending_decisions"]
    if not isinstance(pending, dict):
        raise StateError("progress.pending_decisions must be an object")
    for decision_id, decision in pending.items():
        if not isinstance(decision_id, str) or not decision_id:
            raise StateError("Pending decision ids must be non-empty strings")
        if not isinstance(decision, dict):
            raise StateError(f"Pending decision {decision_id} must be an object")
        scoped = version >= 5 and set(decision) == {"scope", "created_at"}
        if not scoped and set(decision) != {"term_ids", "created_at"}:
            raise StateError(f"Pending decision {decision_id} has invalid fields")
        if scoped:
            if decision["scope"] != "known_all":
                raise StateError(f"Pending decision {decision_id} has an unknown scope")
        else:
            term_ids = decision["term_ids"]
            if not isinstance(term_ids, list) or not term_ids:
                raise StateError(
                    f"Pending decision {decision_id} requires a non-empty term_ids list"
                )
            if len(set(term_ids)) != len(term_ids):
                raise StateError(f"Pending decision {decision_id} has duplicate term_ids")
            for term_id in term_ids:
                if not isinstance(term_id, str) or term_id not in known_ids:
                    raise StateError(f"Pending decision {decision_id} has unknown term {term_id}")
        if not isinstance(decision["created_at"], str):
            raise StateError(f"Pending decision {decision_id} requires created_at")
        _parse_timestamp(decision["created_at"], config["timezone"], field="created_at")

    # Usage history is observational under v3: tiers come from the calendar, so
    # terms need not form a curriculum prefix. The exposure timestamp is still
    # pinned to the newest use so a rolled-back clock is detectable.
    if terms and exposure_value is None:
        raise StateError("Recorded terms require last_exposure_at")
    if not terms and exposure_value is not None:
        raise StateError("Empty term history requires a null last_exposure_at")
    if terms:
        last_exposure = _parse_timestamp(
            exposure_value, config["timezone"], field="last_exposure_at"
        )
        latest_used = max(last_used_times, key=lambda value: value.timestamp())
        if last_exposure.timestamp() != latest_used.timestamp():
            raise StateError("last_exposure_at must equal the latest term last_used_at")
    return state


def _migrate_v1_state(
    state: dict[str, Any], curriculum: list[dict[str, str]]
) -> dict[str, Any]:
    _validate_legacy_state(state, curriculum, version=1)
    migrated = json.loads(json.dumps(state))
    migrated["schema_version"] = 2
    migrated["config"]["exposure_percent"] = DEFAULT_EXPOSURE_PERCENT
    migrated["progress"]["last_exposure_at"] = migrated["progress"].pop(
        "last_any_insertion_at"
    )
    migrated["progress"]["pending_decisions"] = {}
    return _validate_legacy_state(migrated, curriculum, version=2)


def _migrate_v2_state(
    state: dict[str, Any], curriculum: list[dict[str, str]], now: datetime
) -> dict[str, Any]:
    """Convert one-item-per-reply pacing into batch pacing.

    Every term already introduced under v2 becomes part of the baseline known
    set, and the batch calendar restarts today so the first v3 batch is the
    next `batch_size` unseen curriculum items.
    """
    _validate_legacy_state(state, curriculum, version=2)
    migrated = json.loads(json.dumps(state))
    migrated["schema_version"] = 3
    config = migrated["config"]
    config.pop("exposure_percent", None)
    config["batch_size"] = DEFAULT_BATCH_SIZE
    config["baseline_known_count"] = len(migrated["progress"]["terms"])
    config["start_date"] = now.astimezone(_timezone(config["timezone"])).date().isoformat()
    progress = migrated["progress"]
    progress.pop("last_new_term_at", None)
    progress["pending_decisions"] = {}
    return _validate_state(migrated, curriculum, version=3)


def _migrate_v3_state(
    state: dict[str, Any], curriculum: list[dict[str, str]]
) -> dict[str, Any]:
    """Add the per-reply substitution budget.

    v3 offered every known term to every reply, so a large known set produced
    near-total translation instead of ambient substitution. Pending v3 decisions
    are dropped because each reserved an unbounded active set.
    """
    _validate_state(state, curriculum, version=3)
    migrated = json.loads(json.dumps(state))
    migrated["schema_version"] = 4
    migrated["config"]["known_per_reply"] = LEGACY_V4_KNOWN_PER_REPLY
    migrated["progress"]["pending_decisions"] = {}
    return _validate_state(migrated, curriculum, version=4)


def _migrate_v4_state(
    state: dict[str, Any], curriculum: list[dict[str, str]]
) -> dict[str, Any]:
    """Add offer history and allow an uncapped per-reply budget.

    Use counts alone cannot tell a word the reply had no room for from a word
    that was never on the table, so `offers` seeds from `use_count`: a term was
    offered at least as often as it was used.
    """
    _validate_state(state, curriculum, version=4)
    migrated = json.loads(json.dumps(state))
    migrated["schema_version"] = 5
    migrated["progress"]["offers"] = {
        term_id: {
            "count": int(term_state["use_count"]),
            "last_offered_at": term_state["last_used_at"],
        }
        for term_id, term_state in migrated["progress"]["terms"].items()
    }
    return _validate_state(migrated, curriculum, version=5)


def _migrate_v5_state(
    state: dict[str, Any], curriculum: list[dict[str, str]]
) -> dict[str, Any]:
    """Drop the per-reply density cap and offer history.

    The agent now looks words up in the whole vocabulary with `ambient-lookup`
    instead of receiving a sample, so `known_per_reply` and `offers` have no
    reader. Pending decisions are cleared: each was issued under the old scope
    rules and a reply in flight simply records nothing.
    """
    _validate_state(state, curriculum, version=5)
    migrated = json.loads(json.dumps(state))
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["config"].pop("known_per_reply", None)
    migrated["progress"].pop("offers", None)
    migrated["progress"]["pending_decisions"] = {}
    return _validate_state(migrated, curriculum)


def _preserve_migration_source(
    state_path: Path, source: dict[str, Any], *, version: int
) -> bool:
    backup_path = state_path.with_name(f"{state_path.name}.schema-v{version}.backup")
    if backup_path.exists():
        if _read_json(backup_path) != source:
            raise StateError(
                f"Migration backup already exists with different content: {backup_path}"
            )
        return True
    return _atomic_write(backup_path, source)


def _load_or_create_state(
    state_path: Path,
    curriculum: list[dict[str, str]],
    *,
    now_value: str | None,
) -> tuple[dict[str, Any], datetime, str]:
    if state_path.exists():
        provisional = _read_json(state_path)
        if not isinstance(provisional, dict):
            raise StateError("State root must be an object")
        provisional_config = provisional.get("config")
        timezone_name = (
            provisional_config.get("timezone", DEFAULT_TIMEZONE)
            if isinstance(provisional_config, dict)
            else DEFAULT_TIMEZONE
        )
        if not isinstance(timezone_name, str):
            timezone_name = DEFAULT_TIMEZONE
        now = _parse_now(now_value, timezone_name)
        version = provisional.get("schema_version")
        if version in LEGACY_SCHEMA_VERSIONS:
            source_version = version
            staged = provisional
            if source_version == 1:
                staged = _migrate_v1_state(staged, curriculum)
            if source_version <= 2:
                staged = _migrate_v2_state(staged, curriculum, now)
            if source_version <= 3:
                staged = _migrate_v3_state(staged, curriculum)
            if source_version <= 4:
                staged = _migrate_v4_state(staged, curriculum)
            migrated = _migrate_v5_state(staged, curriculum)
            migrated["updated_at"] = now.isoformat()
            _validate_state(migrated, curriculum)
            backup_durable = _preserve_migration_source(
                state_path, provisional, version=source_version
            )
            state_durable = _atomic_write(state_path, migrated)
            durability = "confirmed" if backup_durable and state_durable else "uncertain"
            return migrated, now, durability
        return _validate_state(provisional, curriculum), now, "not-written"

    now = _parse_now(now_value, DEFAULT_TIMEZONE)
    state = _new_state(
        now,
        timezone_name=DEFAULT_TIMEZONE,
        dialect=DEFAULT_DIALECT,
        cadence_days=DEFAULT_CADENCE_DAYS,
        batch_size=DEFAULT_BATCH_SIZE,
    )
    durable = _atomic_write(state_path, state)
    return state, now, "confirmed" if durable else "uncertain"


def _batch_index(state: dict[str, Any], today: date) -> int:
    config = state["config"]
    start = _parse_date(config["start_date"], "start_date")
    if today < start:
        return -1
    return (today - start).days // config["cadence_days"]


def _vocabulary_size(state: dict[str, Any], size: int, batch_index: int) -> int:
    """How many leading curriculum items are in the vocabulary at `batch_index`."""
    config = state["config"]
    if batch_index < 0:
        return 0
    return min(size, config["baseline_known_count"] + (batch_index + 1) * config["batch_size"])


def _tiers(
    state: dict[str, Any], curriculum: list[dict[str, str]], today: date
) -> tuple[list[dict[str, str]], int]:
    """Return (vocabulary, batch_index) for a local date.

    The vocabulary is one list: the words the learner already knows plus every
    weekly addition so far. It is derived purely from elapsed calendar time, so
    a batch joins on its date whether or not anything else happened.
    """
    batch_index = _batch_index(state, today)
    return curriculum[: _vocabulary_size(state, len(curriculum), batch_index)], batch_index


def _manifest_text(known: list[dict[str, str]]) -> str:
    """Grouped `id | spanish | english` listing of the whole vocabulary."""
    lines = [
        f"# ambient-spanish known vocabulary — {len(known)} terms",
        "# Substitute these wherever your own prose expresses the",
        "# meaning. Verbs and nouns first. The sentence's grammar stays English.",
    ]
    by_kind: dict[str, list[dict[str, str]]] = {}
    for term in known:
        by_kind.setdefault(term["kind"], []).append(term)
    ordered_kinds = [kind for kind in MANIFEST_KIND_ORDER if kind in by_kind]
    ordered_kinds += sorted(set(by_kind) - set(MANIFEST_KIND_ORDER))
    for kind in ordered_kinds:
        terms = by_kind[kind]
        lines.append("")
        lines.append(f"## {kind} ({len(terms)})")
        lines.extend(
            f"{term['id']} | {term['spanish']} | {term['english']}" for term in terms
        )
    return "\n".join(lines) + "\n"


def _write_manifest(state_path: Path, known: list[dict[str, str]]) -> dict[str, Any]:
    """Refresh the vocabulary manifest only when its content changed.

    A stable file is worth more than a fresh one here: rewriting it every reply
    would invalidate the reader's cache of a list that changes every third day.
    """
    path = state_path.parent / MANIFEST_FILENAME
    text = _manifest_text(known)
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=6).hexdigest()
    current = None
    if path.exists():
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            current = None
    refreshed = False
    if current != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        temporary.replace(path)
        refreshed = True
    return {
        "path": str(path),
        "digest": digest,
        "count": len(known),
        "refreshed": refreshed,
    }


def _next_batch_date(
    state: dict[str, Any], curriculum: list[dict[str, str]], today: date
) -> date | None:
    config = state["config"]
    start = _parse_date(config["start_date"], "start_date")
    batch_index = _batch_index(state, today)
    if batch_index < 0:
        return start
    next_index = batch_index + 1
    if _vocabulary_size(state, len(curriculum), batch_index) >= len(curriculum):
        return None
    return start + timedelta(days=next_index * config["cadence_days"])


def _reserve_decision(state: dict[str, Any], now: datetime) -> str:
    """Reserve one reply's exposure: any term of the vocabulary of the day issued."""
    pending = state["progress"]["pending_decisions"]
    timezone_name = state["config"]["timezone"]
    ages: list[tuple[float, str]] = []
    for decision_id, decision in pending.items():
        created_at = _parse_timestamp(
            decision["created_at"], timezone_name, field="created_at"
        )
        age = now.timestamp() - created_at.timestamp()
        if age > MAX_DECISION_AGE_SECONDS:
            ages.append((float("inf"), decision_id))
        else:
            ages.append((age, decision_id))
    for age, decision_id in ages:
        if age == float("inf"):
            del pending[decision_id]

    # Replies that legitimately used no Spanish leave their reservation behind.
    # Evicting the oldest keeps `context` working instead of failing closed on a
    # queue of never-consumed tokens.
    while len(pending) >= MAX_PENDING_DECISIONS:
        oldest = max(
            (
                (
                    _parse_timestamp(
                        decision["created_at"], timezone_name, field="created_at"
                    ).timestamp(),
                    decision_id,
                )
                for decision_id, decision in pending.items()
            ),
            key=lambda row: (-row[0], row[1]),
        )
        del pending[oldest[1]]

    decision_id = f"d_{secrets.token_urlsafe(18)}"
    while decision_id in pending:
        decision_id = f"d_{secrets.token_urlsafe(18)}"
    pending[decision_id] = {"scope": "known_all", "created_at": now.isoformat()}
    return decision_id


def _permitted_terms(
    state: dict[str, Any], curriculum: list[dict[str, str]], decision: dict[str, Any]
) -> set[str]:
    """Ids a decision permits: the vocabulary of the day it was issued.

    A decision issued before schema v6 may still carry its own `term_ids` list;
    that list is honoured as written.
    """
    if "term_ids" in decision:
        return set(decision["term_ids"])
    timezone_name = state["config"]["timezone"]
    created_at = _parse_timestamp(
        decision["created_at"], timezone_name, field="created_at"
    )
    issued_on = created_at.astimezone(_timezone(timezone_name)).date()
    vocabulary, _ = _tiers(state, curriculum, issued_on)
    return {term["id"] for term in vocabulary}


def _combined_durability(initial: str, written: bool) -> str:
    if initial == "uncertain" or not written:
        return "uncertain"
    return "confirmed"


def _term_view(term: dict[str, str], term_state: dict[str, Any] | None) -> dict[str, Any]:
    return {**term, "use_count": int(term_state["use_count"]) if term_state else 0}


def _context(
    state: dict[str, Any],
    curriculum: list[dict[str, str]],
    now: datetime,
    state_path: Path,
) -> dict[str, Any]:
    config = state["config"]
    progress = state["progress"]
    timezone_name = config["timezone"]
    today = now.date()
    known, batch_index = _tiers(state, curriculum, today)
    last_exposure_timestamp = progress.get("last_exposure_at")

    active = True
    reason = "active"
    if config["paused"]:
        active, reason = False, "paused"
    elif batch_index < 0:
        active, reason = False, "before_start_date"
    elif any(
        now.timestamp()
        < _parse_timestamp(
            decision["created_at"], timezone_name, field="created_at"
        ).timestamp()
        for decision in progress["pending_decisions"].values()
    ):
        active, reason = False, "clock_before_pending_decision"
    elif last_exposure_timestamp is not None and now.timestamp() < _parse_timestamp(
        last_exposure_timestamp, timezone_name, field="last_exposure_at"
    ).timestamp():
        active, reason = False, "clock_before_last_exposure"
    elif not known:
        active, reason = False, "curriculum_exhausted"
    elif len(known) >= len(curriculum):
        # Substitution continues; only new words have run out.
        reason = "curriculum_complete"

    next_batch = _next_batch_date(state, curriculum, today)

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": now.isoformat(),
        "local_date": today.isoformat(),
        "timezone": timezone_name,
        "dialect": config["dialect"],
        "words_per_week": config["batch_size"],
        "cadence_days": config["cadence_days"],
        "paused": config["paused"],
        "state_path": str(state_path),
        "batch_index": batch_index,
        "vocabulary_count": len(known),
        "next_batch_date": next_batch.isoformat() if next_batch is not None else None,
        "active": active,
        "reason": reason,
    }


def _lookup_binary() -> Path | None:
    """The `ambient-lookup` binary: AMBIENT_LOOKUP_BIN, then the repo's release build."""
    raw = os.environ.get(LOOKUP_BIN_ENV)
    path = Path(raw).expanduser().resolve() if raw else DEFAULT_LOOKUP_BIN
    return path if path.is_file() else None


def _split_ids(raw: str) -> list[str]:
    ids = [chunk.strip() for chunk in raw.split(",")]
    ids = [chunk for chunk in ids if chunk]
    if not ids:
        raise StateError("--used requires at least one curriculum id or Spanish word")
    return list(dict.fromkeys(ids))


def _cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(args.state)
    curriculum = _load_curriculum(_curriculum_path(args.curriculum, state_path))
    now = _parse_now(args.now, args.timezone)
    start = _parse_date(args.start_date, "start_date") if args.start_date else now.date()
    with _locked(state_path):
        if state_path.exists() and not args.force:
            raise StateError(
                f"State already exists: {state_path}; use --force only for an explicit reset"
            )
        state = _new_state(
            now,
            timezone_name=args.timezone,
            dialect=args.dialect,
            cadence_days=DEFAULT_CADENCE_DAYS,
            batch_size=_words_per_week(args.words_per_week),
            baseline_known_count=args.baseline_known,
            start_date=start,
        )
        _validate_state(state, curriculum)
        durable = _atomic_write(state_path, state)
    return {
        "ok": True,
        "state_path": str(state_path),
        "write_durability": "confirmed" if durable else "uncertain",
        "state": state,
    }


def _cmd_context(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(args.state)
    curriculum = _load_curriculum(_curriculum_path(args.curriculum, state_path))
    with _locked(state_path):
        state, now, write_durability = _load_or_create_state(
            state_path, curriculum, now_value=args.now
        )
        result = _context(state, curriculum, now, state_path)
        result["decision_id"] = None
        result["lookup"] = None
        if result["active"]:
            binary = _lookup_binary()
            if binary is None:
                raise StateError(
                    "ambient-lookup binary not found: run python3 scripts/install.py"
                )
            known, _ = _tiers(state, curriculum, now.date())
            # The agent matches its draft against this file through the lookup
            # binary, and the hover mod reads it too, so it holds the whole
            # vocabulary and is rewritten only when that changes.
            manifest = _write_manifest(state_path, known)
            result["lookup"] = {"command": str(binary), "vocabulary": manifest["path"]}
            result["decision_id"] = _reserve_decision(state, now)
            state["updated_at"] = now.isoformat()
            _validate_state(state, curriculum)
            durable = _atomic_write(state_path, state)
            write_durability = _combined_durability(write_durability, durable)
    result["write_durability"] = write_durability
    return result


def _cmd_record(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(args.state)
    curriculum = _load_curriculum(_curriculum_path(args.curriculum, state_path))
    by_id = {term["id"]: term for term in curriculum}
    # `--used` takes ids or the Spanish words themselves, as `ambient-lookup` prints them.
    by_spanish: dict[str, list[str]] = {}
    for term in curriculum:
        by_spanish.setdefault(_accentless(term["spanish"]).lower(), []).append(term["id"])
    used: list[str] = []
    unknown: list[str] = []
    for word in _split_ids(args.used):
        if word in by_id:
            matched = [word]
        else:
            matched = by_spanish.get(_accentless(word).lower(), [])
        if not matched:
            unknown.append(word)
        used.extend(term_id for term_id in matched if term_id not in used)
    if unknown:
        raise StateError(f"Unknown curriculum terms: {', '.join(sorted(unknown))}")

    with _locked(state_path):
        state, now, _ = _load_or_create_state(state_path, curriculum, now_value=args.now)
        progress = state["progress"]
        decision = progress["pending_decisions"].get(args.decision)
        if decision is None:
            raise StateError("Unknown, expired, or already-used exposure decision")
        permitted = _permitted_terms(state, curriculum, decision)
        outside = sorted(term_id for term_id in used if term_id not in permitted)
        if outside:
            raise StateError(
                f"Terms were not part of the reserved active set: {', '.join(outside)}"
            )
        created_at = _parse_timestamp(
            decision["created_at"], state["config"]["timezone"], field="created_at"
        )
        age_seconds = now.timestamp() - created_at.timestamp()
        if age_seconds < 0:
            raise StateError("Clock is before the reserved exposure decision")
        if age_seconds > MAX_DECISION_AGE_SECONDS:
            raise StateError("Exposure decision has expired")
        last_exposure = progress["last_exposure_at"]
        if last_exposure is not None and now.timestamp() < _parse_timestamp(
            last_exposure, state["config"]["timezone"], field="last_exposure_at"
        ).timestamp():
            raise StateError("Clock is before the last recorded exposure")

        terms = progress["terms"]
        first_uses: list[str] = []
        for term_id in used:
            if term_id in terms:
                terms[term_id]["last_used_at"] = now.isoformat()
                terms[term_id]["use_count"] = int(terms[term_id].get("use_count", 0)) + 1
            else:
                terms[term_id] = {
                    "introduced_at": now.isoformat(),
                    "last_used_at": now.isoformat(),
                    "use_count": 1,
                }
                first_uses.append(term_id)

        progress["last_exposure_at"] = now.isoformat()
        del progress["pending_decisions"][args.decision]
        state["updated_at"] = now.isoformat()
        _validate_state(state, curriculum)
        durable = _atomic_write(state_path, state)
    return {
        "ok": True,
        "write_durability": "confirmed" if durable else "uncertain",
        "recorded": {"used": used, "first_uses": first_uses, "at": now.isoformat()},
        "state_path": str(state_path),
    }


def _cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(args.state)
    curriculum_path, curriculum_source = _curriculum_source(args.curriculum, state_path)
    curriculum = _load_curriculum(curriculum_path)
    by_id = {term["id"]: term for term in curriculum}
    with _locked(state_path):
        state, now, write_durability = _load_or_create_state(
            state_path, curriculum, now_value=args.now
        )
    context = _context(state, curriculum, now, state_path)
    context["write_durability"] = write_durability
    context["baseline_known_count"] = state["config"]["baseline_known_count"]
    context["start_date"] = state["config"]["start_date"]
    meta_path = state_path.parent / USER_CURRICULUM_META_FILENAME
    build = None
    if curriculum_source == "user" and meta_path.exists():
        try:
            build = _read_json(meta_path)
        except StateError:
            build = None
    context["curriculum"] = {
        "path": str(curriculum_path),
        "source": curriculum_source,
        "size": len(curriculum),
        "build": build,
    }
    terms = state["progress"]["terms"]
    used_terms = []
    for term_id, term_state in terms.items():
        used_terms.append(
            {
                **by_id[term_id],
                "introduced_at": term_state["introduced_at"],
                "last_used_at": term_state["last_used_at"],
                "use_count": term_state["use_count"],
            }
        )
    context["used_terms"] = sorted(used_terms, key=lambda term: -term["use_count"])
    binary = _lookup_binary()
    context["lookup_binary"] = str(binary) if binary is not None else None
    return context


def _cmd_configure(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(args.state)
    curriculum = _load_curriculum(_curriculum_path(args.curriculum, state_path))
    with _locked(state_path):
        state, now, _ = _load_or_create_state(state_path, curriculum, now_value=args.now)
        config = state["config"]
        changes: dict[str, Any] = {}
        if args.words_per_week is not None:
            config["batch_size"] = _words_per_week(args.words_per_week)
            config["cadence_days"] = DEFAULT_CADENCE_DAYS
            changes["words_per_week"] = args.words_per_week
        if args.baseline_known is not None:
            if args.baseline_known < 0:
                raise StateError("baseline_known_count must not be negative")
            if args.baseline_known > len(curriculum):
                raise StateError("baseline_known_count exceeds the curriculum size")
            config["baseline_known_count"] = args.baseline_known
            changes["baseline_known_count"] = args.baseline_known
        if args.dialect is not None:
            config["dialect"] = args.dialect
            changes["dialect"] = args.dialect
        if args.timezone is not None:
            _timezone(args.timezone)
            config["timezone"] = args.timezone
            changes["timezone"] = args.timezone
            now = _parse_now(args.now, args.timezone)
        if args.pause:
            config["paused"] = True
            changes["paused"] = True
        if args.resume:
            config["paused"] = False
            changes["paused"] = False
        if not changes:
            raise StateError("No configuration change requested")
        state["updated_at"] = now.isoformat()
        _validate_state(state, curriculum)
        durable = _atomic_write(state_path, state)
    return {
        "ok": True,
        "write_durability": "confirmed" if durable else "uncertain",
        "changes": changes,
        "state_path": str(state_path),
    }


def _cmd_levels(args: argparse.Namespace) -> dict[str, Any]:
    lexicon_path = _lexicon_path(args.lexicon)
    packs = _load_lexicon(lexicon_path)
    rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()
    levels = []
    cumulative = 0
    for level in LEVELS:
        pack = packs[level]
        cumulative += len(pack)
        row: dict[str, Any] = {
            "level": level,
            "description": LEVEL_DESCRIPTIONS[level],
            "new_terms": len(pack),
            "known_if_chosen": cumulative,
        }
        if args.sample:
            row["sample"] = [
                {key: term[key] for key in ("id", "spanish", "english", "kind")}
                for term in rng.sample(pack, min(args.sample, len(pack)))
            ]
        levels.append(row)
    return {"ok": True, "lexicon": str(lexicon_path), "levels": levels}


def _merge_history(target: dict[str, Any], source: dict[str, Any], timezone_name: str) -> None:
    """Fold one term's usage history into another's: the same word under a new id."""
    target["use_count"] = int(target["use_count"]) + int(source["use_count"])
    for field, pick in (("introduced_at", min), ("last_used_at", max)):
        target[field] = pick(
            (target[field], source[field]),
            key=lambda value: _parse_timestamp(value, timezone_name, field=field).timestamp(),
        )


def _cmd_vocab(args: argparse.Namespace) -> dict[str, Any]:
    if args.curriculum or os.environ.get("AMBIENT_SPANISH_CURRICULUM"):
        raise StateError(
            "vocab writes the personal curriculum beside the state; "
            "drop --curriculum and unset AMBIENT_SPANISH_CURRICULUM"
        )
    if args.level is None and not args.keep_known and not args.add_known:
        raise StateError("Choose a starting point: --level, --keep-known, or --add-known")
    level = None if args.level in (None, "NONE") else args.level
    state_path = _state_path(args.state)
    packs = _load_lexicon(_lexicon_path(args.lexicon))
    lists = {
        name: [entry for path in paths for entry in _read_word_list(Path(path))]
        for name, paths in (
            ("add_known", args.add_known),
            ("remove_known", args.remove_known),
            ("learn_first", args.learn_first),
        )
    }
    current_path, _ = _curriculum_source(None, state_path)
    personal_path = state_path.parent / USER_CURRICULUM_FILENAME
    meta_path = state_path.parent / USER_CURRICULUM_META_FILENAME

    with _locked(state_path):
        current = _load_curriculum(current_path)
        keep_known: list[dict[str, str]] = []
        keep_queue: list[dict[str, str]] = []
        if state_path.exists():
            state, now, _ = _load_or_create_state(state_path, current, now_value=args.now)
            config = state["config"]
            batch_index = max(_batch_index(state, now.date()), 0)
            unlocked = _vocabulary_size(state, len(current), batch_index)
            if args.keep_known:
                keep_known = current[:unlocked]
                keep_queue = current[unlocked:]
        else:
            now = _parse_now(args.now, DEFAULT_TIMEZONE)
            state = _new_state(
                now,
                timezone_name=DEFAULT_TIMEZONE,
                dialect=DEFAULT_DIALECT,
                cadence_days=DEFAULT_CADENCE_DAYS,
                batch_size=DEFAULT_BATCH_SIZE,
            )
            config = state["config"]

        composed = _compose_vocabulary(
            packs,
            level=level,
            current=current,
            keep_known=keep_known,
            keep_queue=keep_queue,
            **lists,
        )
        known: list[dict[str, str]] = composed["known"]
        queue: list[dict[str, str]] = composed["queue"]

        # Usage history must keep pointing at curriculum ids. A word that moved
        # to a new id takes its history along; a used word the new vocabulary
        # dropped stays, as known, rather than losing what was recorded.
        terms = state["progress"]["terms"]
        timezone_name = config["timezone"]
        current_by_id = {term["id"]: term for term in current}
        final_ids = {term["id"] for term in known + queue}
        final_by_key = {_word_key(term["spanish"]): term for term in known + queue}
        merged: list[dict[str, str]] = []
        kept: list[str] = []
        for term_id in list(terms):
            if term_id in final_ids:
                continue
            old = current_by_id[term_id]
            match = final_by_key.get(_word_key(old["spanish"]))
            if match is None:
                known.append(old)
                final_ids.add(term_id)
                kept.append(term_id)
                continue
            history = terms.pop(term_id)
            if match["id"] in terms:
                _merge_history(terms[match["id"]], history, timezone_name)
            else:
                terms[match["id"]] = history
            merged.append({"from": term_id, "to": match["id"]})

        curriculum = [
            {key: term[key] for key in ("id", "spanish", "english", "kind", "usage")}
            for term in known + queue
        ]
        if not curriculum:
            raise StateError("The resulting vocabulary is empty")
        seen_ids: set[str] = set()
        for term in curriculum:
            base, suffix = term["id"], 2
            while term["id"] in seen_ids:
                term["id"], suffix = f"{base}-{suffix}", suffix + 1
            seen_ids.add(term["id"])
        # Decisions were issued against the old curriculum; a reply in flight
        # simply records nothing.
        state["progress"]["pending_decisions"] = {}
        if args.words_per_week is not None:
            config["batch_size"] = _words_per_week(args.words_per_week)
            config["cadence_days"] = DEFAULT_CADENCE_DAYS
        config["baseline_known_count"] = len(known)
        config["start_date"] = now.date().isoformat()
        state["updated_at"] = now.isoformat()
        _validate_state(state, curriculum)

        batch = config["batch_size"]
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": args.dry_run,
            "level": level or "none",
            "keep_known": args.keep_known,
            "known_count": len(known),
            "queue_count": len(queue),
            "words_per_week": batch,
            "new_this_week": [
                {"spanish": term["spanish"], "english": term["english"]}
                for term in curriculum[len(known) : len(known) + batch]
            ],
            "next_up": [term["spanish"] for term in curriculum[len(known) + batch :][:12]],
            "added_known": len(composed["added"]),
            "removed_known": len(composed["removed"]),
            "learn_first": len(composed["learn_first"]),
            "not_found": composed["not_found"],
            "history": {"moved": merged, "kept_as_known": kept},
            "start_date": config["start_date"],
            "curriculum_path": str(personal_path),
            "state_path": str(state_path),
        }
        if args.dry_run:
            return result

        for path in (personal_path, state_path):
            if path.exists():
                shutil.copy2(path, path.with_name(path.name + ".previous"))
        durable = _atomic_write(personal_path, curriculum)
        durable = _atomic_write(
            meta_path,
            {
                "built_at": now.isoformat(),
                "level": level or "none",
                "keep_known": args.keep_known,
                "known_count": len(known),
                "queue_count": len(queue),
                "added_known": len(composed["added"]),
                "removed_known": len(composed["removed"]),
                "learn_first": len(composed["learn_first"]),
            },
        ) and durable
        durable = _atomic_write(state_path, state) and durable
        result["known_manifest"] = _write_manifest(state_path, curriculum[: len(known)])
    result["write_durability"] = "confirmed" if durable else "uncertain"
    return result


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", help="Override the persistent state path")
    parser.add_argument("--curriculum", help="Override the curriculum JSON path")
    parser.add_argument("--now", help="Use an ISO datetime (primarily for deterministic tests)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Persistent calendar batch pacing for ambient Spanish substitution."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize state")
    _add_common(init)
    init.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    init.add_argument("--dialect", default=DEFAULT_DIALECT)
    init.add_argument(
        "--words-per-week", type=int, default=DEFAULT_BATCH_SIZE, help="New words added each week"
    )
    init.add_argument(
        "--baseline-known",
        type=int,
        default=0,
        help="Leading curriculum items to treat as already known at start",
    )
    init.add_argument("--start-date")
    init.add_argument("--force", action="store_true")
    init.set_defaults(handler=_cmd_init)

    context = subparsers.add_parser(
        "context", help="Get this reply's vocabulary"
    )
    _add_common(context)
    context.set_defaults(handler=_cmd_context)

    record = subparsers.add_parser("record", help="Record the terms actually used")
    _add_common(record)
    record.add_argument(
        "--used", required=True, help="Comma-separated curriculum ids actually used"
    )
    record.add_argument("--decision", required=True)
    record.set_defaults(handler=_cmd_record)

    status = subparsers.add_parser("status", help="Show progress and today's context")
    _add_common(status)
    status.set_defaults(handler=_cmd_status)

    configure = subparsers.add_parser("configure", help="Change non-destructive settings")
    _add_common(configure)
    configure.add_argument("--words-per-week", type=int, help="New words added each week")
    configure.add_argument(
        "--baseline-known",
        type=int,
        help="Leading curriculum items treated as already known, never taught",
    )
    configure.add_argument("--dialect")
    configure.add_argument("--timezone")
    pause_group = configure.add_mutually_exclusive_group()
    pause_group.add_argument("--pause", action="store_true")
    pause_group.add_argument("--resume", action="store_true")
    configure.set_defaults(handler=_cmd_configure)

    levels = subparsers.add_parser(
        "levels", help="List the level packs, optionally with a sample of each"
    )
    levels.add_argument("--lexicon", help="Override the graded lexicon TSV path")
    levels.add_argument(
        "--sample", type=int, default=0, help="Random terms to show per level"
    )
    levels.add_argument("--seed", type=int, help="Make --sample reproducible")
    levels.set_defaults(handler=_cmd_levels)

    vocab = subparsers.add_parser(
        "vocab",
        help="Rebuild the personal curriculum from a level pack and word lists",
    )
    _add_common(vocab)
    vocab.add_argument("--lexicon", help="Override the graded lexicon TSV path")
    vocab.add_argument(
        "--level",
        type=str.upper,
        choices=[*LEVELS, "NONE"],
        help="Treat every lexicon word up to this level as already known",
    )
    vocab.add_argument(
        "--keep-known",
        action="store_true",
        help="Start from the words known today and keep the current queue order",
    )
    for flag, text in (
        ("--add-known", "Word list to mark as known"),
        ("--remove-known", "Word list to take out of known and learn soon"),
        ("--learn-first", "Word list to learn before anything else, in order"),
    ):
        vocab.add_argument(flag, action="append", default=[], metavar="FILE", help=text)
    vocab.add_argument("--words-per-week", type=int, help="New words added each week")
    vocab.add_argument(
        "--dry-run", action="store_true", help="Report the result without writing"
    )
    vocab.set_defaults(handler=_cmd_vocab)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = args.handler(args)
    except VocabularyError as exc:
        print(
            _json_dump({"ok": False, "error": str(exc), **exc.details}), file=sys.stderr
        )
        return 2
    except StateError as exc:
        print(_json_dump({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(_json_dump(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
