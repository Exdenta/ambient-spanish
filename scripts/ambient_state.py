#!/usr/bin/env python3
"""Calendar-governed curriculum and per-reply exposure for ambient Spanish."""

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


SCHEMA_VERSION = 2
DEFAULT_TIMEZONE = "Europe/Madrid"
DEFAULT_DIALECT = "es-ES"
DEFAULT_CADENCE_DAYS = 7
DEFAULT_EXPOSURE_PERCENT = 50
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
    exposure_percent: int,
    start_date: date | None = None,
) -> dict[str, Any]:
    if cadence_days < 1:
        raise StateError("cadence_days must be at least 1")
    if not 0 <= exposure_percent <= 100:
        raise StateError("exposure_percent must be between 0 and 100")
    return {
        "schema_version": SCHEMA_VERSION,
        "config": {
            "timezone": timezone_name,
            "dialect": dialect,
            "cadence_days": cadence_days,
            "exposure_percent": exposure_percent,
            "paused": False,
            "start_date": (start_date or now.date()).isoformat(),
        },
        "progress": {
            "last_exposure_at": None,
            "last_new_term_at": None,
            "pending_decisions": {},
            "terms": {},
        },
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def _validate_state_version(
    state: Any,
    curriculum: list[dict[str, str]],
    *,
    version: int,
) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise StateError("State root must be an object")
    if type(state.get("schema_version")) is not int or state.get(
        "schema_version"
    ) != version:
        raise StateError(
            f"Unsupported state schema_version {state.get('schema_version')}; expected {version}"
        )
    if version not in (1, SCHEMA_VERSION):
        raise StateError(f"No validator exists for state schema_version {version}")
    config = state.get("config")
    progress = state.get("progress")
    if not isinstance(config, dict) or not isinstance(progress, dict):
        raise StateError("State requires config and progress objects")
    config_keys = ["timezone", "dialect", "cadence_days", "paused", "start_date"]
    if version == SCHEMA_VERSION:
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
    if version == SCHEMA_VERSION:
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

    exposure_field = (
        "last_any_insertion_at" if version == 1 else "last_exposure_at"
    )
    progress_keys = [exposure_field, "last_new_term_at", "terms"]
    if version == SCHEMA_VERSION:
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
    introduced_prefix = {
        term["id"] for term in curriculum[: len(introduced_ids)]
    }
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

    if version == SCHEMA_VERSION:
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
            _parse_timestamp(
                decision["created_at"], config["timezone"], field="created_at"
            )
    if terms and progress[exposure_field] is None:
        raise StateError(f"Introduced terms require {exposure_field}")
    if terms and progress["last_new_term_at"] is None:
        raise StateError("Introduced terms require last_new_term_at")
    if not terms and (
        progress[exposure_field] is not None
        or progress["last_new_term_at"] is not None
    ):
        raise StateError("Empty term history requires null enforcement timestamps")
    if terms:
        last_exposure = _parse_timestamp(
            progress[exposure_field],
            config["timezone"],
            field=exposure_field,
        )
        last_new = _parse_timestamp(
            progress["last_new_term_at"],
            config["timezone"],
            field="last_new_term_at",
        )
        latest_used = max(last_used_times, key=lambda value: value.timestamp())
        latest_introduced = max(
            introduced_times, key=lambda value: value.timestamp()
        )
        if last_exposure.timestamp() != latest_used.timestamp():
            raise StateError(
                f"{exposure_field} must equal the latest term last_used_at"
            )
        if last_new.timestamp() != latest_introduced.timestamp():
            raise StateError(
                "last_new_term_at must equal the latest term introduced_at"
            )
    return state


def _validate_state(state: Any, curriculum: list[dict[str, str]]) -> dict[str, Any]:
    return _validate_state_version(state, curriculum, version=SCHEMA_VERSION)


def _migrate_v1_state(
    state: dict[str, Any], curriculum: list[dict[str, str]]
) -> dict[str, Any]:
    _validate_state_version(state, curriculum, version=1)
    migrated = json.loads(json.dumps(state))
    migrated["schema_version"] = SCHEMA_VERSION
    migrated["config"]["exposure_percent"] = DEFAULT_EXPOSURE_PERCENT
    migrated["progress"]["last_exposure_at"] = migrated["progress"].pop(
        "last_any_insertion_at"
    )
    migrated["progress"]["pending_decisions"] = {}
    return _validate_state(migrated, curriculum)


def _preserve_migration_source(state_path: Path, source: dict[str, Any]) -> bool:
    backup_path = state_path.with_name(f"{state_path.name}.schema-v1.backup")
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
        if provisional.get("schema_version") == 1:
            migrated = _migrate_v1_state(provisional, curriculum)
            migrated["updated_at"] = now.isoformat()
            _validate_state(migrated, curriculum)
            backup_durable = _preserve_migration_source(state_path, provisional)
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
        exposure_percent=DEFAULT_EXPOSURE_PERCENT,
    )
    durable = _atomic_write(state_path, state)
    return state, now, "confirmed" if durable else "uncertain"


