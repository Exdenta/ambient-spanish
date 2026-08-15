from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ambient_state.py"
CURRICULUM = ROOT / "references" / "curriculum.json"
MODULE_SPEC = importlib.util.spec_from_file_location("ambient_state", SCRIPT)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
AMBIENT_STATE = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(AMBIENT_STATE)


class AmbientStateCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state = Path(self.temp_dir.name) / "state.json"

    def run_cli(
        self, *args: str, ok: bool = True, exposure_roll: int = 0
    ) -> dict:
        command = [
            sys.executable,
            str(SCRIPT),
            *args,
            "--state",
            str(self.state),
            "--curriculum",
            str(CURRICULUM),
        ]
        environment = os.environ.copy()
        environment["AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE"] = "1"
        environment["AMBIENT_SPANISH_ALLOW_EXPOSURE_OVERRIDE"] = "1"
        environment["AMBIENT_SPANISH_EXPOSURE_ROLL"] = str(exposure_roll)
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        if ok and result.returncode != 0:
            self.fail(f"command failed: {command}\nstdout={result.stdout}\nstderr={result.stderr}")
        if not ok and result.returncode == 0:
            self.fail(f"command unexpectedly succeeded: {command}\nstdout={result.stdout}")
        payload = result.stdout if result.returncode == 0 else result.stderr
        return json.loads(payload)

    def init(self) -> dict:
        return self.run_cli(
            "init",
            "--now",
            "2026-08-15T09:00:00+02:00",
            "--start-date",
            "2026-08-15",
        )

    def record_focus(
        self,
        context: dict,
        *,
        now: str,
        term: str | None = None,
        kind: str | None = None,
        decision: str | None = None,
        ok: bool = True,
    ) -> dict:
        focus = context["focus"]
        if focus is None:
            self.fail(f"context has no focus: {context}")
        return self.run_cli(
            "record",
            "--term",
            term or focus["term"]["id"],
            "--kind",
            kind or focus["action"],
            "--decision",
            decision or focus["decision_id"],
            "--now",
            now,
            ok=ok,
        )

    def test_first_item_is_available_on_start_date(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual("new_item_ready", context["reason"])
        self.assertEqual("introduce", context["focus"]["action"])
        self.assertEqual("poco-a-poco", context["focus"]["term"]["id"])
        self.assertTrue(context["focus"]["decision_id"].startswith("d_"))
        self.assertEqual(1, context["eligible_count"])
        self.assertEqual(50, context["exposure_percent"])
        self.assertTrue(context["exposure_selected"])

    def test_future_start_date_has_no_eligible_item(self) -> None:
        self.run_cli(
            "init",
            "--now",
            "2026-08-15T09:00:00+02:00",
            "--start-date",
            "2026-08-22",
        )
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(0, context["eligible_count"])
        self.assertIsNone(context["focus"])
        self.assertEqual("no_item_due", context["reason"])
        self.assertEqual("2026-08-22", context["next_unlock_date"])

    def test_time_override_is_disabled_without_test_opt_in(self) -> None:
        command = [
            sys.executable,
            str(SCRIPT),
            "context",
            "--now",
            "2030-01-01T00:00:00+01:00",
            "--state",
            str(self.state),
            "--curriculum",
            str(CURRICULUM),
        ]
        environment = os.environ.copy()
        environment.pop("AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE", None)
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("real system clock", result.stderr)

    def test_fifty_percent_exposure_uses_exact_boundary(self) -> None:
        self.init()
        selected = self.run_cli(
            "context",
            "--now",
            "2026-08-15T10:00:00+02:00",
            exposure_roll=49,
        )
        self.assertTrue(selected["exposure_selected"])
        self.assertIsNotNone(selected["focus"])

        skipped = self.run_cli(
            "context",
            "--now",
            "2026-08-15T10:01:00+02:00",
            exposure_roll=50,
        )
        self.assertFalse(skipped["exposure_selected"])
        self.assertIsNone(skipped["focus"])
        self.assertEqual("exposure_skipped", skipped["reason"])

    def test_exposure_percent_is_configurable_from_zero_to_one_hundred(self) -> None:
        self.init()
        changed = self.run_cli(
            "configure",
            "--exposure-percent",
            "0",
            "--now",
            "2026-08-15T09:01:00+02:00",
        )
        self.assertEqual(0, changed["changes"]["exposure_percent"])
        disabled = self.run_cli(
            "context", "--now", "2026-08-15T10:00:00+02:00"
        )
        self.assertIsNone(disabled["focus"])
        self.assertEqual("exposure_skipped", disabled["reason"])
        rejected_record = self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "introduce",
            "--decision",
            "not-a-reserved-decision",
            "--now",
            "2026-08-15T10:00:30+02:00",
            ok=False,
        )
        self.assertIn("Unknown, expired, or already-used", rejected_record["error"])

        self.run_cli(
            "configure",
            "--exposure-percent",
            "100",
            "--now",
            "2026-08-15T10:01:00+02:00",
        )
        always = self.run_cli(
            "context",
            "--now",
            "2026-08-15T10:02:00+02:00",
            exposure_roll=99,
        )
        self.assertIsNotNone(always["focus"])
        self.assertTrue(always["exposure_selected"])

        rejected = self.run_cli(
            "configure",
            "--exposure-percent",
            "101",
            "--now",
            "2026-08-15T10:03:00+02:00",
            ok=False,
        )
        self.assertIn("between 0 and 100", rejected["error"])

    def test_exposure_roll_override_is_disabled_outside_tests(self) -> None:
        self.init()
        command = [
            sys.executable,
            str(SCRIPT),
            "context",
            "--now",
            "2026-08-15T10:00:00+02:00",
            "--state",
            str(self.state),
            "--curriculum",
            str(CURRICULUM),
        ]
        environment = os.environ.copy()
        environment["AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE"] = "1"
        environment["AMBIENT_SPANISH_EXPOSURE_ROLL"] = "0"
        environment.pop("AMBIENT_SPANISH_ALLOW_EXPOSURE_OVERRIDE", None)
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("disabled outside deterministic tests", result.stderr)

    def test_same_day_reuse_is_allowed_with_one_focus_per_reply(self) -> None:
        self.init()
        introduction = self.run_cli(
            "context", "--now", "2026-08-15T09:59:00+02:00"
        )
        recorded = self.record_focus(
            introduction, now="2026-08-15T10:00:00+02:00"
        )
        self.assertTrue(recorded["ok"])
        context = self.run_cli("context", "--now", "2026-08-15T10:01:00+02:00")
        self.assertEqual("ambient_reuse_ready", context["reason"])
        self.assertEqual("review", context["focus"]["action"])
        self.assertEqual("poco-a-poco", context["focus"]["term"]["id"])
        self.assertEqual(1, len([context["focus"]]))

        reused = self.record_focus(
            context, now="2026-08-15T10:02:00+02:00"
        )
        self.assertTrue(reused["ok"])
        duplicate = self.record_focus(
            context, now="2026-08-15T10:02:30+02:00", ok=False
        )
        self.assertIn("Unknown, expired, or already-used", duplicate["error"])
        status = self.run_cli("status", "--now", "2026-08-15T10:03:00+02:00")
        self.assertEqual(2, status["learned_terms"][0]["use_count"])

    def test_two_reserved_reviews_can_record_after_candidate_ranking_changes(self) -> None:
        self.init()
        introduction = self.run_cli(
            "context", "--now", "2026-08-15T09:59:00+02:00"
        )
        self.record_focus(
            introduction, now="2026-08-15T10:00:00+02:00"
        )
        first = self.run_cli(
            "context", "--now", "2026-08-15T10:01:00+02:00"
        )
        second = self.run_cli(
            "context", "--now", "2026-08-15T10:02:00+02:00"
        )
        self.record_focus(first, now="2026-08-15T10:03:00+02:00")
        self.record_focus(second, now="2026-08-15T10:04:00+02:00")
        status = self.run_cli("status", "--now", "2026-08-15T10:05:00+02:00")
        self.assertEqual(3, status["learned_terms"][0]["use_count"])

    def test_reviews_and_new_items_follow_elapsed_days(self) -> None:
        self.init()
        introduction = self.run_cli(
            "context", "--now", "2026-08-15T09:59:00+02:00"
        )
        self.record_focus(
            introduction, now="2026-08-15T10:00:00+02:00"
        )

        day_one = self.run_cli("context", "--now", "2026-08-16T10:00:00+02:00")
        self.assertEqual("review", day_one["focus"]["action"])
        self.record_focus(
            day_one, now="2026-08-16T10:01:00+02:00"
        )

        day_seven = self.run_cli("context", "--now", "2026-08-22T10:00:00+02:00")
        self.assertEqual("introduce", day_seven["focus"]["action"])
        self.assertEqual("vale", day_seven["focus"]["term"]["id"])
        self.record_focus(
            day_seven, now="2026-08-22T10:01:00+02:00"
        )
        status = self.run_cli("status", "--now", "2026-08-22T10:02:00+02:00")
        self.assertEqual("2026-08-29", status["next_introduction_date"])

    def test_message_count_cannot_unlock_the_second_item(self) -> None:
        self.init()
        for minute in range(10):
            context = self.run_cli(
                "context", "--now", f"2026-08-15T10:{minute:02d}:00+02:00"
            )
            self.assertEqual(1, context["eligible_count"])
            self.assertEqual("poco-a-poco", context["focus"]["term"]["id"])

    def test_elapsed_backlog_cannot_cause_rapid_introductions(self) -> None:
        self.run_cli(
            "init",
            "--now",
            "2026-08-15T09:00:00+02:00",
            "--start-date",
            "2026-08-01",
        )
        introduction = self.run_cli(
            "context", "--now", "2026-08-15T09:59:00+02:00"
        )
        self.record_focus(
            introduction, now="2026-08-15T10:00:00+02:00"
        )
        same_day = self.run_cli(
            "context", "--now", "2026-08-15T10:01:00+02:00"
        )
        self.assertEqual(3, same_day["eligible_count"])
        self.assertEqual("review", same_day["focus"]["action"])
        self.assertEqual("2026-08-22", same_day["next_introduction_date"])

        day_six = self.run_cli("context", "--now", "2026-08-21T10:00:00+02:00")
        self.assertEqual("review", day_six["focus"]["action"])
        day_seven = self.run_cli("context", "--now", "2026-08-22T10:00:00+02:00")
        self.assertEqual("introduce", day_seven["focus"]["action"])
        self.assertEqual("vale", day_seven["focus"]["term"]["id"])

    def test_reserved_introduction_rechecks_cadence_after_configuration_change(self) -> None:
        self.init()
        first = self.run_cli(
            "context", "--now", "2026-08-15T09:59:00+02:00"
        )
        self.record_focus(first, now="2026-08-15T10:00:00+02:00")
        reserved_second = self.run_cli(
            "context", "--now", "2026-08-22T09:59:00+02:00"
        )
        self.assertEqual("vale", reserved_second["focus"]["term"]["id"])

        self.run_cli(
            "configure",
            "--cadence-days",
            "30",
            "--now",
            "2026-08-22T10:00:00+02:00",
        )
        rejected = self.record_focus(
            reserved_second,
            now="2026-08-22T10:01:00+02:00",
            ok=False,
        )
        self.assertIn("no longer eligible", rejected["error"])
        status = self.run_cli("status", "--now", "2026-08-22T10:02:00+02:00")
        self.assertEqual(1, status["introduced_count"])

    def test_calendar_unlock_uses_configured_timezone(self) -> None:
        self.init()
        introduction = self.run_cli(
            "context", "--now", "2026-08-15T21:29:00Z"
        )
        self.record_focus(
            introduction, now="2026-08-15T21:30:00Z"
        )
        same_madrid_day = self.run_cli(
            "context", "--now", "2026-08-15T21:59:00Z"
        )
        self.assertEqual("2026-08-15", same_madrid_day["local_date"])
        self.assertEqual("review", same_madrid_day["focus"]["action"])

        next_madrid_day = self.run_cli(
            "context", "--now", "2026-08-15T22:01:00Z"
        )
        self.assertEqual("2026-08-16", next_madrid_day["local_date"])
        self.assertEqual("review", next_madrid_day["focus"]["action"])

        unlock_day = self.run_cli(
            "context", "--now", "2026-08-21T22:01:00Z"
        )
        self.assertEqual("2026-08-22", unlock_day["local_date"])
        self.assertEqual("vale", unlock_day["focus"]["term"]["id"])

    def test_daylight_saving_fallback_does_not_create_a_second_day(self) -> None:
        self.run_cli(
            "init",
            "--now",
            "2026-10-25T00:00:00Z",
            "--start-date",
            "2026-10-25",
        )
        introduction = self.run_cli(
            "context", "--now", "2026-10-25T00:29:00Z"
        )
        self.record_focus(
            introduction, now="2026-10-25T00:30:00Z"
        )
        after_fallback = self.run_cli(
            "context", "--now", "2026-10-25T01:30:00Z"
        )
        self.assertEqual("2026-10-25", after_fallback["local_date"])
        self.assertEqual(1, after_fallback["eligible_count"])
        self.assertEqual("review", after_fallback["focus"]["action"])

    def test_backward_clock_fails_closed(self) -> None:
        self.init()
        introduction = self.run_cli(
            "context", "--now", "2026-08-15T09:59:00+02:00"
        )
        self.record_focus(
            introduction, now="2026-08-15T10:00:00+02:00"
        )
        review = self.run_cli(
            "context", "--now", "2026-08-16T09:59:00+02:00"
        )
        self.record_focus(
            review, now="2026-08-16T10:00:00+02:00"
        )
        backward = self.run_cli(
            "context", "--now", "2026-08-15T12:00:00+02:00"
        )
        self.assertIsNone(backward["focus"])
        self.assertEqual("clock_before_last_exposure", backward["reason"])

    def test_two_concurrent_records_commit_exactly_once(self) -> None:
        self.init()
        contexts = [
            self.run_cli(
                "context", "--now", f"2026-08-15T09:59:0{second}+02:00"
            )
            for second in (0, 1)
        ]
        commands = []
        for context in contexts:
            commands.append(
                [
                    sys.executable,
                    str(SCRIPT),
                    "record",
                    "--term",
                    "poco-a-poco",
                    "--kind",
                    "introduce",
                    "--decision",
                    context["focus"]["decision_id"],
                    "--now",
                    "2026-08-15T10:00:00+02:00",
                    "--state",
                    str(self.state),
                    "--curriculum",
                    str(CURRICULUM),
                ]
            )
        environment = os.environ.copy()
        environment["AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE"] = "1"
        processes = [
            subprocess.Popen(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            for command in commands
        ]
        results = [process.communicate(timeout=10) for process in processes]
        return_codes = sorted(process.returncode for process in processes)
        self.assertEqual([0, 2], return_codes, results)
        status = self.run_cli("status", "--now", "2026-08-15T10:01:00+02:00")
        self.assertEqual(1, status["introduced_count"])
        self.assertEqual(1, status["learned_terms"][0]["use_count"])

    def test_wrong_record_transition_fails_closed(self) -> None:
        self.init()
        context = self.run_cli(
            "context", "--now", "2026-08-15T09:59:00+02:00"
        )
        payload = self.record_focus(
            context,
            term="vale",
            now="2026-08-15T10:00:00+02:00",
            ok=False,
        )
        self.assertIn("does not match reserved focus", payload["error"])

    def test_malformed_schema_v2_progress_fails_closed(self) -> None:
        self.init()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        del state["progress"]["last_exposure_at"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        payload = self.run_cli(
            "context", "--now", "2026-08-15T10:00:00+02:00", ok=False
        )
        self.assertIn("last_exposure_at", payload["error"])

    def test_non_object_config_uses_json_error_contract(self) -> None:
        self.init()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["config"] = []
        self.state.write_text(json.dumps(state), encoding="utf-8")
        payload = self.run_cli(
            "context", "--now", "2026-08-15T10:00:00+02:00", ok=False
        )
        self.assertIn("config and progress objects", payload["error"])

    def test_inconsistent_enforcement_timestamps_fail_closed(self) -> None:
        self.init()
        introduction = self.run_cli(
            "context", "--now", "2026-08-15T09:59:00+02:00"
        )
        self.record_focus(
            introduction, now="2026-08-15T10:00:00+02:00"
        )
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["progress"]["last_exposure_at"] = "2026-08-14T10:00:00+02:00"
        state["progress"]["last_new_term_at"] = "2026-08-14T10:00:00+02:00"
        self.state.write_text(json.dumps(state), encoding="utf-8")
        payload = self.run_cli(
            "context", "--now", "2026-08-15T11:00:00+02:00", ok=False
        )
        self.assertIn("latest term last_used_at", payload["error"])

    def test_schema_v1_migrates_with_preserved_backup(self) -> None:
        self.init()
        introduction = self.run_cli(
            "context", "--now", "2026-08-15T09:59:00+02:00"
        )
        self.record_focus(
            introduction, now="2026-08-15T10:00:00+02:00"
        )
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["schema_version"] = 1
        state["config"].pop("exposure_percent")
        state["progress"].pop("pending_decisions")
        state["progress"]["last_any_insertion_at"] = state["progress"].pop(
            "last_exposure_at"
        )
        self.state.write_text(json.dumps(state), encoding="utf-8")

        migrated = self.run_cli(
            "context", "--now", "2026-08-15T10:01:00+02:00"
        )
        self.assertEqual(2, migrated["schema_version"])
        self.assertEqual(50, migrated["exposure_percent"])
        self.assertEqual("review", migrated["focus"]["action"])

        persisted = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(2, persisted["schema_version"])
        self.assertIn("last_exposure_at", persisted["progress"])
        backup_path = self.state.with_name(f"{self.state.name}.schema-v1.backup")
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
        self.assertEqual(1, backup["schema_version"])
        self.assertIn("last_any_insertion_at", backup["progress"])

    def test_schema_v1_migration_rejects_impossible_fast_history(self) -> None:
        self.init()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["schema_version"] = 1
        state["config"].pop("exposure_percent")
        state["progress"].pop("pending_decisions")
        first_at = "2026-08-15T10:00:00+02:00"
        second_at = "2026-08-15T10:01:00+02:00"
        state["progress"]["terms"] = {
            "poco-a-poco": {
                "introduced_at": first_at,
                "last_used_at": first_at,
                "use_count": 1,
            },
            "vale": {
                "introduced_at": second_at,
                "last_used_at": second_at,
                "use_count": 1,
            },
        }
        state["progress"]["last_any_insertion_at"] = second_at
        state["progress"].pop("last_exposure_at")
        state["progress"]["last_new_term_at"] = second_at
        state["updated_at"] = second_at
        self.state.write_text(json.dumps(state), encoding="utf-8")

        rejected = self.run_cli(
            "context", "--now", "2026-08-15T11:00:00+02:00", ok=False
        )
        self.assertIn("distinct increasing local dates", rejected["error"])
        persisted = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(1, persisted["schema_version"])
        backup_path = self.state.with_name(f"{self.state.name}.schema-v1.backup")
        self.assertFalse(backup_path.exists())

    def test_cadence_change_does_not_invalidate_valid_history_or_v1_migration(self) -> None:
        self.run_cli(
            "init",
            "--now",
            "2026-08-15T09:00:00+02:00",
            "--start-date",
            "2026-08-15",
            "--cadence-days",
            "1",
        )
        first = self.run_cli(
            "context", "--now", "2026-08-15T09:59:00+02:00"
        )
        self.record_focus(first, now="2026-08-15T10:00:00+02:00")
        second = self.run_cli(
            "context", "--now", "2026-08-16T09:59:00+02:00"
        )
        self.assertEqual("vale", second["focus"]["term"]["id"])
        self.record_focus(second, now="2026-08-16T10:00:00+02:00")
        self.run_cli(
            "configure",
            "--cadence-days",
            "7",
            "--now",
            "2026-08-16T10:01:00+02:00",
        )

        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["schema_version"] = 1
        state["config"].pop("exposure_percent")
        state["progress"].pop("pending_decisions")
        state["progress"]["last_any_insertion_at"] = state["progress"].pop(
            "last_exposure_at"
        )
        self.state.write_text(json.dumps(state), encoding="utf-8")

        migrated = self.run_cli(
            "context", "--now", "2026-08-17T10:00:00+02:00"
        )
        self.assertEqual(2, migrated["schema_version"])
        self.assertEqual(7, migrated["cadence_days"])
        self.assertEqual(2, migrated["introduced_count"])

    def test_pending_decision_limit_fails_without_evicting_valid_reservations(self) -> None:
        now = AMBIENT_STATE.datetime.fromisoformat("2026-08-15T10:00:00+02:00")
        state = AMBIENT_STATE._new_state(
            now=now,
            timezone_name="Europe/Madrid",
            dialect="es-ES",
            cadence_days=7,
            exposure_percent=50,
            start_date=now.date(),
        )
        pending = state["progress"]["pending_decisions"]
        for index in range(AMBIENT_STATE.MAX_PENDING_DECISIONS):
            pending[f"decision-{index}"] = {
                "action": "introduce",
                "term_id": "poco-a-poco",
                "created_at": now.isoformat(),
            }
        original_ids = set(pending)

        with self.assertRaisesRegex(
            AMBIENT_STATE.StateError, "Too many unconsumed exposure decisions"
        ):
            AMBIENT_STATE._reserve_decision(
                state,
                {
                    "action": "introduce",
                    "term": {"id": "poco-a-poco"},
                },
                now,
            )

        self.assertEqual(original_ids, set(pending))
        self.assertEqual(AMBIENT_STATE.MAX_PENDING_DECISIONS, len(pending))

    def test_boolean_and_float_integer_fields_fail_closed(self) -> None:
        self.init()
        original = json.loads(self.state.read_text(encoding="utf-8"))
        cases = (
            ("schema_version", True),
            ("schema_version", 1.0),
            ("cadence_days", True),
            ("exposure_percent", True),
            ("exposure_percent", 50.0),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                state = json.loads(json.dumps(original))
                if field == "schema_version":
                    state[field] = value
                else:
                    state["config"][field] = value
                self.state.write_text(json.dumps(state), encoding="utf-8")
                payload = self.run_cli(
                    "context", "--now", "2026-08-15T10:00:00+02:00", ok=False
                )
                self.assertFalse(payload["ok"])

    def test_invalid_timezone_uses_json_error_contract(self) -> None:
        payload = self.run_cli(
            "init",
            "--timezone",
            "/etc/passwd",
            "--now",
            "2026-08-15T10:00:00+02:00",
            ok=False,
        )
        self.assertIn("Unknown IANA timezone", payload["error"])

    def test_directory_sync_failure_does_not_false_report_failed_write(self) -> None:
        destination = Path(self.temp_dir.name) / "atomic.json"
        real_os_open = os.open

        def open_with_directory_failure(path, flags, *args):
            if Path(path) == destination.parent and flags == os.O_RDONLY:
                raise OSError("directory fsync unsupported")
            return real_os_open(path, flags, *args)

        with mock.patch.object(
            AMBIENT_STATE.os, "open", side_effect=open_with_directory_failure
        ):
            durable = AMBIENT_STATE._atomic_write(destination, {"committed": True})
        self.assertFalse(durable)
        self.assertEqual(
            {"committed": True}, json.loads(destination.read_text(encoding="utf-8"))
        )

    def test_pause_preserves_progress_and_suppresses_focus(self) -> None:
        self.init()
        self.run_cli("configure", "--pause", "--now", "2026-08-15T10:00:00+02:00")
        paused = self.run_cli("context", "--now", "2026-08-20T10:00:00+02:00")
        self.assertTrue(paused["paused"])
        self.assertIsNone(paused["focus"])
        self.assertEqual("paused", paused["reason"])
        self.run_cli("configure", "--resume", "--now", "2026-08-20T10:01:00+02:00")
        resumed = self.run_cli("context", "--now", "2026-08-20T10:02:00+02:00")
        self.assertFalse(resumed["paused"])
        self.assertEqual("introduce", resumed["focus"]["action"])

    def test_status_lists_introduced_terms(self) -> None:
        self.init()
        introduction = self.run_cli(
            "context", "--now", "2026-08-15T09:59:00+02:00"
        )
        self.record_focus(
            introduction, now="2026-08-15T10:00:00+02:00"
        )
        status = self.run_cli("status", "--now", "2026-08-15T11:00:00+02:00")
        self.assertEqual(1, len(status["learned_terms"]))
        self.assertEqual("poco a poco", status["learned_terms"][0]["spanish"])

    def test_curriculum_is_unique_and_covers_two_years(self) -> None:
        curriculum = json.loads(CURRICULUM.read_text(encoding="utf-8"))
        ids = [item["id"] for item in curriculum]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 104)


if __name__ == "__main__":
    unittest.main()
