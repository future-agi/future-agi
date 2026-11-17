"""Tests for scripts/mypy_baseline.py and the workflow that runs it.

Run from the repo root: python3 -I scripts/test_mypy_baseline.py

Standard library only. With MYPY_BIN set to a mypy executable, the RealMypy
tests also run that mypy on throwaway projects, proving the parser reads its
real output; CI sets it, and there a missing MYPY_BIN fails instead of
skipping.
"""

import contextlib
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "mypy_baseline.py"
BASELINE = ROOT / "futureagi" / "mypy-baseline.txt"
WORKFLOW = ROOT / ".github" / "workflows" / "python-types.yml"

spec = importlib.util.spec_from_file_location("mypy_baseline", SCRIPT)
mb = importlib.util.module_from_spec(spec)
sys.modules["mypy_baseline"] = mb
spec.loader.exec_module(mb)

MYPY_BIN = (
    os.path.abspath(os.environ["MYPY_BIN"]) if os.environ.get("MYPY_BIN") else None
)
REQUIRE_MYPY = bool(os.environ.get("CI"))


def output(*lines, summary=None):
    """mypy output with a matching summary line."""
    errors = sum(": error: " in line for line in lines)
    files = len({line.split(":", 1)[0] for line in lines if ": error: " in line})
    if summary is None:
        summary = (
            f"Found {errors} error{'s' * (errors != 1)} in {files} file{'s' * (files != 1)} "
            "(checked 9 source files)"
            if errors
            else "Success: no issues found in 9 source files"
        )
    return "\n".join([*lines, summary]) + "\n"


A1 = 'app/a.py:10: error: Incompatible return value type (got "int", expected "str")  [return-value]'
A2 = 'app/a.py:20: error: Item "None" of "X | None" has no attribute "y"  [union-attr]'
B1 = "app/b.py:5: error: Access to generic instance variables via class is ambiguous  [misc]"
B2 = "app/b.py:9: error: Access to generic instance variables via class is ambiguous  [misc]"
NOTE = 'app/a.py:10: note: Revealed type is "builtins.int"'


