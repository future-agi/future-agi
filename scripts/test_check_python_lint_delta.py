"""Tests for scripts/check_python_lint_delta.py.

Run from the repo root: python3 -I scripts/test_check_python_lint_delta.py

The end-to-end tests build a throwaway git repo and run the real ruff. They are
the gate's canary: they prove it fails on a new violation and passes on an old
one. In CI (when CI is set) a missing ruff fails the run instead of skipping.
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_python_lint_delta.py"

spec = importlib.util.spec_from_file_location("check_python_lint_delta", SCRIPT)
gate = importlib.util.module_from_spec(spec)
sys.modules["check_python_lint_delta"] = gate
spec.loader.exec_module(gate)

RUFF = shutil.which("ruff")
REQUIRE_RUFF = bool(os.environ.get("CI"))


def v(path, code, line, source):
    return gate.Violation(path=path, code=code, line=line, column=1, message="m", source=source)


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
        for path in ("futureagi/app/views.py", "futureagi/app/testing_utils.py", "futureagi/contest.py"):
            self.assertFalse(gate.is_test_or_migration(path), path)

    def test_an_unchanged_violation_is_not_new(self):
        base = [v("futureagi/a.py", "BLE001", 4, "except Exception:")]
        head = [v("futureagi/a.py", "BLE001", 9, "except Exception:")]
        self.assertEqual(gate.new_violations(head, base, {"futureagi/a.py": "futureagi/a.py"}, {}), [])

    def test_an_extra_copy_is_new_and_taken_from_changed_lines(self):
        base = [v("futureagi/a.py", "BLE001", 4, "except Exception:")]
        head = [
            v("futureagi/a.py", "BLE001", 4, "except Exception:"),
            v("futureagi/a.py", "BLE001", 11, "except Exception:"),
        ]
        found = gate.new_violations(head, base, {"futureagi/a.py": "futureagi/a.py"}, {"futureagi/a.py": {11: 0}})
        self.assertEqual([x.line for x in found], [11])

    def test_new_code_outranks_a_line_that_only_gained_a_comment(self):
        base = [v("futureagi/a.py", "BLE001", 4, "except Exception:")]
        head = [
            v("futureagi/a.py", "BLE001", 4, "except Exception:"),
            v("futureagi/a.py", "BLE001", 11, "except Exception:"),
        ]
        ranks = {"futureagi/a.py": {4: 1, 11: 0}}
        found = gate.new_violations(head, base, {"futureagi/a.py": "futureagi/a.py"}, ranks)
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
        self.assertEqual(gate.new_violations(head, base, {"futureagi/new.py": "futureagi/old.py"}, {}), [])

    def test_a_different_rule_on_the_same_line_is_new(self):
        base = [v("futureagi/a.py", "S110", 4, "except Exception:")]
        head = [v("futureagi/a.py", "BLE001", 4, "except Exception:")]
        found = gate.new_violations(head, base, {"futureagi/a.py": "futureagi/a.py"}, {})
        self.assertEqual([x.code for x in found], ["BLE001"])

    def test_normalize_drops_the_comment_but_not_a_hash_in_a_string(self):
        self.assertEqual(gate.normalize("    except Exception:  # existing fallback"), "except Exception:")
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
        self.assertTrue(line.startswith("::error file=futureagi/a%2Cb.py,line=3,col=1,title=ruff BLE001 (new in this change)::"))
        self.assertIn("logger.exception", line)
        self.assertNotIn("\n", line)

    def test_a_warning_only_rule_is_annotated_as_a_warning(self):
        self.assertTrue(gate.annotation(v("futureagi/a.py", "S608", 3, "x")).startswith("::warning "))
        self.assertTrue(gate.annotation(v("futureagi/a.py", "BLE001", 3, "x")).startswith("::error "))

    def test_every_banned_rule_has_a_replacement_hint(self):
        for code in gate.BANNED_RULES:
            self.assertIn(code, gate.HINTS, code)

    def test_ci_and_the_pre_commit_hook_pin_the_same_ruff(self):
        workflow = (ROOT / ".github" / "workflows" / "python-lint.yml").read_text()
        hook = (ROOT / "scripts" / "lint-staged-python.sh").read_text()
        pinned = re.search(r'RUFF_VERSION:\s*"([0-9.]+)"', workflow).group(1)
        self.assertEqual(re.search(r'^RUFF_VERSION="([0-9.]+)"$', hook, re.MULTILINE).group(1), pinned)
        self.assertIn('"ruff==${RUFF_VERSION}"', hook)
        self.assertIn('"ruff@${RUFF_VERSION}"', hook)


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
            self.fail("ruff must be on PATH in CI: the canary would otherwise prove nothing")
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
        found = re.findall(r"^::(error|warning) file=([^,]+),line=(\d+),col=\d+,title=ruff ([A-Za-z0-9-]+)", proc.stdout, re.MULTILINE)
        self.warnings = [(f, int(n), c) for level, f, n, c in found if level == "warning"]
        return proc.returncode, [(f, int(n), c) for level, f, n, c in found if level == "error"], proc

    def test_fails_on_new_violations_only(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/mod.py", BASE_MOD)
        self.write("futureagi/app/old_ugly.py", "y=2\n")
        base = self.commit()

        # A comment on the old except line must not draw the new finding to it.
        self.write("futureagi/app/mod.py", HEAD_MOD.replace("except Exception:\n        return 0", "except Exception:  # existing fallback\n        return 0"))
        self.write("futureagi/app/old_ugly.py", "y=3\n")  # unformatted at the base too: not new
        self.write("futureagi/app/new_ugly.py", "x=1\n")  # new and unformatted: new
        self.write("futureagi/app/tests/test_mod.py", 'def test_x():\n    print("x")\n')  # tests: no T201
        self.write("futureagi/app/migrations/0001_x.py", 'print("x")\n')  # excluded
        self.write("futureagi/app/cli.py", 'print("x")\n')  # per-file-ignores survive --select
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
        self.assertEqual(self.warnings, [("futureagi/app/mod.py", 10, "S608")], proc.stdout)

    def test_a_warning_only_finding_does_not_fail_the_check(self):
        self.write("futureagi/pyproject.toml", CONFIG)
        self.write("futureagi/app/q.py", "def q(table):\n    return 1\n")
        base = self.commit()
        self.write("futureagi/app/q.py", 'def q(table):\n    return f"SELECT count() FROM {table}"\n')
        self.commit()

        code, found, proc = self.gate(base)
        self.assertEqual((code, found), (0, []), proc.stdout + proc.stderr)
        self.assertEqual(self.warnings, [("futureagi/app/q.py", 2, "S608")], proc.stdout)

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
        self.assertEqual({(f, c) for f, _, c in found}, {("futureagi/app/now_broken.py", "invalid-syntax")}, proc.stdout)


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
