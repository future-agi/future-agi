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
        merged, measured = merger.merge(plan, [first, second])
        self.assertEqual(measured, 3)
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

    def test_near_instant_tests_and_giant_ids_are_left_out(self):
        plan = {"a::slow": 1.0}
        giant = "a::case[" + "x" * merger.MAX_ID_LENGTH + "]"
        shard = {
            **plan,
            "a::instant": merger.MIN_SECONDS / 2,
            "a::edge": merger.MIN_SECONDS,
            giant: 5.0,
            "a::long": 1.23456789,
        }
        merged, measured = merger.merge(plan, [shard])
        self.assertEqual(measured, 4)
        self.assertEqual(
            merged,
            {"a::slow": 1.0, "a::edge": merger.MIN_SECONDS, "a::long": 1.2346},
        )

    def test_two_shards_that_disagree_keep_the_longer_time(self):
        merged, _ = merger.merge({"a::one": 1.0}, [{"a::one": 2.0}, {"a::one": 3.0}])
        self.assertEqual(merged, {"a::one": 3.0})
        merged, _ = merger.merge({"a::one": 1.0}, [{"a::one": 3.0}, {"a::one": 2.0}])
        self.assertEqual(merged, {"a::one": 3.0})

    def test_the_committed_floor_follows_the_same_rules(self):
        floor = json.loads((ROOT / "futureagi/.test_durations").read_text())
        self.assertGreater(len(floor), 1000)
        self.assertGreaterEqual(min(floor.values()), merger.MIN_SECONDS)
        self.assertLessEqual(max(len(test) for test in floor), merger.MAX_ID_LENGTH)
        # No test of the floor carries a from-zero migrate (about 330 s, on tests that take milliseconds).
        self.assertLess(
            floor.get(
                "accounts/tests/test_2fa_enforcement.py::TestOrgTwoFactorEnforcement::test_enable_2fa_policy",
                0,
            ),
            60,
        )


class CommandLineTest(unittest.TestCase):
    def run_merge(self, *documents):
        with tempfile.TemporaryDirectory(prefix="merge-durations-test-") as tmp:
            paths = []
            for index, document in enumerate(documents):
                path = pathlib.Path(tmp) / f"{index}.json"
                path.write_text(
                    document if isinstance(document, str) else json.dumps(document)
                )
                paths.append(str(path))
            return subprocess.run(
                [sys.executable, "-I", str(SCRIPT), *paths],
                capture_output=True,
                text=True,
            )

    def test_it_writes_the_merged_file_to_stdout(self):
        proc = self.run_merge({"a::one": 1.0}, {"a::one": 2.0, "a::two": 0.5})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"a::one": 2.0, "a::two": 0.5})

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
        self.assertIn("--create-db", migrate)
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
