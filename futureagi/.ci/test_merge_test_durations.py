"""The shard timing merge, and the workflow steps that keep recorded times free of the migrate."""

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "futureagi/.ci/merge_test_durations.py"
SPEC = importlib.util.spec_from_file_location("merge_test_durations", SCRIPT)
merger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merger)
FALLBACK = "futureagi/.test_durations"


def committed_fallback(root=ROOT):
    """The COMMITTED fallback, as bytes. Never the working copy: Backend CI
    replaces that file with the saved timings (every test, about 10 MB) in the
    plan job before these tests run, and in every shard."""
    return subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:{FALLBACK}"],
        check=True,
        capture_output=True,
    ).stdout


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
        blob = committed_fallback()
        floor = json.loads(blob)
        self.assertGreater(len(floor), 1000)
        self.assertLessEqual(max(len(test) for test in floor), merger.MAX_ID_LENGTH)
        # Compact: far fewer than the 70,000 tests of the suite, and a few megabytes.
        self.assertLess(len(floor), 30_000)
        self.assertLess(len(blob), 4_000_000)
        # The Metrics suite is whole, so it holds near-instant tests.
        instant = [
            test for test, seconds in floor.items() if seconds < merger.MIN_SECONDS
        ]
        self.assertGreater(len(instant), 1000)

    def test_no_case_of_the_fallback_carries_a_from_zero_migrate(self):
        # On the old layout the first database test of a shard was charged the
        # migrate (about 330 s). That lands on an arbitrary case, so it shows
        # as one very long case of a parametrized test whose other cases are
        # all near-instant or absent.
        floor = json.loads(committed_fallback())
        families = {}
        for test, seconds in floor.items():
            if "[" in test:
                families.setdefault(test.split("[", 1)[0], []).append(seconds)
        suspect = sorted(
            test
            for test, seconds in floor.items()
            if "[" in test
            and seconds >= 100
            and sum(1 for other in families[test.split("[", 1)[0]] if other >= 1) == 1
        )
        self.assertEqual(suspect, [])


@unittest.skipIf(os.environ.get("TIMINGS_DRY_RUN"), "this is the dry run itself")
class SavedTimingsDryRunTest(unittest.TestCase):
    def test_the_plan_job_test_step_passes_over_restored_saved_timings(self):
        # The plan job restores the saved timings over the fallback's path and
        # then runs these tests; every shard does the same with the plan
        # artifact. Do exactly that in a throwaway repository: commit the
        # fallback, replace the working copy with a 70,000-test file, run the
        # plan job's command on this test file.
        with tempfile.TemporaryDirectory(prefix="timings-dry-run-") as tmp:
            repo = pathlib.Path(tmp)
            (repo / "futureagi/.ci").mkdir(parents=True)
            (repo / ".github/workflows").mkdir(parents=True)
            for name in ("merge_test_durations.py", "test_merge_test_durations.py"):
                shutil.copy(
                    ROOT / "futureagi/.ci" / name, repo / "futureagi/.ci" / name
                )
            shutil.copy(
                ROOT / ".github/workflows/backend-ci.yml",
                repo / ".github/workflows/backend-ci.yml",
            )
            (repo / FALLBACK).write_bytes(committed_fallback())

            def git(*args):
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repo),
                        "-c",
                        "user.name=ci",
                        "-c",
                        "user.email=ci@example.invalid",
                        "-c",
                        "commit.gpgsign=false",
                        *args,
                    ],
                    check=True,
                    capture_output=True,
                )

            git("init", "-q")
            git("add", "-A")
            git("commit", "-q", "--no-verify", "-m", "fallback")
            saved = {
                f"suite/tests/test_module_{n // 50}.py::test_case_{n}[{'x' * 90}]": 0.0123
                for n in range(70_000)
            }
            (repo / FALLBACK).write_text(
                json.dumps(saved, indent=4, sort_keys=True) + "\n"
            )
            self.assertGreater((repo / FALLBACK).stat().st_size, 9_000_000)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "futureagi/.ci",
                    "-p",
                    "test_merge_test_durations.py",
                ],
                cwd=repo,
                env={**os.environ, "TIMINGS_DRY_RUN": "1"},
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            self.assertIn("OK", proc.stderr)


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
