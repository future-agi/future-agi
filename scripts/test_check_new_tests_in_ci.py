#!/usr/bin/env python3
"""Tests for check_new_tests_in_ci.py. Run: python3 -I scripts/test_check_new_tests_in_ci.py

Each case builds a throwaway repository with one workflow, adds files on a branch, and runs
the check on it. Nothing is fetched and nothing outside the temporary folder is touched.
"""

import contextlib
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "check", os.path.join(HERE, "check_new_tests_in_ci.py")
)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

WORKFLOW = """\
name: CI
on:
  pull_request:
    paths:
      - 'tests/test_filtered.py'
jobs:
  tests:
    steps:
      # tests/test_commented.py is not run yet
      - name: Named files
        run: >-
          python -m pytest -q
          tests/test_listed.py
          tests/episode
          --ignore=tests/test_ignored.py
          --ignore=tests/slow
          -c sub/pytest.ini --confcutdir=sub sub
      - name: By module
        run: python3 -m unittest -v test_by_module
      - name: Discovered
        run: python -m unittest discover -s deploy/tests -p 'test_helm_*.py' -v
      - name: The whole tree
        run: uv run pytest -q --whole-tree
"""
TREE = {
    "root": "backend",
    "ini": "backend/pytest.ini",
    "left_out": ["tests/stress"],
    "workflow": "ci.yml",
    "proof": "uv run pytest -q --whole-tree",
    "job": "ci.yml, the tree",
}
INI = "[pytest]\npython_files = test_*.py\nnorecursedirs =\n    scripts\n    .venv\n"


