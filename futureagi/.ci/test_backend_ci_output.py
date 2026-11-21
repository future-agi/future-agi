"""Guard the shard command against unbounded verbose parametrization output."""

import configparser
import pathlib
import re
import shlex
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
        # Exit 5 (nothing collected) passes a main leg only.
        self.assertIn('[ "$code" -eq 5 ] && [ "$SUITE" = main ]', shard)

    def test_the_matrix_has_complementary_suites_with_distinct_artifacts(self):
        changes = self.workflow.split("  changes:\n", 1)[1].split(
            "\n  update-safety:", 1
        )[0]
        self.assertIn('legs main pytest "not metrics" 45 "$SPLITS"', changes)
        self.assertIn('legs metrics metrics metrics 50 "$METRICS_SPLITS"', changes)
        self.assertIn("            metrics:\n", changes)
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


if __name__ == "__main__":
    unittest.main()
