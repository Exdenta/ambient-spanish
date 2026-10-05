from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_ambient_state import AMBIENT_STATE, NON_PENINSULAR, REAL_CURRICULUM, SCRIPT

ROOT = Path(__file__).resolve().parents[1]
REAL_LEXICON = ROOT / "references" / "levels" / "lexicon.tsv"
REAL_EXCLUDED = ROOT / "references" / "levels" / "excluded.tsv"

# Two to three words per level keep composition assertions readable.
FIXTURE_LEXICON_ROWS = [
    ("A0", "hola", "hola", "hello", "phrase", "Use as a greeting."),
    ("A0", "agua", "agua", "water", "noun", ""),
    ("A1", "casa", "casa", "house", "noun", ""),
    ("A1", "comer", "comer", "to eat", "verb", ""),
    ("A1", "grande", "grande", "big", "adjective", ""),
    ("A2", "coche", "coche", "car", "noun", ""),
    ("A2", "beber", "beber", "to drink", "verb", ""),
    ("A2", "rapido", "rápido", "fast", "adjective", ""),
    ("B1", "plazo", "plazo", "deadline", "noun", ""),
    ("B1", "lograr", "lograr", "to achieve", "verb", ""),
    ("B1", "fiable", "fiable", "reliable", "adjective", ""),
    ("B2", "a-medida-que", "a medida que", "as", "connector", ""),
    ("B2", "asequible", "asequible", "affordable", "adjective", ""),
    ("C1", "matiz", "matiz", "nuance", "noun", ""),
    ("C1", "umbral", "umbral", "threshold", "noun", ""),
]


def lexicon_text(rows: list[tuple[str, ...]]) -> str:
    lines = ["\t".join(AMBIENT_STATE.LEXICON_FIELDS)]
    lines.extend("\t".join(row) for row in rows)
    return "\n".join(lines) + "\n"


class VocabTestCase(unittest.TestCase):
    """Runs the CLI without `--curriculum`, so the personal curriculum is used."""

    now = "2026-10-05T09:00:00+02:00"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.state = self.root / "state.json"
        self.lexicon = self.root / "lexicon.tsv"
        self.lexicon.write_text(lexicon_text(FIXTURE_LEXICON_ROWS), encoding="utf-8")
        self.lookup_bin = self.root / "ambient-lookup"
        self.lookup_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.lookup_bin.chmod(0o755)

    def run_raw(self, *args: str, lexicon: bool = True) -> subprocess.CompletedProcess:
        command = [sys.executable, str(SCRIPT), *args]
        if args[0] != "levels":
            command += ["--state", str(self.state)]
        if lexicon and args[0] in ("levels", "vocab"):
            command += ["--lexicon", str(self.lexicon)]
        if args[0] != "levels" and "--now" not in args:
            command += ["--now", self.now]
        environment = os.environ.copy()
        environment["AMBIENT_SPANISH_ALLOW_TIME_OVERRIDE"] = "1"
        environment["AMBIENT_LOOKUP_BIN"] = str(self.lookup_bin)
        environment.pop("AMBIENT_SPANISH_CURRICULUM", None)
        environment.pop("AMBIENT_SPANISH_LEXICON", None)
        return subprocess.run(
            command, text=True, capture_output=True, check=False, env=environment
        )

    def run_cli(self, *args: str, ok: bool = True) -> dict:
        result = self.run_raw(*args)
        if ok and result.returncode != 0:
            self.fail(f"command failed: {args}\nstdout={result.stdout}\nstderr={result.stderr}")
        if not ok and result.returncode == 0:
            self.fail(f"command unexpectedly succeeded: {args}\nstdout={result.stdout}")
        return json.loads(result.stdout if result.returncode == 0 else result.stderr)

    def word_list(self, name: str, text: str) -> str:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def personal_curriculum(self) -> list[dict]:
        return json.loads((self.root / "curriculum.json").read_text(encoding="utf-8"))

    def spanish(self, terms: list[dict]) -> list[str]:
        return [term["spanish"] for term in terms]

    def read_state(self) -> dict:
        return json.loads(self.state.read_text(encoding="utf-8"))


class ShippedLexiconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.packs = AMBIENT_STATE._load_lexicon(REAL_LEXICON)
        cls.terms = [term for level in AMBIENT_STATE.LEVELS for term in cls.packs[level]]

    def test_every_level_has_words(self) -> None:
        for level in AMBIENT_STATE.LEVELS:
            self.assertGreater(len(self.packs[level]), 100, level)

    def test_each_spanish_form_appears_once(self) -> None:
        keys = [AMBIENT_STATE._word_key(term["spanish"]) for term in self.terms]
        self.assertEqual(len(keys), len(set(keys)))

    def test_lexicon_is_peninsular(self) -> None:
        offenders = [term["spanish"] for term in self.terms if term["spanish"] in NON_PENINSULAR]
        self.assertEqual([], offenders)

    def test_every_curated_term_is_graded_under_its_own_id(self) -> None:
        by_spanish = {term["spanish"]: term["id"] for term in self.terms}
        curated = AMBIENT_STATE._load_curriculum(REAL_CURRICULUM)
        mismatched = [
            term["spanish"] for term in curated if by_spanish.get(term["spanish"]) != term["id"]
        ]
        self.assertEqual([], mismatched)

    def test_excluded_words_stay_out(self) -> None:
        lines = REAL_EXCLUDED.read_text(encoding="utf-8").splitlines()[1:]
        excluded = {line.split("\t")[0] for line in lines if line}
        present = sorted(excluded & {term["spanish"] for term in self.terms})
        self.assertEqual([], present)

    def test_single_function_words_are_never_offered(self) -> None:
        function_words = {
            "el", "la", "los", "las", "un", "una", "de", "en", "con", "para",
            "por", "a", "y", "o", "que", "se", "su", "lo", "le", "del", "al",
        }  # fmt: skip
        present = sorted(function_words & {term["spanish"] for term in self.terms})
        self.assertEqual([], present)


class CurriculumResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state = Path(self.temp_dir.name) / "state.json"
        self.previous = os.environ.pop("AMBIENT_SPANISH_CURRICULUM", None)
        self.addCleanup(self.restore_environment)

    def restore_environment(self) -> None:
        os.environ.pop("AMBIENT_SPANISH_CURRICULUM", None)
        if self.previous is not None:
            os.environ["AMBIENT_SPANISH_CURRICULUM"] = self.previous

    def test_shipped_curriculum_without_a_personal_one(self) -> None:
        path, source = AMBIENT_STATE._curriculum_source(None, self.state)
        self.assertEqual((AMBIENT_STATE.DEFAULT_CURRICULUM_PATH, "shipped"), (path, source))

    def test_personal_curriculum_beside_the_state_wins_over_shipped(self) -> None:
        personal = self.state.parent / "curriculum.json"
        personal.write_text("[]", encoding="utf-8")
        self.assertEqual(
            (personal, "user"), AMBIENT_STATE._curriculum_source(None, self.state)
        )

    def test_flag_and_environment_win_over_personal(self) -> None:
        (self.state.parent / "curriculum.json").write_text("[]", encoding="utf-8")
        override = Path(self.temp_dir.name) / "other.json"
        os.environ["AMBIENT_SPANISH_CURRICULUM"] = str(override)
        self.assertEqual("env", AMBIENT_STATE._curriculum_source(None, self.state)[1])
        self.assertEqual("flag", AMBIENT_STATE._curriculum_source(str(override), self.state)[1])


