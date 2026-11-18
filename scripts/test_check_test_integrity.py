"""Self-test the gate, including its CLI in a throwaway git repository.

Run in CI with: python3 -I scripts/test_check_test_integrity.py
No application packages or test runner dependencies are required.
"""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_test_integrity.py"
spec = importlib.util.spec_from_file_location("check_test_integrity", SCRIPT)
gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gate
spec.loader.exec_module(gate)


def focused(name="it"):
    # The staged safety scanner intentionally scans fixture strings too.
    return name + "." + "only('focused', () => {});\n"


def rules(found):
    return [(finding.code, finding.level) for finding in found]


class DetectionTests(unittest.TestCase):
    def test_test_path_conventions(self):
        for path in (
            "futureagi/app/tests.py",
            "futureagi/app/test_a.py",
            "futureagi/app/a_test.py",
            "futureagi/app/tests/helpers.py",
            "frontend/src/a.test.tsx",
            "frontend/src/a.spec.jsx",
            "frontend/__tests__/a.mjs",
            "e2e/specs/a.ts",
            "scripts/test_gate.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.is_test(path))
        for path in (
            "docs/test_a.md",
            "frontend/src/testing.ts",
            "futureagi/app/contest.py",
            "futureagi/conftest.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(gate.is_test(path))

    def test_each_focused_runner_is_an_error(self):
        for runner in ("it", "test", "describe"):
            with self.subTest(runner=runner):
                found = gate.selections("frontend/a.test.ts", focused(runner))
                self.assertEqual(rules(found), [("FOCUSED_TEST", "error")])
                self.assertEqual(found[0].line, 1)

    def test_focus_with_whitespace_across_lines(self):
        found = gate.selections(
            "frontend/a.test.ts", "test\n . only\n ('name', () => {});"
        )
        self.assertEqual(rules(found), [("FOCUSED_TEST", "error")])
        self.assertEqual(found[0].line, 1)

    def test_examples_and_other_objects_do_not_trigger_focus(self):
        text = "// " + focused() + "/* " + focused() + " */\n"
        text += 'const example = "' + focused().strip() + '";\n'
        text += "const template = `" + focused() + "`;\n"
        text += "widget." + focused() + "my" + focused()
        self.assertEqual(gate.selections("frontend/a.test.ts", text), [])
        self.assertEqual(gate.selections("frontend/src/runner.ts", focused()), [])

    def test_regex_quotes_escapes_and_classes_do_not_hide_executable_code(self):
        for prefix in (
            "const pattern = /can't/;\n",
            'const pattern = /say "hello"/g;\n',
            r"const pattern = /can\'t\/stop/;" + "\n",
            "const pattern = /['\"/]/;\n",
            r"const pattern = /[\]'\/]/;" + "\n",
            "expect(screen.getByText(/isn't available/i)).toBeVisible();\n",
            "if (ready) /can't/.test(value);\n",
            "const pattern = () => /can't/;\n",
        ):
            with self.subTest(prefix=prefix):
                text = prefix + focused()
                code, comments = gate.js_source(text)
                self.assertEqual(len(code), len(text))
                self.assertEqual(code.count("\n"), text.count("\n"))
                self.assertEqual(code.index("it"), text.index("it"))
                self.assertEqual(comments, [])
                found = gate.selections("frontend/src/a.test.ts", text)
                self.assertEqual(rules(found), [("FOCUSED_TEST", "error")])
                self.assertEqual(found[0].line, 2)

    def test_regex_contents_are_not_code_or_comments(self):
        text = "const example = /" + re.escape(focused().strip()) + "/;\n"
        text += "const directive = /[/*]@ts-ignore/;\n// eslint-disable\n"
        code, comments = gate.js_source(text)
        self.assertNotIn("only", code)
        self.assertEqual(comments, [(text.index("// eslint"), "// eslint-disable")])
        found = gate.selections("frontend/src/a.test.ts", text)
        self.assertEqual(rules(found), [("BLANKET_ESLINT", "error")])
        self.assertEqual(found[0].line, 3)

    def test_division_does_not_mask_a_focused_call(self):
        for numerator in ("total", "getTotal()", "totals[0]", "total++", "10"):
            with self.subTest(numerator=numerator):
                text = (
                    "const ratio = "
                    + numerator
                    + " / "
                    + focused().strip().removesuffix(";")
                    + " / divisor;\n"
                )
                self.assertEqual(
                    rules(gate.selections("frontend/src/a.test.ts", text)),
                    [("FOCUSED_TEST", "error")],
                )

    def test_suppressions_are_errors_in_all_js_and_ts_source(self):
        for directive in (
            "// eslint-disable",
            "/* eslint-disable */",
            "// eslint-disable-next-line",
            "// eslint-disable-line -- not a rule",
            "/*\n * eslint-disable\n */",
            "// @ts-ignore reason",
            "/* @ts-nocheck */",
        ):
            with self.subTest(directive=directive):
                found = gate.selections(
                    "frontend/src/app.ts", directive + "\nconst x = 1;\n"
                )
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0].level, "error")

    def test_named_suppressions_expect_error_and_string_examples_are_allowed(self):
        text = "\n".join(
            (
                "// eslint-disable-next-line no-console -- CLI output",
                "/* eslint-disable no-console, no-alert -- CLI output */",
                "// @ts-expect-error intentionally invalid input",
                'const example = "// @ts-ignore";',
                "const other = `/* eslint-disable */`;",
            )
        )
        self.assertEqual(gate.selections("frontend/src/app.ts", text), [])
        self.assertEqual(gate.selections("futureagi/a.py", "x = 1  # noqa\n"), [])
        self.assertEqual(
            gate.selections("futureagi/a.py", "x = 1  # type: ignore\n"), []
        )

    def test_multiline_eslint_rule_lists_are_not_blanket_suppressions(self):
        for directive in (
            "/* eslint-disable\n no-alert -- browser API is mocked here\n */",
            "/* eslint-disable-next-line\n no-alert,\n no-console -- mocked browser APIs\n */",
            "/*\n * eslint-disable\n * no-alert -- browser API is mocked here\n */",
        ):
            with self.subTest(directive=directive):
                self.assertEqual(gate.selections("frontend/src/app.ts", directive), [])

    def test_multiline_blanket_eslint_reports_the_directive_line(self):
        text = "const value = 1;\n/*\n * eslint-disable\n * -- browser API is mocked here\n */\n"
        found = gate.selections("frontend/src/app.ts", text)
        self.assertEqual(rules(found), [("BLANKET_ESLINT", "error")])
        self.assertEqual(found[0].line, 3)

    def test_removing_only_the_multiline_rule_list_is_a_new_error(self):
        path = "frontend/src/app.ts"
        old = "/* eslint-disable\n no-alert -- browser API is mocked here\n */\n"
        new = "/* eslint-disable\n -- browser API is mocked here\n */\n"
        found = gate.inspect_change(gate.Change(path, path), old, new)
        self.assertEqual(rules(found), [("BLANKET_ESLINT", "error")])
        self.assertEqual(found[0].line, 1)

    def test_backend_markers_warn_with_or_without_arguments(self):
        for marker in (
            "pytest.mark.skip",
            "pytest.mark.skipif(True)",
            "pytest.mark.xfail(reason='broken')",
            "unittest.skip('broken')",
            "unittest.skipIf(True, 'broken')",
            "unittest.skipUnless(False, 'broken')",
        ):
            with self.subTest(marker=marker):
                found = gate.selections(
                    "futureagi/test_a.py", f"@{marker}\ndef test_a():\n    pass\n"
                )
                self.assertEqual(rules(found), [("BACKEND_SKIP", "warning")])
        found = gate.selections(
            "futureagi/test_a.py", "def test_a():\n    pytest.skip('broken')\n"
        )
        self.assertEqual(rules(found), [("BACKEND_SKIP", "warning")])
        self.assertEqual(found[0].line, 2)

    def test_capability_markers_and_python_examples_are_not_flagged(self):
        text = (
            '"""pytest.mark.skip and pytest.skip examples"""\n'
            "# pytest.mark.xfail\n"
            "pytest.importorskip('optional')\n"
            "@pytest.mark.requires_ee\ndef test_a():\n    pass\n"
        )
        self.assertEqual(gate.selections("futureagi/test_a.py", text), [])

    def test_each_frontend_skip_warns_even_if_it_names_a_ticket(self):
        for runner in ("it", "test", "describe"):
            with self.subTest(runner=runner):
                text = f"{runner}.skip('ticket comes from the author', () => {{}});"
                self.assertEqual(
                    rules(gate.selections("frontend/a.test.ts", text)),
                    [("FRONTEND_SKIP", "warning")],
                )

    def test_assertion_change_and_test_deletion_need_owner_review(self):
        path = "futureagi/tests/test_a.py"
        old = "def test_a():\n    assert value == 1\n\ndef test_b():\n    self.assertEqual(value, 2)\n"
        new = "def test_a():\n    assert value\n"
        found = gate.inspect_change(gate.Change(path, path), old, new)
        self.assertEqual(
            rules(found),
            [("ASSERTION_CHANGED", "warning")] * 2 + [("TEST_DELETED", "warning")],
        )
        self.assertIn("test_b", found[-1].source)
        self.assertTrue(all("code owner" in gate.HINTS[item.code] for item in found))

    def test_assertion_detectors_ignore_examples(self):
        python = '"""assert fake"""\n# self.assertEqual(a, b)\nwith pytest.raises(ValueError):\n    assert value\n'
        self.assertEqual(
            gate.assertions("test_a.py", python),
            [(3, "with pytest.raises(ValueError):"), (4, "assert value")],
        )
        js = (
            '// expect(fake)\nconst example = "expect(fake)";\nexpect(value).toBe(1);\n'
        )
        self.assertEqual(
            gate.assertions("a.test.js", js), [(3, "expect(value).toBe(1);")]
        )

    def test_function_identity_tracks_python_classes_async_and_frontend_names(self):
        python = "class TestA:\n    async def test_a(self):\n        pass\n\nclass TestB:\n    def test_a(self):\n        pass\n"
        self.assertEqual(
            gate.test_functions("test_a.py", python),
            [(2, "TestA.test_a"), (6, "TestB.test_a")],
        )
        js = "describe('suite', () => {\n  it('first', () => {});\n  test.skip(\"second\", () => {});\n});\n"
        self.assertEqual(
            gate.test_functions("a.test.ts", js), [(2, "first"), (3, "second")]
        )

    def test_file_removal_and_rename_out_of_test_discovery_warn(self):
        old = "def test_a():\n    assert True\n"
        for new_path in (None, "futureagi/unused.py"):
            with self.subTest(new_path=new_path):
                found = gate.inspect_change(
                    gate.Change("futureagi/test_a.py", new_path),
                    old,
                    old if new_path else "",
                )
                self.assertEqual(rules(found), [("TEST_FILE_DELETED", "warning")])

    def test_discovery_patterns_are_narrower_than_test_support_paths(self):
        for path in (
            "futureagi/app/tests/test_example.py",
            "futureagi/model_serving/tests/test_model.py",
            "futureagi/agentic_eval/tests/test_agent.py",
            "frontend/src/a.test.tsx",
            "frontend/src/a.spec.jsx",
            "frontend/src/__tests__/helpers.js",
            "frontend/scripts/api-journeys/a.test.mjs",
            "e2e/flows/a.spec.ts",
            "e2e/harness/nested/a.spec.ts",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.is_discoverable_test(path))
        for path in (
            "futureagi/app/tests/helper.py",
            "futureagi/app/tests/example_test.py",
            "frontend/src/sections/__tests__/helper.js",
            "frontend/src/a.test.mjs",
            "frontend/scripts/api-journeys/a.spec.js",
            "e2e/flows/a.ts",
            "e2e/flows/a.spec.js",
            "e2e/lib/a.spec.ts",
        ):
            with self.subTest(path=path):
                self.assertTrue(gate.is_test(path))
                self.assertFalse(gate.is_discoverable_test(path))

    def test_renames_preserving_discovery_do_not_warn(self):
        for old_path, new_path, text in (
            (
                "futureagi/app/tests/test_a.py",
                "futureagi/app/tests/test_b.py",
                "def test_a():\n    assert True\n",
            ),
            (
                "frontend/src/a.test.ts",
                "frontend/src/a.spec.ts",
                "test('a', () => {});\n",
            ),
            (
                "frontend/src/a.test.ts",
                "frontend/src/__tests__/a.ts",
                "test('a', () => {});\n",
            ),
            ("e2e/flows/a.spec.ts", "e2e/harness/a.spec.ts", "test('a', () => {});\n"),
        ):
            with self.subTest(new_path=new_path):
                self.assertEqual(
                    gate.inspect_change(gate.Change(old_path, new_path), text, text), []
                )

    def test_multiset_matching_does_not_hide_an_extra_or_removed_duplicate(self):
        text = focused()
        old = gate.selections("a.test.ts", text)
        new_text = text + "\n" + text
        found = gate.new_findings(
            old,
            gate.selections("a.test.ts", new_text),
            gate.added_lines(text, new_text),
        )
        self.assertEqual(
            [(item.code, item.line) for item in found], [("FOCUSED_TEST", 3)]
        )
        self.assertEqual(
            gate.removed_items([(1, "assert x"), (2, "assert x")], [(8, "assert x")]),
            [(2, "assert x")],
        )

    def test_moved_lines_and_test_file_renames_keep_the_baseline(self):
        old = focused() + "expect(value).toBe(1);\n"
        new = "// unrelated comment\n" + old
        found = gate.inspect_change(gate.Change("old.test.ts", "new.test.ts"), old, new)
        self.assertEqual(found, [])

    def test_new_files_do_not_have_removed_assertions(self):
        found = gate.inspect_change(
            gate.Change(None, "test_a.py"), "", "def test_a():\n    assert True\n"
        )
        self.assertEqual(found, [])

    def test_quarantine_change_is_a_notice(self):
        for change in (
            gate.Change(None, gate.QUARANTINE),
            gate.Change(gate.QUARANTINE, gate.QUARANTINE),
            gate.Change(gate.QUARANTINE, None),
        ):
            with self.subTest(change=change):
                self.assertEqual(
                    rules(gate.inspect_change(change, "[]", "[]")),
                    [("QUARANTINE_CHANGED", "notice")],
                )

    def test_annotations_escape_paths_and_newlines_and_include_remediation(self):
        finding = gate.Finding(
            "a,b%\n.test.ts", 4, "FOCUSED_TEST", "error", "", "new\nmarker"
        )
        annotation = gate.annotation(finding)
        self.assertIn("file=a%2Cb%25%0A.test.ts,line=4", annotation)
        self.assertIn("new%0Amarker", annotation)
        self.assertIn("Remove the focused marker", annotation)
        self.assertNotIn("\n", annotation)


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="test-integrity-canary-")
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.summary = self.repo.parent / (self.repo.name + "-summary.txt")
        self.addCleanup(self.summary.unlink, missing_ok=True)
        self.git("init", "-q", "-b", "main")
        self.write("README.txt", "canary\n")

    def git(self, *args):
        return subprocess.run(
            [
                "git",
                "-c",
                "user.email=canary@example.com",
                "-c",
                "user.name=Canary",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                *args,
            ],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def write(self, relative, text):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "canary")
        return self.git("rev-parse", "HEAD")

    def run_gate(self, base):
        proc = subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--base", base],
            cwd=self.repo,
            capture_output=True,
            text=True,
            env={**os.environ, "GITHUB_STEP_SUMMARY": str(self.summary)},
        )
        found = re.findall(
            r"^::(error|warning|notice) file=([^,]+),line=(\d+),title=Test integrity (\w+)::",
            proc.stdout,
            re.MULTILINE,
        )
        return proc, [
            (level, path, int(line), code) for level, path, line, code in found
        ]

    def test_cli_canary_rejects_new_errors_but_keeps_old_debt(self):
        self.write("frontend/old.test.ts", focused())
        self.write("frontend/old.ts", "// @ts-ignore old debt\nconst value = 1;\n")
        base = self.commit()
        self.write("frontend/old.test.ts", "// moved, not new\n" + focused())
        self.write("frontend/old.ts", "// @ts-ignore old debt\nconst value = 2;\n")
        self.write("frontend/new.test.ts", focused("describe"))
        self.write(
            "frontend/new.ts",
            "/* eslint-disable */\n// @ts-ignore new\n// @ts-nocheck\n",
        )
        self.commit()
        proc, found = self.run_gate(base)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(
            sorted(found),
            sorted(
                [
                    ("error", "frontend/new.test.ts", 1, "FOCUSED_TEST"),
                    ("error", "frontend/new.ts", 1, "BLANKET_ESLINT"),
                    ("error", "frontend/new.ts", 2, "TS_SUPPRESSION"),
                    ("error", "frontend/new.ts", 3, "TS_SUPPRESSION"),
                ]
            ),
            proc.stdout,
        )
        summary = self.summary.read_text()
        self.assertIn("4 error(s), 0 warning(s), 0 notice(s)", summary)
        self.assertIn(base[:12], summary)
        self.assertIn("Remove the focused marker", summary)

    def test_cli_regex_apostrophe_does_not_hide_new_focused_test(self):
        base = self.commit()
        self.write(
            "frontend/src/regex.test.ts", "const pattern = /can't/;\n" + focused()
        )
        self.commit()
        proc, found = self.run_gate(base)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(
            found, [("error", "frontend/src/regex.test.ts", 2, "FOCUSED_TEST")]
        )

    def test_cli_multiline_named_eslint_passes_but_blanket_fails(self):
        base = self.commit()
        self.write(
            "frontend/src/named.ts",
            "/* eslint-disable\n no-alert -- browser API is mocked here\n */\n",
        )
        self.commit()
        proc, found = self.run_gate(base)
        self.assertEqual((proc.returncode, found), (0, []), proc.stdout + proc.stderr)
        self.write(
            "frontend/src/blanket.ts",
            "/* eslint-disable\n -- browser API is mocked here\n */\n",
        )
        self.commit()
        proc, found = self.run_gate(base)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(
            found, [("error", "frontend/src/blanket.ts", 1, "BLANKET_ESLINT")]
        )

    def test_cli_unchanged_renames_out_of_runner_patterns_warn(self):
        for old, new, text in (
            (
                "futureagi/app/tests/test_example.py",
                "futureagi/app/tests/helper.py",
                "def test_example():\n    assert True\n",
            ),
            (
                "e2e/flows/example.spec.ts",
                "e2e/flows/example.ts",
                "test('example', () => { expect(true).toBe(true); });\n",
            ),
            (
                "e2e/harness/example.spec.ts",
                "e2e/harness/example.ts",
                "test('harness', () => {});\n",
            ),
            (
                "frontend/src/tests/example.test.ts",
                "frontend/src/tests/example.ts",
                "test('frontend', () => {});\n",
            ),
            (
                "frontend/src/__tests__/root.ts",
                "frontend/src/tests/root.ts",
                "test('root discovery', () => {});\n",
            ),
        ):
            with self.subTest(old=old, new=new):
                self.write(old, text)
                base = self.commit()
                (self.repo / new).parent.mkdir(parents=True, exist_ok=True)
                self.git("mv", old, new)
                self.commit()
                proc, found = self.run_gate(base)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(
                    found, [("warning", new, 1, "TEST_FILE_DELETED")], proc.stdout
                )
                self.assertIn(old, proc.stdout)
                self.assertIn("code owner", proc.stdout)

    def test_cli_warning_only_changes_pass_and_ask_for_review(self):
        self.write(
            "futureagi/test_a.py",
            "def test_keep():\n    assert value == 1\n\ndef test_drop():\n    pass\n",
        )
        self.write(
            "frontend/a.test.ts", "it('gone', () => {});\nexpect(value).toBe(1);\n"
        )
        self.write("frontend/deleted.test.ts", "test('removed', () => {});\n")
        self.write(gate.QUARANTINE, "[]\n")
        base = self.commit()
        self.write(
            "futureagi/test_a.py",
            "@pytest.mark.xfail\ndef test_keep():\n    assert value\n",
        )
        self.write(
            "frontend/a.test.ts",
            "it.skip('replacement needs an issue', () => {});\nexpect(value).toBeTruthy();\n",
        )
        self.git("rm", "frontend/deleted.test.ts")
        self.write(gate.QUARANTINE, "[{}]\n")
        self.commit()
        proc, found = self.run_gate(base)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(
            sorted(code for _, _, _, code in found),
            sorted(
                [
                    "ASSERTION_CHANGED",
                    "ASSERTION_CHANGED",
                    "TEST_DELETED",
                    "TEST_DELETED",
                    "TEST_FILE_DELETED",
                    "BACKEND_SKIP",
                    "FRONTEND_SKIP",
                    "QUARANTINE_CHANGED",
                ]
            ),
            proc.stdout,
        )
        self.assertNotIn("::error", proc.stdout)
        self.assertIn(gate.QUARANTINE, proc.stdout)
        self.assertIn("code owner", self.summary.read_text())

    def test_cli_uses_merge_base_not_the_latest_target_commit(self):
        ancestor = self.commit()
        self.git("checkout", "-qb", "feature")
        self.write("frontend/a.test.ts", focused())
        self.commit()
        self.git("checkout", "-q", "main")
        self.write("frontend/a.test.ts", focused())
        self.write("target-only.txt", "Keep the target and feature commits distinct.\n")
        self.commit()
        self.git("checkout", "-q", "feature")
        proc, found = self.run_gate("main")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual(found, [("error", "frontend/a.test.ts", 1, "FOCUSED_TEST")])
        self.assertIn(ancestor[:12], self.summary.read_text())

    def test_cli_rename_spaces_and_unchanged_files_keep_existing_debt(self):
        self.write("frontend/old name.test.ts", focused() + "expect(1).toBe(1);\n")
        self.write("frontend/untouched.test.ts", focused("test"))
        base = self.commit()
        self.git("mv", "frontend/old name.test.ts", "frontend/new name.test.ts")
        self.write("README.txt", "canary updated\n")
        self.commit()
        proc, found = self.run_gate(base)
        self.assertEqual((proc.returncode, found), (0, []), proc.stdout + proc.stderr)
        self.assertIn("0 error(s), 0 warning(s), 0 notice(s)", self.summary.read_text())

    def test_cli_bad_base_is_an_operational_error_not_a_clean_pass(self):
        self.commit()
        proc, found = self.run_gate("no-such-ref")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(found, [])
        self.assertIn("::error title=Test integrity gate::", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
