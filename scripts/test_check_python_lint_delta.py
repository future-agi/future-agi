"""Tests for scripts/check_python_lint_delta.py.

Run from the repo root: python3 -I scripts/test_check_python_lint_delta.py

The end-to-end tests build a throwaway git repo and run the real ruff. They are
the gate's canary: they prove it fails on a new violation and passes on an old
one. In CI (when CI is set) a missing ruff fails the run instead of skipping.
"""

import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_python_lint_delta.py"

spec = importlib.util.spec_from_file_location("check_python_lint_delta", SCRIPT)
gate = importlib.util.module_from_spec(spec)
sys.modules["check_python_lint_delta"] = gate
spec.loader.exec_module(gate)

RUFF = shutil.which("ruff")
REQUIRE_RUFF = bool(os.environ.get("CI"))


def v(path, code, line, source):
    return gate.Violation(
        path=path, code=code, line=line, column=1, message="m", source=source
    )


class PureFunctionTests(unittest.TestCase):
    def test_test_and_migration_paths_are_left_out_of_the_banned_pass(self):
        for path in (
            "futureagi/app/tests/test_x.py",
            "futureagi/app/test/helpers.py",
            "futureagi/app/test_views.py",
            "futureagi/app/views_test.py",
            "futureagi/conftest.py",
            "futureagi/app/migrations/0001_initial.py",
        ):
            self.assertTrue(gate.is_test_or_migration(path), path)
        for path in (
            "futureagi/app/views.py",
            "futureagi/app/testing_utils.py",
            "futureagi/contest.py",
        ):
            self.assertFalse(gate.is_test_or_migration(path), path)

    def test_an_unchanged_violation_is_not_new(self):
        base = [v("futureagi/a.py", "BLE001", 4, "except Exception:")]
        head = [v("futureagi/a.py", "BLE001", 9, "except Exception:")]
        self.assertEqual(
            gate.new_violations(head, base, {"futureagi/a.py": "futureagi/a.py"}, {}),
            [],
        )

    def test_an_extra_copy_is_new_and_taken_from_changed_lines(self):
        base = [v("futureagi/a.py", "BLE001", 4, "except Exception:")]
        head = [
            v("futureagi/a.py", "BLE001", 4, "except Exception:"),
            v("futureagi/a.py", "BLE001", 11, "except Exception:"),
        ]
        found = gate.new_violations(
            head,
            base,
            {"futureagi/a.py": "futureagi/a.py"},
            {"futureagi/a.py": {11: 0}},
        )
        self.assertEqual([x.line for x in found], [11])

    def test_new_code_outranks_a_line_that_only_gained_a_comment(self):
        base = [v("futureagi/a.py", "BLE001", 4, "except Exception:")]
        head = [
            v("futureagi/a.py", "BLE001", 4, "except Exception:"),
            v("futureagi/a.py", "BLE001", 11, "except Exception:"),
        ]
        ranks = {"futureagi/a.py": {4: 1, 11: 0}}
        found = gate.new_violations(
            head, base, {"futureagi/a.py": "futureagi/a.py"}, ranks
        )
        self.assertEqual([x.line for x in found], [11])

    def test_line_ranks_tell_new_code_from_a_comment_edit(self):
        diff = (
            "@@ -4 +4 @@ def first():\n"
            "-    except Exception:\n"
            "+    except Exception:  # existing fallback\n"
            "@@ -5,0 +6,3 @@\n"
            "+\n"
            "+def second():\n"
            "+    pass"
        )
        self.assertEqual(gate.line_ranks(diff), {4: 1, 6: 0, 7: 0, 8: 0})

    def test_a_renamed_file_keeps_its_old_violations(self):
        base = [v("futureagi/old.py", "T201", 2, "print(x)")]
        head = [v("futureagi/new.py", "T201", 2, "print(x)")]
        self.assertEqual(
            gate.new_violations(
                head, base, {"futureagi/new.py": "futureagi/old.py"}, {}
            ),
            [],
        )

    def test_a_different_rule_on_the_same_line_is_new(self):
        base = [v("futureagi/a.py", "S110", 4, "except Exception:")]
        head = [v("futureagi/a.py", "BLE001", 4, "except Exception:")]
        found = gate.new_violations(
            head, base, {"futureagi/a.py": "futureagi/a.py"}, {}
        )
        self.assertEqual([x.code for x in found], ["BLE001"])

    def test_normalize_drops_the_comment_but_not_a_hash_in_a_string(self):
        self.assertEqual(
            gate.normalize("    except Exception:  # existing fallback"),
            "except Exception:",
        )
        self.assertEqual(gate.normalize('x = "a # b"  # c'), 'x = "a # b"')
        self.assertEqual(gate.normalize('q = "\\"#"  # c'), 'q = "\\"#"')
        self.assertEqual(gate.normalize('z = """#"""'), 'z = """#"""')
        self.assertEqual(gate.normalize("a  =   1"), "a = 1")

    def test_a_finding_reported_by_both_passes_counts_once(self):
        a = v("futureagi/a.py", "T201", 2, "print(x)")
        self.assertEqual(gate.dedupe([a, a]), [a])
        b = v("futureagi/a.py", "T201", 3, "print(x)")
        self.assertEqual(gate.dedupe([a, b]), [a, b])

    def test_annotation_escapes_and_carries_the_replacement(self):
        line = gate.annotation(v("futureagi/a,b.py", "BLE001", 3, "x"))
        self.assertTrue(
            line.startswith(
                "::error file=futureagi/a%2Cb.py,line=3,col=1,title=ruff BLE001 (new in this change)::"
            )
        )
        self.assertIn("logger.exception", line)
        self.assertNotIn("\n", line)

    def test_a_warning_only_rule_is_annotated_as_a_warning(self):
        self.assertTrue(
            gate.annotation(v("futureagi/a.py", "S608", 3, "x")).startswith(
                "::warning "
            )
        )
        self.assertTrue(
            gate.annotation(v("futureagi/a.py", "BLE001", 3, "x")).startswith(
                "::error "
            )
        )

    def test_every_banned_rule_has_a_replacement_hint(self):
        for code in gate.BANNED_RULES:
            self.assertIn(code, gate.HINTS, code)

    def test_ci_and_the_pre_commit_hook_pin_the_same_ruff(self):
        workflow = (ROOT / ".github" / "workflows" / "python-lint.yml").read_text()
        hook = (ROOT / "scripts" / "lint-staged-python.sh").read_text()
        pinned = re.search(r'RUFF_VERSION:\s*"([0-9.]+)"', workflow).group(1)
        self.assertEqual(
            re.search(r'^RUFF_VERSION="([0-9.]+)"$', hook, re.MULTILINE).group(1),
            pinned,
        )
        self.assertIn('"ruff==${RUFF_VERSION}"', hook)
        self.assertIn('"ruff@${RUFF_VERSION}"', hook)
        self.assertEqual(gate.RUFF_VERSION, pinned)

    def test_formatting_counts_stay_per_file_and_rule(self):
        base = [v("futureagi/a.py", "F811", 2, "old")]
        head = [
            v("futureagi/a.py", "F821", 3, "formatted"),
            v("futureagi/b.py", "F811", 3, "formatted"),
        ]
        files = {path: path for path in ("futureagi/a.py", "futureagi/b.py")}
        found = gate.new_violations(head, base, files, {}, set(files))
        self.assertEqual(
            [(x.path, x.code) for x in found], [(x.path, x.code) for x in head]
        )

    def test_formatting_cannot_increase_even_an_advisory_rule(self):
        path = "futureagi/a.py"
        found = gate.new_violations(
            [v(path, "S608", 3, "sql")], [], {path: path}, {}, {path}
        )
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0].is_warning)
        self.assertTrue(gate.annotation(found[0]).startswith("::error "))
        with (
            patch.object(gate, "check", return_value=found),
            patch.object(
                gate,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, stdout=str(ROOT)),
                    subprocess.CompletedProcess(
                        [], 0, stdout=f"ruff {gate.RUFF_VERSION}"
                    ),
                ],
            ),
            patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}),
            patch("sys.stdout", new_callable=io.StringIO),
        ):
            self.assertEqual(gate.main(["--base", "test"]), 1)

    def test_cli_cannot_override_the_pinned_formatter(self):
        for requested in ("", "0.17.2", gate.RUFF_VERSION):
            with (
                self.subTest(requested=requested),
                patch.object(gate, "check", return_value=[]) as check,
                patch.object(
                    gate,
                    "run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, stdout=str(ROOT)),
                        subprocess.CompletedProcess([], 0, stdout="ruff 0.17.2"),
                    ],
                ),
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                code = gate.main(["--base", "test", "--expect-ruff-version", requested])
                self.assertEqual(code, 2)
                check.assert_not_called()

    def test_formatter_output_must_match_bytes_not_normalized_newlines(self):
        with tempfile.TemporaryDirectory(prefix="lint-bytes-test-") as tmp:
            base = Path(tmp) / "base"
            head = Path(tmp) / "head"
            path = "futureagi/a.py"
            for tree in (base, head):
                (tree / path).parent.mkdir(parents=True)
            (base / path).write_bytes(b"x=1\r\n")
            (head / path).write_bytes(b"x = 1\n")
            proc = subprocess.CompletedProcess([], 0, stdout=b"x = 1\r\n", stderr=b"")
            with (
                patch.object(gate, "ruff_discovered", return_value={path}),
                patch.object(gate.subprocess, "run", return_value=proc) as run,
            ):
                found = gate.formatting_only_files(
                    "ruff", head, base, {path: path}, set()
                )
            self.assertEqual(found, set())
            self.assertEqual(run.call_args.kwargs["input"], b"x=1\r\n")
            self.assertEqual((base / path).read_bytes(), b"x=1\r\n")

    def test_a_moved_file_never_reaches_the_formatting_comparison(self):
        # Another path can fall under other lint settings, so identical bytes
        # prove nothing; nothing is read or run for it.
        with tempfile.TemporaryDirectory(prefix="lint-move-test-") as tmp:
            trees = Path(tmp)
            with patch.object(gate.subprocess, "run") as run:
                found = gate.formatting_only_files(
                    "ruff",
                    trees / "head",
                    trees / "base",
                    {"futureagi/app/helper.py": "futureagi/app/tests/helper.py"},
                    set(),
                )
            self.assertEqual(found, set())
            run.assert_not_called()


