#!/usr/bin/env python3
"""Report the Python lint violations a change adds, compared with its merge base.

The coding standards ban some patterns from new code (internal-docs
coding-standards/12-banned-patterns.md). Old instances stay until a dedicated
cleanup PR, so this check fails only on what the change adds. A violation is new
when its rule and its source line occur more often at the head than at the base.

Three passes run on the changed files under futureagi/:

- the repo's own ruff configuration (futureagi/pyproject.toml);
- the banned-pattern rules that configuration does not select yet, on app code
  only (tests and migrations are left out, as in the standard);
- `ruff format --check`, which fails only for a file that was formatted at the
  base, or is new, and is not formatted at the head.

Usage, from the repo root:

    python3 -I scripts/check_python_lint_delta.py --base <commit>

Exit codes: 0 nothing new, 1 new violations (printed as GitHub annotations),
2 the check itself could not run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

BACKEND_DIR = "futureagi"

# Banned-pattern rules that futureagi/pyproject.toml does not select yet.
# INP001 waits for the dedicated __init__.py PR, and ANN401 flags more than the
# ban (which covers service, selector and cross-app signatures only).
BANNED_RULES = (
    "BLE001",
    "S110",
    "S112",
    "S608",
    "S113",
    "T201",
    "G004",
    "DTZ005",
    "PLW0603",
    "PGH003",
    "PGH004",
)

# What to write instead, from the "Write this instead" column of standard 12.
HINTS = {
    "BLE001": "Catch the narrow exception. Catch Exception only at a boundary (the top of a request, "
    "task or activity, or a call into an external system); there, log it with logger.exception, "
    "then re-raise, fail closed, or continue for a documented best-effort side effect.",
    "S110": "A broad except whose body is only pass erases the failure. Handle it, or log it and then "
    "re-raise or continue on purpose. For one expected error, catch it narrowly or use contextlib.suppress.",
    "S112": "A broad except whose body is only continue erases the failure. Handle it, or log it and then "
    "continue on purpose. For one expected error, catch it narrowly.",
    "E722": "Do not use a bare except. Catch the narrow exception, or Exception at a boundary with "
    "logger.exception.",
    "B904": "Use raise NewError(...) from exc. Use from None only when hiding the cause is deliberate.",
    "S608": "Pass every value as a bound parameter: %(name)s with a params dict through the ClickHouse "
    "client, cursor.execute(sql, params), or the ORM. If only a table or column name from a constant "
    "or an allowlist is interpolated, keep it and add '# noqa: S608 - <reason>'.",
    "S113": "Give the call a finite timeout, on the call or on the client it uses.",
    "T201": "Log with structlog.get_logger(__name__). Management commands use self.stdout.write.",
    "G004": "Use a stable event name and keyword fields on a structlog logger: "
    "logger.warning(\"replica_lag_high\", lag=lag). On a standard logging logger, pass %s arguments "
    "instead; keyword fields raise TypeError there.",
    "DTZ005": "Use django.utils.timezone.now(). Inside a Temporal workflow, use Temporal's clock.",
    "PLW0603": "Use a cached factory function for lazy clients, or contextvars.ContextVar for "
    "request- or task-scoped state.",
    "PGH003": "Name the code and give a reason: '# type: ignore[code]  # reason'.",
    "PGH004": "Name the rule and give a reason: '# noqa: CODE - reason'.",
    "F403": "Import names explicitly. Only a settings module that extends another may use a star import.",
    "F405": "Import names explicitly. A name from a star import is a probable NameError.",
    "F821": "Undefined name: a probable runtime NameError.",
    "FORMAT": "Run `ruff format` on the file (the husky pre-commit hook does this on commit).",
}


@dataclass(frozen=True)
class Violation:
    """One lint finding at the head of the change."""

    path: str  # repo-relative, posix
    code: str
    line: int
    column: int
    message: str
    source: str  # the stripped source line, used to match the same finding at the base


class GateError(Exception):
    """The check could not run, as opposed to finding violations."""


def run(cmd: list[str], cwd: Path, ok_codes: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess:
    """Run a command and raise GateError on an unexpected exit code."""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode not in ok_codes:
        raise GateError(f"{' '.join(cmd)} exited {proc.returncode}: {proc.stderr.strip()[:2000]}")
    return proc


def is_test_or_migration(path: str) -> bool:
    """True for test modules and migrations, which the banned-pattern pass leaves out."""
    p = PurePosixPath(path)
    parts = set(p.parts)
    name = p.name
    return (
        "migrations" in parts
        or "tests" in parts
        or "test" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def changed_files(repo: Path, base: str) -> dict[str, str | None]:
    """Map each changed .py file under futureagi/ at the head to its path at the base.

    The base path is None for an added file. Deleted files are left out.
    """
    out = run(
        ["git", "-c", "core.quotepath=off", "diff", "--name-status", "-M", "-z", f"{base}...HEAD", "--", BACKEND_DIR],
        repo,
    ).stdout
    fields = out.split("\0")
    result: dict[str, str | None] = {}
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status.startswith(("R", "C")):
            old, new = fields[i + 1], fields[i + 2]
            i += 3
            base_path: str | None = old if status.startswith("R") else None
        else:
            old = new = fields[i + 1]
            i += 2
            base_path = None if status.startswith("A") else old
        if status.startswith("D") or not new.endswith(".py"):
            continue
        result[new] = base_path
    return result


def added_lines(repo: Path, base: str, path: str) -> set[int]:
    """Line numbers at the head that the change added or modified in one file."""
    out = run(["git", "diff", "-U0", f"{base}...HEAD", "--", path], repo).stdout
    lines: set[int] = set()
    for m in re.finditer(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", out, re.MULTILINE):
        start, count = int(m.group(1)), int(m.group(2) or "1")
        lines.update(range(start, start + count))
    return lines


def source_line(tree: Path, path: str, line: int) -> str:
    """The stripped text of one line, or an empty string if it is out of range."""
    try:
        text = (tree / path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return text[line - 1].strip() if 0 < line <= len(text) else ""


def ruff_check(ruff: str, tree: Path, paths: list[str], select: tuple[str, ...] | None) -> list[Violation]:
    """Run `ruff check` on repo-relative paths inside one tree and parse its findings."""
    rel = [str(PurePosixPath(p).relative_to(BACKEND_DIR)) for p in paths]
    if not rel:
        return []
    # --force-exclude keeps the config's exclude list (migrations) for paths passed by name.
    cmd = [ruff, "check", "--no-cache", "--no-fix", "--force-exclude", "--exit-zero", "--output-format", "json"]
    if select:
        cmd += ["--select", ",".join(select)]
    proc = run(cmd + ["--", *rel], tree / BACKEND_DIR)
    findings = json.loads(proc.stdout or "[]")
    root = (tree / BACKEND_DIR).resolve()
    violations = []
    for f in findings:
        rel_path = Path(f["filename"]).resolve().relative_to(root).as_posix()
        path = f"{BACKEND_DIR}/{rel_path}"
        line = f["location"]["row"]
        violations.append(
            Violation(
                path=path,
                code=f.get("code") or "syntax-error",
                line=line,
                column=f["location"]["column"],
                message=f["message"],
                source=source_line(tree, path, line),
            )
        )
    return violations


def ruff_unformatted(ruff: str, tree: Path, paths: list[str]) -> set[str]:
    """Repo-relative paths that `ruff format --check` would reformat inside one tree."""
    rel = [str(PurePosixPath(p).relative_to(BACKEND_DIR)) for p in paths]
    if not rel:
        return set()
    # JSON output: ruff 0.17 prints display diagnostics, not "Would reformat:" lines.
    cmd = [ruff, "format", "--check", "--no-cache", "--force-exclude", "--output-format", "json"]
    proc = run(cmd + ["--", *rel], tree / BACKEND_DIR, ok_codes=(0, 1))
    root = (tree / BACKEND_DIR).resolve()
    return {
        f"{BACKEND_DIR}/{Path(f['filename']).resolve().relative_to(root).as_posix()}"
        for f in json.loads(proc.stdout or "[]")
    }


def new_violations(
    head: list[Violation],
    base: list[Violation],
    renames: dict[str, str | None],
    changed: dict[str, set[int]],
) -> list[Violation]:
    """Head findings with no matching finding at the base.

    Findings match on (head path, rule, source line text), counted as a multiset,
    so a moved line does not count as new. When a key has more findings at the
    head than at the base, the extra ones are taken from changed lines first.
    """
    to_head = {old: new for new, old in renames.items() if old}
    base_count = Counter((to_head.get(v.path, v.path), v.code, v.source) for v in base)
    groups: dict[tuple[str, str, str], list[Violation]] = {}
    for v in head:
        groups.setdefault((v.path, v.code, v.source), []).append(v)
    result = []
    for key, items in groups.items():
        excess = len(items) - base_count.get(key, 0)
        if excess <= 0:
            continue
        lines = changed.get(key[0], set())
        items = sorted(items, key=lambda v: (v.line not in lines, v.line))
        result.extend(items[:excess])
    return sorted(result, key=lambda v: (v.path, v.line, v.code))


def escape_data(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(s: str) -> str:
    return escape_data(s).replace(":", "%3A").replace(",", "%2C")


def annotation(v: Violation) -> str:
    """A GitHub Actions error annotation that says what to write instead."""
    hint = HINTS.get(v.code, "")
    text = f"{v.code}: {v.message}" + (f" {hint}" if hint else "")
    title = escape_property(f"ruff {v.code} (new in this change)")
    return f"::error file={escape_property(v.path)},line={v.line},col={v.column},title={title}::{escape_data(text)}"


def check(repo: Path, base: str, ruff: str) -> list[Violation]:
    """Run all three passes and return the violations the change adds."""
    files = changed_files(repo, base)
    if not files:
        return []
    head_paths = sorted(files)
    base_paths = sorted(p for p in files.values() if p)
    app_head = [p for p in head_paths if not is_test_or_migration(p)]
    app_base = [p for p in base_paths if not is_test_or_migration(p)]
    changed = {p: added_lines(repo, base, p) for p in head_paths}

    with tempfile.TemporaryDirectory(prefix="lint-delta-base-") as tmp:
        base_tree = Path(tmp) / "base"
        run(["git", "worktree", "add", "--detach", "--quiet", str(base_tree), base], repo)
        try:
            head = ruff_check(ruff, repo, head_paths, None) + ruff_check(ruff, repo, app_head, BANNED_RULES)
            old = ruff_check(ruff, base_tree, base_paths, None) + ruff_check(ruff, base_tree, app_base, BANNED_RULES)
            head_dirty = ruff_unformatted(ruff, repo, head_paths)
            base_dirty = ruff_unformatted(ruff, base_tree, base_paths)
        finally:
            run(["git", "worktree", "remove", "--force", str(base_tree)], repo, ok_codes=(0, 128))

    found = new_violations(head, old, files, changed)
    for path in sorted(head_dirty):
        base_path = files.get(path)
        if base_path is None or base_path not in base_dirty:
            found.append(Violation(path, "FORMAT", 1, 1, "File is not formatted.", ""))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="the merge-base commit to compare with")
    parser.add_argument("--ruff", default="ruff", help="the ruff executable")
    parser.add_argument("--expect-ruff-version", help="fail unless `ruff --version` reports this version")
    args = parser.parse_args(argv)
    try:
        repo = Path(run(["git", "rev-parse", "--show-toplevel"], Path.cwd()).stdout.strip())
        version = run([args.ruff, "--version"], repo).stdout.strip()
        if args.expect_ruff_version and version.split()[-1] != args.expect_ruff_version:
            raise GateError(f"expected ruff {args.expect_ruff_version}, found {version}")
        found = check(repo, args.base, args.ruff)
    except (GateError, OSError, json.JSONDecodeError) as exc:
        print(f"::error title=Python lint gate::{escape_data(str(exc))}")
        return 2

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    lines = [f"Compared with {args.base[:12]} using {version}."]
    if found:
        lines.append(f"**{len(found)} new violation(s).** Each annotation says what to write instead.")
        lines += [f"- `{v.path}:{v.line}` {v.code} {v.message}" for v in found]
    else:
        lines.append("No new violations.")
    for v in found:
        print(annotation(v))
    print("\n".join(lines))
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Python lint (new violations only)\n\n" + "\n".join(lines) + "\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
