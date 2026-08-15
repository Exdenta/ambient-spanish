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

    def run_cli(self, *args: str, ok: bool = True) -> dict:
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

    def test_first_item_is_available_on_start_date(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual("new_item_ready", context["reason"])
        self.assertEqual("introduce", context["focus"]["action"])
        self.assertEqual("poco-a-poco", context["focus"]["term"]["id"])
        self.assertEqual(1, context["eligible_count"])

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

    def test_record_enforces_one_insertion_per_local_day(self) -> None:
        self.init()
        recorded = self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "introduce",
            "--now",
            "2026-08-15T10:00:00+02:00",
        )
        self.assertTrue(recorded["ok"])
        context = self.run_cli("context", "--now", "2026-08-15T23:59:00+02:00")
        self.assertIsNone(context["focus"])
        self.assertEqual("daily_limit_reached", context["reason"])

        rejected = self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "review",
            "--now",
            "2026-08-15T23:59:30+02:00",
            ok=False,
        )
        self.assertIn("daily_limit_reached", rejected["error"])

    def test_reviews_and_new_items_follow_elapsed_days(self) -> None:
        self.init()
        self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "introduce",
            "--now",
            "2026-08-15T10:00:00+02:00",
        )

        day_one = self.run_cli("context", "--now", "2026-08-16T10:00:00+02:00")
        self.assertEqual("review", day_one["focus"]["action"])
        self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "review",
            "--now",
            "2026-08-16T10:01:00+02:00",
        )

        day_two = self.run_cli("context", "--now", "2026-08-17T10:00:00+02:00")
        self.assertIsNone(day_two["focus"])
        day_three = self.run_cli("context", "--now", "2026-08-18T10:00:00+02:00")
        self.assertEqual("review", day_three["focus"]["action"])
        self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "review",
            "--now",
            "2026-08-18T10:01:00+02:00",
        )

        day_seven = self.run_cli("context", "--now", "2026-08-22T10:00:00+02:00")
        self.assertEqual("review", day_seven["focus"]["action"])
        self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "review",
            "--now",
            "2026-08-22T10:01:00+02:00",
        )

        day_eight = self.run_cli("context", "--now", "2026-08-23T10:00:00+02:00")
        self.assertEqual(2, day_eight["eligible_count"])
        self.assertEqual("introduce", day_eight["focus"]["action"])
        self.assertEqual("vale", day_eight["focus"]["term"]["id"])

    def test_message_count_cannot_unlock_the_second_item(self) -> None:
        self.init()
        for minute in range(10):
            context = self.run_cli(
                "context", "--now", f"2026-08-15T10:{minute:02d}:00+02:00"
            )
            self.assertEqual(1, context["eligible_count"])
            self.assertEqual("poco-a-poco", context["focus"]["term"]["id"])

    def test_daily_boundary_uses_configured_timezone(self) -> None:
        self.init()
        self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "introduce",
            "--now",
            "2026-08-15T21:30:00Z",
        )
        same_madrid_day = self.run_cli(
            "context", "--now", "2026-08-15T21:59:00Z"
        )
        self.assertEqual("2026-08-15", same_madrid_day["local_date"])
        self.assertEqual("daily_limit_reached", same_madrid_day["reason"])

        next_madrid_day = self.run_cli(
            "context", "--now", "2026-08-15T22:01:00Z"
        )
        self.assertEqual("2026-08-16", next_madrid_day["local_date"])
        self.assertEqual("review", next_madrid_day["focus"]["action"])

    def test_daylight_saving_fallback_does_not_create_a_second_day(self) -> None:
        self.run_cli(
            "init",
            "--now",
            "2026-10-25T00:00:00Z",
            "--start-date",
            "2026-10-25",
        )
        self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "introduce",
            "--now",
            "2026-10-25T00:30:00Z",
        )
        after_fallback = self.run_cli(
            "context", "--now", "2026-10-25T01:30:00Z"
        )
        self.assertEqual("2026-10-25", after_fallback["local_date"])
        self.assertEqual("daily_limit_reached", after_fallback["reason"])

    def test_backward_clock_fails_closed(self) -> None:
        self.init()
        self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "introduce",
            "--now",
            "2026-08-15T10:00:00+02:00",
        )
        self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "review",
            "--now",
            "2026-08-16T10:00:00+02:00",
        )
        backward = self.run_cli(
            "context", "--now", "2026-08-15T12:00:00+02:00"
        )
        self.assertIsNone(backward["focus"])
        self.assertEqual("clock_before_last_insertion", backward["reason"])

    def test_two_concurrent_records_commit_exactly_once(self) -> None:
        self.init()
        base_command = [
            sys.executable,
            str(SCRIPT),
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "introduce",
            "--now",
            "2026-08-15T10:00:00+02:00",
            "--state",
            str(self.state),
            "--curriculum",
            str(CURRICULUM),
        ]
        environment = os.environ.copy()
        environment["AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE"] = "1"
        processes = [
            subprocess.Popen(
                base_command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            for _ in range(2)
        ]
        results = [process.communicate(timeout=10) for process in processes]
        return_codes = sorted(process.returncode for process in processes)
        self.assertEqual([0, 2], return_codes, results)
        status = self.run_cli("status", "--now", "2026-08-15T10:01:00+02:00")
        self.assertEqual(1, status["introduced_count"])
        self.assertEqual(1, status["learned_terms"][0]["use_count"])

    def test_wrong_record_transition_fails_closed(self) -> None:
        self.init()
        payload = self.run_cli(
            "record",
            "--term",
            "vale",
            "--kind",
            "introduce",
            "--now",
            "2026-08-15T10:00:00+02:00",
            ok=False,
        )
        self.assertIn("does not match current focus", payload["error"])

    def test_malformed_schema_v1_progress_fails_closed(self) -> None:
        self.init()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        del state["progress"]["last_any_insertion_at"]
        self.state.write_text(json.dumps(state), encoding="utf-8")
        payload = self.run_cli(
            "context", "--now", "2026-08-15T10:00:00+02:00", ok=False
        )
        self.assertIn("last_any_insertion_at", payload["error"])

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
        self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "introduce",
            "--now",
            "2026-08-15T10:00:00+02:00",
        )
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["progress"]["last_any_insertion_at"] = "2026-08-14T10:00:00+02:00"
        state["progress"]["last_new_term_at"] = "2026-08-14T10:00:00+02:00"
        self.state.write_text(json.dumps(state), encoding="utf-8")
        payload = self.run_cli(
            "context", "--now", "2026-08-15T11:00:00+02:00", ok=False
        )
        self.assertIn("latest term last_used_at", payload["error"])

    def test_boolean_and_float_integer_fields_fail_closed(self) -> None:
        self.init()
        original = json.loads(self.state.read_text(encoding="utf-8"))
        cases = (
            ("schema_version", True),
            ("schema_version", 1.0),
            ("cadence_days", True),
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
        self.run_cli(
            "record",
            "--term",
            "poco-a-poco",
            "--kind",
            "introduce",
            "--now",
            "2026-08-15T10:00:00+02:00",
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