def run_main(argv, stdin_text="", env=None):
    """Run main() in-process; returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    stdin = io.TextIOWrapper(io.BytesIO(stdin_text.encode("utf-8")), encoding="utf-8")
    environ = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
    environ.update(env or {})
    with (
        mock.patch.dict(os.environ, environ, clear=True),
        mock.patch.object(sys, "stdin", stdin),
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
    ):
        code = mb.main(argv)
    return code, out.getvalue(), err.getvalue()


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="mypy-baseline-test-")
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.baseline = self.dir / "mypy-baseline.txt"

    def write_baseline(self, *entries):
        self.baseline.write_text("".join(f"{e}\n" for e in entries), encoding="utf-8")

    def filter(self, text, *extra, env=None):
        return run_main(["filter", "--baseline", str(self.baseline), *extra], text, env)

    def sync(self, text, *extra):
        return run_main(["sync", "--baseline", str(self.baseline), *extra], text)


class NormalizationTests(unittest.TestCase):
    def entries(self, *lines):
        return [e.entry for e in mb.parse_output(output(*lines))]

    def test_line_and_column_become_zero(self):
        expected = 'app/a.py:0: error: Name "x" is not defined  [name-defined]'
        for line in (
            'app/a.py:12: error: Name "x" is not defined  [name-defined]',
            'app/a.py:12:5: error: Name "x" is not defined  [name-defined]',
            'app/a.py:12:5:12:9: error: Name "x" is not defined  [name-defined]',
            './app/a.py:12: error: Name "x" is not defined  [name-defined]',
        ):
            with self.subTest(line=line):
                self.assertEqual(self.entries(line), [expected])

    def test_an_error_without_a_line_is_kept(self):
        self.assertEqual(
            self.entries("app/a.py: error: Something about the file  [misc]"),
            ["app/a.py:0: error: Something about the file  [misc]"],
        )

    def test_on_line_n_inside_the_message_is_normalized(self):
        self.assertEqual(
            self.entries(
                'app/a.py:466: error: Name "wrapper" already defined on line 454  [no-redef]'
            ),
            ['app/a.py:0: error: Name "wrapper" already defined on line 0  [no-redef]'],
        )

    def test_the_original_line_number_is_kept_for_reporting(self):
        (error,) = mb.parse_output(output(A1))
        self.assertEqual((error.path, error.line, error.raw), ("app/a.py", 10, A1))

    def test_notes_are_ignored(self):
        self.assertEqual(
            self.entries(
                A1, NOTE, "app/a.py:10: note: See https://mypy.readthedocs.io/x"
            ),
            [mb.entry_for("app/a.py", A1.split(": error: ")[1])],
        )

    def test_an_error_without_a_code_is_kept(self):
        self.assertEqual(
            self.entries("app/a.py:3: error: invalid syntax"),
            ["app/a.py:0: error: invalid syntax"],
        )


class CompletenessTests(unittest.TestCase):
    """A run that did not finish is never a clean result."""

    def assert_incomplete(self, text, exit_code=None, match=""):
        with self.assertRaisesRegex(mb.Incomplete, match):
            mb.parse_output(text, exit_code)

    def test_an_abort_is_incomplete(self):
        self.assert_incomplete(
            'app/utils/graph.py: error: Duplicate module named "graph"\n'
            "app/utils/graph.py: note: See https://mypy.readthedocs.io/x\n"
            "Found 1 error in 1 file (errors prevented further checking)\n",
            match="stopped before checking everything",
        )

    def test_a_crash_with_partial_output_is_incomplete(self):
        # On an internal error mypy prints the errors so far to stdout, then the
        # traceback to stderr, and no summary line.
        self.assert_incomplete(f"{A1}\n{A2}\n", match="no summary line")

    def test_empty_output_is_incomplete(self):
        self.assert_incomplete("", match="no summary line")
        self.assert_incomplete("\n\n", match="no summary line")

    def test_a_summary_count_that_does_not_match_is_incomplete(self):
        self.assert_incomplete(
            output(A1, summary="Found 2 errors in 1 file (checked 9 source files)"),
            match="summary says 2",
        )
        self.assert_incomplete(
            output(A1, summary="Success: no issues found in 9 source files")
        )

    def test_lines_after_the_summary_are_incomplete(self):
        self.assert_incomplete(output(A1) + A2 + "\n", match="follows mypy's summary")

    def test_an_unknown_line_is_incomplete(self):
        self.assert_incomplete(
            output("Traceback (most recent call last):", A1),
            match="not a mypy diagnostic",
        )
        self.assert_incomplete(
            output("app/a.py:1: warning: odd"), match="not a mypy diagnostic"
        )

    def test_the_exit_code_must_match_the_summary(self):
        self.assertEqual(len(mb.parse_output(output(A1), 1)), 1)
        self.assertEqual(mb.parse_output(output(), 0), [])
        for text, code in (
            (output(A1), 0),
            (output(A1), 2),
            (output(), 1),
            (output(), 2),
            (output(A1), -9),
        ):
            with self.subTest(code=code, text=text):
                self.assert_incomplete(text, code, match="mypy exited")

    def test_a_success_is_complete(self):
        self.assertEqual(mb.parse_output(output()), [])


class FilterTests(TempDirTestCase):
    def test_no_change_passes(self):
        self.write_baseline(*[e.entry for e in mb.parse_output(output(A1, B1, B2))])
        code, out, _ = self.filter(output(A1, B1, B2))
        self.assertEqual(code, 0, out)
        self.assertIn("0 new, 0 fixed", out)

    def test_a_moved_error_passes(self):
        self.write_baseline("app/a.py:0: " + A1.split(": ", 1)[1])
        code, out, _ = self.filter(output(A1.replace(":10:", ":250:")))
        self.assertEqual(code, 0, out)
        self.assertIn("0 new, 0 fixed", out)

    def test_a_new_error_fails_and_is_printed_with_its_line(self):
        self.write_baseline(mb.parse_output(output(A1))[0].entry)
        code, out, _ = self.filter(output(A1, A2))
        self.assertEqual(code, 1, out)
        self.assertIn("1 new mypy error(s)", out)
        self.assertIn(f"    {A2}", out)
        self.assertIn("union-attr 1", out)
        self.assertIn("1 new, 0 fixed", out)

    def test_duplicates_are_counted(self):
        entry = mb.parse_output(output(B1))[0].entry
        self.write_baseline(entry, entry)
        code, out, _ = self.filter(output(B1, B2))
        self.assertEqual(code, 0, out)
        third = B1.replace(":5:", ":40:")
        code, out, _ = self.filter(output(B1, B2, third))
        self.assertEqual(code, 1, out)
        self.assertIn(f"3 now, baseline allows 2: {entry}", out)
        for line in (B1, B2, third):
            self.assertIn(f"    {line}", out)
        self.assertIn("1 new, 0 fixed", out)

    def test_the_same_message_in_another_file_is_new(self):
        self.write_baseline(mb.parse_output(output(B1))[0].entry)
        code, out, _ = self.filter(output(B1.replace("app/b.py", "app/c.py")))
        self.assertEqual(code, 1, out)
        self.assertIn("1 new, 1 fixed", out)

    def test_a_fixed_error_is_reported_and_does_not_fail(self):
        entries = [e.entry for e in mb.parse_output(output(A1, A2, B1, B2))]
        self.write_baseline(*sorted(entries))
        code, out, _ = self.filter(output(A1, B1))
        self.assertEqual(code, 0, out)
        self.assertIn("make mypy-baseline-sync", out)
        self.assertIn(f"fixed: {mb.parse_output(output(A2))[0].entry}", out)
        self.assertIn(f"fixed: {mb.parse_output(output(B2))[0].entry}", out)
        self.assertIn("0 new, 2 fixed", out)

    def test_a_note_change_does_not_fail(self):
        self.write_baseline(mb.parse_output(output(A1))[0].entry)
        code, out, _ = self.filter(output(A1, NOTE, "app/z.py:1: note: unrelated"))
        self.assertEqual(code, 0, out)

    def test_an_abort_fails_even_with_no_errors_listed(self):
        self.write_baseline()
        code, _, err = self.filter(
            "Found 1 error in 1 file (errors prevented further checking)\n"
        )
        self.assertEqual(code, 2)
        self.assertIn("stopped before checking everything", err)

    def test_a_crash_fails(self):
        self.write_baseline(mb.parse_output(output(A1))[0].entry)
        for text, extra in (
            (f"{A1}\n", ()),  # no summary
            ("", ()),  # nothing at all
            (output(A1), ("--mypy-exit-code", "2")),  # summary printed, then a crash
        ):
            with self.subTest(text=text, extra=extra):
                code, out, err = self.filter(text, *extra)
                self.assertEqual(code, 2, out + err)
                self.assertNotIn("0 new", out)

    def test_a_missing_or_broken_baseline_fails(self):
        code, _, err = self.filter(output(A1))
        self.assertEqual(code, 2)
        self.assertIn("no baseline", err)
        entry = mb.parse_output(output(A1))[0].entry
        self.baseline.write_text(
            f"<<<<<<< HEAD\n{entry}\n=======\n>>>>>>> x\n", encoding="utf-8"
        )
        code, _, err = self.filter(output(A1))
        self.assertEqual(code, 2)
        self.assertIn("not a baseline entry", err)
        self.write_baseline(A1)  # a line number that was not normalized
        code, _, err = self.filter(output(A1))
        self.assertEqual(code, 2)
        self.assertIn("not a baseline entry", err)

    def test_input_file_and_exit_code(self):
        self.write_baseline()
        path = self.dir / "out.txt"
        path.write_text(output(A1), encoding="utf-8")
        code, out, _ = self.filter("", "--input", str(path), "--mypy-exit-code", "1")
        self.assertEqual(code, 1, out)
        path.write_text(output(), encoding="utf-8")
        code, out, _ = self.filter("", "--input", str(path), "--mypy-exit-code", "0")
        self.assertEqual(code, 0, out)

    def test_github_annotations_point_at_the_repo_path(self):
        self.write_baseline()
        backend = self.dir / "futureagi"
        backend.mkdir()
        summary = self.dir / "summary.md"
        cwd = os.getcwd()
        os.chdir(backend)
        try:
            code, out, _ = self.filter(
                output(A1),
                env={
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_WORKSPACE": str(self.dir.resolve()),
                    "GITHUB_STEP_SUMMARY": str(summary),
                },
            )
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 1, out)
        self.assertIn(
            "::error file=futureagi/app/a.py,line=10,title=New mypy error::", out
        )
        self.assertIn("New errors: 1", summary.read_text(encoding="utf-8"))

    def test_the_command_line_entry_point(self):
        # python3 -I, as CI and the Makefile run it.
        self.write_baseline()
        for text, expected in ((output(), 0), (output(A1), 1), (f"{A1}\n", 2)):
            with self.subTest(expected=expected):
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(SCRIPT),
                        "filter",
                        "--baseline",
                        str(self.baseline),
                    ],
                    check=False,
                    input=text,
                    capture_output=True,
                    text=True,
                    env={"PATH": os.environ.get("PATH", "")},
                )
                self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)


class SyncTests(TempDirTestCase):
    def test_sync_writes_a_sorted_multiset_without_notes(self):
        code, out, _ = self.sync(output(B2, A2, NOTE, B1, A1))
        self.assertEqual(code, 0, out)
        lines = self.baseline.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, sorted(lines))
        self.assertEqual(len(lines), 4)
        self.assertEqual(Counter(lines)[mb.parse_output(output(B1))[0].entry], 2)
        self.assertTrue(all(":0: error: " in line for line in lines))
        self.assertFalse(any(": note: " in line for line in lines))
        self.assertIn("4 entries (was 0)", out)

    def test_filter_passes_right_after_sync(self):
        text = output(A1, A2, B1, B2, NOTE)
        self.assertEqual(self.sync(text)[0], 0)
        code, out, _ = self.filter(text)
        self.assertEqual(code, 0, out)
        self.assertIn("0 new, 0 fixed", out)

    def test_sync_reports_added_and_removed(self):
        self.sync(output(A1, B1))
        code, out, _ = self.sync(output(A1, A2))
        self.assertEqual(code, 0, out)
        self.assertIn("2 entries (was 2), 1 removed, 1 added", out)
        self.assertIn(mb.parse_output(output(A2))[0].entry, out)

    def test_sync_of_a_clean_run_empties_the_baseline(self):
        self.write_baseline(mb.parse_output(output(A1))[0].entry)
        self.assertEqual(self.sync(output())[0], 0)
        self.assertEqual(self.baseline.read_text(encoding="utf-8"), "")
        self.assertEqual(self.filter(output())[0], 0)

    def test_sync_refuses_a_run_that_did_not_finish(self):
        self.write_baseline("keep/me.py:0: error: old  [misc]")
        before = self.baseline.read_bytes()
        for text in (
            f"{A1}\n",
            "Found 1 error in 1 file (errors prevented further checking)\n",
            "",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.sync(text)[0], 2)
                self.assertEqual(self.baseline.read_bytes(), before)
        self.assertEqual(self.sync(output(A1), "--mypy-exit-code", "2")[0], 2)
        self.assertEqual(self.baseline.read_bytes(), before)

    def test_sync_rebuilds_a_conflicted_baseline(self):
        entry = mb.parse_output(output(A1))[0].entry
        self.baseline.write_text(
            f"<<<<<<< HEAD\n{entry}\n=======\n>>>>>>> x\n", encoding="utf-8"
        )
        self.assertEqual(self.sync(output(A1))[0], 0)
        self.assertEqual(self.baseline.read_text(encoding="utf-8"), f"{entry}\n")


class CommittedBaselineTests(unittest.TestCase):
    def test_the_committed_baseline_is_sorted_and_normalized(self):
        text = BASELINE.read_text(encoding="utf-8")
        counts = mb.parse_baseline(text, str(BASELINE))
        lines = text.splitlines()
        self.assertEqual(lines, sorted(lines), "edit the baseline only with sync")
        self.assertEqual(sum(counts.values()), len(lines), "no blank lines")
        self.assertTrue(text == "" or text.endswith("\n"))
        self.assertFalse(any(": note: " in line for line in lines))
        self.assertFalse(any(re.search(r"\bon line [1-9]", line) for line in lines))


def top_level_block(text, key):
    """The lines under a column-0 key, up to the next column-0 key."""
    return re.search(rf"(?ms)^{re.escape(key)}:[^\n]*\n(.*?)(?=^\S|\Z)", text).group(1)


def job_block(text, job_id):
    """A job's lines, up to the next job (two-space indent) or column-0 key."""
    jobs = top_level_block(text, "jobs")
    found = re.search(rf"(?ms)^  {re.escape(job_id)}:\n(.*?)(?=^  \S|\Z)", jobs)
    if not found:
        raise AssertionError(f"no job {job_id!r} in {WORKFLOW.name}")
    return found.group(1)


