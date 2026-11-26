#!/usr/bin/env python3
"""Tests for before_push.py. Run: python3 -I scripts/test_before_push.py

Each case writes a list to a temporary folder and runs the runner on it.
"""

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "before_push", os.path.join(HERE, "before_push.py")
)
before_push = importlib.util.module_from_spec(spec)
spec.loader.exec_module(before_push)


def run(text, *argv):
    root = tempfile.mkdtemp()
    path = os.path.join(root, "before-push.list")
    with open(path, "w") as handle:
        handle.write(text)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = before_push.main(["--file", path, *argv], root=root)
    return code, out.getvalue(), root


class Runner(unittest.TestCase):
    def test_an_empty_list_passes(self):
        code, out, _ = run("# nothing yet\n\n")
        self.assertEqual(code, 0)
        self.assertIn("the list is empty", out)

    def test_one_failing_item_is_named(self):
        code, out, _ = run(
            "first | true\nmanifest | echo out of date; exit 3\nlast | true\n"
        )
        self.assertEqual(code, 1)
        self.assertIn("FAILED manifest", out)
        self.assertIn("| out of date", out)
        self.assertIn("ok     last", out)  # the items after a failure still run
        self.assertEqual(
            out.strip().split("\n")[-1], "Before a push: 1 of 3 failed: manifest"
        )

    def test_every_failing_item_is_named(self):
        code, out, _ = run("a | false\nb | true\nc | false\n")
        self.assertEqual(code, 1)
        self.assertTrue(out.strip().endswith("2 of 3 failed: a, c"))

    def test_all_passing(self):
        code, out, _ = run("a | true\nb | test 1 = 1\n")
        self.assertEqual(code, 0)
        self.assertIn("all 2 passed", out)

    def test_commands_run_from_the_repository_root(self):
        code, out, root = run("where | pwd > here.txt\n")
        self.assertEqual(code, 0, out)
        with open(os.path.join(root, "here.txt")) as handle:
            self.assertEqual(
                os.path.realpath(handle.read().strip()), os.path.realpath(root)
            )

    def test_a_command_may_hold_a_bar(self):
        code, out, _ = run("pipe | echo yes | grep -q yes\n")
        self.assertEqual(code, 0, out)

    def test_a_line_with_no_bar_is_exit_2(self):
        code, out, _ = run("a | true\njust a command\n")
        self.assertEqual(code, 2)
        self.assertIn("line 2", out)

    def test_a_name_used_twice_is_exit_2(self):
        code, out, _ = run("a | true\na | false\n")
        self.assertEqual(code, 2)
        self.assertIn("a second time", out)

    def test_a_missing_list_is_exit_2(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = before_push.main(
                ["--file", os.path.join(tempfile.mkdtemp(), "absent.list")]
            )
        self.assertEqual(code, 2)

    def test_only_runs_the_named_item(self):
        code, out, _ = run("a | false\nb | true\n", "--only", "b")
        self.assertEqual(code, 0, out)
        self.assertNotIn("a  (", out)
        self.assertEqual(run("a | true\n", "--only", "zzz")[0], 2)

    def test_list_prints_and_runs_nothing(self):
        code, out, root = run("a | touch ran.txt\n", "--list")
        self.assertEqual(code, 0)
        self.assertIn("a | touch ran.txt", out)
        self.assertFalse(os.path.exists(os.path.join(root, "ran.txt")))

    def test_an_item_that_runs_too_long_fails(self):
        code, out, _ = run("slow | sleep 5\n", "--timeout", "1")
        self.assertEqual(code, 1)
        self.assertIn("stopped after 1 seconds", out)


class ThisRepository(unittest.TestCase):
    def test_the_shipped_list_can_be_read(self):
        names = [name for name, _ in before_push.read_items(before_push.LIST)]
        self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
