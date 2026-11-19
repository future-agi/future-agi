"""Guard the shard command against unbounded verbose parametrization output."""

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
