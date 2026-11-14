#!/usr/bin/env python3
"""Delete package test suites from site-packages, except runtime `tests` packages.

Most `tests` directories in site-packages are test suites nothing imports
(pandas 35 MB, scipy 31 MB, numpy 15 MB, scikit-learn 14 MB, ...). A few
packages use `tests` as the name of a real subpackage: elevenlabs'
`conversational_ai.tests` API client is imported by its parent package, for
example. A `tests` directory is therefore kept when any other module of the
same distribution refers to it:

  * `from .tests import ...` / `from ..tests import ...` resolving to it,
  * `import a.b.tests` / `from a.b.tests import ...`,
  * the dotted name as a string ("a.b.tests"), or ".tests" as a string in its
    parent package (lazy-import tables).

Files inside test directories, conftest.py and `testing`/`_testing` helper
modules do not count as references. The scan is textual, so it errs on the
side of keeping a directory. Only directories named exactly `tests` are
considered: `test` is left alone because `django/test` is a runtime package.

Usage: prune_test_dirs.py SITE_PACKAGES [--dry-run]
"""

from __future__ import annotations

import os
import re
import shutil
import sys

_FROM_RELATIVE = re.compile(r"^\s*from\s+(\.+)(tests)(?:\.|\s)", re.MULTILINE)
_ABSOLUTE = re.compile(r"(?:^|[\s(\"'])([A-Za-z_][\w.]*\.tests)\b", re.MULTILINE)
_LAZY_RELATIVE = re.compile(r"[\"']\.tests[\"']")
_HELPER_NAMES = {"conftest.py", "testing.py", "_testing.py"}


def _is_test_path(rel_parts: tuple[str, ...]) -> bool:
    return any(part in {"tests", "test", "testing", "_testing"} for part in rel_parts)


def _module_package(sp: str, path: str) -> str:
    rel = os.path.relpath(path, sp)
    parts = rel[: -len(".py")].split(os.sep)
    if parts[-1] == "__init__":
        return ".".join(parts[:-1])
    return ".".join(parts[:-1])


def referenced_tests_packages(sp: str, top: str) -> set[str]:
    """Dotted names of `*.tests` packages referenced by non-test code of top."""
    refs: set[str] = set()
    root = os.path.join(sp, top)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_parts = tuple(os.path.relpath(dirpath, sp).split(os.sep))
        dirnames[:] = [d for d in dirnames if not _is_test_path((d,))]
        if _is_test_path(rel_parts):
            continue
        for name in filenames:
            if not name.endswith(".py") or name in _HELPER_NAMES:
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            if ".tests" not in text and "tests" not in text:
                continue
            package = _module_package(sp, path)
            for dots, _ in _FROM_RELATIVE.findall(text):
                base = package.split(".")
                up = len(dots) - 1
                if up > len(base):
                    continue
                target = base[: len(base) - up] if up else base
                refs.add(".".join([*target, "tests"]))
            for dotted in _ABSOLUTE.findall(text):
                refs.add(dotted)
            if _LAZY_RELATIVE.search(text):
                refs.add(f"{package}.tests")
    return refs


def main(sp: str, dry_run: bool = False) -> None:
    removed = kept = 0
    freed = 0
    cache: dict[str, set[str]] = {}
    for dirpath, dirnames, _ in os.walk(sp):
        if "tests" not in dirnames:
            continue
        dirnames.remove("tests")  # never descend into a suite
        path = os.path.join(dirpath, "tests")
        rel = os.path.relpath(path, sp)
        parts = rel.split(os.sep)
        if len(parts) > 1:
            top = parts[0]
            if top not in cache:
                cache[top] = referenced_tests_packages(sp, top)
            if ".".join(parts) in cache[top]:
                kept += 1
                print(f"prune_test_dirs: keeping {rel} (imported by its package)")
                continue
        size = sum(
            os.path.getsize(os.path.join(d, f))
            for d, _, files in os.walk(path)
            for f in files
            if not os.path.islink(os.path.join(d, f))
        )
        freed += size
        removed += 1
        if not dry_run:
            shutil.rmtree(path)
    verb = "would remove" if dry_run else "removed"
    print(
        f"prune_test_dirs: {verb} {removed} test directories "
        f"({freed / 1e6:.1f} MB), kept {kept}"
    )


if __name__ == "__main__":
    main(sys.argv[1], dry_run="--dry-run" in sys.argv[2:])
