#!/usr/bin/env python3
"""Fail when a change adds a Python test file that no CI job runs.

    python3 scripts/check_new_tests_in_ci.py [--base origin/dev] [--all] [FILE ...]

With no FILE it checks the test files HEAD adds since it left the base. --all checks every
test file in the repository. A test file counts as run when:

  named       a workflow under .github/workflows, or an action under .github/actions, names
              its path in a command, its module after `-m unittest`, or a folder above it
              as a pytest target (a folder that exists from the repository root);
  discovered  a workflow runs `unittest discover -s <folder> -p <pattern>` that matches it;
  collected   it sits in a tree listed in TREES, which one job collects whole.

Not counted: comments, `paths:` and `filters:` blocks, and what --ignore and --deselect name.
It reads names only. It does not see a job that is skipped, a -k or -m that leaves the
file's tests out, or a list of files a script builds. Test files in other languages are
listed and not judged.

Exit 0: every file is run. 1: some are not. 2: it could not decide (git failed, or CI
changed under a TREES entry).
"""

import argparse
import configparser
import fnmatch
import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# What counts as a Python test file, by name.
TEST_NAMES = ("test_*.py", "tests.py")
# Test files this check lists and does not judge.
OTHER_TESTS = ("*.test.*", "*.spec.*", "*_test.go")
# Trees one job collects whole. `proof` is workflow text that shows the job still does it:
# when it is gone the check stops with exit 2 and this table needs an edit.
TREES = [
    {
        "root": "futureagi",
        "ini": "futureagi/pytest.ini",
        "left_out": ["tests/stress"],
        "workflow": "backend-ci.yml",
        "proof": "uv run pytest -q -ra --ignore=tests/stress",
        "job": "backend-ci.yml, the pytest shards",
    },
    {
        "root": "futureagi/agentic_eval",
        "ini": "futureagi/agentic_eval/pytest.ini",
        "left_out": [],
        "workflow": "backend-ci.yml",
        "proof": "-c agentic_eval/pytest.ini --confcutdir=agentic_eval agentic_eval",
        "job": "backend-ci.yml, the evaluator suite",
    },
]
# pytest options whose next word is a value, not a target.
PYTEST_OPTIONS_WITH_VALUE = {
    "-k", "-m", "-c", "-p", "-o", "-n", "-W", "-r", "--confcutdir", "--rootdir", "--junitxml",
    "--basetemp", "--durations-path", "--splits", "--group", "--splitting-algorithm",
    "--cov", "--cov-report", "--maxfail", "--tb", "--color",
}  # fmt: skip
# pytest's own list when the ini names none.
DEFAULT_NORECURSE = "*.egg .* _darcs build CVS dist node_modules venv {arch}"


def workflows(root):
    """{file name: (text as written, text with comments, filter blocks and ignores removed)}."""
    out = {}
    found = glob.glob(os.path.join(root, ".github", "workflows", "*.y*ml"))
    found += glob.glob(os.path.join(root, ".github", "actions", "*", "action.y*ml"))
    for path in sorted(found):
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
        kept, block = [], None
        for line in raw.split("\n"):
            indent = len(line) - len(line.lstrip())
            if block is not None and (not line.strip() or indent > block):
                continue
            block = None
            if re.match(r"\s*(paths|paths-ignore|filters)\s*:", line):
                block = indent
                continue
            if not line.strip().startswith("#"):
                kept.append(re.sub(r"\s#\s.*$", "", line))
        text = re.sub(r"--(?:ignore|deselect)(?:=|\s+)\S+", " ", "\n".join(kept))
        name = os.path.basename(path)
        out[name if "workflows" in path else os.path.relpath(path, root)] = (raw, text)
    return out


def clean(path):
    path = path.strip("'\"")
    while path.startswith(("./", "../")):
        path = path.split("/", 1)[1]
    return path.rstrip("/")


def named(f, flows, root):
    for name, (_, text) in flows.items():
        text = re.sub(r"\s-p\s+\S+", " ", text)  # a discover pattern is not a path
        tokens = re.findall(r"[\w./-]+\.py", text)
        for modules in re.findall(r"-m\s+unittest\b(?!\s+discover)([^\n|;&]*)", text):
            tokens += [
                m.replace(".", "/") + ".py"
                for m in modules.split()
                if re.fullmatch(r"[A-Za-z_][\w.]*", m)
            ]
        for token in tokens:
            token = clean(token)
            if f == token or f.endswith("/" + token):
                return "named in " + name
        for folder in pytest_folders(text):
            # Counted only when the folder exists from the repository root. A folder
            # given from a job's own working folder is not seen: list it in TREES.
            if f.startswith(folder + "/") and os.path.isdir(os.path.join(root, folder)):
                return f"in {folder}/, which {name} gives to pytest"
    return None


