"""Exercise real coverage tools in CI; check_* avoids backend pytest discovery."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "changed_line_coverage.py"
ROOT = SCRIPT.parents[2]


class ChangedLineCoverageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.backend = self.root / "futureagi"
        self.package = self.backend / "tracer"
        self.package.mkdir(parents=True)
        (self.package / "__init__.py").write_text("")
        self.source = self.package / "sample.py"
        self.source.write_text(
            "def first():\n    return 1\n\ndef second():\n    return 2\n"
            "\ndef never():\n    return 3\n"
        )
        self.command("git", "init", "-q")
        self.command("git", "add", ".")
        self.commit("baseline")
        self.command("git", "branch", "comparison")
        self.source.write_text(self.source.read_text().replace("return ", "return 1"))
        (self.package / "unimported.py").write_text("VALUE = 99\n")
        self.command("git", "add", ".")
        self.commit("change three returns and add an unimported module")
        self.config = self.root / "coverage.ini"
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.output = self.root / "reports"
        self.summary = self.root / "summary.txt"
        self.filters = json.dumps(["backend", "coverage_tracer", "update_safety"])
        self.command(
            sys.executable,
            "-I",
            str(SCRIPT),
            "configure",
            "--filters",
            self.filters,
            "--config",
            str(self.config),
        )

    def command(self, *args, cwd=None, env=None):
        result = subprocess.run(
            args,
            cwd=cwd or self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def commit(self, message):
        self.command(
            "git",
            "-c",
            "user.name=Coverage fixture",
            "-c",
            "user.email=ci@example.invalid",
            "commit",
            "-qm",
            message,
        )

    def collect(self):
        checks = self.backend / "checks"
        checks.mkdir()
        for group, (function, value) in enumerate((("first", 11), ("second", 12)), 1):
            test = checks / f"test_{function}.py"
            test.write_text(
                f"from tracer.sample import {function}\n\n"
                f"def test_result():\n    assert {function}() == {value}\n"
            )
            env = dict(
                os.environ, COVERAGE_FILE=str(self.artifacts / f".coverage.{group}")
            )
            self.command(
                sys.executable,
                "-m",
                "pytest",
                "-q",
                str(test),
                "--cov",
                f"--cov-config={self.config}",
                "--cov-report=",
                "--cov-fail-under=0",
                cwd=self.backend,
                env=env,
            )

    def report(self, result="success"):
        return self.command(
            sys.executable,
            "-I",
            str(SCRIPT),
            "report",
            "--filters",
            self.filters,
            "--config",
            str(self.config),
            "--artifacts",
            str(self.artifacts),
            "--output",
            str(self.output),
            "--summary",
            str(self.summary),
            "--base",
            "comparison",
            "--shards",
            "2",
            "--test-result",
            result,
            cwd=self.backend,
        )

    def test_union_counts_unimported_lines_and_annotates_only_uncovered_changes(self):
        self.collect()
        result = self.report()
        report = json.loads((self.output / "diff-coverage.json").read_text())
        self.assertEqual(report["total_num_lines"], 4)
        self.assertEqual(report["total_num_violations"], 2)
        stats = report["src_stats"]["futureagi/tracer/sample.py"]
        self.assertEqual(stats["violation_lines"], [8])
        self.assertEqual(stats["covered_lines"], [2, 5])
        summary = self.summary.read_text()
        self.assertIn("50.00% (2/4 changed executable lines)", summary)
        self.assertIn("Reference target: 80%. No enforced threshold.", summary)
        self.assertIn("::notice file=futureagi/tracer/sample.py,line=8,", result.stdout)
        self.assertIn(
            "::notice file=futureagi/tracer/unimported.py,line=1,", result.stdout
        )
        self.assertNotIn("::error", result.stdout)

    def test_missing_shard_is_unavailable_not_perfect_coverage(self):
        result = self.report()
        self.assertIn("coverage shards=0/2", self.summary.read_text())
        self.assertIn("::warning::", result.stdout)
        self.assertNotIn("100", self.summary.read_text())
        self.assertFalse(self.output.exists())

    def test_failed_test_run_does_not_publish_a_percentage(self):
        self.collect()
        self.report("failure")
        self.assertIn("backend result=failure", self.summary.read_text())
        self.assertFalse((self.output / "diff-coverage.json").exists())

    def test_unchanged_executable_lines_report_not_applicable(self):
        self.collect()
        self.command("git", "branch", "-f", "comparison", "HEAD")
        self.report()
        self.assertIn("N/A: no changed executable lines", self.summary.read_text())
        self.assertNotIn("100.00%", self.summary.read_text())

    def test_comparison_uses_merge_base_when_base_branch_advances(self):
        baseline = self.command("git", "rev-parse", "comparison").stdout.strip()
        self.command("git", "checkout", "-q", "comparison")
        self.source.write_text(self.source.read_text().replace("return 1", "return 11"))
        self.command("git", "add", str(self.source))
        self.commit("independent base branch change")
        self.command("git", "checkout", "-q", "-")
        self.collect()
        self.report()
        self.assertIn(f"Merge base: `{baseline}`", self.summary.read_text())
        self.assertIn("50.00% (2/4 changed executable lines)", self.summary.read_text())

    def test_only_selected_packages_are_instrumented(self):
        config = self.config.read_text()
        self.assertIn("source = tracer\n", config)
        self.assertIn("relative_files = true", config)
        self.assertNotIn("agentic_eval", config)
        self.assertNotIn("model_hub", config)
        self.assertIn("fail_under = 0", config)

    def test_agentic_eval_report_discloses_excluded_standalone_tests(self):
        self.filters = json.dumps(["coverage_tracer", "coverage_agentic_eval"])
        package = self.backend / "agentic_eval"
        package.mkdir()
        (package / "__init__.py").write_text("")
        self.collect()
        self.report()
        self.assertIn("Its own tests are excluded", self.summary.read_text())

    def test_no_package_filters_refuse_instrumentation(self):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                "configure",
                "--filters",
                '["backend"]',
                "--config",
                str(self.config),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No eval/trace package changed", result.stderr)

    def test_required_gate_does_not_depend_on_coverage(self):
        workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        aggregator = workflow.split("  backend-tests:\n", 1)[1]
        self.assertNotIn("coverage", aggregator)
        self.assertIn('"--cov-fail-under=0"', workflow)
        self.assertIn("include-hidden-files: true", workflow)


if __name__ == "__main__":
    unittest.main()
