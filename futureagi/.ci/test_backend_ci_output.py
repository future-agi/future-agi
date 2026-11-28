"""Guard backend suite selection and what the workflow reports about each run."""

import configparser
import json
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import textwrap
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class BackendOutputTest(unittest.TestCase):
    def test_shard_keeps_compact_progress_and_full_failure_reporting(self):
        workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        shard = workflow.split("      - name: Run shard\n", 1)[1].split(
            "      - name:", 1
        )[0]
        command = re.search(r"\buv run pytest ([^\n]+)", shard)
        self.assertIsNotNone(command)
        args = shlex.split(command.group(1).removesuffix("\\").strip())
        # pytest.ini already adds -v. The existing 1 MiB body parametrization
        # turns extra verbosity into an enormous progress line, not more tests.
        self.assertIn("-q", args)
        self.assertFalse(
            any(
                re.fullmatch(r"-v+|--verbose|--verbosity(?:=.*)?", arg) for arg in args
            ),
            args,
        )
        self.assertIn("-ra", args)
        self.assertIn("--junitxml=junit-${{ matrix.group }}.xml", shard)


class MetricsShardTest(unittest.TestCase):
    """The Metrics suite runs in its own legs of the shard job, never twice."""

    def setUp(self):
        self.workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()

    def test_every_shard_keeps_pytest_ini_markers_and_adds_its_suite_term(self):
        ini = configparser.ConfigParser(interpolation=None)
        ini.read(ROOT / "futureagi/pytest.ini")
        addopts = ini["pytest"]["addopts"]
        markers = re.search(r'-m "([^"]+)"', addopts).group(1)
        self.assertIn(f'  PYTEST_MARKERS: "{markers}"\n', self.workflow)
        registered = [
            line.split(":", 1)[0].strip()
            for line in ini["pytest"]["markers"].splitlines()
        ]
        self.assertIn("metrics", registered)  # --strict-markers
        shard = self.workflow.split("      - name: Run shard\n", 1)[1].split(
            "      - name:", 1
        )[0]
        self.assertIn('-m "($PYTEST_MARKERS) and $SUITE_MARKERS"', shard)
        self.assertIn('--splits "$SUITE_SPLITS"', shard)
        # An unplanned empty leg is a failure, not an accepted exit code.
        self.assertNotIn("set +e", shard)
        self.assertNotIn("exit 0", shard)

    def test_the_matrix_has_complementary_suites_with_distinct_artifacts(self):
        changes = self.workflow.split("  changes:\n", 1)[1].split(
            "\n  update-safety:", 1
        )[0]
        self.assertIn('legs main pytest "not metrics" 45 "$SPLITS"', changes)
        self.assertIn('legs metrics metrics metrics 50 "$METRICS_SPLITS"', changes)
        self.assertIn("metrics: ${{ steps.filter.outputs.metrics }}", changes)
        job = self.workflow.split("  test:\n", 1)[1].split("\n  saml-multiworker:", 1)[
            0
        ]
        self.assertIn("matrix: ${{ fromJSON(needs.changes.outputs.shards) }}", job)
        self.assertIn("timeout-minutes: ${{ matrix.timeout }}", job)
        for prefix in ("junit-results-", "timing-data-"):
            self.assertIn(
                f"name: {prefix}${{{{ matrix.suite }}}}-${{{{ matrix.group }}}}", job
            )
        gate = self.workflow.split("  backend-tests:\n", 1)[1]
        self.assertIn("        test,\n", gate.split("    if:", 1)[0])


class EvaluatorJobTest(unittest.TestCase):
    def test_standalone_evaluator_suite_is_a_required_backend_dependency(self):
        workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        gate = workflow.split("  backend-tests:\n", 1)[1]
        needs = gate.split("    needs:\n", 1)[1].split("    if:", 1)[0]
        self.assertIn("agentic-eval,", needs)

    def test_evaluator_job_keeps_its_own_root_and_backend_working_directory(self):
        workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        job = workflow.split("  agentic-eval:\n", 1)[1].split("  slim-smoke:\n", 1)[0]
        self.assertIn("working-directory: futureagi", job)
        self.assertIn("uv sync --frozen --extra dev", job)
        self.assertIn(
            "-c agentic_eval/pytest.ini --confcutdir=agentic_eval agentic_eval", job
        )
        self.assertIn("--junitxml=agentic-eval-junit.xml", job)
        self.assertNotIn("continue-on-error:", job)
        self.assertNotIn("--ignore=", job)


class ExtendedOverlayTest(unittest.TestCase):
    def setUp(self):
        workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        _, marker, rest = workflow.partition("\n  extended-overlay:\n")
        self.assertTrue(marker, "missing cloud suite job")
        self.job = re.split(r"\n  [\w-]+:\n", rest, maxsplit=1)[0]
        self.gate = workflow.split("\n  backend-tests:\n")[1]

    def test_internal_backend_runs_select_the_whole_directory(self):
        self.assertIn(
            "    if: needs.changes.outputs.backend == 'true' && needs.changes.outputs.internal == 'true'\n",
            self.job,
        )
        self.assertIn("        extended-overlay,\n", self.gate.split("    if:")[0])
        self.assertIn("CLOUD_DEPLOYMENT: DEV", self.job)
        self.assertIn("--ds=tfc.settings.test --create-db", self.job)
        self.assertIn(" ee/cloud/tests > /dev/null 2>&1\n", self.job)
        self.assertNotIn("continue-on-error", self.job)
        self.assertNotIn("upload-artifact", self.job)

    def test_summary_counts_only_complete_passes_and_rejects_vacuous_runs(self):
        script = textwrap.dedent(self.job.rsplit("        run: |\n", 1)[1])
        retention = "ee/cloud/tests/test_retention.py::test_example"
        other = "ee/cloud/tests/test_other.py::test_example"
        passed = [[phase, "passed"] for phase in ("setup", "call", "teardown")]
        skipped = [["setup", "runtime-skip", "fixture detail"], ["teardown", "passed"]]
        for rows, counts, ok in (
            ([], (0, 0, 0), False),
            ([(retention, skipped)], (1, 0, 0), False),
            ([(retention, skipped), (other, passed)], (2, 1, 0), False),
            # Collection includes one deselected case with no terminal record.
            ([(retention, passed), (other, skipped)], (3, 1, 1), True),
        ):
            with self.subTest(counts=counts), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                summary = root / "summary"
                (root / "cloud-ids.txt").write_text("id\n" * counts[0])
                outcomes = [json.dumps(["key", *row]) + "\n" for row in rows]
                (root / "cloud-terminal.jsonl").write_text("".join(outcomes))
                result = subprocess.run(
                    ["bash", "-ec", script],
                    env={
                        **os.environ,
                        "RUNNER_TEMP": tmp,
                        "GITHUB_STEP_SUMMARY": str(summary),
                    },
                    capture_output=True,
                )
                self.assertEqual(result.returncode == 0, ok, result.stderr)
                collected, passes, covered = counts
                self.assertEqual(
                    summary.read_text().strip(),
                    f"Extended overlay: {collected} collected, {passes} passed ({covered} retention).",
                )


if __name__ == "__main__":
    unittest.main()