def pytest_folders(text):
    """Folders a workflow gives to pytest as a target, the way it gives a file."""
    folders = []
    for found in re.finditer(r"\bpytest\b", text):
        rest = re.split(
            r"&&|\|\||[;|]|\n\s*(?:-\s+)?[\w-]+:(?:\s|$)",
            text[found.end() :],
            maxsplit=1,
        )[0]
        words = rest.replace("\\\n", " ").split()
        for at, word in enumerate(words):
            before = words[at - 1] if at else ""
            word = clean(word.split("::")[0])
            if (
                word
                and re.fullmatch(r"[\w./-]+", word)
                and not word.startswith("-")
                and before not in PYTEST_OPTIONS_WITH_VALUE
                and not word.endswith(".py")
                # a file of another kind is not a folder
                and not re.search(r"\.\w+$", word)
            ):
                folders.append(word)
    return folders


def discovered(f, flows, root):
    folder, base = os.path.dirname(f), os.path.basename(f)
    for name, (_, text) in flows.items():
        for args in re.findall(r"unittest\s+discover\b([^\n]*)", text):
            start = re.search(r"\s-s\s+(\S+)", args)
            pattern = re.search(r"\s-p\s+(\S+)", args)
            start = clean(start.group(1)) if start else ""
            pattern = pattern.group(1).strip("'\"") if pattern else "test*.py"
            pattern = re.sub(r"\$\{[^}]*\}|\$\w+", "*", pattern)
            if not fnmatch.fnmatch(base, pattern):
                continue
            parts = folder.split("/")
            for i in range(
                len(parts), 0, -1
            ):  # the start folder, or a package below it
                head = "/".join(parts[:i])
                if not start or head == start or head.endswith("/" + start):
                    below = ["/".join(parts[:j]) for j in range(i + 1, len(parts) + 1)]
                    if all(
                        os.path.exists(os.path.join(root, d, "__init__.py"))
                        for d in below
                    ):
                        return "discovered in " + name
    return None


def collected(f, root, trees):
    for tree in sorted(trees, key=lambda t: -len(t["root"])):
        if not f.startswith(tree["root"] + "/"):
            continue
        ini = configparser.ConfigParser(interpolation=None)
        ini.read(os.path.join(root, tree["ini"]), encoding="utf-8")
        files = ini.get("pytest", "python_files", fallback="test_*.py").split()
        skip = ini.get("pytest", "norecursedirs", fallback=DEFAULT_NORECURSE).split()
        rel = f[len(tree["root"]) + 1 :]
        if not any(fnmatch.fnmatch(os.path.basename(f), p) for p in files):
            return None
        if any(fnmatch.fnmatch(d, p) for d in rel.split("/")[:-1] for p in skip):
            return None
        if any(rel.startswith(x.rstrip("/") + "/") for x in tree["left_out"]):
            return None
        return "collected by " + tree["job"]
    return None


def git(root, *args):
    done = subprocess.run(
        ["git", "-C", root, *args], capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        print(
            "could not decide: git {} failed: {}".format(
                " ".join(args), done.stderr.strip()
            )
        )
        sys.exit(2)
    return [line for line in done.stdout.split("\n") if line]


def is_test(f, names):
    return any(fnmatch.fnmatch(os.path.basename(f), p) for p in names)


def main(argv=None, root=ROOT, trees=None):
    trees = TREES if trees is None else trees
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="origin/dev")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args(argv)

    flows = workflows(root)
    for tree in trees:
        raw = flows.get(tree["workflow"], ("", ""))[0]
        if " ".join(tree["proof"].split()) not in " ".join(raw.split()):
            print(
                "could not decide: {} no longer holds `{}`, so it is not shown that a job "
                "collects {}. Edit TREES in {}.".format(
                    tree["workflow"], tree["proof"], tree["root"], __file__
                )
            )
            return 2

    if args.files:
        files = args.files
    elif args.all:
        files = git(root, "ls-files")
    else:
        files = git(
            root,
            "diff",
            "--name-only",
            "--no-renames",
            "--diff-filter=A",
            args.base + "...HEAD",
        )
    tests = [f for f in files if is_test(f, TEST_NAMES)]
    others = [f for f in files if not f.endswith(".py") and is_test(f, OTHER_TESTS)]

    missing = []
    for f in tests:
        how = (
            named(f, flows, root)
            or discovered(f, flows, root)
            or collected(f, root, trees)
        )
        if not how:
            missing.append(f)
        if not args.all or not how:
            verdict = "run" if how else "NOT RUN"
            print(f"{verdict:<8} {f}" + (f"  ({how})" if how else ""))
    for f in others if not args.all else []:
        print(f"unjudged {f}  (not a Python test; this check does not judge it)")
    print(f"{len(tests)} Python test file(s) checked, {len(missing)} run by no CI job.")
    if missing:
        print(
            "Add each one to the job that should run it, in the same commit, or say in the "
            "pull request why no job runs it."
        )
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
