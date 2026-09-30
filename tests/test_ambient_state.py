from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
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

    def run_cli(self, *args: str, ok: bool = True) -> dict:
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
        result = subprocess.run(
            command, text=True, capture_output=True, check=False, env=environment
        )
        if ok and result.returncode != 0:
            self.fail(
                f"command failed: {command}\nstdout={result.stdout}\nstderr={result.stderr}"
            )
        if not ok and result.returncode == 0:
            self.fail(f"command unexpectedly succeeded: {command}\nstdout={result.stdout}")
        return json.loads(result.stdout if result.returncode == 0 else result.stderr)

    def init(self, *extra: str, start: str = "2026-08-15") -> dict:
        return self.run_cli(
            "init", "--now", f"{start}T09:00:00+02:00", "--start-date", start, *extra
        )

    def write_state(self, document: dict) -> None:
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text(json.dumps(document), encoding="utf-8")

    @staticmethod
    def ids(terms: list[dict]) -> list[str]:
        return [term["id"] for term in terms]


class TierDerivationTests(CliTestCase):
    def test_first_batch_is_learning_and_nothing_is_known(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertTrue(context["active"])
        self.assertEqual("active", context["reason"])
        self.assertEqual(0, context["batch_index"])
        self.assertEqual([], self.ids(context["known"]))
        self.assertEqual(["t00", "t01", "t02"], self.ids(context["learning"]))
        self.assertTrue(
            all(term["gloss"] == "bracketed" for term in context["learning"])
        )
        self.assertEqual("2026-08-18", context["next_batch_date"])
        self.assertTrue(context["decision_id"].startswith("d_"))

    def test_batch_promotes_on_cadence_date(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-18T10:00:00+02:00")
        self.assertEqual(1, context["batch_index"])
        self.assertEqual(["t00", "t01", "t02"], self.ids(context["known"]))
        self.assertEqual(["t03", "t04", "t05"], self.ids(context["learning"]))
        self.assertTrue(all(term["gloss"] == "omit" for term in context["known"]))

    def test_no_promotion_before_the_cadence_date(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-17T23:59:00+02:00")
        self.assertEqual(0, context["batch_index"])
        self.assertEqual(["t00", "t01", "t02"], self.ids(context["learning"]))

    def test_promotion_happens_without_any_recorded_use(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-21T10:00:00+02:00")
        self.assertEqual(2, context["batch_index"])
        self.assertEqual(["t00", "t01", "t02", "t03", "t04", "t05"], self.ids(context["known"]))
        self.assertEqual(["t06", "t07", "t08"], self.ids(context["learning"]))

    def test_missed_time_does_not_queue_a_backlog(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-24T10:00:00+02:00")
        self.assertEqual(3, context["batch_index"])
        self.assertEqual(3, context["learning_count"])
        self.assertEqual(9, context["known_count"])
        self.assertEqual(["t09", "t10", "t11"], self.ids(context["learning"]))

    def test_baseline_known_offsets_the_first_batch(self) -> None:
        self.init("--baseline-known", "4")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(["t00", "t01", "t02", "t03"], self.ids(context["known"]))
        self.assertEqual(["t04", "t05", "t06"], self.ids(context["learning"]))

    def test_batch_size_is_configurable(self) -> None:
        self.init("--batch-size", "5")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(["t00", "t01", "t02", "t03", "t04"], self.ids(context["learning"]))

    def test_future_start_date_is_inactive(self) -> None:
        self.init(start="2026-09-01")
        context = self.run_cli("context", "--now", "2026-08-20T10:00:00+02:00")
        self.assertFalse(context["active"])
        self.assertEqual("before_start_date", context["reason"])
        self.assertEqual([], context["known"])
        self.assertEqual([], context["learning"])
        self.assertIsNone(context["decision_id"])

    def test_completed_curriculum_stays_active_with_no_learning_batch(self) -> None:
        self.init("--baseline-known", "12")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertTrue(context["active"])
        self.assertEqual("curriculum_complete", context["reason"])
        self.assertEqual(12, context["known_count"])
        self.assertEqual(0, context["learning_count"])
        self.assertIsNone(context["next_batch_date"])

    def test_running_past_the_curriculum_end_keeps_everything_known(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-09-15T10:00:00+02:00")
        self.assertTrue(context["active"])
        self.assertEqual("curriculum_complete", context["reason"])
        self.assertEqual(12, context["known_count"])

    def test_final_partial_batch_is_truncated(self) -> None:
        self.init("--baseline-known", "10")
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(["t10", "t11"], self.ids(context["learning"]))
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
        context = self.run_cli("context", "--now", "2026-08-18T10:00:00+02:00")
        self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t01,t04",
            "--now",
            "2026-08-18T10:05:00+02:00",
        )
        status = self.run_cli("status", "--now", "2026-08-18T10:10:00+02:00")
        self.assertEqual(["t01", "t04"], sorted(self.ids(status["used_terms"])))

    def test_history_need_not_form_a_curriculum_prefix(self) -> None:
        self.init()
        context = self.run_cli("context", "--now", "2026-08-21T10:00:00+02:00")
        self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t07",
            "--now",
            "2026-08-21T10:05:00+02:00",
        )
        status = self.run_cli("status", "--now", "2026-08-21T10:10:00+02:00")
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

    def test_unknown_and_duplicate_ids_are_rejected(self) -> None:
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
        duplicate = self.run_cli(
            "record",
            "--decision",
            context["decision_id"],
            "--used",
            "t00,t00",
            "--now",
            "2026-08-15T10:05:00+02:00",
            ok=False,
        )
        self.assertIn("duplicate", duplicate["error"])

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
    def test_configure_changes_cadence_and_batch_size(self) -> None:
        self.init()
        changed = self.run_cli(
            "configure",
            "--cadence-days",
            "1",
            "--batch-size",
            "2",
            "--now",
            "2026-08-15T09:30:00+02:00",
        )
        self.assertEqual({"cadence_days": 1, "batch_size": 2}, changed["changes"])
        context = self.run_cli("context", "--now", "2026-08-17T10:00:00+02:00")
        self.assertEqual(2, context["batch_index"])
        self.assertEqual(["t00", "t01", "t02", "t03"], self.ids(context["known"]))
        self.assertEqual(["t04", "t05"], self.ids(context["learning"]))

    def test_configure_baseline_known_shifts_the_tier_boundary(self) -> None:
        self.init()
        changed = self.run_cli(
            "configure", "--baseline-known", "6", "--now", "2026-08-15T09:30:00+02:00"
        )
        self.assertEqual({"baseline_known_count": 6}, changed["changes"])
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(
            ["t00", "t01", "t02", "t03", "t04", "t05"], self.ids(context["known"])
        )
        self.assertEqual(["t06", "t07", "t08"], self.ids(context["learning"]))

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
        for flag, value in (("--cadence-days", "0"), ("--batch-size", "0")):
            failure = self.run_cli(
                "configure", flag, value, "--now", "2026-08-15T09:30:00+02:00", ok=False
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
            "schema_version": 3,
            "config": {
                "timezone": "Europe/Madrid",
                "dialect": "es-ES",
                "cadence_days": 3,
                "batch_size": 3,
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
        self.assertEqual(3, context["schema_version"])
        self.assertEqual(0, context["batch_index"])
        self.assertEqual(["t00", "t01", "t02"], self.ids(context["known"]))
        self.assertEqual(["t03", "t04", "t05"], self.ids(context["learning"]))
        self.assertEqual("2026-08-18", context["next_batch_date"])

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

    def test_v1_migrates_through_to_v3(self) -> None:
        source = self.v2_state()
        source["schema_version"] = 1
        del source["config"]["exposure_percent"]
        source["progress"]["last_any_insertion_at"] = source["progress"].pop(
            "last_exposure_at"
        )
        del source["progress"]["pending_decisions"]
        self.write_state(source)
        context = self.run_cli("context", "--now", "2026-08-15T10:00:00+02:00")
        self.assertEqual(3, context["schema_version"])
        self.assertEqual(["t00", "t01", "t02"], self.ids(context["known"]))
        backup = self.state.with_name(f"{self.state.name}.schema-v1.backup")
        self.assertTrue(backup.exists())


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
            result = subprocess.run(command, text=True, capture_output=True, check=True)
            context = json.loads(result.stdout)
            self.assertEqual(3, context["schema_version"])
            self.assertEqual(AMBIENT_STATE.DEFAULT_CADENCE_DAYS, context["cadence_days"])
            self.assertEqual(AMBIENT_STATE.DEFAULT_BATCH_SIZE, context["batch_size"])
            self.assertEqual(0, context["batch_index"])
            self.assertEqual(0, context["known_count"])
            self.assertEqual(
                AMBIENT_STATE.DEFAULT_BATCH_SIZE, context["learning_count"]
            )


if __name__ == "__main__":
    unittest.main()
