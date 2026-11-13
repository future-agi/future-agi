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
        diff = "\n".join(
            [
                "@@ -4 +4 @@ def first():",
                "-    except Exception:",
                "+    except Exception:  # existing fallback",
                "@@ -5,0 +6,3 @@",
                "+",
                "+def second():",
                "+    pass",
            ]
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
            with patch.object(gate.subprocess, "run", return_value=proc) as run:
                found = gate.formatting_only_files(
                    "ruff", head, base, {path: path}, set()
                )
            self.assertEqual(found, set())
            self.assertEqual(run.call_args.kwargs["input"], b"x=1\r\n")
            self.assertEqual((base / path).read_bytes(), b"x=1\r\n")


WORKFLOW = ROOT / ".github" / "workflows" / "python-lint.yml"

# The paths whose change makes Ruff run: Python under futureagi/, its ruff
# config, and the gate, its tests, the pre-commit hook and this workflow.
LINT_PATHS = (
    "futureagi/**/*.py",
    "futureagi/pyproject.toml",
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
        self.assertRegex(summary, r"(?m)^    needs: \[changes, ruff-delta\]\s*$")
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
        self.assertIn(
            'git show "origin/${BASE_REF}:scripts/check_python_lint_delta.py" > "$gate"',
            ruff,
        )
        self.assertRegex(
            ruff,
            r'python3 -I "\$gate" --base "\$base" --expect-ruff-version "\$RUFF_VERSION"',
        )
        self.assertNotRegex(ruff, r"python3 -I scripts/check_python_lint_delta\.py")
        # The self-test runs only when the branch carries it.
        self.assertIn("if [ -f scripts/test_check_python_lint_delta.py ]; then", ruff)

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
            # (changes result, python output, ruff-delta result, passes)
            ("success", "true", "success", True),
            ("success", "false", "skipped", True),
            ("success", "true", "failure", False),
            ("success", "true", "cancelled", False),
            # Skipped although Python changed, or without an answer from changes.
            ("success", "true", "skipped", False),
            ("success", "", "skipped", False),
            ("success", "false", "failure", False),
            ("failure", "", "skipped", False),
            ("cancelled", "", "skipped", False),
            ("skipped", "", "skipped", False),
            ("failure", "true", "success", False),
            # A changes job that set its output and then did not succeed.
            ("failure", "false", "skipped", False),
            ("cancelled", "false", "skipped", False),
            ("skipped", "false", "skipped", False),
        ]
        with tempfile.TemporaryDirectory(prefix="lint-summary-test-") as tmp:
            path = Path(tmp) / "summary.sh"
            path.write_text(script)
            for changes, python, ruff, passes in cases:
                # The shell GitHub runs a step's script with.
                proc = subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(path)],
                    capture_output=True,
                    text=True,
                    env={
                        "PATH": os.environ["PATH"],
                        "CHANGES": changes,
                        "PYTHON_CHANGED": python,
                        "RUFF": ruff,
                    },
                )
                with self.subTest(changes=changes, python=python, ruff=ruff):
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


@unittest.skipUnless(RUFF or REQUIRE_RUFF, "ruff is not installed")
class EndToEndTests(unittest.TestCase):
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

    def gate(self, base):
        proc = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--base", base],
            cwd=self.repo,
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
            (code, found), (1, [(path, 1, "T201")]), proc.stdout + proc.stderr
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
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("ruff format", proc.stdout)
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
        hook_pin = re.search(r"repo: https://github\.com/astral-sh/ruff-pre-commit\n\s+rev: v([0-9.]+)\n", pre_commit)
        self.assertIsNotNone(hook_pin, "futureagi/.pre-commit-config.yaml has no ruff-pre-commit hook")
        self.assertEqual(hook_pin.group(1), pinned)


if __name__ == "__main__":
    unittest.main(verbosity=2)
