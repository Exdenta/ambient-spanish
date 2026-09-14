#!/usr/bin/env python3
"""Calendar-governed batch curriculum and per-reply substitution set for ambient Spanish."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    import fcntl
except ImportError:  # pragma: no cover - Unix is the supported Codex runtime.
    fcntl = None


SCHEMA_VERSION = 3
LEGACY_SCHEMA_VERSIONS = (1, 2)
DEFAULT_TIMEZONE = "Europe/Madrid"
DEFAULT_DIALECT = "es-ES"
DEFAULT_CADENCE_DAYS = 3
DEFAULT_BATCH_SIZE = 3
DEFAULT_EXPOSURE_PERCENT = 50  # Legacy v2 field, retained only for migration.
MAX_PENDING_DECISIONS = 128
MAX_DECISION_AGE_SECONDS = 24 * 60 * 60
DEFAULT_STATE_PATH = Path.home() / ".codex" / "state" / "ambient-spanish" / "state.json"
DEFAULT_CURRICULUM_PATH = Path(__file__).resolve().parent.parent / "references" / "curriculum.json"


class StateError(RuntimeError):
    """Raised when state or a requested transition is invalid."""


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _state_path(value: str | None) -> Path:
    raw = value or os.environ.get("AMBIENT_SPANISH_STATE")
    return Path(raw).expanduser().resolve() if raw else DEFAULT_STATE_PATH


def _curriculum_path(value: str | None) -> Path:
    raw = value or os.environ.get("AMBIENT_SPANISH_CURRICULUM")
    return Path(raw).expanduser().resolve() if raw else DEFAULT_CURRICULUM_PATH


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


def _atomic_write(path: Path, value: dict[str, Any]) -> bool:
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


def _validate_state(state: Any, curriculum: list[dict[str, str]]) -> dict[str, Any]:
    """Validate a schema v3 document. Unknown or legacy versions fail closed here."""
    if not isinstance(state, dict):
        raise StateError("State root must be an object")
    if type(state.get("schema_version")) is not int or state.get("schema_version") != SCHEMA_VERSION:
        raise StateError(
            f"Unsupported state schema_version {state.get('schema_version')}; expected {SCHEMA_VERSION}"
        )
    config = state.get("config")
    progress = state.get("progress")
    if not isinstance(config, dict) or not isinstance(progress, dict):
        raise StateError("State requires config and progress objects")
    for key in (
        "timezone",
        "dialect",
        "cadence_days",
        "batch_size",
        "baseline_known_count",
        "paused",
        "start_date",
    ):
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

    for key in ("last_exposure_at", "pending_decisions", "terms"):
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

    pending = progress["pending_decisions"]
    if not isinstance(pending, dict):
        raise StateError("progress.pending_decisions must be an object")
    for decision_id, decision in pending.items():
        if not isinstance(decision_id, str) or not decision_id:
            raise StateError("Pending decision ids must be non-empty strings")
        if not isinstance(decision, dict):
            raise StateError(f"Pending decision {decision_id} must be an object")
        if set(decision) != {"term_ids", "created_at"}:
            raise StateError(f"Pending decision {decision_id} has invalid fields")
        term_ids = decision["term_ids"]
        if not isinstance(term_ids, list) or not term_ids:
            raise StateError(f"Pending decision {decision_id} requires a non-empty term_ids list")
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
    migrated["schema_version"] = SCHEMA_VERSION
    config = migrated["config"]
    config.pop("exposure_percent", None)
    config["batch_size"] = DEFAULT_BATCH_SIZE
    config["baseline_known_count"] = len(migrated["progress"]["terms"])
    config["start_date"] = now.astimezone(_timezone(config["timezone"])).date().isoformat()
    progress = migrated["progress"]
    progress.pop("last_new_term_at", None)
    progress["pending_decisions"] = {}
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
            migrated = _migrate_v2_state(staged, curriculum, now)
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


def _tiers(
    state: dict[str, Any], curriculum: list[dict[str, str]], today: date
) -> tuple[list[dict[str, str]], list[dict[str, str]], int]:
    """Split the curriculum into (known, learning, batch_index) for a local date.

    `known` is used bare and unglossed. `learning` is the current batch and is
    the only tier that carries a bracketed English gloss. Both are derived
    purely from elapsed calendar time, so a batch is promoted on its date
    whether or not its words were ever actually used.
    """
    config = state["config"]
    batch_index = _batch_index(state, today)
    if batch_index < 0:
        return [], [], batch_index
    size = len(curriculum)
    learning_start = min(
        size, config["baseline_known_count"] + batch_index * config["batch_size"]
    )
    learning_end = min(size, learning_start + config["batch_size"])
    return curriculum[:learning_start], curriculum[learning_start:learning_end], batch_index


def _next_batch_date(
    state: dict[str, Any], curriculum: list[dict[str, str]], today: date
) -> date | None:
    config = state["config"]
    start = _parse_date(config["start_date"], "start_date")
    batch_index = _batch_index(state, today)
    if batch_index < 0:
        return start
    next_index = batch_index + 1
    next_learning_start = (
        config["baseline_known_count"] + next_index * config["batch_size"]
    )
    if next_learning_start >= len(curriculum):
        return None
    return start + timedelta(days=next_index * config["cadence_days"])


def _reserve_decision(
    state: dict[str, Any], term_ids: list[str], now: datetime
) -> str:
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
    pending[decision_id] = {
        "term_ids": list(term_ids),
        "created_at": now.isoformat(),
    }
    return decision_id


def _combined_durability(initial: str, written: bool) -> str:
    if initial == "uncertain" or not written:
        return "uncertain"
    return "confirmed"


def _term_view(
    term: dict[str, str], tier: str, term_state: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        **term,
        "tier": tier,
        "gloss": "bracketed" if tier == "learning" else "omit",
        "use_count": int(term_state["use_count"]) if term_state else 0,
    }


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
    terms = progress["terms"]
    known, learning, batch_index = _tiers(state, curriculum, today)
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
    elif not known and not learning:
        active, reason = False, "curriculum_exhausted"
    elif not learning:
        # Every term is known. Substitution continues unglossed; only new
        # vocabulary has run out.
        reason = "curriculum_complete"

    known_view = [_term_view(term, "known", terms.get(term["id"])) for term in known]
    learning_view = [
        _term_view(term, "learning", terms.get(term["id"])) for term in learning
    ]
    next_batch = _next_batch_date(state, curriculum, today)

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": now.isoformat(),
        "local_date": today.isoformat(),
        "timezone": timezone_name,
        "dialect": config["dialect"],
        "cadence_days": config["cadence_days"],
        "batch_size": config["batch_size"],
        "paused": config["paused"],
        "state_path": str(state_path),
        "batch_index": batch_index,
        "known_count": len(known_view),
        "learning_count": len(learning_view),
        "known": known_view if active else [],
        "learning": learning_view if active else [],
        "next_batch_date": next_batch.isoformat() if next_batch is not None else None,
        "active": active,
        "reason": reason,
    }


def _split_ids(raw: str) -> list[str]:
    ids = [chunk.strip() for chunk in raw.split(",")]
    ids = [chunk for chunk in ids if chunk]
    if not ids:
        raise StateError("--used requires at least one curriculum id")
    if len(set(ids)) != len(ids):
        raise StateError("--used contains duplicate curriculum ids")
    return ids


def _cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(args.state)
    curriculum = _load_curriculum(_curriculum_path(args.curriculum))
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
            cadence_days=args.cadence_days,
            batch_size=args.batch_size,
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
    curriculum = _load_curriculum(_curriculum_path(args.curriculum))
    with _locked(state_path):
        state, now, write_durability = _load_or_create_state(
            state_path, curriculum, now_value=args.now
        )
        result = _context(state, curriculum, now, state_path)
        result["decision_id"] = None
        if result["active"]:
            term_ids = [term["id"] for term in result["known"] + result["learning"]]
            decision_id = _reserve_decision(state, term_ids, now)
            result["decision_id"] = decision_id
            state["updated_at"] = now.isoformat()
            _validate_state(state, curriculum)
            durable = _atomic_write(state_path, state)
            write_durability = _combined_durability(write_durability, durable)
    result["write_durability"] = write_durability
    return result


def _cmd_record(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(args.state)
    curriculum = _load_curriculum(_curriculum_path(args.curriculum))
    by_id = {term["id"]: term for term in curriculum}
    used = _split_ids(args.used)
    unknown = sorted(term_id for term_id in used if term_id not in by_id)
    if unknown:
        raise StateError(f"Unknown curriculum terms: {', '.join(unknown)}")

    with _locked(state_path):
        state, now, _ = _load_or_create_state(state_path, curriculum, now_value=args.now)
        progress = state["progress"]
        decision = progress["pending_decisions"].get(args.decision)
        if decision is None:
            raise StateError("Unknown, expired, or already-used exposure decision")
        permitted = set(decision["term_ids"])
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
    curriculum = _load_curriculum(_curriculum_path(args.curriculum))
    by_id = {term["id"]: term for term in curriculum}
    with _locked(state_path):
        state, now, write_durability = _load_or_create_state(
            state_path, curriculum, now_value=args.now
        )
    context = _context(state, curriculum, now, state_path)
    context["write_durability"] = write_durability
    context["baseline_known_count"] = state["config"]["baseline_known_count"]
    context["start_date"] = state["config"]["start_date"]
    used_terms = []
    for term_id, term_state in state["progress"]["terms"].items():
        used_terms.append(
            {
                **by_id[term_id],
                "introduced_at": term_state["introduced_at"],
                "last_used_at": term_state["last_used_at"],
                "use_count": term_state["use_count"],
            }
        )
    context["used_terms"] = sorted(used_terms, key=lambda term: -term["use_count"])
    return context


def _cmd_configure(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(args.state)
    curriculum = _load_curriculum(_curriculum_path(args.curriculum))
    with _locked(state_path):
        state, now, _ = _load_or_create_state(state_path, curriculum, now_value=args.now)
        config = state["config"]
        changes: dict[str, Any] = {}
        if args.cadence_days is not None:
            if args.cadence_days < 1:
                raise StateError("cadence_days must be at least 1")
            config["cadence_days"] = args.cadence_days
            changes["cadence_days"] = args.cadence_days
        if args.batch_size is not None:
            if args.batch_size < 1:
                raise StateError("batch_size must be at least 1")
            config["batch_size"] = args.batch_size
            changes["batch_size"] = args.batch_size
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
    init.add_argument("--cadence-days", type=int, default=DEFAULT_CADENCE_DAYS)
    init.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
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
        "context", help="Get this reply's known and learning term sets"
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
    configure.add_argument("--cadence-days", type=int)
    configure.add_argument("--batch-size", type=int)
    configure.add_argument("--dialect")
    configure.add_argument("--timezone")
    pause_group = configure.add_mutually_exclusive_group()
    pause_group.add_argument("--pause", action="store_true")
    pause_group.add_argument("--resume", action="store_true")
    configure.set_defaults(handler=_cmd_configure)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = args.handler(args)
    except StateError as exc:
        print(_json_dump({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(_json_dump(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
