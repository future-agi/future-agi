"""Weight floors for known long tests: raise only, one place, and a warning when a floored test is gone."""

import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "futureagi/.ci/duration_floors.py"
FLOORS = ROOT / "futureagi/.ci/test_duration_floors.json"
SPEC = importlib.util.spec_from_file_location("duration_floors", SCRIPT)
floors_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(floors_module)

LONG = "tracer/tests/test_long.py::test_pause_path[3200]"


def layout(document):
    """merge_test_durations.py's output layout."""
    return json.dumps(document, indent=4, sort_keys=True) + "\n"


def record_line(test):
    """One line of a collected_ids.py record (that plugin imports pytest)."""
    digest = hashlib.sha256(test.encode("utf-8", "surrogateescape")).hexdigest()
    return f"{digest[:24]} {test[:300]}\n"


class Files:
    def __init__(self, testcase):
        self.dir = tempfile.TemporaryDirectory(prefix="duration-floors-")
        testcase.addCleanup(self.dir.cleanup)
        self.root = pathlib.Path(self.dir.name)

    def text(self, name, content, errors="strict"):
        path = self.root / name
        path.write_text(content, encoding="utf-8", errors=errors)
        return path


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.files = Files(self)

    def apply(self, floors, durations):
        floors_path = self.files.text("floors.json", json.dumps(floors))
        durations_path = self.files.text(".test_durations", layout(durations))
        out = io.StringIO()
        floors_module.apply(floors_path, durations_path, out)
        return durations_path, out.getvalue()

    def test_a_short_last_sample_is_raised_to_the_floor_and_nothing_else_changes(self):
        durations = {LONG: 444.7225, "a::one": 1.5, "b::two": 0.0123}
        path, out = self.apply({LONG: 1230}, durations)
        self.assertEqual(path.read_text(), layout({**durations, LONG: 1230}))
        self.assertIn(f"raised {LONG}: 444.7225 s -> 1230 s\n", out)

    def test_a_longer_time_is_kept_and_the_file_is_not_rewritten(self):
        durations = {LONG: 1299.5, "a::one": 1.5}
        floors_path = self.files.text("floors.json", json.dumps({LONG: 1230}))
        # Not merge's layout on purpose: an untouched file keeps its bytes.
        original = json.dumps(durations)
        durations_path = self.files.text(".test_durations", original)
        out = io.StringIO()
        floors_module.apply(floors_path, durations_path, out)
        self.assertEqual(durations_path.read_text(), original)
        self.assertIn(f"kept {LONG}: 1299.5 s (floor 1230 s)\n", out.getvalue())

    def test_an_equal_time_is_kept(self):
        _, out = self.apply({LONG: 1230}, {LONG: 1230.0})
        self.assertIn("kept ", out)

    def test_a_test_without_a_time_is_not_added_and_says_so(self):
        # The timings merge starts from the plan, so an added entry would be
        # carried forever, even after the test is gone.
        durations = {"a::one": 1.5}
        path, out = self.apply({LONG: 1230}, durations)
        self.assertEqual(json.loads(path.read_text()), durations)
        self.assertIn(f"::warning title=Duration floors::{LONG} has no time", out)

    def test_applying_twice_gives_the_same_bytes(self):
        durations = {LONG: 444.7225, "a::one": 1.5}
        path, _ = self.apply({LONG: 1230}, durations)
        first = path.read_bytes()
        out = io.StringIO()
        floors_module.apply(self.files.root / "floors.json", path, out)
        self.assertEqual(path.read_bytes(), first)
        digest = hashlib.sha256(first).hexdigest()
        self.assertIn(f"planned .test_durations sha256 {digest}\n", out.getvalue())

    def test_an_id_with_a_lone_surrogate_round_trips(self):
        odd = "a::case[\udc80]"
        path, out = self.apply({odd: 30}, {odd: 2.0, "a::one": 1.5})
        self.assertEqual(json.loads(path.read_text())[odd], 30)
        self.assertTrue(path.read_text().isascii())
        self.assertIn("raised a::case[\\udc80]: 2.0 s -> 30 s\n", out)