def step_block(job, name):
    """One step of a job, from its `- name:` line to the next step."""
    found = re.search(
        rf"(?ms)^      - name: {re.escape(name)}\n(.*?)(?=^      - |\Z)", job
    )
    if not found:
        raise AssertionError(f"no step {name!r}")
    return found.group(1)


def run_script(block):
    """The single `run: |` script in a job or step, dedented."""
    runs = re.findall(r"(?m)^( *)run: \|\n((?:\1 .*\n|\s*\n)+)", block)
    if len(runs) != 1:
        raise AssertionError(f"expected one run: | script, found {len(runs)}")
    return textwrap.dedent(runs[0][1])


# The paths whose change makes mypy run.
TYPE_PATHS = (
    "futureagi/**/*.py",
    "futureagi/pyproject.toml",
    "futureagi/uv.lock",
    "futureagi/mypy-baseline.txt",
    "scripts/mypy_baseline.py",
    "scripts/test_mypy_baseline.py",
    ".github/workflows/python-types.yml",
)


class WorkflowTests(unittest.TestCase):
    """The check reports on every PR, so it can be required later; for now it
    is advisory. "Python Types Pass" is the one summary check."""

    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_every_pr_and_every_push_to_dev_triggers_it(self):
        on = top_level_block(self.text, "on")
        pull_request = re.search(r"(?ms)^  pull_request:\n(.*?)(?=^  \S|\Z)", on)
        push = re.search(r"(?ms)^  push:\n(.*?)(?=^  \S|\Z)", on)
        self.assertIsNotNone(pull_request)
        self.assertIsNotNone(push)
        # Retargets arrive as edited events, with changes.base populated.
        self.assertEqual(
            pull_request.group(1).strip(),
            "types: [opened, synchronize, reopened, edited]",
        )
        self.assertEqual(push.group(1).strip(), "branches: [dev]")
        self.assertEqual(re.findall(r"(?m)^  (\S+):", on), ["pull_request", "push"])
        self.assertNotRegex(on, r"(?m)^\s*(paths|paths-ignore)\s*:")
        self.assertNotRegex(on, r"(?m)^\s*branches-ignore\s*:")

    def test_only_ordinary_edits_get_an_isolated_concurrency_group(self):
        concurrency = top_level_block(self.text, "concurrency")
        # Retargets and pushes share the PR group and cancel stale checks.
        # Body/title edits get a unique group, so even a skipped run cannot
        # cancel the active check. Dev pushes retain one group per commit.
        self.assertRegex(
            concurrency,
            r"(?m)^  group: python-types-\$\{\{ github\.event\.action == 'edited' "
            r"&& !github\.event\.changes\.base && github\.run_id "
            r"\|\| github\.event_name == 'push' && github\.sha "
            r"\|\| github\.ref \}\}\s*$",
        )
        self.assertRegex(concurrency, r"(?m)^  cancel-in-progress: true\s*$")

    def test_ordinary_edits_skip_changes_and_the_fail_closed_summary(self):
        guard = "github.event.action != 'edited' || github.event.changes.base"
        changes = job_block(self.text, "changes")
        summary = job_block(self.text, "python-types-pass")
        self.assertRegex(changes, rf"(?m)^    if: {re.escape(guard)}\s*$")
        # Without the same guard, always() would fail on deliberately skipped
        # changes/mypy jobs and leave a red check on an ordinary body edit.
        self.assertRegex(
            summary, rf"(?m)^    if: always\(\) && \({re.escape(guard)}\)\s*$"
        )

    def test_the_header_says_it_is_advisory_and_names_the_summary(self):
        header = self.text.split("\non:", 1)[0]
        self.assertIn("name: Python types (advisory)", header)
        self.assertIn(
            'Advisory for now: "Python Types Pass" is not a required check.', header
        )

    def test_a_changes_job_decides_from_the_type_paths(self):
        changes = job_block(self.text, "changes")
        self.assertIn("uses: dorny/paths-filter@v3", changes)
        self.assertRegex(changes, r"(?m)^      pull-requests: read\b")
        self.assertRegex(
            changes,
            r"(?m)^      python: \$\{\{ \(github\.event_name == 'push' "
            r"\|\| github\.event\.changes\.base\) && 'true' "
            r"\|\| steps\.filter\.outputs\.python \}\}\s*$",
        )
        # Pushes and retargets force mypy; other PR events use the path filter.
        steps = re.findall(r"(?ms)^      - (.*?)(?=^      - |\Z)", changes)
        self.assertEqual(len(steps), 2)
        for step in steps:
            self.assertRegex(
                step, r"(?m)^        if: github\.event_name == 'pull_request'\s*$"
            )
        filters = changes.split("            python:\n", 1)[1]
        listed = re.findall(r"(?m)^              - '([^']+)'\s*$", filters)
        self.assertEqual(sorted(listed), sorted(TYPE_PATHS))

    def test_mypy_runs_only_when_changes_says_so(self):
        job = job_block(self.text, "mypy-baseline")
        self.assertRegex(job, r"(?m)^    name: Mypy \(baseline\)\s*$")
        self.assertRegex(job, r"(?m)^    needs: changes\s*$")
        self.assertRegex(
            job, r"(?m)^    if: needs\.changes\.outputs\.python == 'true'\s*$"
        )

    def test_mypy_runs_on_the_whole_repo_and_its_status_reaches_the_filter(self):
        job = job_block(self.text, "mypy-baseline")
        self.assertIn("run: uv sync --frozen --no-dev --extra dev", job)
        self.assertIn("python3 -I scripts/test_mypy_baseline.py", job)
        script = run_script(step_block(job, "Run mypy and compare with the baseline"))
        self.assertIn(
            '.venv/bin/mypy . > "$RUNNER_TEMP/mypy-output.txt" || status=$?', script
        )
        self.assertRegex(
            script,
            r"python3 -I \.\./scripts/mypy_baseline\.py filter \\\n\s+"
            r'--mypy-exit-code "\$status" --input "\$RUNNER_TEMP/mypy-output\.txt"\n',
        )
        # The filter is the step's last command, so its exit status is the step's.
        self.assertTrue(
            script.rstrip().endswith('--input "$RUNNER_TEMP/mypy-output.txt"')
        )

    def test_mypy_loads_the_production_settings_with_only_the_placeholder_key(self):
        pyproject = (ROOT / "futureagi" / "pyproject.toml").read_text(encoding="utf-8")
        stubs = re.search(
            r"(?ms)^\[tool\.django-stubs\]\n(.*?)(?=^\[|\Z)", pyproject
        ).group(1)
        self.assertEqual(
            re.findall(r"(?m)^django_settings_module = (.+)$", stubs),
            ['"tfc.settings.settings"'],
        )
        step = step_block(
            job_block(self.text, "mypy-baseline"),
            "Run mypy and compare with the baseline",
        )
        env = re.search(r"(?ms)^        env:\n(.*?)(?=^        \S)", step).group(1)
        names = re.findall(r"(?m)^          ([A-Z_][A-Z0-9_]*):", env)
        self.assertEqual(names, ["EE_LICENSE_KEY"], "set nothing else")
        self.assertRegex(env, r"(?m)^          EE_LICENSE_KEY: mypy-placeholder\s*$")
        # stderr (the settings' log lines) stays out of the parsed output.
        self.assertNotIn("2>&1", step)

    def test_the_self_test_runs_before_mypy_with_the_real_mypy(self):
        job = job_block(self.text, "mypy-baseline")
        steps = re.findall(r"(?m)^      - (?:name: (.+)|uses: (.+))$", job)
        names = [name or uses for name, uses in steps]
        self.assertLess(
            names.index("Install backend and typing dependencies"),
            names.index("Self-test the baseline script"),
        )
        self.assertLess(
            names.index("Self-test the baseline script"),
            names.index("Run mypy and compare with the baseline"),
        )
        self_test = step_block(job, "Self-test the baseline script")
        self.assertRegex(
            self_test, r"(?m)^          MYPY_BIN: futureagi/\.venv/bin/mypy\s*$"
        )

    def test_no_mypy_cache_is_restored(self):
        # Every run is cold; a stale cache must never hide an error.
        self.assertNotRegex(self.text, r"uses: actions/cache")
        self.assertNotRegex(self.text, r"(?m)^\s+path: .*mypy_cache")

    def test_the_mypy_version_matches_the_lock_file(self):
        pinned = re.search(r'(?m)^  MYPY_VERSION: "([^"]+)"', self.text).group(1)
        lock = (ROOT / "futureagi" / "uv.lock").read_text(encoding="utf-8")
        locked = re.search(r'(?m)^name = "mypy"\nversion = "([^"]+)"', lock).group(1)
        self.assertEqual(pinned, locked)

    def test_the_summary_runs_after_both_jobs_except_on_ordinary_edits(self):
        names = re.findall(
            r"(?m)^    name: Python Types Pass\s*$", top_level_block(self.text, "jobs")
        )
        self.assertEqual(
            len(names), 1, "exactly one job may carry the summary check's name"
        )
        summary = job_block(self.text, "python-types-pass")
        self.assertRegex(summary, r"(?m)^    name: Python Types Pass\s*$")
        self.assertRegex(summary, r"(?m)^    needs: \[changes, mypy-baseline\]\s*$")
        # always(), not !cancelled(): a cancelled run must fail the check, not skip it.
        self.assertRegex(
            summary,
            r"(?m)^    if: always\(\) && \(github\.event\.action != 'edited' "
            r"\|\| github\.event\.changes\.base\)\s*$",
        )
        self.assertRegex(
            summary, r"(?m)^          CHANGES: \$\{\{ needs\.changes\.result \}\}\s*$"
        )
        self.assertRegex(
            summary,
            r"(?m)^          PYTHON_CHANGED: \$\{\{ needs\.changes\.outputs\.python \}\}\s*$",
        )
        self.assertRegex(
            summary,
            r"(?m)^          MYPY: \$\{\{ needs\.mypy-baseline\.result \}\}\s*$",
        )

    def test_nothing_turns_a_failure_into_a_pass(self):
        self.assertNotIn("continue-on-error", self.text)
        self.assertNotRegex(self.text, r"\|\|\s*(true\b|:(?=\s|$))")
        self.assertNotRegex(self.text, r"(?m)^\s*set \+e\b")

    def test_the_summary_fails_closed(self):
        script = run_script(job_block(self.text, "python-types-pass"))
        cases = [
            # (changes result, python output, mypy-baseline result, passes)
            ("success", "true", "success", True),
            ("success", "false", "skipped", True),
            ("success", "true", "failure", False),
            ("success", "true", "cancelled", False),
            ("success", "true", "skipped", False),
            ("success", "", "skipped", False),
            ("success", "false", "failure", False),
            ("failure", "", "skipped", False),
            ("cancelled", "", "skipped", False),
            ("skipped", "", "skipped", False),
            ("failure", "true", "success", False),
            ("failure", "false", "skipped", False),
            ("cancelled", "false", "skipped", False),
            ("skipped", "false", "skipped", False),
        ]
        with tempfile.TemporaryDirectory(prefix="types-summary-test-") as tmp:
            path = Path(tmp) / "summary.sh"
            path.write_text(script, encoding="utf-8")
            for changes, python, mypy, passes in cases:
                proc = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(path)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={
                        "PATH": os.environ["PATH"],
                        "CHANGES": changes,
                        "PYTHON_CHANGED": python,
                        "MYPY": mypy,
                    },
                )
                with self.subTest(changes=changes, python=python, mypy=mypy):
                    self.assertEqual(
                        proc.returncode, 0 if passes else 1, proc.stdout + proc.stderr
                    )
                    if not passes:
                        self.assertIn("::error::", proc.stdout)