def _as_local_date(timestamp: str | None, timezone_name: str) -> date | None:
    if timestamp is None:
        return None
    return _parse_timestamp(timestamp, timezone_name, field="timestamp").date()


def _eligible_count(state: dict[str, Any], curriculum_size: int, today: date) -> int:
    config = state["config"]
    start = _parse_date(config["start_date"], "start_date")
    if today < start:
        return 0
    elapsed = (today - start).days
    return min(curriculum_size, 1 + elapsed // config["cadence_days"])


def _review_gap_days(age_days: int) -> int:
    if age_days < 1:
        return 1
    if age_days < 3:
        return 2
    if age_days < 7:
        return 4
    if age_days < 14:
        return 7
    if age_days < 30:
        return 14
    if age_days < 60:
        return 30
    return 60


def _gloss_mode(action: str, introduced_on: date | None, today: date) -> str:
    if action == "introduce" or introduced_on is None:
        return "required"
    age_days = (today - introduced_on).days
    if age_days <= 21:
        return "required"
    if age_days <= 60:
        return "optional"
    return "omit"


def _exposure_roll() -> int:
    override = os.environ.get("AMBIENT_SPANISH_EXPOSURE_ROLL")
    if override is None:
        return secrets.randbelow(100)
    if os.environ.get("AMBIENT_SPANISH_ALLOW_EXPOSURE_OVERRIDE") != "1":
        raise StateError(
            "AMBIENT_SPANISH_EXPOSURE_ROLL is disabled outside deterministic tests"
        )
    try:
        roll = int(override)
    except ValueError as exc:
        raise StateError("Exposure roll override must be an integer from 0 to 99") from exc
    if str(roll) != override or not 0 <= roll <= 99:
        raise StateError("Exposure roll override must be an integer from 0 to 99")
    return roll


def _candidate(
    state: dict[str, Any],
    curriculum: list[dict[str, str]],
    now: datetime,
) -> tuple[dict[str, Any] | None, str]:
    config = state["config"]
    progress = state["progress"]
    timezone_name = config["timezone"]
    today = now.date()
    introduced = progress["terms"]
    eligible_count = _eligible_count(state, len(curriculum), today)
    curriculum_index = {term["id"]: index for index, term in enumerate(curriculum)}

    last_new_date = _as_local_date(progress.get("last_new_term_at"), timezone_name)
    intro_allowed = last_new_date is None or (
        today - last_new_date
    ).days >= config["cadence_days"]
    next_new = next(
        (term for term in curriculum[:eligible_count] if term["id"] not in introduced),
        None,
    )
    if next_new is not None and intro_allowed:
        return (
            {
                "action": "introduce",
                "term": next_new,
                "gloss": "required",
                "guidance": "Use once and include a brief English gloss at the natural first mention.",
            },
            "new_item_ready",
        )

    due: list[tuple[int, int, dict[str, str], dict[str, Any]]] = []
    for term_id, term_state in introduced.items():
        introduced_on = _as_local_date(term_state.get("introduced_at"), timezone_name)
        last_used_on = _as_local_date(term_state.get("last_used_at"), timezone_name)
        if introduced_on is None or last_used_on is None:
            raise StateError(f"Introduced term {term_id} lacks timestamps")
        age_at_last_use = max(0, (last_used_on - introduced_on).days)
        gap = _review_gap_days(age_at_last_use)
        overdue = (today - last_used_on).days - gap
        if overdue >= 0:
            term = curriculum[curriculum_index[term_id]]
            due.append((overdue, -curriculum_index[term_id], term, term_state))

    if due:
        _, _, term, term_state = max(due, key=lambda row: (row[0], row[1]))
        introduced_on = _as_local_date(term_state["introduced_at"], timezone_name)
        return (
            {
                "action": "review",
                "term": term,
                "gloss": _gloss_mode("review", introduced_on, today),
                "guidance": "Use once, naturally, without turning the reply into a lesson.",
            },
            "review_due",
        )

    if introduced:
        term_id, term_state = min(
            introduced.items(),
            key=lambda row: (
                row[1]["use_count"],
                _parse_timestamp(
                    row[1]["last_used_at"], timezone_name, field="last_used_at"
                ).timestamp(),
                curriculum_index[row[0]],
            ),
        )
        introduced_on = _as_local_date(term_state["introduced_at"], timezone_name)
        return (
            {
                "action": "review",
                "term": curriculum[curriculum_index[term_id]],
                "gloss": _gloss_mode("review", introduced_on, today),
                "guidance": "Reuse once, naturally, without turning the reply into a lesson.",
            },
            "ambient_reuse_ready",
        )

    if next_new is not None:
        return None, "introduction_cadence_not_elapsed"
    return None, "no_item_due"


def _next_introduction_date(
    state: dict[str, Any], curriculum: list[dict[str, str]]
) -> date | None:
    config = state["config"]
    progress = state["progress"]
    introduced = progress["terms"]
    next_index = next(
        (index for index, term in enumerate(curriculum) if term["id"] not in introduced),
        None,
    )
    if next_index is None:
        return None
    start = _parse_date(config["start_date"], "start_date")
    curriculum_date = start + timedelta(days=next_index * config["cadence_days"])
    last_new_date = _as_local_date(
        progress.get("last_new_term_at"), config["timezone"]
    )
    if last_new_date is None:
        return curriculum_date
    cadence_date = last_new_date + timedelta(days=config["cadence_days"])
    return max(curriculum_date, cadence_date)


def _reserve_decision(
    state: dict[str, Any], focus: dict[str, Any], now: datetime
) -> str:
    pending = state["progress"]["pending_decisions"]
    timezone_name = state["config"]["timezone"]
    expired = []
    for decision_id, decision in pending.items():
        created_at = _parse_timestamp(
            decision["created_at"], timezone_name, field="created_at"
        )
        if now.timestamp() - created_at.timestamp() > MAX_DECISION_AGE_SECONDS:
            expired.append(decision_id)
    for decision_id in expired:
        del pending[decision_id]

    if len(pending) >= MAX_PENDING_DECISIONS:
        raise StateError(
            "Too many unconsumed exposure decisions; finish or wait for existing decisions to expire"
        )

    decision_id = f"d_{secrets.token_urlsafe(18)}"
    while decision_id in pending:
        decision_id = f"d_{secrets.token_urlsafe(18)}"
    pending[decision_id] = {
        "action": focus["action"],
        "term_id": focus["term"]["id"],
        "created_at": now.isoformat(),
    }
    return decision_id


def _combined_durability(initial: str, written: bool) -> str:
    if initial == "uncertain" or not written:
        return "uncertain"
    return "confirmed"


def _context(
    state: dict[str, Any],
    curriculum: list[dict[str, str]],
    now: datetime,
    state_path: Path,
    *,
    apply_exposure_gate: bool = True,
) -> dict[str, Any]:
    config = state["config"]
    progress = state["progress"]
    timezone_name = config["timezone"]
    today = now.date()
    eligible_count = _eligible_count(state, len(curriculum), today)
    introduced = progress["terms"]
    last_exposure_timestamp = progress.get("last_exposure_at")

    focus: dict[str, Any] | None = None
    reason = "no_item_due"
    exposure_selected = False

    if config["paused"]:
        reason = "paused"
    elif any(
        now.timestamp()
        < _parse_timestamp(
            decision["created_at"], timezone_name, field="created_at"
        ).timestamp()
        for decision in progress["pending_decisions"].values()
    ):
        reason = "clock_before_pending_decision"
    elif last_exposure_timestamp is not None and now.timestamp() < _parse_timestamp(
        last_exposure_timestamp, timezone_name, field="last_exposure_at"
    ).timestamp():
        reason = "clock_before_last_exposure"
    else:
        candidate, candidate_reason = _candidate(state, curriculum, now)
        if candidate is not None and apply_exposure_gate:
            percent = config["exposure_percent"]
            exposure_selected = percent == 100 or (
                percent > 0 and _exposure_roll() < percent
            )
            if exposure_selected:
                focus = candidate
                reason = candidate_reason
            else:
                reason = "exposure_skipped"
        else:
            focus = candidate
            reason = candidate_reason
            exposure_selected = candidate is not None

    start = _parse_date(config["start_date"], "start_date")
    next_index = min(eligible_count, len(curriculum) - 1)
    next_unlock = None
    if eligible_count < len(curriculum):
        next_unlock = (start + timedelta(days=next_index * config["cadence_days"])).isoformat()
    next_introduction = _next_introduction_date(state, curriculum)

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": now.isoformat(),
        "local_date": today.isoformat(),
        "timezone": timezone_name,
        "dialect": config["dialect"],
        "cadence_days": config["cadence_days"],
        "exposure_percent": config["exposure_percent"],
        "exposure_selected": exposure_selected,
        "paused": config["paused"],
        "state_path": str(state_path),
        "eligible_count": eligible_count,
        "introduced_count": len(introduced),
        "next_unlock_date": next_unlock,
        "next_introduction_date": (
            next_introduction.isoformat() if next_introduction is not None else None
        ),
        "focus": focus,
        "reason": reason,
    }