class LevelsCommandTests(VocabTestCase):
    def test_counts_are_per_level_and_cumulative(self) -> None:
        levels = self.run_cli("levels")["levels"]
        self.assertEqual(["A0", "A1", "A2", "B1", "B2", "C1"], [row["level"] for row in levels])
        self.assertEqual([2, 3, 3, 3, 2, 2], [row["new_terms"] for row in levels])
        self.assertEqual([2, 5, 8, 11, 13, 15], [row["known_if_chosen"] for row in levels])

    def test_sample_draws_from_the_level_itself(self) -> None:
        levels = self.run_cli("levels", "--sample", "2", "--seed", "4")["levels"]
        a1 = {"casa", "comer", "grande"}
        self.assertEqual(2, len(levels[1]["sample"]))
        self.assertTrue({term["spanish"] for term in levels[1]["sample"]} <= a1)
        again = self.run_cli("levels", "--sample", "2", "--seed", "4")["levels"]
        self.assertEqual(levels[1]["sample"], again[1]["sample"])


class VocabCompositionTests(VocabTestCase):
    def test_level_makes_everything_up_to_it_known(self) -> None:
        result = self.run_cli("vocab", "--level", "a1", "--words-per-week", "3")
        curriculum = self.personal_curriculum()
        self.assertEqual(5, result["known_count"])
        self.assertEqual(
            ["hola", "agua", "casa", "comer", "grande"], self.spanish(curriculum[:5])
        )
        self.assertEqual(3, result["words_per_week"])
        self.assertNotIn("learning", result)
        self.assertEqual(["coche", "beber", "rápido"], self.spanish(result["new_this_week"]))
        state = self.read_state()
        self.assertEqual(5, state["config"]["baseline_known_count"])
        self.assertEqual(3, state["config"]["batch_size"])
        self.assertEqual(7, state["config"]["cadence_days"])
        self.assertEqual("2026-10-05", state["config"]["start_date"])

    def test_context_and_status_follow_the_personal_curriculum(self) -> None:
        self.run_cli("vocab", "--level", "A2", "--words-per-week", "3")
        context = self.run_cli("context")
        # Eight baseline words plus the first weekly batch, available immediately.
        self.assertEqual(11, context["vocabulary_count"])
        self.assertNotIn("learning", context)
        self.run_cli("record", "--decision", context["decision_id"], "--used", "plazo,casa")
        status = self.run_cli("status")
        self.assertEqual("user", status["curriculum"]["source"])
        self.assertEqual("A2", status["curriculum"]["build"]["level"])
        manifest = (self.root / "vocabulary.txt").read_text(encoding="utf-8")
        self.assertIn("11 terms", manifest)
        self.assertIn("plazo", manifest)

    def test_words_per_week_defaults_and_can_be_changed_later(self) -> None:
        first = self.run_cli("vocab", "--level", "A0")
        self.assertEqual(AMBIENT_STATE.DEFAULT_BATCH_SIZE, first["words_per_week"])
        self.assertEqual(AMBIENT_STATE.DEFAULT_BATCH_SIZE, len(first["new_this_week"]))
        again = self.run_cli("vocab", "--level", "A0", "--words-per-week", "2")
        self.assertEqual(2, again["words_per_week"])
        self.assertEqual(2, len(again["new_this_week"]))
        self.assertEqual(2, self.read_state()["config"]["batch_size"])

    def test_dry_run_writes_nothing(self) -> None:
        result = self.run_cli("vocab", "--level", "B1", "--dry-run")
        self.assertTrue(result["dry_run"])
        self.assertEqual(11, result["known_count"])
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / "curriculum.json").exists())

    def test_added_words_resolve_by_article_accent_and_custom_gloss(self) -> None:
        added = self.word_list(
            "add.txt",
            "# words I know\nel coche\nrapido\nmolar | to be cool | verb\n",
        )
        result = self.run_cli("vocab", "--level", "A1", "--add-known", added, "--words-per-week", "3")
        known = self.spanish(self.personal_curriculum()[: result["known_count"]])
        self.assertEqual(
            ["hola", "agua", "casa", "comer", "grande", "coche", "rápido", "molar"], known
        )
        custom = self.personal_curriculum()[7]
        self.assertEqual(("molar", "verb", "to be cool"), (custom["id"], custom["kind"], custom["english"]))
        self.assertEqual(["beber", "plazo", "lograr"], self.spanish(result["new_this_week"]))

    def test_json_word_lists_are_accepted(self) -> None:
        added = self.word_list(
            "add.json",
            json.dumps(["plazo", {"spanish": "guay", "english": "cool", "kind": "adj"}]),
        )
        result = self.run_cli("vocab", "--level", "none", "--add-known", added)
        self.assertEqual(
            ["plazo", "guay"], self.spanish(self.personal_curriculum()[: result["known_count"]])
        )

    def test_unglossed_unknown_words_fail_without_writing(self) -> None:
        added = self.word_list("add.txt", "casa\nzzzpalabra\n")
        error = self.run_cli("vocab", "--level", "A1", "--add-known", added, ok=False)
        self.assertEqual(
            [{"list": "add-known", "problem": "missing english, kind", "spanish": "zzzpalabra"}],
            error["unresolved"],
        )
        self.assertFalse(self.state.exists())

    def test_removed_and_learn_first_words_lead_the_queue(self) -> None:
        learn = self.word_list("learn.txt", "umbral\ncomer\n")
        removed = self.word_list("removed.txt", "casa\nnoexiste\n")
        result = self.run_cli(
            "vocab", "--level", "A1", "--learn-first", learn, "--remove-known", removed,
            "--words-per-week", "3",
        )
        self.assertEqual(3, result["known_count"])
        self.assertEqual(["umbral", "comer", "casa"], self.spanish(result["new_this_week"]))
        self.assertEqual(["noexiste"], result["not_found"])

    def test_a_word_both_known_and_unknown_is_rejected(self) -> None:
        added = self.word_list("add.txt", "plazo\n")
        learn = self.word_list("learn.txt", "plazo\n")
        error = self.run_cli(
            "vocab", "--level", "A0", "--add-known", added, "--learn-first", learn, ok=False
        )
        self.assertEqual(["plazo"], error["conflicts"])

    def test_a_starting_point_is_required(self) -> None:
        error = self.run_cli("vocab", ok=False)
        self.assertIn("starting point", error["error"])

    def test_curriculum_overrides_are_refused(self) -> None:
        result = self.run_raw("vocab", "--level", "A1", "--curriculum", str(self.lexicon))
        self.assertEqual(2, result.returncode)
        self.assertIn("personal curriculum", json.loads(result.stderr)["error"])