class Repo:
    def __init__(self, workflow=WORKFLOW):
        self.root = tempfile.mkdtemp()
        self.git("init", "-q", "-b", "base")
        self.write(".github/workflows/ci.yml", workflow)
        self.write("backend/pytest.ini", INI)
        self.write("tests/test_listed.py", "")
        self.commit("base")
        self.git("checkout", "-q", "-b", "work")

    def git(self, *args):
        subprocess.run(
            [
                "git",
                "-C",
                self.root,
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            check=True,
            capture_output=True,
        )

    def write(self, path, text=""):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def add(self, *paths):
        for path in paths:
            self.write(path)
        self.commit("add")

    def run(self, *argv, trees=(TREE,)):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(
                ["--base", "base", *argv], root=self.root, trees=list(trees)
            )
        return code, out.getvalue()


class NewTestFiles(unittest.TestCase):
    def test_an_unlisted_new_test_file_fails(self):
        repo = Repo()
        repo.add("tests/test_new.py")
        code, out = repo.run()
        self.assertEqual(code, 1)
        self.assertIn("NOT RUN  tests/test_new.py", out)

    def test_it_passes_once_the_workflow_names_the_file(self):
        repo = Repo()
        repo.add("tests/test_new.py")
        repo.write(
            ".github/workflows/ci.yml",
            WORKFLOW.replace(
                "tests/test_listed.py",
                "tests/test_listed.py\n          tests/test_new.py",
            ),
        )
        repo.commit("list it")
        code, out = repo.run()
        self.assertEqual(code, 0, out)
        self.assertIn("named in ci.yml", out)

    def test_no_new_test_file_passes(self):
        repo = Repo()
        repo.add("src/app.py", "docs/notes.md")
        self.assertEqual(repo.run()[0], 0)

    def test_a_changed_file_is_not_a_new_one(self):
        repo = Repo(WORKFLOW.replace("tests/test_listed.py", "tests/other.py"))
        repo.write("tests/test_listed.py", "x = 1\n")
        repo.commit("edit")
        code, out = repo.run()
        self.assertEqual(code, 0, out)
        self.assertIn("0 Python test file(s) checked", out)

    def test_a_comment_a_paths_filter_and_an_ignore_do_not_count(self):
        repo = Repo()
        repo.add(
            "tests/test_commented.py", "tests/test_filtered.py", "tests/test_ignored.py"
        )
        code, out = repo.run()
        self.assertEqual(code, 1)
        for name in ("test_commented", "test_filtered", "test_ignored"):
            self.assertIn(f"NOT RUN  tests/{name}.py", out)

    def test_a_folder_given_to_pytest_counts_for_the_files_below_it(self):
        repo = Repo()
        repo.add(
            "tests/episode/test_one.py",
            "tests/episode/deep/test_two.py",
            "tests/slow/test_ignored_folder.py",
            "sub/test_at_the_root.py",
            "other/sub/test_not_the_same_sub.py",
        )
        code, out = repo.run()
        self.assertEqual(code, 1)
        self.assertIn(
            "run      tests/episode/test_one.py  (in tests/episode/, which ci.yml gives to pytest)",
            out,
        )
        self.assertIn("run      tests/episode/deep/test_two.py", out)
        self.assertIn("run      sub/test_at_the_root.py", out)
        self.assertIn("NOT RUN  tests/slow/test_ignored_folder.py", out)
        self.assertIn("NOT RUN  other/sub/test_not_the_same_sub.py", out)

    def test_a_word_after_pytest_that_is_not_a_folder_here_counts_for_nothing(self):
        repo = Repo(WORKFLOW.replace("tests/episode", "tests/gone"))
        repo.add("tests/episode/test_one.py")
        self.assertEqual(repo.run()[0], 1)

    def test_a_module_named_after_unittest_counts(self):
        repo = Repo()
        repo.add("scripts/test_by_module.py")
        code, out = repo.run()
        self.assertEqual(code, 0, out)

    def test_discovery_goes_by_folder_and_pattern(self):
        repo = Repo()
        repo.add(
            "deploy/tests/test_helm_chart.py",
            "deploy/tests/test_other.py",
            "deploy/tests/sub/test_helm_deep.py",
        )
        code, out = repo.run()
        self.assertEqual(code, 1)
        self.assertIn(
            "run      deploy/tests/test_helm_chart.py  (discovered in ci.yml)", out
        )
        self.assertIn("NOT RUN  deploy/tests/test_other.py", out)
        self.assertIn(
            "NOT RUN  deploy/tests/sub/test_helm_deep.py", out
        )  # sub is not a package

    def test_discovery_reaches_a_package_below_the_start_folder(self):
        repo = Repo()
        repo.add("deploy/tests/sub/__init__.py", "deploy/tests/sub/test_helm_deep.py")
        self.assertEqual(repo.run()[0], 0)

    def test_a_tree_one_job_collects_whole(self):
        repo = Repo()
        repo.add(
            "backend/app/tests/test_in_tree.py",
            "backend/app/scripts/test_not_recursed.py",
            "backend/tests/stress/test_left_out.py",
            "backend/app/tests/check_wrong_name_test.py",
        )
        code, out = repo.run()
        self.assertEqual(code, 1)
        self.assertIn(
            "run      backend/app/tests/test_in_tree.py  (collected by ci.yml, the tree)",
            out,
        )
        self.assertIn("NOT RUN  backend/app/scripts/test_not_recursed.py", out)
        self.assertIn("NOT RUN  backend/tests/stress/test_left_out.py", out)
        self.assertNotIn(
            "check_wrong_name_test.py", out
        )  # not a test file by this repository's names

    def test_it_stops_when_ci_no_longer_shows_the_tree_is_collected(self):
        repo = Repo(
            WORKFLOW.replace(
                "uv run pytest -q --whole-tree", "uv run pytest -q tests/unit"
            )
        )
        repo.add("backend/app/tests/test_in_tree.py")
        code, out = repo.run()
        self.assertEqual(code, 2)
        self.assertIn("could not decide", out)

    def test_a_test_in_another_language_is_listed_and_not_judged(self):
        repo = Repo()
        repo.add("frontend/src/button.test.jsx", "collector/server_test.go")
        code, out = repo.run()
        self.assertEqual(code, 0, out)
        self.assertIn("unjudged frontend/src/button.test.jsx", out)

    def test_files_given_by_name_are_checked_without_a_diff(self):
        repo = Repo()
        code, out = repo.run("tests/test_listed.py", "tests/test_absent.py")
        self.assertEqual(code, 1)
        self.assertIn("run      tests/test_listed.py", out)
        self.assertIn("NOT RUN  tests/test_absent.py", out)

    def test_a_base_git_does_not_know_is_exit_2(self):
        repo = Repo()
        with (
            self.assertRaises(SystemExit) as stop,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            check.main(["--base", "no-such-ref"], root=repo.root, trees=[])
        self.assertEqual(stop.exception.code, 2)


class ThisRepository(unittest.TestCase):
    def test_the_trees_table_still_matches_the_workflows(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["README.md"])
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
