from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ambient_state.py"
REAL_CURRICULUM = ROOT / "references" / "curriculum.json"
MODULE_SPEC = importlib.util.spec_from_file_location("ambient_state", SCRIPT)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
AMBIENT_STATE = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(AMBIENT_STATE)

# A fixed fixture keeps assertions stable when references/curriculum.json is
# edited. One test separately checks that the shipped curriculum still loads.
FIXTURE_CURRICULUM = [
    {
        "id": f"t{index:02d}",
        "spanish": f"palabra{index:02d}",
        "english": f"word{index:02d}",
        "kind": "connector",
        "usage": f"Use case {index:02d}.",
    }
    for index in range(12)
]


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state = Path(self.temp_dir.name) / "state.json"
        self.curriculum = Path(self.temp_dir.name) / "curriculum.json"
        self.curriculum.write_text(
            json.dumps(FIXTURE_CURRICULUM), encoding="utf-8"
        )
        self.lookup_bin = Path(self.temp_dir.name) / "ambient-lookup"
        self.lookup_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.lookup_bin.chmod(0o755)

    def run_raw(self, *args: str) -> subprocess.CompletedProcess:
        command = [
            sys.executable,
            str(SCRIPT),
            *args,
            "--state",
            str(self.state),
            "--curriculum",
            str(self.curriculum),
        ]
        environment = os.environ.copy()
        environment["AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE"] = "1"
        environment["AMBIENT_LOOKUP_BIN"] = str(self.lookup_bin)
        return subprocess.run(
            command, text=True, capture_output=True, check=False, env=environment
        )

    def run_cli(self, *args: str, ok: bool = True) -> dict:
        result = self.run_raw(*args)
        command = [str(SCRIPT), *args]
        if ok and result.returncode != 0:
            self.fail(
                f"command failed: {command}\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        if not ok and result.returncode == 0:
            self.fail(f"command unexpectedly succeeded: {command}\nstdout={result.stdout}")
        return json.loads(result.stdout if result.returncode == 0 else result.stderr)

    def init(self, *extra: str, start: str = "2026-08-15") -> dict:
        """Init with three words per week, unless the test sets another pace.

        That keeps the 12-term fixture small but still lets it grow over
        several weeks.
        """
        if "--words-per-week" not in extra:
            extra = ("--words-per-week", "3", *extra)
        return self.run_cli(
            "init", "--now", f"{start}T09:00:00+02:00", "--start-date", start, *extra
        )

    def write_state(self, document: dict) -> None:
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(json.dumps(document), encoding="utf-8")

    @staticmethod
    def ids(terms: list[dict]) -> list[str]:
        return [term["id"] for term in terms]

    def vocabulary_ids(self) -> list[str]:
        """Ids in vocabulary.txt, the whole vocabulary as of the last `context` call."""
        manifest = self.state.parent / AMBIENT_STATE.MANIFEST_FILENAME
        return [
            line.split(" | ")[0]
            for line in manifest.read_text(encoding="utf-8").splitlines()
            if " | " in line
        ]


class TierDerivationTests(CliTestCase):
    def test_first_weekly_batch_is_in_the_vocabulary_immediately(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertTrue(context["active"])
        self.assertEqual("active", context["reason"])
        self.assertEqual(0, context["batch_index"])
        self.assertEqual(["t00", "t01", "t02"], self.vocabulary_ids())
        self.assertEqual(3, context["vocabulary_count"])
        self.assertEqual(3, context["words_per_week"])
        self.assertEqual(7, context["cadence_days"])
        self.assertEqual("2026-08-22", context["next_batch_date"])
        self.assertTrue(context["decision_id"].startswith("d_"))

    def test_context_has_one_vocabulary_and_no_learning_tier(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        for key in ("learning", "learning_count", "batch_size"):
            self.assertNotIn(key, context)
        for key in ("known", "known_count", "known_scope", "known_offered_count", "known_per_reply"):
            self.assertNotIn(key, context)
        self.assertEqual(3, context["vocabulary_count"])

    def test_batch_joins_the_vocabulary_exactly_seven_days_later(self) -> None:
        self.init("--words-per-week", "2")
        day_zero = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(["t00", "t01"], self.vocabulary_ids())
        self.assertEqual("2026-08-22", day_zero["next_batch_date"])
        before = self.run_cli("context", "--now", "2026-08-21T23:59:00+02:00")
        self.assertEqual(0, before["batch_index"])
        self.assertEqual(["t00", "t01"], self.vocabulary_ids())
        on_the_day = self.run_cli("context", "--now", "2026-08-22T00:01:00+02:00")
        self.assertEqual(1, on_the_day["batch_index"])
        self.assertEqual(["t00", "t01", "t02", "t03"], self.vocabulary_ids())
        self.assertEqual("2026-08-29", on_the_day["next_batch_date"])

    def test_batch_promotes_on_cadence_date(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-22T10:00:00+02:00")
        self.assertEqual(1, context["batch_index"])
        self.assertEqual(
            ["t00", "t01", "t02", "t03", "t04", "t05"], self.vocabulary_ids()
        )

    def test_no_promotion_before_the_cadence_date(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-21T23:59:00+02:00")
        self.assertEqual(0, context["batch_index"])
        self.assertEqual(["t00", "t01", "t02"], self.vocabulary_ids())

    def test_promotion_happens_without_any_recorded_use(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-29T10:00:00+02:00")
        self.assertEqual(2, context["batch_index"])
        self.assertEqual(
            [f"t{index:02d}" for index in range(9)], self.vocabulary_ids()
        )

    def test_missed_time_does_not_queue_a_backlog(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-09-01T10:00:00+02:00")
        self.assertEqual(2, context["batch_index"])
        self.assertEqual(9, context["vocabulary_count"])
        self.assertEqual("2026-09-05", context["next_batch_date"])

    def test_baseline_known_offsets_the_first_batch(self) -> None:
        self.init("--baseline-known", "4")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(
            [f"t{index:02d}" for index in range(7)], self.vocabulary_ids()
        )

    def test_words_per_week_is_configurable(self) -> None:
        self.init("--words-per-week", "5")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(["t00", "t01", "t02", "t03", "t04"], self.vocabulary_ids())
        self.assertEqual(5, context["words_per_week"])

    def test_future_start_date_is_inactive(self) -> None:
        self.init(start="2026-09-01")
        context = self.run_cli("context", "--now", "2026-08-20T10:00:00+02:00")
        self.assertFalse(context["active"])
        self.assertEqual("before_start_date", context["reason"])
        self.assertIsNone(context["lookup"])
        self.assertIsNone(context["decision_id"])

    def test_completed_curriculum_stays_active_with_no_new_words(self) -> None:
        self.init("--baseline-known", "12")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertTrue(context["active"])
        self.assertEqual("curriculum_complete", context["reason"])
        self.assertEqual(12, context["vocabulary_count"])
        self.assertIsNone(context["next_batch_date"])

    def test_running_past_the_curriculum_end_keeps_everything_known(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-09-15T10:00:00+02:00")
        self.assertTrue(context["active"])
        self.assertEqual("curriculum_complete", context["reason"])
        self.assertEqual(12, context["vocabulary_count"])

    def test_final_partial_batch_is_truncated(self) -> None:
        self.init("--baseline-known", "10")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(12, context["vocabulary_count"])
        self.assertEqual("t11", self.vocabulary_ids()[-1])
        self.assertIsNone(context["next_batch_date"])

    def test_paused_state_is_inactive(self) -> None:
        self.init()
        self.run_cli("configure", "--pause", "--now", "2026-08-15T09:30:00+02:00")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertFalse(context["active"])
        self.assertEqual("paused", context["reason"])
        self.run_cli("configure", "--resume", "--now", "2026-08-15T10:30:00+02:00")
        resumed = self.run_cli("context", "--now", "2026-08-15T11:00:00+02:00")
        self.assertTrue(resumed["active"])


class RecordTests(CliTestCase):
    def test_records_a_subset_of_the_active_set(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        recorded = self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t00,t02",
            "--now",
            "2026-08-15T10:05:00+02:00",
        )
        self.assertTrue(recorded["ok"])
        self.assertEqual(["t00", "t02"], recorded["recorded"]["used"])
        self.assertEqual(["t00", "t02"], recorded["recorded"]["first_uses"])
        status = self.run_cli("status", "--now", "2026-08-15T10:10:00+02:00")
        self.assertEqual(["t00", "t02"], sorted(self.ids(status["used_terms"])))

    def test_repeat_use_increments_the_counter(self) -> None:
        self.init()
        for minute, expected in ((10, 1), (20, 2)):
            context = self.run_cli("context", "--now", f"2026-08-15T{minute}:00:00+02:00")
            self.run_cli(
                "record",
                "--decision",
                context["decision_id"],
                "--used",
                "t00",
                "--now",
                f"2026-08-15T{minute}:30:00+02:00",
            )
            status = self.run_cli("status", "--now", f"2026-08-15T{minute}:45:00+02:00")
            self.assertEqual(expected, status["used_terms"][0]["use_count"])

    def test_known_terms_can_be_recorded_after_promotion(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-22T10:00:00+02:00")
        self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t01,t04",
            "--now",
            "2026-08-22T10:05:00+02:00",
        )
        status = self.run_cli("status", "--now", "2026-08-22T10:10:00+02:00")
        self.assertEqual(["t01", "t04"], sorted(self.ids(status["used_terms"])))

    def test_history_need_not_form_a_curriculum_prefix(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-29T10:00:00+02:00")
        self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t07",
            "--now",
            "2026-08-29T10:05:00+02:00",
        )
        status = self.run_cli("status", "--now", "2026-08-29T10:10:00+02:00")
        self.assertEqual(["t07"], self.ids(status["used_terms"]))

    def test_decision_is_single_use(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t00",
            "--now",
            "2026-08-15T10:05:00+02:00",
        )
        failure = self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t01",
            "--now",
            "2026-08-15T10:06:00+02:00",
            ok=False,
        )
        self.assertFalse(failure["ok"])
        self.assertIn("already-used", failure["error"])

    def test_terms_outside_the_reserved_set_are_rejected(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        failure = self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t00,t09",
            "--now",
            "2026-08-15T10:05:00+02:00",
            ok=False,
        )
        self.assertIn("not part of the reserved active set", failure["error"])

    def test_unknown_terms_are_rejected_and_duplicates_collapse(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        unknown = self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "nope",
            "--now",
            "2026-08-15T10:05:00+02:00",
            ok=False,
        )
        self.assertIn("Unknown curriculum terms", unknown["error"])
        # A repeated word is harmless: it is recorded once.
        duplicate = self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t00,t00",
            "--now",
            "2026-08-15T10:05:00+02:00",
        )
        self.assertEqual(["t00"], duplicate["recorded"]["used"])

    def test_expired_decision_is_rejected(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        failure = self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t00",
            "--now",
            "2026-08-16T11:00:00+02:00",
            ok=False,
        )
        self.assertIn("expired", failure["error"])

    def test_clock_rollback_blocks_recording_and_deactivates_context(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-16T10:00:00+02:00")
        self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t00",
            "--now",
            "2026-08-16T10:05:00+02:00",
        )
        rolled_back = self.run_cli("context", "--now", "2026-08-16T09:00:00+02:00")
        self.assertFalse(rolled_back["active"])
        self.assertEqual("clock_before_last_exposure", rolled_back["reason"])


class ConfigurationTests(CliTestCase):
    def test_configure_words_per_week(self) -> None:
        self.init()
        changed = self.run_cli(
            "configure",
            "--words-per-week",
            "2",
            "--now",
            "2026-08-15T09:30:00+02:00",
        )
        self.assertEqual({"words_per_week": 2}, changed["changes"])
        first = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(2, first["words_per_week"])
        self.assertEqual(7, first["cadence_days"])
        self.assertEqual(["t00", "t01"], self.vocabulary_ids())
        context = self.run_cli("context", "--now", "2026-08-29T10:00:00+02:00")
        self.assertEqual(2, context["batch_index"])
        self.assertEqual(
            ["t00", "t01", "t02", "t03", "t04", "t05"], self.vocabulary_ids()
        )

    def test_configure_baseline_known_shifts_the_vocabulary_boundary(self) -> None:
        self.init()
        changed = self.run_cli(
            "configure", "--baseline-known", "6", "--now", "2026-08-15T09:30:00+02:00"
        )
        self.assertEqual({"baseline_known_count": 6}, changed["changes"])
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(
            [f"t{index:02d}" for index in range(9)], self.vocabulary_ids()
        )

    def test_configure_rejects_an_out_of_range_baseline(self) -> None:
        self.init()
        too_large = self.run_cli(
            "configure", "--baseline-known", "13", "--now", "2026-08-15T09:30:00+02:00", ok=False
        )
        self.assertIn("exceeds the curriculum size", too_large["error"])
        negative = self.run_cli(
            "configure", "--baseline-known", "-1", "--now", "2026-08-15T09:30:00+02:00", ok=False
        )
        self.assertIn("must not be negative", negative["error"])

    def test_invalid_configuration_is_rejected(self) -> None:
        self.init()
        failure = self.run_cli(
            "configure", "--words-per-week", "0", "--now", "2026-08-15T09:30:00+02:00", ok=False
        )
        self.assertIn("must be at least 1", failure["error"])

    def test_empty_configure_fails(self) -> None:
        self.init()
        failure = self.run_cli(
            "configure", "--now", "2026-08-15T09:30:00+02:00", ok=False
        )
        self.assertIn("No configuration change requested", failure["error"])

    def test_init_refuses_to_clobber_without_force(self) -> None:
        self.init()
        failure = self.run_cli("init", "--now", "2026-08-15T09:00:00+02:00", ok=False)
        self.assertIn("State already exists", failure["error"])

    def test_time_override_requires_the_escape_hatch(self) -> None:
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
            str(self.curriculum),
        ]
        environment = os.environ.copy()
        environment.pop("AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE", None)
        result = subprocess.run(
            command, text=True, capture_output=True, check=False, env=environment
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--now is disabled", result.stderr)


class ValidationTests(CliTestCase):
    def base_state(self) -> dict:
        return {
            "schema_version": 4,
            "config": {
                "timezone": "Europe/Madrid",
                "dialect": "es-ES",
                "cadence_days": 3,
                "batch_size": 3,
                "known_per_reply": 12,
                "baseline_known_count": 0,
                "paused": False,
                "start_date": "2026-08-15",
            },
            "progress": {
                "last_exposure_at": None,
                "pending_decisions": {},
                "terms": {},
            },
            "created_at": "2026-08-15T09:00:00+02:00",
            "updated_at": "2026-08-15T09:00:00+02:00",
        }

    def test_unknown_schema_version_fails_closed(self) -> None:
        document = self.base_state()
        document["schema_version"] = 99
        self.write_state(document)
        failure = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00", ok=False)
        self.assertIn("Unsupported state schema_version 99", failure["error"])

    def test_boolean_is_not_accepted_as_an_integer(self) -> None:
        document = self.base_state()
        document["config"]["batch_size"] = True
        self.write_state(document)
        failure = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00", ok=False)
        self.assertIn("batch_size must be an integer", failure["error"])

    def test_baseline_beyond_curriculum_is_rejected(self) -> None:
        document = self.base_state()
        document["config"]["baseline_known_count"] = 13
        self.write_state(document)
        failure = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00", ok=False)
        self.assertIn("exceeds the curriculum size", failure["error"])

    def test_unknown_curriculum_id_in_history_is_rejected(self) -> None:
        document = self.base_state()
        document["progress"]["terms"] = {
            "ghost": {
                "introduced_at": "2026-08-15T10:00:00+02:00",
                "last_used_at": "2026-08-15T10:00:00+02:00",
                "use_count": 1,
            }
        }
        document["progress"]["last_exposure_at"] = "2026-08-15T10:00:00+02:00"
        self.write_state(document)
        failure = self.run_cli("context", "--now", "2026-08-15T11:00:00+02:00", ok=False)
        self.assertIn("unknown curriculum ids: ghost", failure["error"])

    def test_exposure_timestamp_must_match_newest_use(self) -> None:
        document = self.base_state()
        document["progress"]["terms"] = {
            "t00": {
                "introduced_at": "2026-08-15T10:00:00+02:00",
                "last_used_at": "2026-08-15T10:00:00+02:00",
                "use_count": 1,
            }
        }
        document["progress"]["last_exposure_at"] = "2026-08-15T12:00:00+02:00"
        self.write_state(document)
        failure = self.run_cli("context", "--now", "2026-08-15T13:00:00+02:00", ok=False)
        self.assertIn("must equal the latest term last_used_at", failure["error"])

    def test_legacy_decision_shape_is_rejected(self) -> None:
        document = self.base_state()
        document["progress"]["pending_decisions"] = {
            "d_x": {
                "action": "introduce",
                "term_id": "t00",
                "created_at": "2026-08-15T10:00:00+02:00",
            }
        }
        self.write_state(document)
        failure = self.run_cli("context", "--now", "2026-08-15T11:00:00+02:00", ok=False)
        self.assertIn("invalid fields", failure["error"])


class MigrationTests(CliTestCase):
    def v2_state(self) -> dict:
        return {
            "schema_version": 2,
            "config": {
                "timezone": "Europe/Madrid",
                "dialect": "es-ES",
                "cadence_days": 3,
                "exposure_percent": 50,
                "paused": False,
                "start_date": "2026-08-01",
            },
            "progress": {
                "last_exposure_at": "2026-08-10T10:00:00+02:00",
                "last_new_term_at": "2026-08-07T10:00:00+02:00",
                "pending_decisions": {},
                "terms": {
                    "t00": {
                        "introduced_at": "2026-08-01T10:00:00+02:00",
                        "last_used_at": "2026-08-09T10:00:00+02:00",
                        "use_count": 12,
                    },
                    "t01": {
                        "introduced_at": "2026-08-04T10:00:00+02:00",
                        "last_used_at": "2026-08-10T10:00:00+02:00",
                        "use_count": 5,
                    },
                    "t02": {
                        "introduced_at": "2026-08-07T10:00:00+02:00",
                        "last_used_at": "2026-08-08T10:00:00+02:00",
                        "use_count": 2,
                    },
                },
            },
            "created_at": "2026-08-01T09:00:00+02:00",
            "updated_at": "2026-08-10T10:00:00+02:00",
        }

    def test_v2_terms_become_baseline_known(self) -> None:
        self.write_state(self.v2_state())
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(6, context["schema_version"])
        self.assertEqual(0, context["batch_index"])
        # The three used terms are the baseline; the first weekly batch joins on top.
        expected = min(12, 3 + AMBIENT_STATE.DEFAULT_BATCH_SIZE)
        self.assertEqual(
            [f"t{index:02d}" for index in range(expected)], self.vocabulary_ids()
        )
        self.assertEqual(AMBIENT_STATE.DEFAULT_BATCH_SIZE, context["words_per_week"])

    def test_v2_migration_preserves_history_and_backs_up(self) -> None:
        source = self.v2_state()
        self.write_state(source)
        status = self.run_cli("status", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(3, status["baseline_known_count"])
        self.assertEqual("2026-08-15", status["start_date"])
        counts = {term["id"]: term["use_count"] for term in status["used_terms"]}
        self.assertEqual({"t00": 12, "t01": 5, "t02": 2}, counts)
        backup = self.state.with_name(f"{self.state.name}.schema-v2.backup")
        self.assertTrue(backup.exists())
        self.assertEqual(source, json.loads(backup.read_text(encoding="utf-8")))
        migrated = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertNotIn("exposure_percent", migrated["config"])
        self.assertNotIn("last_new_term_at", migrated["progress"])

    def test_v1_migrates_through_to_the_current_schema(self) -> None:
        source = self.v2_state()
        source["schema_version"] = 1
        del source["config"]["exposure_percent"]
        source["progress"]["last_any_insertion_at"] = source["progress"].pop(
            "last_exposure_at"
        )
        del source["progress"]["pending_decisions"]
        self.write_state(source)
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(6, context["schema_version"])
        # Baseline of three plus the default weekly batch, capped by the fixture.
        self.assertEqual(["t00", "t01", "t02"], self.vocabulary_ids()[:3])
        self.assertEqual(min(12, 3 + AMBIENT_STATE.DEFAULT_BATCH_SIZE), context["vocabulary_count"])
        backup = self.state.with_name(f"{self.state.name}.schema-v1.backup")
        self.assertTrue(backup.exists())

    def test_v3_migrates_through_and_drops_unbounded_decisions(self) -> None:
        source = self.v2_state()
        source["schema_version"] = 3
        del source["config"]["exposure_percent"]
        del source["progress"]["last_new_term_at"]
        source["config"]["batch_size"] = 3
        source["config"]["baseline_known_count"] = 3
        source["config"]["start_date"] = "2026-08-15"
        source["progress"]["pending_decisions"] = {
            "d_unbounded": {
                "term_ids": [f"t{index:02d}" for index in range(12)],
                "created_at": "2026-08-15T09:00:00+02:00",
            }
        }
        self.write_state(source)
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(6, context["schema_version"])
        migrated = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertNotIn("known_per_reply", migrated["config"])
        self.assertNotIn("offers", migrated["progress"])
        self.assertNotIn("d_unbounded", migrated["progress"]["pending_decisions"])
        backup = self.state.with_name(f"{self.state.name}.schema-v3.backup")
        self.assertTrue(backup.exists())
        counts = {
            term_id: term["use_count"]
            for term_id, term in migrated["progress"]["terms"].items()
        }
        self.assertEqual({"t00": 12, "t01": 5, "t02": 2}, counts)


class LookupContextTests(CliTestCase):
    """`context` hands the agent a lookup command and the vocabulary file, not the words."""

    def test_context_returns_the_lookup_command_and_vocabulary_path(self) -> None:
        self.init("--baseline-known", "6")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(9, context["vocabulary_count"])
        self.assertEqual(
            {"command", "vocabulary"}, set(context["lookup"]), "lookup carries exactly these"
        )
        self.assertEqual(str(self.lookup_bin.resolve()), context["lookup"]["command"])
        vocabulary = Path(context["lookup"]["vocabulary"])
        self.assertTrue(vocabulary.is_absolute())
        self.assertEqual(self.state.parent.resolve() / "vocabulary.txt", vocabulary.resolve())
        for removed in ("known", "known_manifest", "known_scope", "known_per_reply"):
            self.assertNotIn(removed, context)

    def test_context_fails_when_the_binary_is_missing(self) -> None:
        self.init()
        self.lookup_bin.unlink()
        failure = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00", ok=False)
        self.assertEqual(
            "ambient-lookup binary not found: run python3 scripts/install.py", failure["error"]
        )
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual({}, state["progress"]["pending_decisions"])

    def test_binary_resolves_from_the_environment_then_the_repo_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            elsewhere = Path(temp_dir) / "other-lookup"
            elsewhere.write_text("#!/bin/sh\n", encoding="utf-8")
            previous = os.environ.get("AMBIENT_LOOKUP_BIN")
            self.addCleanup(
                lambda: os.environ.pop("AMBIENT_LOOKUP_BIN", None)
                if previous is None
                else os.environ.__setitem__("AMBIENT_LOOKUP_BIN", previous)
            )
            os.environ["AMBIENT_LOOKUP_BIN"] = str(elsewhere)
            self.assertEqual(elsewhere.resolve(), AMBIENT_STATE._lookup_binary())
            os.environ["AMBIENT_LOOKUP_BIN"] = str(Path(temp_dir) / "absent")
            self.assertIsNone(AMBIENT_STATE._lookup_binary())
            del os.environ["AMBIENT_LOOKUP_BIN"]
            self.assertEqual(
                AMBIENT_STATE.DEFAULT_LOOKUP_BIN,
                ROOT / "rust" / "ambient-lookup" / "target" / "release" / "ambient-lookup",
            )

    def test_an_inactive_context_needs_no_binary(self) -> None:
        self.init()
        self.run_cli("configure", "--pause", "--now", "2026-08-15T09:30:00+02:00")
        self.lookup_bin.unlink()
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertFalse(context["active"])
        self.assertIsNone(context["lookup"])

    def test_status_reports_the_binary_or_null_without_failing(self) -> None:
        self.init()
        present = self.run_cli("status", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(str(self.lookup_bin.resolve()), present["lookup_binary"])
        self.lookup_bin.unlink()
        absent = self.run_cli("status", "--now", "2026-08-15T10:00:00+02:00")
        self.assertIsNone(absent["lookup_binary"])
        self.assertEqual(3, absent["vocabulary_count"])

    def test_density_flags_are_gone(self) -> None:
        for command in ("init", "configure"):
            failure = self.run_raw(
                command, "--known-per-reply", "4", "--now", "2026-08-15T10:00:00+02:00"
            )
            self.assertNotEqual(0, failure.returncode)
            self.assertIn("--known-per-reply", failure.stderr)


class VocabularyFileTests(CliTestCase):
    """vocabulary.txt holds the whole vocabulary, for the lookup binary and the hover mod."""

    def test_file_lists_every_known_term_and_is_stable(self) -> None:
        self.init("--baseline-known", "6")
        self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        manifest = self.state.parent / "vocabulary.txt"
        text = manifest.read_text(encoding="utf-8")
        for index in range(9):
            self.assertIn(f"t{index:02d} | palabra{index:02d} | word{index:02d}", text)
        self.assertNotIn("t09 |", text)
        written = manifest.stat().st_mtime_ns
        self.run_cli("context", "--now", "2026-08-15T11:00:00+02:00")
        self.assertEqual(written, manifest.stat().st_mtime_ns)
        self.assertEqual(text, manifest.read_text(encoding="utf-8"))

    def test_file_is_rewritten_when_a_batch_promotes(self) -> None:
        self.init("--baseline-known", "6")
        self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(9, len(self.vocabulary_ids()))
        self.run_cli("context", "--now", "2026-08-22T10:00:00+02:00")
        self.assertEqual(12, len(self.vocabulary_ids()))

    def test_any_vocabulary_term_can_be_recorded(self) -> None:
        self.init("--baseline-known", "6")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        recorded = self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t00,t05,t08",
            "--now",
            "2026-08-15T10:05:00+02:00",
        )
        self.assertEqual(["t00", "t05", "t08"], recorded["recorded"]["used"])

    def test_record_accepts_the_spanish_words_the_lookup_prints(self) -> None:
        self.init("--baseline-known", "6")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        recorded = self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "palabra00,PALABRA05,t08,palabra00",
            "--now",
            "2026-08-15T10:05:00+02:00",
        )
        self.assertEqual(["t00", "t05", "t08"], recorded["recorded"]["used"])

    def test_terms_beyond_the_current_vocabulary_are_rejected(self) -> None:
        self.init("--baseline-known", "6", "--words-per-week", "2")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        failure = self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t11",
            "--now",
            "2026-08-15T10:05:00+02:00",
            ok=False,
        )
        self.assertIn("not part of the reserved active set", failure["error"])

    def test_decision_is_scoped_not_a_list_of_ids(self) -> None:
        self.init("--baseline-known", "6")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        decision = json.loads(self.state.read_text(encoding="utf-8"))["progress"][
            "pending_decisions"
        ][context["decision_id"]]
        self.assertEqual({"scope", "created_at"}, set(decision))
        self.assertEqual("known_all", decision["scope"])

    def test_an_old_term_ids_decision_is_still_honoured(self) -> None:
        self.init()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["progress"]["pending_decisions"] = {
            "d_old": {"term_ids": ["t00", "t01"], "created_at": "2026-08-15T10:00:00+02:00"}
        }
        self.write_state(state)
        recorded = self.run_cli(
            "record",
            "--decision",
            "d_old",
            "--used",
            "t01",
            "--now",
            "2026-08-15T10:05:00+02:00",
        )
        self.assertEqual(["t01"], recorded["recorded"]["used"])


class V6MigrationTests(CliTestCase):
    def v5_state(self) -> dict:
        return {
            "schema_version": 5,
            "config": {
                "timezone": "Europe/Madrid",
                "dialect": "es-ES",
                "cadence_days": 3,
                "batch_size": 3,
                "known_per_reply": 12,
                "baseline_known_count": 3,
                "paused": False,
                "start_date": "2026-08-15",
            },
            "progress": {
                "last_exposure_at": "2026-08-15T10:00:00+02:00",
                "pending_decisions": {
                    "d_scoped": {"scope": "known_all", "created_at": "2026-08-15T10:30:00+02:00"},
                    "d_listed": {"term_ids": ["t00"], "created_at": "2026-08-15T10:31:00+02:00"},
                },
                "terms": {
                    "t00": {
                        "introduced_at": "2026-08-15T09:00:00+02:00",
                        "last_used_at": "2026-08-15T10:00:00+02:00",
                        "use_count": 7,
                    }
                },
                "offers": {
                    "t00": {"count": 9, "last_offered_at": "2026-08-15T10:00:00+02:00"},
                    "t01": {"count": 4, "last_offered_at": "2026-08-15T10:00:00+02:00"},
                },
            },
            "created_at": "2026-08-15T09:00:00+02:00",
            "updated_at": "2026-08-15T10:30:00+02:00",
        }

    def test_v5_drops_the_cap_offers_and_pending_decisions(self) -> None:
        source = self.v5_state()
        self.write_state(source)
        context = self.run_cli("context", "--now", "2026-08-16T11:00:00+02:00")
        self.assertEqual(6, context["schema_version"])
        migrated = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(6, migrated["schema_version"])
        self.assertNotIn("known_per_reply", migrated["config"])
        self.assertNotIn("offers", migrated["progress"])
        self.assertEqual(
            [context["decision_id"]], list(migrated["progress"]["pending_decisions"])
        )
        self.assertEqual(7, migrated["progress"]["terms"]["t00"]["use_count"])
        self.assertEqual(3, migrated["config"]["batch_size"])

    def test_v5_original_is_preserved_as_a_backup(self) -> None:
        source = self.v5_state()
        self.write_state(source)
        self.run_cli("status", "--now", "2026-08-16T11:00:00+02:00")
        backup = self.state.with_name(f"{self.state.name}.schema-v5.backup")
        self.assertEqual(source, json.loads(backup.read_text(encoding="utf-8")))

    def test_v4_migrates_through_v5_to_v6(self) -> None:
        source = self.v5_state()
        source["schema_version"] = 4
        del source["progress"]["offers"]
        source["progress"]["pending_decisions"] = {}
        self.write_state(source)
        context = self.run_cli("context", "--now", "2026-08-16T11:00:00+02:00")
        self.assertEqual(6, context["schema_version"])
        migrated = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertNotIn("known_per_reply", migrated["config"])
        self.assertNotIn("offers", migrated["progress"])
        self.assertTrue(
            self.state.with_name(f"{self.state.name}.schema-v4.backup").exists()
        )

    def test_a_fresh_init_writes_v6_without_the_old_fields(self) -> None:
        self.run_cli("init", "--now", "2026-08-15T09:00:00+02:00")
        document = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertNotIn("known_per_reply", document["config"])
        self.assertNotIn("offers", document["progress"])
        self.assertEqual(6, document["schema_version"])


class DecisionHousekeepingTests(CliTestCase):
    def test_unconsumed_decisions_are_evicted_instead_of_failing(self) -> None:
        self.init()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["progress"]["pending_decisions"] = {
            f"d_stale{index:03d}": {
                "term_ids": ["t00"],
                "created_at": f"2026-08-15T10:00:{index % 60:02d}.{index:03d}000+02:00",
            }
            for index in range(AMBIENT_STATE.MAX_PENDING_DECISIONS)
        }
        self.write_state(state)
        context = self.run_cli("context", "--now", "2026-08-15T11:00:00+02:00")
        self.assertTrue(context["active"])
        pending = json.loads(self.state.read_text(encoding="utf-8"))["progress"][
            "pending_decisions"
        ]
        self.assertEqual(AMBIENT_STATE.MAX_PENDING_DECISIONS, len(pending))
        self.assertNotIn("d_stale000", pending)
        self.assertIn(context["decision_id"], pending)

    def test_expired_decisions_are_pruned(self) -> None:
        self.init()
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["progress"]["pending_decisions"] = {
            "d_old": {
                "term_ids": ["t00"],
                "created_at": "2026-08-15T10:00:00+02:00",
            }
        }
        self.write_state(state)
        self.run_cli("context", "--now", "2026-08-17T10:00:00+02:00")
        pending = json.loads(self.state.read_text(encoding="utf-8"))["progress"][
            "pending_decisions"
        ]
        self.assertNotIn("d_old", pending)
        self.assertEqual(1, len(pending))


# Latin-American forms that must not appear in an es-ES curriculum, mapped to
# the Peninsular equivalent the entry should use instead.
NON_PENINSULAR = {
    "computadora": "ordenador",
    "computador": "ordenador",
    "celular": "móvil",
    "carro": "coche",
    "auto": "coche",
    "papa": "patata",
    "jugo": "zumo",
    "frijoles": "judías",
    "palta": "aguacate",
    "durazno": "melocotón",
    "refrigerador": "nevera",
    "refrigeradora": "nevera",
    "departamento": "piso",
    "boleto": "billete",
    "platicar": "charlar",
    "manejar": "conducir",
    "rentar": "alquilar",
    "elevador": "ascensor",
    "estacionamiento": "aparcamiento",
    "remera": "camiseta",
    "lentes": "gafas",
    "arete": "pendiente",
    "chamarra": "cazadora",
    "cuadra": "manzana",
    "banqueta": "acera",
    "ahorita": "ahora",
    "enojarse": "enfadarse",
    "lindo": "bonito",
    "chévere": "guay",
    "chido": "guay",
    "jalar": "tirar",
    "botar": "tirar",
    "saco": "chaqueta",
    "apurarse": "darse prisa",
}


class ShippedCurriculumTests(unittest.TestCase):
    def test_shipped_curriculum_loads(self) -> None:
        curriculum = AMBIENT_STATE._load_curriculum(REAL_CURRICULUM)
        self.assertGreaterEqual(len(curriculum), 12)
        self.assertEqual(len({term["id"] for term in curriculum}), len(curriculum))

    def test_shipped_curriculum_is_peninsular(self) -> None:
        curriculum = AMBIENT_STATE._load_curriculum(REAL_CURRICULUM)
        offenders = [
            f"{term['spanish']} (use {NON_PENINSULAR[term['spanish']]} instead)"
            for term in curriculum
            if term["spanish"] in NON_PENINSULAR
        ]
        self.assertEqual([], offenders, f"non-Peninsular entries: {offenders}")

    def test_prose_fields_only_name_latin_american_forms_to_contrast_them(self) -> None:
        """A LatAm form may appear in `english`/`usage` only as an explicit
        contrast on the entry that teaches its Peninsular equivalent — never as
        the form the entry recommends."""
        curriculum = AMBIENT_STATE._load_curriculum(REAL_CURRICULUM)
        offenders = []
        for term in curriculum:
            for field in ("english", "usage"):
                for bad, good in NON_PENINSULAR.items():
                    if not re.search(rf"\b{re.escape(bad)}\b", term[field]):
                        continue
                    contrasting = term["spanish"] == good or good in term[field]
                    if not contrasting:
                        offenders.append(f"{term['id']}.{field}: {bad}")
        self.assertEqual([], sorted(set(offenders)))

    def test_missing_state_bootstraps_with_batch_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "state.json"
            command = [
                sys.executable,
                str(SCRIPT),
                "context",
                "--state",
                str(state_path),
                "--curriculum",
                str(REAL_CURRICULUM),
            ]
            dummy = Path(temp_dir) / "ambient-lookup"
            dummy.write_text("#!/bin/sh\n", encoding="utf-8")
            dummy.chmod(0o755)
            environment = {**os.environ, "AMBIENT_LOOKUP_BIN": str(dummy)}
            result = subprocess.run(
                command, text=True, capture_output=True, check=True, env=environment
            )
            context = json.loads(result.stdout)
            self.assertEqual(6, context["schema_version"])
            self.assertEqual(AMBIENT_STATE.DEFAULT_CADENCE_DAYS, context["cadence_days"])
            self.assertEqual(AMBIENT_STATE.DEFAULT_BATCH_SIZE, context["words_per_week"])
            self.assertNotIn("known_per_reply", context)
            self.assertEqual(0, context["batch_index"])
            self.assertEqual(AMBIENT_STATE.DEFAULT_BATCH_SIZE, context["vocabulary_count"])


if __name__ == "__main__":
    unittest.main()