class VocabHistoryTests(VocabTestCase):
    def use(self, *term_ids: str, now: str) -> None:
        context = self.run_cli("context", "--now", now)
        self.run_cli(
            "record", "--decision", context["decision_id"], "--used", ",".join(term_ids),
            "--now", now,
        )

    def test_keep_known_preserves_the_vocabulary_and_queue_order(self) -> None:
        self.run_cli("vocab", "--level", "A1", "--words-per-week", "3")
        added = self.word_list("add.txt", "matiz\n")
        result = self.run_cli(
            "vocab", "--keep-known", "--add-known", added, "--now", "2026-10-12T09:00:00+02:00"
        )
        # A week in, the first batch (coche/beber/rápido) has joined the vocabulary
        # and the second (plazo/lograr/fiable) is current; keep-known carries over
        # the first and `matiz`, and the current batch is re-added by the rebuild.
        self.assertEqual(9, result["known_count"])
        self.assertEqual("matiz", self.personal_curriculum()[8]["spanish"])
        self.assertEqual(["plazo", "lograr", "fiable"], self.spanish(result["new_this_week"]))

    def test_repeated_keep_known_rebuilds_do_not_grow_the_vocabulary(self) -> None:
        self.run_cli("vocab", "--level", "A1", "--words-per-week", "3")
        for _ in range(3):
            self.run_cli("vocab", "--keep-known", "--now", "2026-10-12T09:00:00+02:00")
            context = self.run_cli("context", "--now", "2026-10-12T10:00:00+02:00")
            self.assertEqual(11, context["vocabulary_count"])
        manifest = (self.root / "vocabulary.txt").read_text(encoding="utf-8")
        self.assertIn("# ambient-spanish vocabulary — 11 terms", manifest)

    def test_keep_known_with_a_future_start_date_unlocks_nothing_early(self) -> None:
        self.run_cli("init", "--start-date", "2026-11-01", "--words-per-week", "3")
        self.run_cli("vocab", "--level", "A1", "--now", "2026-10-05T09:00:00+02:00")
        self.assertEqual("2026-11-01", self.read_state()["config"]["start_date"])
        self.run_cli("vocab", "--keep-known", "--now", "2026-10-06T09:00:00+02:00")
        state = self.read_state()
        self.assertEqual("2026-11-01", state["config"]["start_date"])
        self.assertEqual(5, state["config"]["baseline_known_count"])
        status = self.run_cli("status", "--now", "2026-10-06T10:00:00+02:00")
        self.assertEqual(0, status["vocabulary_count"])

    def test_vocabulary_file_holds_the_live_vocabulary(self) -> None:
        result = self.run_cli("vocab", "--level", "A1", "--words-per-week", "3")
        self.assertEqual(8, result["known_manifest"]["count"])
        lines = (self.root / "vocabulary.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual("# ambient-spanish vocabulary — 8 terms", lines[0])
        self.assertEqual(8, len([line for line in lines if " | " in line]))

    def test_a_used_word_the_new_vocabulary_drops_stays_known(self) -> None:
        added = self.word_list("add.txt", "molar | to be cool | verb\n")
        self.run_cli("vocab", "--level", "A0", "--add-known", added)
        self.use("molar", "agua", now="2026-10-05T10:00:00+02:00")
        result = self.run_cli("vocab", "--level", "A2", "--now", "2026-10-06T09:00:00+02:00")
        self.assertEqual(["molar"], result["history"]["kept_as_known"])
        known = self.spanish(self.personal_curriculum()[: result["known_count"]])
        self.assertEqual("molar", known[-1])
        self.assertEqual(1, self.read_state()["progress"]["terms"]["molar"]["use_count"])

    def test_history_follows_a_word_whose_id_changed(self) -> None:
        self.run_cli("vocab", "--level", "A1")
        self.use("casa", now="2026-10-05T10:00:00+02:00")
        rows = [
            ("A1", "casa-hogar", *row[2:]) if row[1] == "casa" else row
            for row in FIXTURE_LEXICON_ROWS
        ]
        self.lexicon.write_text(lexicon_text(rows), encoding="utf-8")
        result = self.run_cli("vocab", "--level", "A1", "--now", "2026-10-06T09:00:00+02:00")
        self.assertEqual([{"from": "casa", "to": "casa-hogar"}], result["history"]["moved"])
        terms = self.read_state()["progress"]["terms"]
        self.assertNotIn("casa", terms)
        self.assertEqual(1, terms["casa-hogar"]["use_count"])

    def test_rebuild_drops_pending_decisions(self) -> None:
        self.run_cli("vocab", "--level", "A1")
        self.run_cli("context", "--now", "2026-10-05T10:00:00+02:00")
        self.assertTrue(self.read_state()["progress"]["pending_decisions"])
        self.run_cli("vocab", "--level", "A0", "--now", "2026-10-05T11:00:00+02:00")
        state = self.read_state()
        self.assertEqual({}, state["progress"]["pending_decisions"])
        self.assertNotIn("offers", state["progress"])
        self.assertTrue((self.root / "state.json.previous").exists())
        self.assertTrue((self.root / "curriculum.json.previous").exists())


if __name__ == "__main__":
    unittest.main()