def _cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(args.state)
    curriculum = _load_curriculum(_curriculum_path(args.curriculum))
    now = _parse_now(args.now, args.timezone)
    start = _parse_date(args.start_date, "start_date") if args.start_date else now.date()
    with _locked(state_path):
        if state_path.exists() and not args.force:
            raise StateError(f"State already exists: {state_path}; use --force only for an explicit reset")
        state = _new_state(
            now,
            timezone_name=args.timezone,
            dialect=args.dialect,
            cadence_days=args.cadence_days,
            exposure_percent=args.exposure_percent,
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
        if result["focus"] is not None:
            decision_id = _reserve_decision(state, result["focus"], now)
            result["focus"] = {**result["focus"], "decision_id": decision_id}
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
    if args.term not in by_id:
        raise StateError(f"Unknown curriculum term: {args.term}")

    with _locked(state_path):
        state, now, _ = _load_or_create_state(
            state_path, curriculum, now_value=args.now
        )
        progress = state["progress"]
        decision = progress["pending_decisions"].get(args.decision)
        if decision is None:
            raise StateError("Unknown, expired, or already-used exposure decision")
        if decision["term_id"] != args.term or decision["action"] != args.kind:
            raise StateError(
                f"Requested record does not match reserved focus: {decision['action']} {decision['term_id']}"
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
        if args.kind == "introduce":
            if args.term in terms:
                raise StateError(f"Term is already introduced: {args.term}")
            current_candidate, _ = _candidate(state, curriculum, now)
            if (
                current_candidate is None
                or current_candidate["action"] != "introduce"
                or current_candidate["term"]["id"] != args.term
            ):
                raise StateError(
                    "Reserved introduction is no longer eligible under the current calendar pacing"
                )
            terms[args.term] = {
                "introduced_at": now.isoformat(),
                "last_used_at": now.isoformat(),
                "use_count": 1,
            }
            progress["last_new_term_at"] = now.isoformat()
        else:
            if args.term not in terms:
                raise StateError(f"Cannot review an unintroduced term: {args.term}")
            terms[args.term]["last_used_at"] = now.isoformat()
            terms[args.term]["use_count"] = int(terms[args.term].get("use_count", 0)) + 1

        progress["last_exposure_at"] = now.isoformat()
        del progress["pending_decisions"][args.decision]
        state["updated_at"] = now.isoformat()
        _validate_state(state, curriculum)
        durable = _atomic_write(state_path, state)
    return {
        "ok": True,
        "write_durability": "confirmed" if durable else "uncertain",
        "recorded": {"term": args.term, "kind": args.kind, "at": now.isoformat()},
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
    learned = []
    for term_id, term_state in state["progress"]["terms"].items():
        learned.append(
            {
                **by_id[term_id],
                "introduced_at": term_state["introduced_at"],
                "last_used_at": term_state["last_used_at"],
                "use_count": term_state["use_count"],
            }
        )
    context["learned_terms"] = learned
    return context


def _cmd_configure(args: argparse.Namespace) -> dict[str, Any]:
    state_path = _state_path(args.state)
    curriculum = _load_curriculum(_curriculum_path(args.curriculum))
    with _locked(state_path):
        state, now, _ = _load_or_create_state(
            state_path, curriculum, now_value=args.now
        )
        config = state["config"]
        changes: dict[str, Any] = {}
        if args.cadence_days is not None:
            if args.cadence_days < 1:
                raise StateError("cadence_days must be at least 1")
            config["cadence_days"] = args.cadence_days
            changes["cadence_days"] = args.cadence_days
        if args.exposure_percent is not None:
            if not 0 <= args.exposure_percent <= 100:
                raise StateError("exposure_percent must be between 0 and 100")
            config["exposure_percent"] = args.exposure_percent
            changes["exposure_percent"] = args.exposure_percent
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
        description="Persistent calendar pacing and per-reply exposure for ambient Spanish."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize state")
    _add_common(init)
    init.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    init.add_argument("--dialect", default=DEFAULT_DIALECT)
    init.add_argument("--cadence-days", type=int, default=DEFAULT_CADENCE_DAYS)
    init.add_argument(
        "--exposure-percent", type=int, default=DEFAULT_EXPOSURE_PERCENT
    )
    init.add_argument("--start-date")
    init.add_argument("--force", action="store_true")
    init.set_defaults(handler=_cmd_init)

    context = subparsers.add_parser("context", help="Get this reply's permitted ambient item")
    _add_common(context)
    context.set_defaults(handler=_cmd_context)

    record = subparsers.add_parser("record", help="Record an item actually used")
    _add_common(record)
    record.add_argument("--term", required=True)
    record.add_argument("--kind", choices=("introduce", "review"), required=True)
    record.add_argument("--decision", required=True)
    record.set_defaults(handler=_cmd_record)

    status = subparsers.add_parser("status", help="Show progress and today's context")
    _add_common(status)
    status.set_defaults(handler=_cmd_status)

    configure = subparsers.add_parser("configure", help="Change non-destructive settings")
    _add_common(configure)
    configure.add_argument("--cadence-days", type=int)
    configure.add_argument("--exposure-percent", type=int)
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
