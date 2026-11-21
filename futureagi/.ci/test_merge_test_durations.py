"""The shard timing merge, and the workflow steps that keep recorded times free of the migrate."""

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "futureagi/.ci/merge_test_durations.py"
SPEC = importlib.util.spec_from_file_location("merge_test_durations", SCRIPT)
merger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merger)


class MergeTest(unittest.TestCase):
    def test_each_test_takes_the_time_of_the_shard_that_ran_it(self):
        plan = {"a::one": 1.0, "a::two": 2.0, "b::three": 3.0, "c::gone": 9.0}
        first = {**plan, "a::one": 1.5, "d::new": 4.0}
        second = {**plan, "b::three": 0.5}
        merged, measured_by = merger.merge(plan, [first, second])
        self.assertEqual(measured_by, {"a::one": 0, "d::new": 0, "b::three": 1})
        self.assertEqual(
            merged,
            {
                "a::one": 1.5,
                "a::two": 2.0,
                "b::three": 0.5,
                "c::gone": 9.0,
                "d::new": 4.0,
            },
        )

    def test_every_measured_test_is_kept_and_only_giant_ids_are_left_out(self):
        # A test without a time gets its suite's average from pytest-split, so
        # the saved timings hold the near-instant tests too.
        giant = "a::case[" + "x" * merger.MAX_ID_LENGTH + "]"
        times = {"a::instant": 0.0012345, giant: 5.0, "a::long": 1.23456789}
        self.assertEqual(merger.tidy(times), {"a::instant": 0.0012, "a::long": 1.2346})

    def test_the_compact_form_keeps_the_slow_tests_and_the_whole_suites(self):
        times = {
            "main::instant": merger.MIN_SECONDS / 2,
            "main::edge": merger.MIN_SECONDS,
            "main::slow": 3.0,
            "metrics::instant": merger.MIN_SECONDS / 2,
        }
        self.assertEqual(sorted(merger.compact(times)), ["main::edge", "main::slow"])
        self.assertEqual(
            sorted(merger.compact(times, whole={"metrics::instant"})),
            ["main::edge", "main::slow", "metrics::instant"],
        )

    def test_two_shards_that_disagree_keep_the_longer_time(self):
        merged, by = merger.merge({"a::one": 1.0}, [{"a::one": 2.0}, {"a::one": 3.0}])
        self.assertEqual((merged, by), ({"a::one": 3.0}, {"a::one": 1}))
        merged, by = merger.merge({"a::one": 1.0}, [{"a::one": 3.0}, {"a::one": 2.0}])
        self.assertEqual((merged, by), ({"a::one": 3.0}, {"a::one": 0}))

    def test_the_committed_fallback_is_compact_and_clean(self):
        path = ROOT / "futureagi/.test_durations"
        floor = json.loads(path.read_text())
        self.assertGreater(len(floor), 1000)
        self.assertLessEqual(max(len(test) for test in floor), merger.MAX_ID_LENGTH)
        # Compact: far fewer than the 70,000 tests of the suite, and a few megabytes.
        self.assertLess(len(floor), 30_000)
        self.assertLess(path.stat().st_size, 4_000_000)
        # The Metrics suite is whole, so it holds near-instant tests.
        instant = [
            test for test, seconds in floor.items() if seconds < merger.MIN_SECONDS
        ]
        self.assertGreater(len(instant), 1000)
        # No test of the fallback carries a from-zero migrate (about 330 s, on tests that take milliseconds).
        self.assertLess(
            floor.get(
                "accounts/tests/test_2fa_enforcement.py::TestOrgTwoFactorEnforcement::test_enable_2fa_policy",
                0,
            ),
            60,
        )