WORKFLOW = ROOT / ".github" / "workflows" / "python-lint.yml"

# The paths whose change makes Ruff run: Python under futureagi/, its ruff
# config, and the gate, its tests, the pre-commit hook and this workflow.
LINT_PATHS = (
    "futureagi/**/*.py",
    "futureagi/pyproject.toml",
    "futureagi/**/pyproject.toml",
    "futureagi/**/ruff.toml",
    "futureagi/**/.ruff.toml",
    "pyproject.toml",
    "ruff.toml",
    ".ruff.toml",
    "scripts/check_python_lint_delta.py",
    "scripts/test_check_python_lint_delta.py",
    "scripts/lint-staged-python.sh",
    ".github/workflows/python-lint.yml",
    # RuffPinTests checks these two match the CI ruff pin.
    "futureagi/Makefile",
    "futureagi/.pre-commit-config.yaml",
)


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


def run_script(block):
    """The single `run: |` script in a job, dedented."""
    runs = re.findall(r"(?m)^( *)run: \|\n((?:\1 .*\n|\s*\n)+)", block)
    if len(runs) != 1:
        raise AssertionError(f"expected one run: | script, found {len(runs)}")
    return textwrap.dedent(runs[0][1])


class WorkflowTests(unittest.TestCase):
    """A required check must report on every PR, so the workflow always runs and
    a summary job, "Python Lint Pass", is the one check to require."""

    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text()

    def test_the_workflow_has_no_paths_or_branches_filter(self):
        on = top_level_block(self.text, "on")
        self.assertRegex(on, r"(?m)^  pull_request:\s*$")
        self.assertRegex(
            on, r"(?m)^    types: \[opened, synchronize, reopened, edited\]\s*$"
        )
        # A workflow-level paths filter means no check at all on a PR without
        # Python changes, and a required check that never starts never passes.
        self.assertNotRegex(on, r"(?m)^\s*(paths|paths-ignore)\s*:")
        # Stacked PRs into feature branches get the check too.
        self.assertNotRegex(on, r"(?m)^\s*(branches|branches-ignore)\s*:")

    def test_the_header_names_the_check_to_require(self):
        header = self.text.split("\non:", 1)[0]
        self.assertIn('The check to require is "Python Lint Pass"', header)
        self.assertNotIn("Advisory", header)

    def test_a_changes_job_decides_from_the_lint_paths(self):
        changes = job_block(self.text, "changes")
        self.assertIn("uses: dorny/paths-filter@v3", changes)
        self.assertRegex(changes, r"(?m)^      pull-requests: read\b")
        self.assertRegex(
            changes,
            r"(?m)^      python: \$\{\{ steps\.filter\.outputs\.python \}\}\s*$",
        )
        self.assertNotRegex(changes, r"(?m)^    if:", "the changes job must always run")
        filters = changes.split("            python:\n", 1)[1]
        listed = re.findall(r"(?m)^              - '([^']+)'\s*$", filters)
        self.assertEqual(sorted(listed), sorted(LINT_PATHS))

    def test_ruff_runs_only_when_changes_says_so(self):
        ruff = job_block(self.text, "ruff-delta")
        self.assertRegex(ruff, r"(?m)^    name: Ruff \(new violations only\)\s*$")
        self.assertRegex(ruff, r"(?m)^    needs: changes\s*$")
        self.assertRegex(
            ruff, r"(?m)^    if: needs\.changes\.outputs\.python == 'true'\s*$"
        )

    def test_the_summary_job_always_runs_after_both_jobs(self):
        names = re.findall(
            r"(?m)^    name: Python Lint Pass\s*$", top_level_block(self.text, "jobs")
        )
        self.assertEqual(
            len(names), 1, "exactly one job may carry the required check's name"
        )
        summary = job_block(self.text, "python-lint-pass")
        self.assertRegex(summary, r"(?m)^    name: Python Lint Pass\s*$")
        self.assertRegex(
            summary, r"(?m)^    needs: \[changes, ruff-delta, ruff-self-test\]\s*$"
        )
        self.assertRegex(
            summary,
            r"(?m)^          SELF_TEST: \$\{\{ needs\.ruff-self-test\.result \}\}\s*$",
        )
        # always(), not !cancelled(): a cancelled run must fail the check, not skip it.
        self.assertRegex(summary, r"(?m)^    if: always\(\)\s*$")
        # The script below reads these; wired to anything else, its test proves nothing.
        self.assertRegex(
            summary, r"(?m)^          CHANGES: \$\{\{ needs\.changes\.result \}\}\s*$"
        )
        self.assertRegex(
            summary,
            r"(?m)^          PYTHON_CHANGED: \$\{\{ needs\.changes\.outputs\.python \}\}\s*$",
        )
        self.assertRegex(
            summary, r"(?m)^          RUFF: \$\{\{ needs\.ruff-delta\.result \}\}\s*$"
        )

    def test_the_gate_runs_from_the_base_branch(self):
        # A PR branched before the gate has no scripts/check_python_lint_delta.py,
        # and a PR that edits the gate must not be judged by its own edit.
        ruff = job_block(self.text, "ruff-delta")
        self.assertIn('trusted_ref="$(git rev-parse "origin/${BASE_REF}")"', ruff)
        self.assertIn(
            'git show "${trusted_ref}:scripts/check_python_lint_delta.py" > "$gate"',
            ruff,
        )
        self.assertIn(
            'git show "${trusted_ref}:.github/workflows/python-lint.yml"', ruff
        )
        self.assertIn(
            'python3 -I "$gate" --base "$base" "${config_ref_args[@]}" --ruff "$venv/bin/ruff" --expect-ruff-version "$RUFF_VERSION"',
            ruff,
        )
        self.assertIn('if grep -q -- "--config-ref" "$gate"; then', ruff)
        self.assertNotRegex(ruff, r"python3 -I scripts/check_python_lint_delta\.py")
        # No head-provided executable runs before the reporting step in this job.
        self.assertNotIn("scripts/test_check_python_lint_delta.py", ruff)
        self.assertNotIn("Install ruff for head self-tests", ruff)
        self.assertNotIn("Self-test the gate", ruff)
        self.assertLess(
            ruff.index("actions/checkout@v4"), ruff.index("Report new violations")
        )
        self.assertNotRegex(
            ruff.split("      - name: Report new violations", 1)[0], r"(?m)^\s*run:"
        )
        self.assertIn(
            "if [ -f scripts/test_check_python_lint_delta.py ]; then",
            job_block(self.text, "ruff-self-test"),
        )
        self.assertIn(
            "python3 -I scripts/test_check_python_lint_delta.py",
            job_block(self.text, "ruff-self-test"),
        )

    def test_nothing_turns_a_failure_into_a_pass(self):
        # continue-on-error on any job or step can report a real failure as
        # success to the summary, or make the summary itself green regardless.
        self.assertNotIn("continue-on-error", self.text)
        # The gate's exit status is the check: nothing may swallow it.
        self.assertNotRegex(self.text, r"\|\|\s*(true\b|:(?=\s|$))")
        self.assertNotRegex(self.text, r"(?m)^\s*set \+e\b")

    def test_the_summary_fails_closed(self):
        script = run_script(job_block(self.text, "python-lint-pass"))
        cases = [
            # (changes result, python output, gate result, test result, passes)
            ("success", "true", "success", "success", True),
            ("success", "false", "skipped", "skipped", True),
            ("success", "true", "failure", "success", False),
            ("success", "true", "success", "failure", False),
            ("success", "true", "success", "cancelled", False),
            ("success", "true", "cancelled", "success", False),
            ("success", "true", "skipped", "success", False),
            ("success", "true", "success", "skipped", False),
            ("success", "", "skipped", "skipped", False),
            ("success", "false", "failure", "skipped", False),
            ("success", "false", "skipped", "failure", False),
            ("success", "false", "success", "success", False),
            ("failure", "", "skipped", "skipped", False),
            ("cancelled", "", "skipped", "skipped", False),
            ("skipped", "", "skipped", "skipped", False),
            ("failure", "true", "success", "success", False),
            ("failure", "false", "skipped", "skipped", False),
        ]
        with tempfile.TemporaryDirectory(prefix="lint-summary-test-") as tmp:
            path = Path(tmp) / "summary.sh"
            path.write_text(script)
            for changes, python, ruff, self_test, passes in cases:
                # The shell GitHub runs a step's script with.
                proc = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(path)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env={
                        "PATH": os.environ["PATH"],
                        "CHANGES": changes,
                        "PYTHON_CHANGED": python,
                        "RUFF": ruff,
                        "SELF_TEST": self_test,
                    },
                )
                with self.subTest(
                    changes=changes, python=python, ruff=ruff, self_test=self_test
                ):
                    self.assertEqual(
                        proc.returncode, 0 if passes else 1, proc.stdout + proc.stderr
                    )
                    if not passes:
                        self.assertIn("::error::", proc.stdout)