class FloorsFileTest(unittest.TestCase):
    def setUp(self):
        self.files = Files(self)

    def test_the_committed_floors_load(self):
        floors = floors_module.load_floors(FLOORS)
        self.assertTrue(floors)
        self.assertTrue(all("::" in test for test in floors))

    def test_malformed_floors_are_refused(self):
        for content in (
            "[]",
            '{"a::one": 0}',
            '{"a::one": -5}',
            '{"a::one": true}',
            '{"a::one": "1230"}',
            '{"a::one": null}',
            '{"a::one": NaN}',
            '{"a::one": Infinity}',
            '{"": 10}',
            '{"a::one": 10, "a::one": 20}',
            "{",
        ):
            with self.subTest(content=content):
                path = self.files.text("floors.json", content)
                with self.assertRaises(ValueError):
                    floors_module.load_floors(path)

    def test_the_command_fails_on_malformed_floors_or_timings(self):
        good = self.files.text("good.json", json.dumps({LONG: 1230}))
        bad = self.files.text("bad.json", '{"a::one": 0}')
        timings = self.files.text(".test_durations", layout({LONG: 1.0}))
        not_times = self.files.text("not-times", json.dumps({LONG: "slow"}))
        for floors, durations in ((bad, timings), (good, not_times)):
            with self.subTest(floors=floors.name, durations=durations.name):
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(SCRIPT),
                        "apply",
                        str(floors),
                        str(durations),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertIn("duration floors: ", proc.stderr)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.files = Files(self)

    def check(self, floors, *records):
        floors_path = self.files.text("floors.json", json.dumps(floors))
        out = io.StringIO()
        floors_module.check(floors_path, [str(path) for path in records], out)
        return out.getvalue()

    def record(self, name, *tests):
        return self.files.text(
            name, "".join(record_line(test) for test in tests), "surrogateescape"
        )

    def test_a_collected_floor_is_silent(self):
        shard = self.record("one.txt", "a::one", LONG)
        self.assertEqual(self.check({LONG: 1230}, shard), "")

    def test_a_floor_no_shard_collected_is_named(self):
        shard = self.record("one.txt", "a::one")
        out = self.check({LONG: 1230}, shard)
        self.assertEqual(
            out,
            f"::warning title=Duration floors::{LONG} is not among the collected tests, "
            "so its floor no longer applies; update futureagi/.ci/test_duration_floors.json\n",
        )

    def test_any_shard_record_counts(self):
        first = self.record("one.txt", "a::one")
        second = self.record("two.txt", LONG)
        self.assertEqual(self.check({LONG: 1230}, first, second), "")

    def test_long_and_odd_ids_match_by_digest(self):
        giant = "a::case[" + "x" * 400 + "]"
        odd = "a::case[\udc80%]"
        shard = self.record("one.txt", giant, odd)
        self.assertEqual(self.check({giant: 60, odd: 60}, shard), "")

    def test_a_message_stays_on_one_line(self):
        shard = self.record("one.txt", "a::one")
        out = self.check({"a::two[50%\nx]": 60}, shard)
        self.assertEqual(out.count("\n"), 1)
        self.assertIn("a::two[50%25%0Ax]", out)

    def test_it_only_ever_warns(self):
        shard = self.record("one.txt", LONG)
        missing = self.files.root / "absent/collected-ids.txt"
        self.assertIn("no shard collection record", self.check({LONG: 1230}, missing))
        bad = self.files.text("bad.json", '{"a::one": 0}')
        for floors in (bad, self.files.root / "no-such.json"):
            with self.subTest(floors=floors.name):
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(SCRIPT),
                        "check",
                        str(floors),
                        str(shard),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(
                    "::warning title=Duration floors::could not check the floors: ",
                    proc.stdout,
                )


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        self.plan = self.workflow.split("  plan:\n", 1)[1].split("\n  test:\n", 1)[0]

    def test_floors_are_applied_once_in_the_plan_before_the_single_upload(self):
        # Every shard downloads the one test-durations-plan artifact and hashes
        # it into its receipt, so the shards keep one durations identity.
        apply = (
            "python3 -I futureagi/.ci/duration_floors.py apply "
            "futureagi/.ci/test_duration_floors.json futureagi/.test_durations"
        )
        self.assertEqual(self.workflow.count("duration_floors.py apply"), 1)
        self.assertIn(apply, self.plan)
        order = [
            "- name: Restore newest timing cache over the committed floor",
            "- name: Test CI asset verification and failure paths",
            apply,
            "name: test-durations-plan",
        ]
        positions = [self.plan.index(text) for text in order]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(self.plan.count("name: test-durations-plan"), 1)
        shard = self.workflow.split("\n  test:\n", 1)[1].split(
            "\n  saml-multiworker:", 1
        )[0]
        self.assertNotIn("duration_floors", shard)

    def test_the_collection_job_checks_the_floors_against_every_record(self):
        job = self.workflow.split("  collection:\n", 1)[1].split(
            "\n  terminal-parity:", 1
        )[0]
        self.assertIn("sparse-checkout: futureagi/.ci", job)
        self.assertIn(
            "python3 -I futureagi/.ci/duration_floors.py check "
            'futureagi/.ci/test_duration_floors.json "$RUNNER_TEMP"/collected/*/collected-ids.txt',
            job,
        )
        self.assertEqual(self.workflow.count("duration_floors.py check"), 1)


if __name__ == "__main__":
    unittest.main()