class CommandLineTest(unittest.TestCase):
    def run_merge(self, *documents, options=()):
        with tempfile.TemporaryDirectory(prefix="merge-durations-test-") as tmp:
            paths = []
            for index, document in enumerate(documents):
                path = pathlib.Path(tmp) / f"{index}.json"
                path.write_text(
                    document if isinstance(document, str) else json.dumps(document)
                )
                paths.append(str(path))
            return subprocess.run(
                [sys.executable, "-I", str(SCRIPT), *options, *paths],
                capture_output=True,
                text=True,
            )

    def test_it_writes_the_merged_file_to_stdout(self):
        proc = self.run_merge({"a::one": 1.0}, {"a::one": 2.0, "a::two": 0.5})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"a::one": 2.0, "a::two": 0.5})

    def test_the_saved_timings_hold_every_test_and_the_compact_form_does_not(self):
        plan = {"a::slow": 1.0}
        shards = (
            {"a::slow": 2.0, "a::instant": 0.001},
            {"a::slow": 1.0, "m::instant": 0.002},
        )
        proc = self.run_merge(plan, *shards)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout),
            {"a::slow": 2.0, "a::instant": 0.001, "m::instant": 0.002},
        )
        proc = self.run_merge(plan, *shards, options=("--compact",))
        self.assertEqual(json.loads(proc.stdout), {"a::slow": 2.0})
        # The second shard file is 2.json: its tests stay whole.
        proc = self.run_merge(plan, *shards, options=("--compact", "--whole", "2.json"))
        self.assertEqual(json.loads(proc.stdout), {"a::slow": 2.0, "m::instant": 0.002})
        proc = self.run_merge(plan, *shards, options=("--whole", "2.json"))
        self.assertEqual(proc.returncode, 2)

    def test_it_refuses_to_save_a_plan_no_shard_measured(self):
        # Every shard failed before its upload: saving would only re-save old times as new.
        proc = self.run_merge({"a::one": 1.0}, {"a::one": 1.0})
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")
        proc = self.run_merge({"a::one": 1.0})
        self.assertEqual(proc.returncode, 2)

    def test_it_refuses_a_file_that_is_not_test_times(self):
        for bad in ("[1, 2]", '{"a::one": "fast"}', '{"a::one": true}'):
            proc = self.run_merge({"a::one": 1.0}, bad)
            self.assertEqual(proc.returncode, 1, bad)
            self.assertEqual(proc.stdout, "")


class TimingWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()

    def test_a_shard_never_migrates_inside_its_measured_run(self):
        # With a dump the shard restores; without one it migrates in a step of
        # its own. Either way the database exists before "Run shard", so
        # --store-durations cannot charge a migrate to the first test.
        job = self.workflow.split("  test:\n", 1)[1].split("\n  saml-multiworker:", 1)[
            0
        ]
        migrate = job.split(
            "      - name: Migrate the test database from zero (no dump for this key)\n",
            1,
        )[1].split("\n      - ", 1)[0]
        self.assertIn("if: steps.db-dump.outputs.cache-hit != 'true'\n", migrate)
        self.assertIn("uv run pytest .ci/migrated_db.py", migrate)
        self.assertLess(
            job.index("- name: Migrate the test database from zero"),
            job.index("- name: Run shard"),
        )

    def test_dev_pushes_save_the_merged_timings_under_the_key_the_plan_restores(self):
        plan = self.workflow.split("  plan:\n", 1)[1].split("\n  test:", 1)[0]
        self.assertIn("key: test-durations-${{ github.run_id }}", plan)
        self.assertIn("            test-durations-\n", plan)
        job = self.workflow.split("  timings:\n", 1)[1].split("\n  backend-tests:", 1)[
            0
        ]
        self.assertIn(
            "if: ${{ !cancelled() && github.event_name == 'push' && github.ref == 'refs/heads/dev' }}",
            job,
        )
        self.assertIn("needs: [plan, test]", job)
        self.assertIn("futureagi/.ci/merge_test_durations.py", job)
        self.assertIn("uses: actions/cache/save@", job)
        self.assertIn("path: futureagi/.test_durations", job)
        self.assertIn("key: test-durations-${{ github.run_id }}", job)
        # The shards record times on the same event, and only there.
        shard = self.workflow.split("  test:\n", 1)[1].split(
            "\n  saml-multiworker:", 1
        )[0]
        self.assertIn(
            "github.event_name == 'push' && github.ref == 'refs/heads/dev' && '--store-durations'",
            shard,
        )


if __name__ == "__main__":
    unittest.main()