BASE_MOD = textwrap.dedent(
    """\
    def first():
        try:
            return 1
        except Exception:
            return 0
    """
)

HEAD_MOD = BASE_MOD + textwrap.dedent(
    """\


    def second(cursor, name):
        try:
            cursor.execute(f"SELECT * FROM t WHERE name = '{name}'")
        except Exception:
            return None
        print(name)
        return name
    """
)

CONFIG = textwrap.dedent(
    """\
    [tool.ruff]
    line-length = 88
    exclude = ["migrations"]

    [tool.ruff.lint]
    select = ["E", "F", "B"]

    [tool.ruff.lint.per-file-ignores]
    "app/cli.py" = ["T201"]
    """
)


class RepositoryFixture:
    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "c")
        return self.git("rev-parse", "HEAD")


@unittest.skipUnless(RUFF or REQUIRE_RUFF, "ruff is not installed")
class EndToEndTests(RepositoryFixture, unittest.TestCase):
    def setUp(self):
        if not RUFF:
            self.fail(
                "ruff must be on PATH in CI: the canary would otherwise prove nothing"
            )
        self.tmp = tempfile.TemporaryDirectory(prefix="lint-delta-test-")
        self.repo = Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self, base, config_ref=None):
        args = [sys.executable, "-I", str(SCRIPT), "--base", base]
        if config_ref is not None:
            args.extend(["--config-ref", config_ref])
        proc = subprocess.run(
            args,
            cwd=self.repo,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "GITHUB_STEP_SUMMARY": ""},
        )
        found = re.findall(
            r"^::(error|warning) file=([^,]+),line=(\d+),col=\d+,title=ruff ([A-Za-z0-9-]+)",
            proc.stdout,
            re.MULTILINE,
        )
        self.warnings = [
            (f, int(n), c) for level, f, n, c in found if level == "warning"
        ]
        return (
            proc.returncode,
            [(f, int(n), c) for level, f, n, c in found if level == "error"],
            proc,
        )

    def test_fails_on_new_violations_only(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/mod.py", BASE_MOD)
        self.write("futureagi/app/old_ugly.py", "y=2\n")
        base = self.commit()

        # A comment on the old except line must not draw the new finding to it.
        self.write(
            "futureagi/app/mod.py",
            HEAD_MOD.replace(
                "except Exception:\n        return 0",
                "except Exception:  # existing fallback\n        return 0",
            ),
        )
        self.write(
            "futureagi/app/old_ugly.py", "y=3\n"
        )  # unformatted at the base too: not new
        self.write("futureagi/app/new_ugly.py", "x=1\n")  # new and unformatted: new
        self.write(
            "futureagi/app/tests/test_mod.py", 'def test_x():\n    print("x")\n'
        )  # tests: no T201
        self.write("futureagi/app/migrations/0001_x.py", 'print("x")\n')  # excluded
        self.write(
            "futureagi/app/cli.py", 'print("x")\n'
        )  # per-file-ignores survive --select
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual(code, 1, proc.stdout + proc.stderr)
        self.assertEqual(
            sorted(found),
            sorted(
                [
                    ("futureagi/app/mod.py", 11, "BLE001"),
                    ("futureagi/app/mod.py", 13, "T201"),
                    ("futureagi/app/new_ugly.py", 1, "FORMAT"),
                ]
            ),
            proc.stdout,
        )
        self.assertEqual(
            self.warnings, [("futureagi/app/mod.py", 10, "S608")], proc.stdout
        )

    def test_a_warning_only_finding_does_not_fail_the_check(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/q.py", "def q(table):\n    return 1\n")
        base = self.commit()
        self.write(
            "futureagi/app/q.py",
            'def q(table):\n    return f"SELECT count() FROM {table}"\n',
        )
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)
        self.assertEqual(
            self.warnings, [("futureagi/app/q.py", 2, "S608")], proc.stdout
        )

    def test_passes_when_only_old_violations_remain(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/mod.py", BASE_MOD)
        base = self.commit()
        # A new comment line above, and a comment added to the old except line.
        head = "# A comment that changes nothing.\n" + BASE_MOD.replace(
            "except Exception:", "except Exception:  # existing fallback"
        )
        self.write("futureagi/app/mod.py", head)
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)

    def format_file(self, path):
        subprocess.run(
            [RUFF, "format", "--no-cache", path],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )

    def test_exact_reformat_preserves_duplicate_wrapped_and_requoted_findings(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/app/mod.py"
        original = "print('first')\nprint('first')\nprint('" + "long " * 22 + "')\n"
        self.write(path, original)
        base = self.commit()
        self.format_file(path)
        self.assertNotEqual((self.repo / path).read_text(), original)
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)
        self.assertEqual(self.warnings, [])

    def test_exact_reformat_that_exposes_a_suppressed_violation_fails(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/app/tests/test_fixture.py"
        # Reduced from the two fixture files excluded from #3980. Wrapping the
        # signature moves noqa off the parameter where Ruff reports F811.
        self.write(
            path,
            "from fixtures import users_engine\n\n\n"
            "def test_user_detail_graph_uses_complete_physical_winners(users_engine, replacement, filter_kind):  # noqa: F811\n"
            "    return users_engine, replacement, filter_kind\n",
        )
        base = self.commit()
        old = gate.ruff_check(RUFF, self.repo, [path], None)
        self.assertFalse([x for x in old if x.code == "F811"])
        self.format_file(path)
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual(code, 1, proc.stdout + proc.stderr)
        self.assertEqual([(f, c) for f, _, c in found], [(path, "F811")], proc.stdout)

    def test_same_count_semantic_edit_does_not_get_count_only_matching(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/app/mod.py"
        self.write(path, "print('old')\n")
        base = self.commit()
        self.write(path, "print('new')\n")
        self.format_file(path)
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual(
            (code, found), (1, [(path, 1, "T201")]), proc.stdout + proc.stderr
        )

    def test_formatting_one_file_does_not_relax_another_file(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/formatted.py", "print('old')\n")
        self.write("futureagi/app/mixed.py", "print('old')\n")
        base = self.commit()
        self.format_file("futureagi/app/formatted.py")
        self.write("futureagi/app/mixed.py", 'print("new")\n')
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual(
            (code, found),
            (1, [("futureagi/app/mixed.py", 1, "T201")]),
            proc.stdout + proc.stderr,
        )

    def test_new_exclusion_cannot_hide_a_new_finding(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        path = "futureagi/app/hidden.py"
        self.write(path, 'print("new")\n')
        self.write(
            "futureagi/pyproject.toml",
            CONFIG.replace(
                'exclude = ["migrations"]', 'exclude = ["migrations", "hidden.py"]'
            ),
        )
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual(
            (code, found), (1, [(path, 1, "T201")]), proc.stdout + proc.stderr
        )

    def test_nested_config_cannot_hide_a_new_finding(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        for config_name, config_text in (
            ("ruff.toml", 'exclude = ["hidden.py"]\n'),
            (".ruff.toml", 'exclude = ["hidden.py"]\n'),
            ("pyproject.toml", '[tool.ruff]\nexclude = ["hidden.py"]\n'),
        ):
            with self.subTest(config=config_name):
                path = "futureagi/app/hidden.py"
                self.write(path, 'print("new")\n')
                self.write("futureagi/app/" + config_name, config_text)
                self.commit()
                code, found, proc = self.gate(base)
                self.assertEqual(
                    (code, found), (1, [(path, 1, "T201")]), proc.stdout + proc.stderr
                )
                self.git("reset", "--hard", base)

    def test_full_head_tree_keeps_relative_import_resolution(self):
        config = CONFIG.replace(
            'select = ["E", "F", "B"]', 'select = ["E", "F", "B", "TID251"]'
        )
        config += "\n[tool.ruff.lint.flake8-tidy-imports.banned-api]\n"
        config += '"accounts.services.token_service.issue_sos_tokens" = { msg = "Use another API." }\n'
        self.write("futureagi/pyproject.toml", config)
        self.write("futureagi/accounts/__init__.py", "")
        self.write("futureagi/accounts/services/__init__.py", "")
        self.write(
            "futureagi/accounts/services/token_service.py",
            "def issue_sos_tokens():\n    pass\n",
        )
        base = self.commit()
        self.write(
            "futureagi/pyproject.toml",
            config.replace("Use another API.", "Use a reviewed API."),
        )
        path = "futureagi/accounts/services/new.py"
        self.write(
            path,
            "from .token_service import issue_sos_tokens\n\n\nissue_sos_tokens()\n",
        )
        self.commit()
        code, found, proc = self.gate(base)
        self.assertEqual(code, 1, proc.stdout + proc.stderr)
        self.assertIn((path, 1, "TID251"), found, proc.stdout)

    def test_full_head_tree_keeps_first_party_import_classification(self):
        config = CONFIG.replace(
            'select = ["E", "F", "B"]', 'select = ["E", "F", "B", "I"]'
        )
        config = config.replace("line-length = 88", 'line-length = 88\nsrc = ["."]')
        self.write("futureagi/pyproject.toml", config)
        self.write("futureagi/tfc/__init__.py", "")
        self.write("futureagi/tfc/utils/__init__.py", "")
        self.write(
            "futureagi/tfc/utils/general_methods.py",
            "class GeneralMethods:\n    pass\n",
        )
        base = self.commit()
        self.write(
            "futureagi/pyproject.toml", config.replace('"app/cli.py"', '"app/other.py"')
        )
        path = "futureagi/app/new.py"
        self.write(
            path,
            'import django\nfrom tfc.utils.general_methods import GeneralMethods\n\n__all__ = ["GeneralMethods", "django"]\n',
        )
        self.commit()
        code, found, proc = self.gate(base)
        self.assertEqual(code, 1, proc.stdout + proc.stderr)
        self.assertIn((path, 1, "I001"), found, proc.stdout)

    def test_renaming_config_away_still_uses_base_settings(self):
        config = CONFIG.replace(
            'select = ["E", "F", "B"]', 'select = ["E", "F", "B", "TID251"]'
        )
        config += "\n[tool.ruff.lint.flake8-tidy-imports.banned-api]\n"
        config += '"library.old_api" = { msg = "Use another API." }\n'
        self.write("futureagi/pyproject.toml", config)
        base = self.commit()
        self.git("mv", "futureagi/pyproject.toml", "futureagi/pyproject.toml.disabled")
        path = "futureagi/app/new.py"
        self.write(path, "from library import old_api\n\nold_api()\n")
        self.commit()
        code, found, proc = self.gate(base)
        self.assertEqual(code, 1, proc.stdout + proc.stderr)
        self.assertIn((path, 1, "TID251"), found, proc.stdout)

    def test_config_only_invalid_nested_ruff_toml_stops(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/existing.py", "VALUE = 1\n")
        base = self.commit()
        path = "futureagi/app/ruff.toml"
        self.write(path, "line-length = true\n")
        self.commit()

        self.assert_gate_stopped(base, path)

    def test_config_only_invalid_nested_pyproject_stops(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        path = "futureagi/app/pyproject.toml"
        self.write(path, "[tool.ruff]\nline-length = true\n")
        self.commit()

        self.assert_gate_stopped(base, path)

    def test_config_only_invalid_project_metadata_stops(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write(
            "futureagi/model_serving/pyproject.toml",
            '[project]\nname = "serving"\nrequires-python = ">=3.11"\n',
        )
        base = self.commit()
        path = "futureagi/model_serving/pyproject.toml"
        self.write(
            path,
            '[project]\nname = "serving"\nrequires-python = "^3.11"\n',
        )
        self.commit()
        self.assert_gate_stopped(base, path)

    def test_config_only_new_non_table_project_stops(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        path = "futureagi/app/pyproject.toml"
        self.write(path, 'project = "x"\n')
        self.commit()
        self.assert_gate_stopped(base, path)

    def test_config_deletion_exposes_invalid_shadowed_config(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/ruff.toml", "line-length = 99\n")
        hidden = "futureagi/app/pyproject.toml"
        self.write(hidden, "[tool.ruff]\nline-length = true\n")
        base = self.commit()
        self.git("rm", "-q", "futureagi/app/ruff.toml")
        self.commit()
        self.assert_gate_stopped(base, hidden)

    def test_config_rename_exposes_invalid_shadowed_config(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/ruff.toml", "line-length = 99\n")
        hidden = "futureagi/app/pyproject.toml"
        self.write(hidden, "[tool.ruff]\nline-length = true\n")
        base = self.commit()
        self.git("mv", "futureagi/app/ruff.toml", "futureagi/app/ruff.toml.old")
        self.commit()
        self.assert_gate_stopped(base, hidden)

    def test_shadowed_config_uses_effective_ruff_settings(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/.ruff.toml", "line-length = 99\n")
        base = self.commit()
        self.write("futureagi/app/ruff.toml", "line-length = true\n")
        self.commit()
        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)

    def test_valid_project_metadata_without_ruff_settings_passes(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        self.write(
            "futureagi/app/pyproject.toml",
            '[project]\nname = "sample"\nrequires-python = ">=3.11"\n',
        )
        self.commit()
        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)

    def test_valid_model_serving_version_bump_passes(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/model_serving/pyproject.toml"
        self.write(
            path,
            '[project]\nname = "serving"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n',
        )
        base = self.commit()
        self.write(
            path,
            '[project]\nname = "serving"\nversion = "0.1.1"\nrequires-python = ">=3.11"\n',
        )
        self.commit()
        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)

    def test_config_only_other_config_names_and_errors_stop(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        cases = (
            ("futureagi/app/.ruff.toml", "line-length = true\n"),
            ("futureagi/app/ruff.toml", "unknown-setting = true\n"),
            ("futureagi/app/ruff.toml", 'required-version = ">=99.0.0"\n'),
            ("futureagi/pyproject.toml", "[tool.ruff]\nline-length = true\n"),
            ("ruff.toml", "line-length = true\n"),
        )
        for path, content in cases:
            with self.subTest(path=path, content=content):
                self.write(path, content)
                self.commit()
                self.assert_gate_stopped(base, path)
                self.git("reset", "--hard", base)

    def test_config_error_hides_temporary_probe_root(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        path = "futureagi/app/ruff.toml"
        self.write(path, '[lint.per-file-ignores]\n"[bad" = ["F401"]\n')
        self.commit()
        code, _, proc = self.gate(base)
        self.assertEqual(code, 2, proc.stdout + proc.stderr)
        self.assertIn(path, proc.stdout)
        self.assertNotIn("lint-config-check-", proc.stdout)

    def test_config_only_valid_nested_config_passes(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        self.write("futureagi/app/ruff.toml", "line-length = 99\n")
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)
        self.assertIn("No new violations.", proc.stdout)

    def test_config_validation_reads_committed_head_not_checkout(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        path = "futureagi/app/ruff.toml"
        self.write(path, "line-length = 99\n")
        valid_head = self.commit()
        self.write(path, "line-length = true\n")
        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)
        self.assertIn("No new violations.", proc.stdout)

        self.write(path, "line-length = true\n")
        self.commit()
        self.write(path, "line-length = 99\n")
        self.assert_gate_stopped(valid_head, path)

    def test_invalid_config_alongside_python_change_stops(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/existing.py", "VALUE = 1\n")
        base = self.commit()
        path = "futureagi/app/ruff.toml"
        self.write(path, "line-length = true\n")
        self.write("futureagi/app/existing.py", "VALUE = 2\n")
        self.commit()

        self.assert_gate_stopped(base, path)

    def test_non_table_tool_or_ruff_reports_gate_error(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        for content in ('tool = "x"\n', '[tool]\nruff = "x"\n'):
            with self.subTest(content=content):
                self.write("futureagi/app/pyproject.toml", content)
                self.write("futureagi/app/new.py", 'print("new")\n')
                self.commit()
                code, _, proc = self.gate(base)
                self.assertEqual(code, 2, proc.stdout + proc.stderr)
                self.assertIn("title=Python lint gate", proc.stdout)
                self.assertNotIn("Traceback", proc.stderr)
                self.git("reset", "--hard", base)

    def banned_config(self):
        return (
            CONFIG.replace(
                'select = ["E", "F", "B"]', 'select = ["E", "F", "B", "TID251"]'
            )
            + "\n[tool.ruff.lint.flake8-tidy-imports.banned-api]\n"
            + '"library.old_api" = { msg = "Use another API." }\n'
        )

    def assert_gate_stopped(self, base, path):
        code, _, proc = self.gate(base)
        self.assertEqual(code, 2, proc.stdout + proc.stderr)
        self.assertIn("title=Python lint gate", proc.stdout)
        self.assertIn(path, proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_committed_configs_only_include_backend_ancestors(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/.ruff.toml", "line-length = 88\n")
        self.write("ruff.toml", "line-length = 88\n")
        self.write("other/pyproject.toml", "[tool.ruff]\nline-length = 88\n")
        self.write("other/deep/.ruff.toml", "line-length = 88\n")
        ref = self.commit()
        self.assertEqual(
            gate.committed_configs(self.repo, ref),
            {
                "ruff.toml": b"line-length = 88\n",
                "futureagi/pyproject.toml": CONFIG.encode(),
                "futureagi/app/.ruff.toml": b"line-length = 88\n",
            },
        )

    def test_outside_config_entries_do_not_affect_later_backend_changes(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        original = self.commit()
        for kind in ("extend", "symlink", "non_utf8", "non_table", "gitlink"):
            with self.subTest(kind=kind):
                self.git("reset", "--hard", original)
                self.git("clean", "-fdx")
                if kind == "extend":
                    self.write(
                        "sdk/pyproject.toml", '[tool.ruff]\nextend = "elsewhere.toml"\n'
                    )
                elif kind == "symlink":
                    (self.repo / "docs").mkdir(exist_ok=True)
                    (self.repo / "docs/ruff.toml").symlink_to(
                        "../futureagi/pyproject.toml"
                    )
                elif kind == "non_utf8":
                    folder = os.fsencode(self.repo) + b"/docs"
                    os.makedirs(folder, exist_ok=True)
                    with open(folder + b"/caf\xe9.txt", "wb") as fh:
                        fh.write(b"x\n")
                elif kind == "non_table":
                    self.write("frontend/tool/pyproject.toml", 'tool = "x"\n')
                else:
                    self.write("docs/placeholder", "x\n")
                    self.git("add", "-A")
                    self.git(
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        "160000," + self.git("rev-parse", "HEAD") + ",docs/ruff.toml",
                    )
                before = self.commit()
                self.write("futureagi/app/ok.py", "VALUE = 1\n")
                self.commit()
                code, found, proc = self.gate(before)
                self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)
                self.git("reset", "--hard", before)
                self.write("futureagi/app/new.py", 'print("new")\n')
                self.commit()
                code, found, proc = self.gate(before)
                self.assertEqual(
                    (code, found),
                    (1, [("futureagi/app/new.py", 1, "T201")]),
                    proc.stdout + proc.stderr,
                )

    def test_non_utf8_config_name_in_scope_stops(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        folder = os.fsencode(self.repo) + b"/futureagi/\xffdir"
        os.mkdir(folder)
        with open(folder + b"/ruff.toml", "wb") as fh:
            fh.write(b"line-length = 88\n")
        self.commit()
        self.assert_gate_stopped(base, "UTF-8")

    def test_config_snapshot_uses_committed_bytes_not_checkout_contents(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        self.write(
            "futureagi/pyproject.toml",
            CONFIG.replace("line-length = 88", "line-length = 99"),
        )
        self.assertEqual(
            gate.committed_configs(self.repo, base)["futureagi/pyproject.toml"],
            CONFIG.encode(),
        )

    def test_config_only_symlink_stops_before_lint(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        self.write("futureagi/alternate.toml", CONFIG)
        self.git("rm", "-q", "futureagi/pyproject.toml")
        (self.repo / "futureagi/pyproject.toml").symlink_to("alternate.toml")
        self.commit()
        self.assert_gate_stopped(base, "futureagi/pyproject.toml")

    def test_root_config_gitlink_stops_before_lint(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        self.write("futureagi/app/new.py", 'print("new")\n')
        self.git("add", "-A")
        self.git(
            "update-index", "--add", "--cacheinfo", "160000," + base + ",ruff.toml"
        )
        self.git("commit", "-q", "-m", "c")
        self.assert_gate_stopped(base, "ruff.toml")

    @unittest.skipUnless(Path("/proc/self/cwd").exists(), "requires procfs")
    def test_proc_cwd_config_at_root_stops_before_lint(self):
        config = self.banned_config()
        self.write("futureagi/pyproject.toml", config)
        base = self.commit()
        self.write("cfg.toml", config)
        self.write("futureagi/cfg.toml", CONFIG)
        self.git("rm", "-q", "futureagi/pyproject.toml")
        (self.repo / "futureagi/pyproject.toml").symlink_to("/proc/self/cwd/cfg.toml")
        self.write("futureagi/app/new.py", "from library import old_api\n\nold_api()\n")
        self.commit()
        self.assert_gate_stopped(base, "futureagi/pyproject.toml")

    @unittest.skipUnless(Path("/proc/self/cwd").exists(), "requires procfs")
    def test_proc_cwd_config_with_relative_import_stops_before_lint(self):
        config = self.banned_config()
        self.write("futureagi/pyproject.toml", config)
        self.write("futureagi/app/__init__.py", "")
        self.write("futureagi/app/library.py", "def old_api():\n    pass\n")
        base = self.commit()
        self.write("cfg.toml", config)
        self.write("futureagi/cfg.toml", CONFIG)
        self.git("rm", "-q", "futureagi/pyproject.toml")
        (self.repo / "futureagi/pyproject.toml").symlink_to("/proc/self/cwd/cfg.toml")
        self.write(
            "futureagi/app/new.py", "from .library import old_api\n\nold_api()\n"
        )
        self.commit()
        self.assert_gate_stopped(base, "futureagi/pyproject.toml")

    @unittest.skipUnless(Path("/proc/self/cwd").exists(), "requires procfs")
    def test_proc_cwd_nested_pyproject_stops_even_if_root_has_no_ruff_table(self):
        self.write("futureagi/pyproject.toml", self.banned_config())
        base = self.commit()
        self.write("lax.toml", '[project]\nname = "notes"\n')
        self.write(
            "futureagi/lax.toml",
            '[tool.ruff]\nextend = "../pyproject.toml"\n'
            '[tool.ruff.lint]\nignore = ["TID251"]\n',
        )
        path = self.repo / "futureagi/app/pyproject.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to("/proc/self/cwd/lax.toml")
        self.write("futureagi/app/new.py", "from library import old_api\n\nold_api()\n")
        self.commit()
        self.assert_gate_stopped(base, "futureagi/app/pyproject.toml")

    @unittest.skipUnless(Path("/proc/self/cwd").exists(), "requires procfs")
    def test_proc_cwd_nested_pyproject_with_another_finding_stops(self):
        self.write("futureagi/pyproject.toml", self.banned_config())
        base = self.commit()
        self.write("lax.toml", '[project]\nname = "notes"\n')
        self.write(
            "futureagi/lax.toml",
            '[tool.ruff]\nextend = "../pyproject.toml"\n[tool.ruff.lint]\nignore = ["TID251"]\n',
        )
        path = self.repo / "futureagi/app/pyproject.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to("/proc/self/cwd/lax.toml")
        self.write("futureagi/app/new.py", "from library import old_api\n\nold_api()\n")
        self.write("futureagi/printed.py", 'print("new")\n')
        self.commit()
        self.assert_gate_stopped(base, "futureagi/app/pyproject.toml")

    def test_config_gitlink_stops_before_lint(self):
        self.write("futureagi/pyproject.toml", self.banned_config())
        base = self.commit()
        self.write("futureagi/app/new.py", "from library import old_api\n\nold_api()\n")
        self.git("add", "-A")
        self.git(
            "update-index",
            "--add",
            "--cacheinfo",
            "160000," + base + ",futureagi/app/ruff.toml",
        )
        self.git("commit", "-q", "-m", "c")
        self.assert_gate_stopped(base, "futureagi/app/ruff.toml")

    def test_extend_in_base_config_stops_before_lint(self):
        self.write(
            "futureagi/pyproject.toml",
            self.banned_config().replace(
                "line-length = 88", 'line-length = 88\nextend = "../shared.toml"'
            ),
        )
        self.write("shared.toml", "[tool.ruff]\nline-length = 88\n")
        base = self.commit()
        self.write("futureagi/app/new.py", 'print("new")\n')
        self.commit()
        self.assert_gate_stopped(base, "futureagi/pyproject.toml")

    def test_extend_in_head_config_stops_before_lint(self):
        self.write("futureagi/pyproject.toml", self.banned_config())
        base = self.commit()
        self.write("futureagi/app/ruff.toml", 'extend = "../pyproject.toml"\n')
        self.write("futureagi/app/new.py", 'print("new")\n')
        self.commit()
        self.assert_gate_stopped(base, "futureagi/app/ruff.toml")

    def test_unparsed_trusted_configs_stop_before_lint(self):
        cases = (
            (
                "ruff.toml",
                'extend = "lintcfg/shared.toml"\nlint.per-file-ignores = {\n  "unused.py" = ["E501"],\n}\n',
            ),
            ("ruff.toml", '﻿extend = "lintcfg/shared.toml"\n'),
            (
                "pyproject.toml",
                '[tool.ruff]\nextend = "lintcfg/shared.toml"\nlint.per-file-ignores = {\n  "unused.py" = ["E501"],\n}\n',
            ),
            ("ruff.toml", 'extend = "lintcfg/shared.toml"\n'),
        )
        self.write("futureagi/pyproject.toml", self.banned_config())
        original = self.commit()
        for name, content in cases:
            with self.subTest(name=name, content=content):
                self.git("reset", "--hard", original)
                self.git("clean", "-fdx")
                self.write("futureagi/model_hub/" + name, content)
                target = "futureagi/model_hub/lintcfg/shared.toml"
                self.write(target, 'extend = "../../pyproject.toml"\n')
                base = self.commit()
                self.write(
                    target,
                    'extend = "../../pyproject.toml"\n[lint]\nignore = ["TID251"]\n',
                )
                self.write(
                    "futureagi/model_hub/new.py",
                    "from library import old_api\n\nold_api()\n",
                )
                self.commit()
                self.assert_gate_stopped(base, "futureagi/model_hub/" + name)

    def test_unparsed_head_config_stops_its_own_pr(self):
        # Ruff may accept a config this check cannot read; the PR adding it
        # fails its own check rather than every later backend PR.
        self.write("futureagi/pyproject.toml", self.banned_config())
        base = self.commit()
        path = "futureagi/model_hub/ruff.toml"
        self.write(
            path,
            'lint.per-file-ignores = {\n  "unused.py" = ["E501"],\n}\n',
        )
        self.write("futureagi/model_hub/new.py", 'print("new")\n')
        self.commit()
        self.assert_gate_stopped(base, path)

    def test_config_with_byte_order_mark_is_read(self):
        self.write("futureagi/pyproject.toml", "\ufeff" + self.banned_config())
        base = self.commit()
        self.write(
            "futureagi/app/new.py",
            "from library import old_api\n\nold_api()\n",
        )
        self.commit()
        code, _, proc = self.gate(base)
        self.assertEqual(code, 1, proc.stdout + proc.stderr)
        self.assertIn("TID251", proc.stdout)
        self.assertNotIn("not parseable", proc.stdout)

    def test_tip_settings_judge_stale_branch_and_preserve_old_findings(self):
        path = "futureagi/app/new.py"
        self.write("futureagi/pyproject.toml", CONFIG)
        old = self.commit()
        self.write("futureagi/pyproject.toml", self.banned_config())
        tip = self.commit()
        self.git("checkout", "-q", "-b", "stale", old)
        self.write(path, "from library import old_api\n\nold_api()\n")
        self.commit()
        code, found, proc = self.gate(old, tip)
        self.assertEqual(
            (code, found), (1, [(path, 1, "TID251")]), proc.stdout + proc.stderr
        )
        self.git("checkout", "-q", "-B", "control", old)
        self.write(path, 'print("new")\n')
        self.commit()
        code, found, proc = self.gate(old, tip)
        self.assertEqual(
            (code, found), (1, [(path, 1, "T201")]), proc.stdout + proc.stderr
        )
        self.git("checkout", "-q", "-B", "old_finding", old)
        self.write(path, "from library import old_api\n\nold_api()\n")
        before = self.commit()
        self.write(path, "from library import old_api\n\nold_api()\n\nVALUE = 1\n")
        self.commit()
        code, found, proc = self.gate(before, tip)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)

    def test_tip_settings_in_merged_side_branch_judge_new_import(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        root = self.commit()
        self.git("checkout", "-q", "-b", "side", root)
        weak = self.commit_with_file("futureagi/app/side.py", "VALUE = 1\n")
        self.write("futureagi/pyproject.toml", self.banned_config())
        self.commit()
        self.git("checkout", "-q", "main")
        self.write("futureagi/app/main.py", "VALUE = 1\n")
        self.commit()
        self.git("merge", "-q", "--no-ff", "-m", "merge side", "side")
        tip = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "-b", "stale", weak)
        path = "futureagi/app/new.py"
        self.write(path, "from library import old_api\n\nold_api()\n")
        self.commit()
        code, found, proc = self.gate(weak, tip)
        self.assertEqual(
            (code, found), (1, [(path, 1, "TID251")]), proc.stdout + proc.stderr
        )

    def commit_with_file(self, path, content):
        self.write(path, content)
        return self.commit()

    def test_symlinked_python_file_outside_backend_reports_gate_error(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        self.write("outside_pkg/real.py", 'print("new")\n')
        path = "futureagi/app/link.py"
        self.link(path, "../../outside_pkg/real.py")
        self.commit()
        self.assert_gate_stopped(base, path)

    def test_non_utf8_path_reports_gate_error(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        folder = os.fsencode(self.repo) + b"/futureagi/\xffdir"
        os.mkdir(folder)
        with open(folder + b"/new.py", "wb") as fh:
            fh.write(b'print("new")\n')
        self.commit()
        code, _, proc = self.gate(base)
        self.assertEqual(code, 2, proc.stdout + proc.stderr)
        self.assertIn("title=Python lint gate", proc.stdout)
        self.assertIn("UTF-8", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_overlay_stops_at_a_symlinked_config_directory(self):
        config = CONFIG.replace(
            'select = ["E", "F", "B"]', 'select = ["E", "F", "B", "TID251"]'
        )
        config += "\n[tool.ruff.lint.flake8-tidy-imports.banned-api]\n"
        config += '"library.old_api" = { msg = "Use another API." }\n'
        self.write("futureagi/pyproject.toml", config)
        self.write("futureagi/legacy/pyproject.toml", '[project]\nname = "legacy"\n')
        self.write("futureagi/legacy/old.py", "VALUE = 1\n")
        base = self.commit()

        for layout in ("deleted", "moved", "extra_finding", "absolute"):
            with self.subTest(layout=layout):
                self.git("reset", "--hard", base)
                self.git("clean", "-fdx")
                if layout in ("moved", "extra_finding"):
                    self.git("mv", "futureagi/legacy", "futureagi/legacy_app")
                else:
                    self.git("rm", "-r", "-q", "futureagi/legacy")
                if layout == "absolute":
                    target = self.repo / "outside"
                    target.mkdir()
                else:
                    target = "../../base/futureagi"
                (self.repo / "futureagi/legacy").symlink_to(
                    target, target_is_directory=True
                )
                self.write(
                    "futureagi/pyproject.toml",
                    config.replace("Use another API.", "Use a reviewed API."),
                )
                self.write(
                    "futureagi/app/new.py", "from library import old_api\n\nold_api()\n"
                )
                if layout == "extra_finding":
                    self.write("futureagi/app/printed.py", 'print("new")\n')
                self.commit()

                code, _, proc = self.gate(base)
                self.assertEqual(code, 2, proc.stdout + proc.stderr)
                self.assertIn("title=Python lint gate", proc.stdout)
                self.assertIn("futureagi/legacy/pyproject.toml", proc.stdout)
                self.assertNotIn("Traceback", proc.stderr)
                if layout == "absolute":
                    self.assertEqual(list(target.iterdir()), [])
                self.git("reset", "--hard", base)

    def test_overlay_stops_before_copying_a_nested_config_via_a_symlink(self):
        config = CONFIG.replace(
            'select = ["E", "F", "B"]', 'select = ["E", "F", "B", "TID251"]'
        )
        config += "\n[tool.ruff.lint.flake8-tidy-imports.banned-api]\n"
        config += '"library.old_api" = { msg = "Use another API." }\n'
        self.write("futureagi/pyproject.toml", config)
        self.write(
            "futureagi/legacy/ruff.toml",
            'extend = "../pyproject.toml"\n\n[lint]\nignore = ["TID251"]\n',
        )
        self.write("futureagi/legacy/old.py", "VALUE = 1\n")
        base = self.commit()

        self.git("rm", "-r", "-q", "futureagi/legacy")
        (self.repo / "futureagi/legacy").symlink_to("app", target_is_directory=True)
        self.write("futureagi/app/new.py", "from library import old_api\n\nold_api()\n")
        self.commit()
        code, _, proc = self.gate(base)
        self.assertEqual(code, 2, proc.stdout + proc.stderr)
        self.assertIn("futureagi/legacy/ruff.toml", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_config_parent_replaced_by_a_file_keeps_ordinary_verdicts(self):
        config = CONFIG.replace(
            'select = ["E", "F", "B"]', 'select = ["E", "F", "B", "TID251"]'
        )
        config += "\n[tool.ruff.lint.flake8-tidy-imports.banned-api]\n"
        config += '"library.old_api" = { msg = "Use another API." }\n'
        self.write("futureagi/pyproject.toml", config)
        self.write("futureagi/legacy/pyproject.toml", '[project]\nname = "legacy"\n')
        base = self.commit()
        for source, expected in (
            ("VALUE = 1\n", (0, [])),
            (
                "from library import old_api\n\nold_api()\n",
                (1, [("futureagi/app/new.py", 1, "TID251")]),
            ),
        ):
            with self.subTest(source=source):
                self.git("reset", "--hard", base)
                self.git("rm", "-r", "-q", "futureagi/legacy")
                self.write("futureagi/legacy", "replaced\n")
                self.write("futureagi/app/new.py", source)
                self.commit()
                code, found, proc = self.gate(base)
                self.assertEqual((code, found), expected, proc.stdout + proc.stderr)

    def test_overlay_stops_at_a_symlinked_config_file(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        base = self.commit()
        self.git("rm", "-q", "futureagi/pyproject.toml")
        self.write(
            "futureagi/alternate.toml",
            CONFIG.replace("line-length = 88", "line-length = 89"),
        )
        (self.repo / "futureagi/pyproject.toml").symlink_to("alternate.toml")
        self.write("futureagi/app/new.py", 'print("new")\n')
        self.commit()
        code, _, proc = self.gate(base)
        self.assertEqual(code, 2, proc.stdout + proc.stderr)
        self.assertIn("futureagi/pyproject.toml", proc.stdout)
        self.assertIn("title=Python lint gate", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_symlinked_python_file_with_config_edit_keeps_its_target(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/real.py", "VALUE = 1\n")
        base = self.commit()
        self.write("futureagi/app/real.py", 'print("new")\n')
        (self.repo / "futureagi/app/link.py").symlink_to("real.py")
        self.write(
            "futureagi/pyproject.toml",
            CONFIG.replace("line-length = 88", "line-length = 89"),
        )
        self.commit()
        code, found, proc = self.gate(base)
        self.assertEqual(code, 1, proc.stdout + proc.stderr)
        self.assertIn(("futureagi/app/real.py", 1, "T201"), found, proc.stdout)
        self.assertNotIn("title=Python lint gate", proc.stdout)

    def test_config_edit_and_exact_reformat_keep_old_counts(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/app/mod.py"
        self.write(path, "print('old')\n")
        base = self.commit()
        self.write(
            "futureagi/pyproject.toml",
            CONFIG + "\n[tool.ruff.lint.flake8-tidy-imports.banned-api]\n"
            '"library.old_api" = { msg = "Use another API." }\n',
        )
        self.format_file(path)
        self.commit()

        # Both trees use the base settings. A config edit cannot turn this
        # old finding into a new one just because the formatter changed quotes.
        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)
        self.write(path, (self.repo / path).read_text() + 'print("new")\n')
        self.commit()
        code, found, proc = self.gate(base)
        self.assertEqual(code, 1, proc.stdout + proc.stderr)
        self.assertIn((path, 2, "T201"), found, proc.stdout)

    def test_ordinary_reformat_still_uses_count_comparison(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/app/mod.py"
        self.write(path, "print('old')\n")
        base = self.commit()
        self.format_file(path)
        self.commit()
        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)

        # Non-Ruff metadata in the same TOML file does not change matching.
        self.write(
            "futureagi/pyproject.toml", CONFIG + '\n[project]\nname = "example"\n'
        )
        self.commit()
        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)

    def test_formatter_uses_base_config_not_a_changed_head_config(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/app/mod.py"
        self.write(path, 'print("old")\n')
        base = self.commit()
        self.write(
            "futureagi/pyproject.toml",
            CONFIG + '\n[tool.ruff.format]\nquote-style = "single"\n',
        )
        self.format_file(path)
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual(
            (code, found),
            (1, [(path, 1, "T201"), (path, 1, "FORMAT")]),
            proc.stdout + proc.stderr,
        )

    def test_moved_type_ignore_remains_visible_to_raw_base_comparison(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/app/mod.py"
        self.write(
            path,
            "def f(value):\n    return value.some_method(first_argument=value, second_argument=value, third_argument=value, fourth_argument=value)  # type: ignore\n",
        )
        base = self.commit()
        self.format_file(path)
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)
        self.assertEqual(
            [
                x.code
                for x in gate.ruff_check(RUFF, self.repo, [path], gate.BANNED_RULES)
            ],
            ["PGH003"],
        )

    def test_formatter_crash_fails_closed(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/app/mod.py"
        self.write(path, "print('old')\n")
        base = self.commit()
        self.format_file(path)
        self.commit()
        fake = self.repo / "crashing-ruff"
        # Only the new stdin formatter operation crashes; lint and format-check
        # still work, so this proves that predicate failures cannot become passes.
        fake.write_text(
            f'#!/bin/sh\nfor arg do\n  if [ "$arg" = "--stdin-filename" ]; then exit 2; fi\ndone\nexec "{RUFF}" "$@"\n'
        )
        fake.chmod(0o755)
        proc = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--base", base, "--ruff", str(fake)],
            cwd=self.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("ruff format", proc.stdout)
        self.assertNotIn("No new violations.", proc.stdout)
        self.assertEqual(
            len(self.git("worktree", "list", "--porcelain").split("worktree ")) - 1, 1
        )

    def test_a_moved_formatted_file_is_judged_at_its_new_path(self):
        # Identical bytes under tests/ and then under app/: the banned-pattern
        # pass applies only at the new path. Its findings are new, as for any
        # moved file, and the advisory rule stays a warning.
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write(
            "futureagi/app/tests/helper.py",
            'def q(table):\n    print(table)\n    return f"SELECT count() FROM {table}"\n',
        )
        base = self.commit()
        self.git("mv", "futureagi/app/tests/helper.py", "futureagi/app/helper.py")
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual(
            (code, found),
            (1, [("futureagi/app/helper.py", 2, "T201")]),
            proc.stdout + proc.stderr,
        )
        self.assertEqual(
            self.warnings, [("futureagi/app/helper.py", 3, "S608")], proc.stdout
        )

    def link(self, rel, target):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            path.unlink()
        path.symlink_to(target)

    def test_an_excluded_unresolved_symlink_does_not_stop_the_check(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/app/migrations/0002_link.py"
        self.link(path, "missing_one.py")
        base = self.commit()
        self.link(path, "missing_two.py")
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)
        self.assertEqual(self.warnings, [])
        self.assertIn("No new violations.", proc.stdout)

    def test_the_formatting_comparison_reads_only_files_ruff_includes(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        excluded = "futureagi/app/migrations/0002_link.py"
        included = "futureagi/app/link.py"
        self.link(excluded, "missing.py")
        self.link(included, "missing.py")
        self.assertEqual(
            gate.formatting_only_files(
                RUFF, self.repo, self.repo, {excluded: excluded}, set()
            ),
            set(),
        )
        # --show-files can omit a broken named link; the regular format pass
        # still reports an included unreadable file instead of accepting it.
        self.assertEqual(
            gate.formatting_only_files(
                RUFF, self.repo, self.repo, {included: included}, set()
            ),
            set(),
        )
        with self.assertRaisesRegex(gate.GateError, re.escape(included)):
            gate.ruff_unformatted(RUFF, self.repo, [included])

    def test_unrelated_config_does_not_stop_changed_file_checks(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/app/a.py"
        self.write(path, "x=1\n")
        for config_path, config_text in (
            ("futureagi/other/ruff.toml", "line-length = true\n"),
            (
                "futureagi/other/pyproject.toml",
                "[tool.ruff.lint]\nunknown-setting = true\n",
            ),
        ):
            with self.subTest(config=config_path):
                self.write(config_path, config_text)
                base = self.commit()
                for change, text in (
                    ("formatting", "x = 1\n"),
                    ("ordinary", "x=2\n"),
                ):
                    with self.subTest(change=change):
                        self.write(path, text)
                        self.commit()
                        code, found, proc = self.gate(base)
                        self.assertEqual(
                            (code, found), (0, []), proc.stdout + proc.stderr
                        )
                        self.git("reset", "--hard", base)
                (self.repo / config_path).unlink()
                self.commit()

    def test_config_for_a_changed_file_still_fails_closed(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/ruff.toml", "line-length = true\n")
        path = "futureagi/app/a.py"
        self.write(path, "x = 1\n")
        base = self.commit()
        self.write(path, "x = 2\n")
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (2, []), proc.stdout + proc.stderr)
        self.assertIn("app/ruff.toml", proc.stdout)

    def test_an_included_unreadable_file_fails_closed(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        path = "futureagi/app/link.py"
        self.link(path, "missing_one.py")
        base = self.commit()
        self.link(path, "missing_two.py")
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (2, []), proc.stdout + proc.stderr)
        self.assertIn(path, proc.stdout)
        self.assertNotIn("No new violations.", proc.stdout)
        self.assertEqual(
            len(self.git("worktree", "list", "--porcelain").split("worktree ")) - 1, 1
        )

    def test_a_syntax_error_is_reported_and_does_not_stop_the_check(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/was_broken.py", "def (:\n")
        base = self.commit()
        self.write("futureagi/app/was_broken.py", "x = 1\n")  # fixed: nothing new
        self.write("futureagi/app/now_broken.py", "def (:\n")  # new syntax error
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual(code, 1, proc.stdout + proc.stderr)
        # Ruff can report several diagnostics for one syntax error.
        self.assertEqual(
            {(f, c) for f, _, c in found},
            {("futureagi/app/now_broken.py", "invalid-syntax")},
            proc.stdout,
        )


@unittest.skipUnless(RUFF or REQUIRE_RUFF, "ruff is not installed")
class WorkflowExecutionTests(RepositoryFixture, unittest.TestCase):
    """Run the actual reporting step against separate base and PR-head commits.

    Only package provisioning is stubbed, to keep self-tests offline. The base
    gate, version checks, Git comparisons and lint all execute for real.
    """

    def setUp(self):
        if not RUFF:
            self.fail("ruff must be on PATH in CI")
        self.tmp = tempfile.TemporaryDirectory(prefix="lint-workflow-test-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.runner = Path(self.tmp.name) / "runner"
        self.runner.mkdir()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.installs = Path(self.tmp.name) / "installs"
        self.git("init", "-q", "-b", "main")
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/mod.py", "value = 1\n")
        self.write("scripts/check_python_lint_delta.py", SCRIPT.read_text())
        self.write(".github/workflows/python-lint.yml", WORKFLOW.read_text())
        # Provisioning shim: the requested pin must be the base pin, never the
        # head's synthetic upgrade. Every other Python command runs normally.
        provisioner = self.bin / "python3"
        provisioner.write_text(
            f"#!{sys.executable}\n"
            "import os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:1] == ['-I']: args = args[1:]\n"
            "if args[:2] == ['-m', 'venv']:\n"
            "    bin_dir = pathlib.Path(args[-1]) / 'bin'\n"
            "    bin_dir.mkdir(parents=True)\n"
            "    python = bin_dir / 'python'\n"
            "    python.write_text(pathlib.Path(__file__).read_text())\n"
            "    python.chmod(0o755)\n"
            "elif args[:3] == ['-m', 'pip', 'install']:\n"
            "    pin = next(arg for arg in args if arg.startswith('ruff=='))\n"
            "    pathlib.Path(os.environ['INSTALL_LOG']).write_text(pin)\n"
            "    if pin != os.environ['BASE_RUFF_PIN']: sys.exit(87)\n"
            "    ruff = pathlib.Path(__file__).parent / 'ruff'\n"
            "    if not ruff.exists(): ruff.symlink_to(os.environ['REAL_RUFF'])\n"
            "else:\n"
            "    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n"
        )
        provisioner.chmod(0o755)
        head_ruff = self.bin / "ruff"
        head_ruff.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print('ruff ' + os.environ['HEAD_RUFF_VERSION'])\n"
            "else:\n"
            "    os.execv(os.environ['REAL_RUFF'], [os.environ['REAL_RUFF'], *sys.argv[1:]])\n"
        )
        head_ruff.chmod(0o755)

    def base(self):
        self.git("update-ref", "refs/remotes/origin/main", self.commit())

    def report(self, head_version="99.99.99"):
        text = WORKFLOW.read_text()
        self.write(
            ".github/workflows/python-lint.yml",
            text.replace(
                f'RUFF_VERSION: "{gate.RUFF_VERSION}"',
                f'RUFF_VERSION: "{head_version}"',
            ),
        )
        self.commit()
        return self.run_step("Report new violations", head_version)

    def run_step(self, name, workflow_version="99.99.99"):
        step = (
            job_block(
                WORKFLOW.read_text(),
                "ruff-self-test"
                if name == "Install ruff for head self-tests"
                else "ruff-delta",
            )
            .split(f"      - name: {name}\n", 1)[1]
            .split("      - name:", 1)[0]
        )
        return subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-eo",
                "pipefail",
                "-c",
                run_script(step),
            ],
            cwd=self.repo,
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                "RUNNER_TEMP": str(self.runner),
                "GITHUB_STEP_SUMMARY": "",
                "BASE_REF": "main",
                "RUFF_VERSION": workflow_version,
                "HEAD_RUFF_VERSION": workflow_version,
                "REAL_RUFF": RUFF,
                "BASE_RUFF_PIN": f"ruff=={gate.RUFF_VERSION}",
                "INSTALL_LOG": str(self.installs),
            },
        )

    def test_head_self_tests_use_head_pin_not_merge_workflow_pin(self):
        self.write("scripts/test_check_python_lint_delta.py", "# Fixture self-test.\n")
        proc = self.run_step("Install ruff for head self-tests")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.installs.read_text(), f"ruff=={gate.RUFF_VERSION}")

    def test_head_without_self_tests_needs_no_head_pin(self):
        (self.repo / ".github/workflows/python-lint.yml").unlink()
        proc = self.run_step("Install ruff for head self-tests")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse(self.installs.exists())

    def test_base_pin_with_trailing_comment_passes(self):
        self.write(
            ".github/workflows/python-lint.yml",
            WORKFLOW.read_text().replace(
                f'RUFF_VERSION: "{gate.RUFF_VERSION}"',
                f'RUFF_VERSION: "{gate.RUFF_VERSION}"  # synchronized pin\t',
            ),
        )
        self.base()
        proc = self.report()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.installs.read_text(), f"ruff=={gate.RUFF_VERSION}")

    def test_ruff_upgrade_without_python_changes_passes(self):
        self.base()
        proc = self.report()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"using ruff {gate.RUFF_VERSION}", proc.stdout)
        self.assertIn("No new violations.", proc.stdout)
        self.assertEqual(self.installs.read_text(), f"ruff=={gate.RUFF_VERSION}")

    def test_head_gate_and_pin_edits_cannot_accept_a_new_violation(self):
        self.base()
        self.write("scripts/check_python_lint_delta.py", "raise SystemExit(0)\n")
        self.write("futureagi/app/mod.py", 'value = 1\nprint("new")\n')
        proc = self.report()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("title=ruff T201", proc.stdout)
        self.assertIn(f"using ruff {gate.RUFF_VERSION}", proc.stdout)

    def test_head_pin_alone_cannot_change_the_judging_ruff(self):
        self.base()
        self.write("futureagi/app/mod.py", 'value = 1\nprint("new")\n')
        proc = self.report()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("title=ruff T201", proc.stdout)

    def test_base_gate_crash_fails_closed_despite_passing_head_gate(self):
        self.write(
            "scripts/check_python_lint_delta.py",
            'raise RuntimeError("base gate crashed")\n',
        )
        self.base()
        self.write("scripts/check_python_lint_delta.py", "raise SystemExit(0)\n")
        proc = self.report()
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("base gate crashed", proc.stderr)
        self.assertNotIn("No new violations.", proc.stdout)

    def test_invalid_base_pin_fails_closed(self):
        for pins in (
            "env: {}\n",
            'env:\n  RUFF_VERSION: "not-a-version"\n',
            f'env:\n  RUFF_VERSION: "{gate.RUFF_VERSION}"\n' * 2,
        ):
            with self.subTest(pins=pins):
                self.write(".github/workflows/python-lint.yml", pins)
                self.base()
                proc = self.report(head_version=gate.RUFF_VERSION)
                self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertFalse(self.installs.exists())
                self.assertNotIn("No new violations.", proc.stdout)


class RuffPinTests(unittest.TestCase):
    """`make lint`, `make format` and the futureagi pre-commit hooks run the ruff CI pins."""

    def test_the_makefile_and_the_pre_commit_config_pin_the_ci_ruff(self):
        workflow = (ROOT / ".github" / "workflows" / "python-lint.yml").read_text()
        pinned = re.search(r'RUFF_VERSION:\s*"([0-9.]+)"', workflow).group(1)

        makefile = (ROOT / "futureagi" / "Makefile").read_text()
        make_pin = re.search(r"^RUFF_VERSION := ([0-9.]+)$", makefile, re.MULTILINE)
        self.assertIsNotNone(make_pin, "futureagi/Makefile does not set RUFF_VERSION")
        self.assertEqual(make_pin.group(1), pinned)
        self.assertIn('"ruff==$(RUFF_VERSION)"', makefile)

        pre_commit = (ROOT / "futureagi" / ".pre-commit-config.yaml").read_text()
        hook_pin = re.search(
            r"repo: https://github\.com/astral-sh/ruff-pre-commit\n\s+rev: v([0-9.]+)\n",
            pre_commit,
        )
        self.assertIsNotNone(
            hook_pin, "futureagi/.pre-commit-config.yaml has no ruff-pre-commit hook"
        )
        self.assertEqual(hook_pin.group(1), pinned)


if __name__ == "__main__":
    unittest.main(verbosity=2)
