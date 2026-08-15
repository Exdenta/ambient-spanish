#!/usr/bin/env python3
"""Calendar-governed state engine for the ambient-spanish skill."""

from __future__ import annotations

import argparse
import json
import os
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


SCHEMA_VERSION = 1
DEFAULT_TIMEZONE = "Europe/Madrid"
DEFAULT_DIALECT = "es-ES"
DEFAULT_CADENCE_DAYS = 7
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
    start_date: date | None = None,
) -> dict[str, Any]:
    if cadence_days < 1:
        raise StateError("cadence_days must be at least 1")
    return {
        "schema_version": SCHEMA_VERSION,
        "config": {
            "timezone": timezone_name,
            "dialect": dialect,
            "cadence_days": cadence_days,
            "paused": False,
            "start_date": (start_date or now.date()).isoformat(),
        },
        "progress": {
            "last_any_insertion_at": None,
            "last_new_term_at": None,
            "terms": {},
        },
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def _validate_state(state: Any, curriculum: list[dict[str, str]]) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise StateError("State root must be an object")
    if type(state.get("schema_version")) is not int or state.get(
        "schema_version"
    ) != SCHEMA_VERSION:
        raise StateError(
            f"Unsupported state schema_version {state.get('schema_version')}; expected {SCHEMA_VERSION}"
        )
    config = state.get("config")
    progress = state.get("progress")
    if not isinstance(config, dict) or not isinstance(progress, dict):
        raise StateError("State requires config and progress objects")
    for key in ("timezone", "dialect", "cadence_days", "paused", "start_date"):
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
    if not isinstance(config["paused"], bool):
        raise StateError("config.paused must be a boolean")
    for field in ("created_at", "updated_at"):
        if not isinstance(state.get(field), str):
            raise StateError(f"State requires string {field}")
        _parse_timestamp(state[field], config["timezone"], field=field)

    for key in ("last_any_insertion_at", "last_new_term_at", "terms"):
        if key not in progress:
            raise StateError(f"State progress is missing {key}")
    for field in ("last_any_insertion_at", "last_new_term_at"):
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
    if terms and progress["last_any_insertion_at"] is None:
        raise StateError("Introduced terms require last_any_insertion_at")
    if terms and progress["last_new_term_at"] is None:
        raise StateError("Introduced terms require last_new_term_at")
    if not terms and (
        progress["last_any_insertion_at"] is not None
        or progress["last_new_term_at"] is not None
    ):
        raise StateError("Empty term history requires null enforcement timestamps")
    if terms:
        last_any = _parse_timestamp(
            progress["last_any_insertion_at"],
            config["timezone"],
            field="last_any_insertion_at",
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
        if last_any.timestamp() != latest_used.timestamp():
            raise StateError(
                "last_any_insertion_at must equal the latest term last_used_at"
            )
        if last_new.timestamp() != latest_introduced.timestamp():
            raise StateError(
                "last_new_term_at must equal the latest term introduced_at"
            )
    return state


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
        return _validate_state(provisional, curriculum), now, "not-written"

    now = _parse_now(now_value, DEFAULT_TIMEZONE)
    state = _new_state(
        now,
        timezone_name=DEFAULT_TIMEZONE,
        dialect=DEFAULT_DIALECT,
        cadence_days=DEFAULT_CADENCE_DAYS,
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
    eligible_count = _eligible_count(state, len(curriculum), today)
    introduced = progress["terms"]
    last_any_timestamp = progress.get("last_any_insertion_at")
    last_any_date = _as_local_date(progress.get("last_any_insertion_at"), timezone_name)

    focus: dict[str, Any] | None = None
    reason = "no_item_due"

    if config["paused"]:
        reason = "paused"
    elif last_any_timestamp is not None and now.timestamp() < _parse_timestamp(
        last_any_timestamp, timezone_name, field="last_any_insertion_at"
    ).timestamp():
        reason = "clock_before_last_insertion"
    elif last_any_date == today:
        reason = "daily_limit_reached"
    else:
        due: list[tuple[int, int, dict[str, str], dict[str, Any]]] = []
        curriculum_index = {term["id"]: index for index, term in enumerate(curriculum)}
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

        last_new_date = _as_local_date(progress.get("last_new_term_at"), timezone_name)
        intro_allowed = last_new_date is None or (
            today - last_new_date
        ).days >= config["cadence_days"]
        next_new = next(
            (term for term in curriculum[:eligible_count] if term["id"] not in introduced),
            None,
        )

        if due:
            _, _, term, term_state = max(due, key=lambda row: (row[0], row[1]))
            introduced_on = _as_local_date(term_state["introduced_at"], timezone_name)
            focus = {
                "action": "review",
                "term": term,
                "gloss": _gloss_mode("review", introduced_on, today),
                "guidance": "Use once, naturally, without turning the reply into a lesson.",
            }
            reason = "review_due"
        elif next_new is not None and intro_allowed:
            focus = {
                "action": "introduce",
                "term": next_new,
                "gloss": "required",
                "guidance": "Use once and include a brief English gloss at the natural first mention.",
            }
            reason = "new_item_ready"
        elif next_new is not None:
            reason = "introduction_cadence_not_elapsed"

    start = _parse_date(config["start_date"], "start_date")
    next_index = min(eligible_count, len(curriculum) - 1)
    next_unlock = None
    if eligible_count < len(curriculum):
        next_unlock = (start + timedelta(days=next_index * config["cadence_days"])).isoformat()

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": now.isoformat(),
        "local_date": today.isoformat(),
        "timezone": timezone_name,
        "dialect": config["dialect"],
        "cadence_days": config["cadence_days"],
        "paused": config["paused"],
        "state_path": str(state_path),
        "eligible_count": eligible_count,
        "introduced_count": len(introduced),
        "next_unlock_date": next_unlock,
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
        current = _context(state, curriculum, now, state_path)
        focus = current["focus"]
        if focus is None:
            raise StateError(f"No item may be recorded now: {current['reason']}")
        if focus["term"]["id"] != args.term or focus["action"] != args.kind:
            raise StateError(
                f"Requested record does not match current focus: {focus['action']} {focus['term']['id']}"
            )

        progress = state["progress"]
        terms = progress["terms"]
        if args.kind == "introduce":
            if args.term in terms:
                raise StateError(f"Term is already introduced: {args.term}")
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

        progress["last_any_insertion_at"] = now.isoformat()
        state["updated_at"] = now.isoformat()
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
        description="Persistent, calendar-governed state for ambient Spanish learning."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize state")
    _add_common(init)
    init.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    init.add_argument("--dialect", default=DEFAULT_DIALECT)
    init.add_argument("--cadence-days", type=int, default=DEFAULT_CADENCE_DAYS)
    init.add_argument("--start-date")
    init.add_argument("--force", action="store_true")
    init.set_defaults(handler=_cmd_init)

    context = subparsers.add_parser("context", help="Get today's permitted ambient item")
    _add_common(context)
    context.set_defaults(handler=_cmd_context)

    record = subparsers.add_parser("record", help="Record an item actually used")
    _add_common(record)
    record.add_argument("--term", required=True)
    record.add_argument("--kind", choices=("introduce", "review"), required=True)
    record.set_defaults(handler=_cmd_record)

    status = subparsers.add_parser("status", help="Show progress and today's context")
    _add_common(status)
    status.set_defaults(handler=_cmd_status)

    configure = subparsers.add_parser("configure", help="Change non-destructive settings")
    _add_common(configure)
    configure.add_argument("--cadence-days", type=int)
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
