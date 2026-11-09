"""Run with python -I scripts/test_check_python_types_delta.py; mypy is required."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/check_python_types_delta.py"
SPEC = importlib.util.spec_from_file_location("check_python_types_delta", SCRIPT)
assert SPEC and SPEC.loader
GATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = GATE
SPEC.loader.exec_module(GATE)
VERSION = "1.19.1"
CONFIG = '[tool.mypy]\npython_version = "3.11"\ncheck_untyped_defs = true\n'
OLD_ERROR = 'value: int = "old"\n'


class DeltaTests(unittest.TestCase):
    def finding(
        self, line: int, *, path: str = "futureagi/a.py", code: str = "assignment"
    ):
        return GATE.Finding(path, code, line, 1, "incompatible", 'value: int = "old"')

    def test_counts_and_new_line_preference(self):
        old = self.finding(1)
        existing = self.finding(20)
        added = self.finding(3)
        self.assertEqual(
            GATE.new_findings(
                [existing, added], [old], {old.path: old.path}, {old.path: {3}}
            ),
            [added],
        )
        self.assertEqual(GATE.new_findings([existing], [old], {}, {}), [])
        self.assertEqual(GATE.new_findings([], [old], {}, {}), [])

    def test_file_and_code_are_part_of_the_key(self):
        old = self.finding(1)
        for new in (
            self.finding(2, path="futureagi/b.py"),
            self.finding(2, code="return-value"),
        ):
            with self.subTest(new=new):
                self.assertEqual(GATE.new_findings([new], [old], {}, {}), [new])

    def test_rename_maps_baseline_to_head(self):
        old = self.finding(1)
        new = self.finding(10, path="futureagi/renamed.py")
        self.assertEqual(GATE.new_findings([new], [old], {new.path: old.path}, {}), [])

    def test_normalization_preserves_hashes_inside_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.py"
            source.write_text(
                'value  = "# data"  # comment\ntext = """\n# literal\n"""\n'
            )
            self.assertEqual(
                GATE.normalized_lines(source),
                ['value = "# data"', 'text = """', "# literal", '"""'],
            )

    def test_unlocated_error_cannot_be_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "futureagi").mkdir()
            (root / "futureagi/a.py").write_text(OLD_ERROR)
            diagnostic = {
                "file": "a.py",
                "line": 0,
                "column": 0,
                "severity": "error",
                "code": "assignment",
                "message": "bad",
            }
            with self.assertRaises(GATE.GateError):
                GATE.parse_findings(json.dumps(diagnostic), root, ["futureagi/a.py"])

    def test_annotation_escape(self):
        self.assertEqual(GATE.escape("a%,:\n", property_value=True), "a%25%2C%3A%0A")

    def test_workflow_is_advisory_and_uses_locked_version(self):
        lock = tomllib.loads((ROOT / "futureagi/uv.lock").read_text())
        versions = {p["version"] for p in lock["package"] if p["name"] == "mypy"}
        self.assertEqual(versions, {VERSION})
        workflow = (ROOT / ".github/workflows/python-types.yml").read_text()
        self.assertIn(f'MYPY_VERSION: "{VERSION}"', workflow)
        self.assertNotIn("branches:", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("Backend Tests Pass", workflow)
        self.assertIn('"futureagi/**/*.py"', workflow)
        self.assertIn("uv sync --frozen --no-dev --extra dev", workflow)


class GitIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="mypy-delta-test-")
        self.addCleanup(directory.cleanup)
        self.repo = Path(directory.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Gate Test")
        self.git("config", "user.email", "gate@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/existing.py", OLD_ERROR)
        self.commit()
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def write(self, path: str, content: str):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def check(self, code: int, *, version: str = VERSION) -> str:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(SCRIPT),
                "--base",
                self.base,
                "--expect-mypy-version",
                version,
            ],
            cwd=self.repo,
            env={
                key: value
                for key, value in os.environ.items()
                if key != "GITHUB_STEP_SUMMARY"
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout

    def test_old_error_survives_line_shift_and_comment_change(self):
        self.write(
            "futureagi/existing.py", '\n\nvalue: int = "old"  # different comment\n'
        )
        self.commit()
        output = self.check(0)
        self.assertIn("mypy head: 1 file(s)", output)
        self.assertIn("mypy base: 1 file(s)", output)
        self.assertIn("0 new type error(s)", output)
        self.assertNotIn("::warning", output)
        self.assertEqual(
            len(self.git("worktree", "list", "--porcelain").split("worktree ")), 2
        )

    def test_new_error_in_modified_file_fails_with_annotation(self):
        self.write("futureagi/existing.py", OLD_ERROR + 'new: int = "bad"\n')
        self.commit()
        output = self.check(1)
        self.assertIn("1 new type error(s)", output)
        self.assertIn("file=futureagi/existing.py,line=2", output)
        self.assertIn("assignment:", output)
        self.assertNotIn("file=futureagi/existing.py,line=1", output)

    def test_added_file_is_checked_without_a_baseline(self):
        self.write("futureagi/added.py", 'added: int = "bad"\n')
        self.commit()
        output = self.check(1)
        self.assertIn("file=futureagi/added.py,line=1", output)
        self.assertIn("mypy base: no existing changed Python files", output)

    def test_duplicate_error_count_growth_is_not_set_subtraction(self):
        first = 'def first() -> int:\n    return "bad"\n'
        self.write("futureagi/existing.py", first)
        self.commit()
        self.base = self.git("rev-parse", "HEAD")
        self.write(
            "futureagi/existing.py",
            first + '\ndef second() -> int:\n    return "bad"\n',
        )
        self.commit()
        output = self.check(1)
        self.assertIn("1 new type error(s)", output)
        self.assertIn("file=futureagi/existing.py,line=5", output)
        self.assertIn("return-value:", output)

    def test_rename_does_not_reset_old_errors(self):
        self.git("mv", "futureagi/existing.py", "futureagi/renamed.py")
        self.commit()
        output = self.check(0)
        self.assertIn("Head: 1 file(s), 1 error(s)", output)
        self.assertIn("Base: 1 file(s), 1 error(s)", output)

    def test_deleted_files_and_non_backend_files_do_not_run_mypy(self):
        self.git("rm", "futureagi/existing.py")
        self.write("scripts/outside.py", 'outside: int = "bad"\n')
        self.commit()
        self.assertIn("mypy head: no existing changed Python files", self.check(0))

    def test_fixed_error_is_not_new(self):
        self.write("futureagi/existing.py", "value: int = 1\n")
        self.commit()
        self.assertIn("0 new type error(s)", self.check(0))

    def test_base_branch_advancing_uses_merge_base_not_tip(self):
        self.git("checkout", "-qb", "base-branch")
        self.write("futureagi/existing.py", "value: int = 1\n")
        self.commit()
        self.git("checkout", "-qb", "pr-branch", self.base)
        self.base = "base-branch"
        self.write("futureagi/existing.py", "\n" + OLD_ERROR)
        self.commit()
        self.assertIn("Base: 1 file(s), 1 error(s)", self.check(0))

    def test_imported_unchanged_errors_are_outside_the_delta(self):
        self.write(
            "futureagi/caller.py", "from existing import value\nanswer: int = value\n"
        )
        self.commit()
        self.assertIn("0 new type error(s)", self.check(0))

    def test_broken_config_is_incomplete_not_clean(self):
        self.write(
            "futureagi/pyproject.toml", CONFIG + 'plugins = ["missing_gate_plugin"]\n'
        )
        self.write("futureagi/existing.py", "value: int = 1\n")
        self.commit()
        self.assertIn("comparison incomplete", self.check(2))

    def test_duplicate_modules_are_incomplete_not_clean(self):
        self.write("futureagi/a/shared.py", "value: int = 1\n")
        self.write("futureagi/b/shared.py", "value: int = 1\n")
        self.commit()
        self.assertIn("comparison incomplete", self.check(2))

    def test_wrong_mypy_version_cannot_be_clean(self):
        self.assertIn("Expected mypy", self.check(2, version="0.0.0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