class LocalToolingTests(unittest.TestCase):
    def test_make_targets_run_the_filter_and_sync(self):
        makefile = (ROOT / "futureagi" / "Makefile").read_text(encoding="utf-8")
        for target, command in (("mypy", "filter"), ("mypy-baseline-sync", "sync")):
            with self.subTest(target=target):
                recipe = re.search(
                    rf"(?ms)^{re.escape(target)}:\n((?:\t.*\n)+)", makefile
                ).group(1)
                self.assertIn(
                    f"../scripts/mypy_baseline.py {command} --mypy-exit-code", recipe
                )
                # The venv's mypy, under the same settings environment as CI.
                self.assertIn(
                    "EE_LICENSE_KEY=mypy-placeholder .venv/bin/mypy . > "
                    ".mypy_cache/mypy-output.txt",
                    recipe,
                )
                self.assertIn("test -x .venv/bin/mypy ||", recipe)
                self.assertNotIn("2>&1", recipe)
        self.assertNotIn("check_mypy.sh", makefile)
        self.assertNotIn("generate_mypy_baseline.sh", makefile)
        self.assertNotIn("mypy-update-baseline", makefile)

    def test_the_pre_commit_config_does_not_run_whole_repo_mypy(self):
        config = (ROOT / "futureagi" / ".pre-commit-config.yaml").read_text(
            encoding="utf-8"
        )
        hooks = [
            line for line in config.splitlines() if not line.lstrip().startswith("#")
        ]
        self.assertFalse(
            [line for line in hooks if "mypy" in line], "no hook may run mypy"
        )

    def test_the_old_tools_are_gone(self):
        for path in (
            "futureagi/bin/check_mypy.sh",
            "futureagi/bin/generate_mypy_baseline.sh",
            "scripts/check_python_types_delta.py",
            "scripts/test_check_python_types_delta.py",
        ):
            self.assertFalse((ROOT / path).exists(), path)


