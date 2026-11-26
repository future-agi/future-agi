#!/usr/bin/env python3
"""Run this repository's "Before a push" list and say which items failed.

    python3 scripts/before_push.py [--list] [--only NAME] [--file PATH] [--timeout SECONDS]

The list is scripts/before-push.list: one item per line, `name | command`. Blank lines and
lines that start with # are skipped. Each command runs in bash from the repository root.
Every item runs even when an earlier one fails, so one run shows everything that is wrong.

Exit 0: every item passed, or the list is empty. 1: at least one item failed; the last line
names them. 2: the list itself is wrong (a line with no `|`, an empty name or command, a
name used twice, an unknown --only).
"""

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "scripts", "before-push.list")
TAIL = 25  # lines of a failed item's output to show


def read_items(path):
    """[(name, command)] from the list file. Raises ValueError on a line it cannot read."""
    items, seen = [], set()
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, bar, command = (part.strip() for part in line.partition("|"))
            if not bar or not name or not command:
                raise ValueError(f"line {number} is not `name | command`: {line}")
            if name in seen:
                raise ValueError(f"line {number} uses the name `{name}` a second time")
            seen.add(name)
            items.append((name, command))
    return items


def run_item(command, root, timeout):
    """(passed, output). A command that runs past the timeout has failed."""
    try:
        done = subprocess.run(
            ["bash", "-c", command],
            cwd=root,
            text=True,
            check=False,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except subprocess.TimeoutExpired as late:
        return False, (late.stdout or "") + f"\nstopped after {timeout} seconds"
    return done.returncode == 0, done.stdout


def main(argv=None, root=ROOT):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", default=LIST)
    ap.add_argument(
        "--list", action="store_true", help="print the items and run nothing"
    )
    ap.add_argument("--only", action="append", default=[], metavar="NAME")
    ap.add_argument(
        "--timeout", type=int, default=900, help="seconds per item (default 900)"
    )
    args = ap.parse_args(argv)

    try:
        items = read_items(args.file)
    except (OSError, ValueError) as problem:
        print(f"The list could not be read: {problem}")
        return 2
    unknown = [name for name in args.only if name not in dict(items)]
    if unknown:
        print("No item is named: {}".format(", ".join(unknown)))
        return 2
    if args.only:
        items = [item for item in items if item[0] in args.only]
    if args.list:
        for name, command in items:
            print(f"{name} | {command}")
        return 0
    if not items:
        print("Before a push: the list is empty, nothing to run.")
        return 0

    failed = []
    for name, command in items:
        started = time.time()
        passed, output = run_item(command, root, args.timeout)
        verdict = "ok" if passed else "FAILED"
        print(f"{verdict:<6} {name}  ({time.time() - started:.1f}s)", flush=True)
        if not passed:
            failed.append(name)
            print(f"       $ {command}")
            for line in output.rstrip("\n").split("\n")[-TAIL:]:
                print(f"       | {line}")
    if failed:
        names = ", ".join(failed)
        print(f"Before a push: {len(failed)} of {len(items)} failed: {names}")
        return 1
    print(f"Before a push: all {len(items)} passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