@unittest.skipUnless(
    MYPY_BIN or REQUIRE_MYPY, "set MYPY_BIN to run the real-mypy tests"
)
class RealMypyTests(TempDirTestCase):
    """The real mypy's output parses: errors, notes, the summary and an abort."""

    def mypy(self, files):
        if not MYPY_BIN:
            self.fail("CI is set but MYPY_BIN is not")
        project = self.dir / "project"
        project.mkdir(exist_ok=True)
        for name, text in files.items():
            (project / name).write_text(textwrap.dedent(text), encoding="utf-8")
        (project / "mypy.ini").write_text("[mypy]\n", encoding="utf-8")
        proc = subprocess.run(
            [
                MYPY_BIN,
                "--config-file",
                "mypy.ini",
                "--no-incremental",
                "--cache-dir",
                os.devnull,
                ".",
            ],
            check=False,
            cwd=project,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return proc.returncode, proc.stdout

    def test_errors_and_notes(self):
        code, out = self.mypy(
            {
                "a.py": """\
                    def f(a: int) -> str:
                        return a

                    reveal_type(f)
                    x: int = "s"
                    y: int = "t"
                """
            }
        )
        self.assertEqual(code, 1, out)
        self.assertIn(": note: ", out)
        errors = mb.parse_output(out, code)
        self.assertEqual(len(errors), 3, out)
        self.assertEqual(
            sorted(e.entry for e in errors),
            [
                'a.py:0: error: Incompatible return value type (got "int", expected "str")  [return-value]',
                'a.py:0: error: Incompatible types in assignment (expression has type "str", variable has type "int")  [assignment]',
                'a.py:0: error: Incompatible types in assignment (expression has type "str", variable has type "int")  [assignment]',
            ],
        )
        self.assertEqual(self.sync(out, "--mypy-exit-code", str(code))[0], 0)
        self.assertEqual(self.filter(out, "--mypy-exit-code", str(code))[0], 0)

        # One more of a baselined error fails; moving lines does not.
        code, out = self.mypy(
            {
                "a.py": """\
                    z = 1

                    def f(a: int) -> str:
                        return a

                    x: int = "s"
                    y: int = "t"
                    w: int = "u"
                """
            }
        )
        status, report, _ = self.filter(out, "--mypy-exit-code", str(code))
        self.assertEqual(status, 1, report)
        self.assertIn("3 now, baseline allows 2", report)
        self.assertIn("a.py:8: error: Incompatible types in assignment", report)

    def test_a_clean_run(self):
        code, out = self.mypy({"a.py": "x: int = 1\n"})
        self.assertEqual(code, 0, out)
        self.assertEqual(mb.parse_output(out, code), [])

    def test_a_blocking_error_is_incomplete(self):
        code, out = self.mypy({"a.py": "def f(:\n", "b.py": "x: int = 's'\n"})
        self.assertNotIn(code, (0, 1), out)
        with self.assertRaises(mb.Incomplete):
            mb.parse_output(out, code)
        with self.assertRaises(mb.Incomplete):
            mb.parse_output(out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
