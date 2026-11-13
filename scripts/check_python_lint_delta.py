#!/usr/bin/env python3
"""Report the Python lint violations a change adds, compared with its merge base.

The coding standards ban some patterns from new code (internal-docs
coding-standards/12-banned-patterns.md). Old instances stay until a dedicated
cleanup PR, so this check fails only on what the change adds. A violation is new
when its rule and its source line occur more often at the head than at the base.
For a file at an unchanged path whose head bytes exactly equal pinned Ruff's
formatting of the original base bytes, compare per-rule counts instead. Always
lint the unformatted base: formatting can move a suppression and expose a
previously hidden violation.

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
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

BACKEND_DIR = "futureagi"
RUFF_VERSION = "0.17.3"
SYNTAX_ERROR = "invalid-syntax"  # the code ruff 0.17 gives a syntax error; older versions gave none

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

# Reported as warnings, except for count increases in proven formatting-only files.
# S608 also fires when only a table or column
# name from code is interpolated: over 2026-08-08..2026-11-07 most of its 248 new
# hits were ClickHouse query builders joining SQL fragments, not values. Failing on
# it would teach people to add a noqa without reading, so a reviewer checks each one.
WARN_ONLY = frozenset({"S608"})

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
    "S608": "Check that every interpolated part is a table or column name, or a fragment, built from "
    "constants or an allowlist in code. Pass every value as a bound parameter: %(name)s with a params "
    "dict through the ClickHouse client, cursor.execute(sql, params), or the ORM. This is a warning; "
    "a reviewer checks it.",
    "S113": "Give the call a finite timeout, on the call or on the client it uses.",
    "T201": "Log with structlog.get_logger(__name__). Management commands use self.stdout.write.",
    "G004": "Use a stable event name and keyword fields on a structlog logger: "
    'logger.warning("replica_lag_high", lag=lag). On a standard logging logger, pass %s arguments '
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
    SYNTAX_ERROR: "The file does not parse.",
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
    formatting_regression: bool = False

    @property
    def is_warning(self) -> bool:
        # Even a normally advisory rule must not increase in a formatting-only file.
        return self.code in WARN_ONLY and not self.formatting_regression


class GateError(Exception):
    """The check could not run, as opposed to finding violations."""


def run(
    cmd: list[str], cwd: Path, ok_codes: tuple[int, ...] = (0,)
) -> subprocess.CompletedProcess:
    """Run a command and raise GateError on an unexpected exit code."""
    proc = subprocess.run(cmd, cwd=cwd, check=False, capture_output=True, text=True)
    if proc.returncode not in ok_codes:
        raise GateError(
            f"{' '.join(cmd)} exited {proc.returncode}: {proc.stderr.strip()[:2000]}"
        )
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
        [
            "git",
            "-c",
            "core.quotepath=off",
            "diff",
            "--name-status",
            "-M",
            "-z",
            f"{base}...HEAD",
            "--",
            BACKEND_DIR,
        ],
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


def line_ranks(diff: str) -> dict[int, int]:
    """Rank the head lines of a `git diff -U0` for one file: 0 new code, 1 touched.

    A line is new code unless its hunk removed a line with the same code (ignoring
    comments and whitespace): adding a comment to an old line only touches it.
    Lines the diff does not show rank 2, which callers apply as the default.
    """
    ranks: dict[int, int] = {}
    removed: Counter = Counter()
    added: list[tuple[int, str]] = []
    line_no = 0

    def flush() -> None:
        for number, code in added:
            if removed[code] > 0:
                removed[code] -= 1
                ranks[number] = 1
            else:
                ranks[number] = 0
        removed.clear()
        added.clear()

    for raw in diff.splitlines():
        header = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if header:
            flush()
            line_no = int(header.group(1))
        elif raw.startswith("-") and not raw.startswith("---"):
            removed[normalize(raw[1:])] += 1
        elif raw.startswith("+") and not raw.startswith("+++"):
            added.append((line_no, normalize(raw[1:])))
            line_no += 1
    flush()
    return ranks


def changed_line_ranks(repo: Path, base: str, path: str) -> dict[int, int]:
    """line_ranks for one file's change since the base."""
    return line_ranks(
        run(["git", "diff", "-U0", f"{base}...HEAD", "--", path], repo).stdout
    )


def normalize(line: str) -> str:
    """A line's code without its trailing comment and with whitespace collapsed.

    Adding or editing a comment on an old line must not make its finding new.
    A `#` inside a string literal on the same line is kept.
    """
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if line.startswith(quote, i):
                i += len(quote)
                quote = ""
                continue
        elif ch == "#":
            line = line[:i]
            break
        elif ch in "'\"":
            quote = ch * 3 if line.startswith(ch * 3, i) else ch
            i += len(quote)
            continue
        i += 1
    return " ".join(line.split())


def source_line(tree: Path, path: str, line: int) -> str:
    """The normalized text of one line, or an empty string if it is out of range."""
    try:
        text = (tree / path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return normalize(text[line - 1]) if 0 < line <= len(text) else ""


def ruff_check(
    ruff: str, tree: Path, paths: list[str], select: tuple[str, ...] | None
) -> list[Violation]:
    """Run `ruff check` on repo-relative paths inside one tree and parse its findings."""
    rel = [str(PurePosixPath(p).relative_to(BACKEND_DIR)) for p in paths]
    if not rel:
        return []
    # --force-exclude keeps the config's exclude list (migrations) for paths passed by name.
    cmd = [
        ruff,
        "check",
        "--no-cache",
        "--no-fix",
        "--force-exclude",
        "--exit-zero",
        "--output-format",
        "json",
    ]
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
                code=f.get("code") or SYNTAX_ERROR,
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
    cmd = [
        ruff,
        "format",
        "--check",
        "--no-cache",
        "--force-exclude",
        "--output-format",
        "json",
    ]
    proc = run(cmd + ["--", *rel], tree / BACKEND_DIR, ok_codes=(0, 1))
    root = (tree / BACKEND_DIR).resolve()
    return {
        f"{BACKEND_DIR}/{Path(f['filename']).resolve().relative_to(root).as_posix()}"
        for f in json.loads(proc.stdout or "[]")
    }


def ruff_discovered(ruff: str, tree: Path) -> set[str]:
    """Repo-relative paths of the backend files Ruff's configuration includes in one tree.

    Ruff's own file discovery applies the configured exclusions, as --force-exclude
    does for a path passed by name, without reading any file. It still lists an
    included file it cannot read, such as a broken symlink. Ignore files are not
    applied: the lint and format passes name their paths, and Ruff does not
    filter named paths by .gitignore.
    """
    proc = run(
        [
            ruff,
            "check",
            "--no-cache",
            "--force-exclude",
            "--no-respect-gitignore",
            "--show-files",
            "--",
            ".",
        ],
        tree / BACKEND_DIR,
    )
    # Absolute paths under the resolved directory. Not resolved per file: that
    # would follow a symlinked file to its target.
    root = f"{(tree / BACKEND_DIR).resolve().as_posix()}/"
    return {
        f"{BACKEND_DIR}/{line[len(root) :]}"
        for line in proc.stdout.splitlines()
        if line.startswith(root)
    }


def formatting_only_files(
    ruff: str,
    head_tree: Path,
    base_tree: Path,
    files: dict[str, str | None],
    base_broken: set[str],
) -> set[str]:
    """Prove exact formatter output, using raw base bytes and base-side config.

    Only a file at an unchanged path qualifies: a moved file can fall under other
    lint settings (per-file ignores, excludes, the test and migration rules), so
    identical bytes do not make its findings comparable by count. A file that
    Ruff's configuration excludes in either tree is skipped before anything is
    read; an included file that cannot be read fails closed.

    Stdin keeps the baseline worktree untouched. Byte I/O deliberately preserves
    line endings: text-mode newline normalization would weaken the equality test.
    New and unparseable base files cannot qualify; formatter failures fail closed.
    """
    candidates = sorted(
        path
        for path, base_path in files.items()
        if base_path == path and path not in base_broken
    )
    if not candidates:
        return set()
    included = ruff_discovered(ruff, base_tree) & ruff_discovered(ruff, head_tree)
    result = set()
    for path in candidates:
        if path not in included:
            continue
        try:
            base_bytes = (base_tree / path).read_bytes()
            head_bytes = (head_tree / path).read_bytes()
        except OSError as exc:
            raise GateError(
                f"cannot read {path}, which the Ruff configuration includes: {exc}"
            ) from exc
        rel = str(PurePosixPath(path).relative_to(BACKEND_DIR))
        proc = subprocess.run(
            [
                ruff,
                "format",
                "--no-cache",
                "--force-exclude",
                "--stdin-filename",
                rel,
                "-",
            ],
            cwd=base_tree / BACKEND_DIR,
            check=False,
            input=base_bytes,
            capture_output=True,
        )
        if proc.returncode != 0:
            error = proc.stderr.decode("utf-8", errors="replace").strip()[:2000]
            raise GateError(f"ruff format {path} exited {proc.returncode}: {error}")
        if proc.stdout == head_bytes:
            result.add(path)
    return result


def new_violations(
    head: list[Violation],
    base: list[Violation],
    renames: dict[str, str | None],
    ranks: dict[str, dict[int, int]],
    formatting_only: set[str] | frozenset[str] = frozenset(),
) -> list[Violation]:
    """Head findings with no matching finding at the base.

    Findings match on (head path, rule, normalized source line), counted as a
    multiset, so a moved line does not count as new. When a key has more findings
    at the head than at the base, the extra ones are taken from new code first,
    then from touched lines (see line_ranks), then from the rest. Only files proven
    to be exact formatter output may omit source text from the key. Their baseline
    findings still come from the original bytes, not the formatted baseline.
    """
    to_head = {old: new for new, old in renames.items() if old}

    def key(path: str, violation: Violation) -> tuple[str, str, str]:
        return path, violation.code, "" if path in formatting_only else violation.source

    base_count = Counter(key(to_head.get(v.path, v.path), v) for v in base)
    groups: dict[tuple[str, str, str], list[Violation]] = {}
    for v in head:
        groups.setdefault(key(v.path, v), []).append(v)
    result = []
    for group, items in groups.items():
        excess = len(items) - base_count.get(group, 0)
        if excess <= 0:
            continue
        file_ranks = ranks.get(group[0], {})
        items = sorted(items, key=lambda v: (file_ranks.get(v.line, 2), v.line))
        result.extend(
            replace(v, formatting_regression=True) if v.path in formatting_only else v
            for v in items[:excess]
        )
    return sorted(result, key=lambda v: (v.path, v.line, v.code))


def dedupe(violations: list[Violation]) -> list[Violation]:
    """Drop a finding that both lint passes reported for the same place and rule."""
    seen: set[tuple[str, str, int, int, str]] = set()
    result = []
    for v in violations:
        key = (v.path, v.code, v.line, v.column, v.message)
        if key not in seen:
            seen.add(key)
            result.append(v)
    return result


def escape_data(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(s: str) -> str:
    return escape_data(s).replace(":", "%3A").replace(",", "%2C")


def annotation(v: Violation) -> str:
    """A GitHub Actions annotation that says what to write instead: a warning for WARN_ONLY rules."""
    hint = HINTS.get(v.code, "")
    if v.formatting_regression:
        hint = "Formatting-only changes must not increase any per-file/rule count; check moved suppressions."
    text = f"{v.code}: {v.message}" + (f" {hint}" if hint else "")
    level = "warning" if v.is_warning else "error"
    title = escape_property(f"ruff {v.code} (new in this change)")
    return f"::{level} file={escape_property(v.path)},line={v.line},col={v.column},title={title}::{escape_data(text)}"


def check(repo: Path, base: str, ruff: str) -> list[Violation]:
    """Run all three passes and return the violations the change adds."""
    files = changed_files(repo, base)
    if not files:
        return []
    head_paths = sorted(files)
    base_paths = sorted(p for p in files.values() if p)
    app_head = [p for p in head_paths if not is_test_or_migration(p)]
    app_base = [p for p in base_paths if not is_test_or_migration(p)]
    ranks = {p: changed_line_ranks(repo, base, p) for p in head_paths}

    with tempfile.TemporaryDirectory(prefix="lint-delta-base-") as tmp:
        base_tree = Path(tmp) / "base"
        run(
            ["git", "worktree", "add", "--detach", "--quiet", str(base_tree), base],
            repo,
        )
        try:
            head = dedupe(
                ruff_check(ruff, repo, head_paths, None)
                + ruff_check(ruff, repo, app_head, BANNED_RULES)
            )
            old = dedupe(
                ruff_check(ruff, base_tree, base_paths, None)
                + ruff_check(ruff, base_tree, app_base, BANNED_RULES)
            )
            # The formatter cannot parse a file with a syntax error (ruff exits 2). Leave such
            # files out of the format pass: the lint pass already reports a new syntax error,
            # and a file that did not parse at the base counts as unformatted there.
            head_broken = {v.path for v in head if v.code == SYNTAX_ERROR}
            base_broken = {v.path for v in old if v.code == SYNTAX_ERROR}
            formatting_only = formatting_only_files(
                ruff, repo, base_tree, files, base_broken
            )
            head_dirty = ruff_unformatted(
                ruff, repo, [p for p in head_paths if p not in head_broken]
            )
            base_dirty = base_broken | ruff_unformatted(
                ruff, base_tree, [p for p in base_paths if p not in base_broken]
            )
        finally:
            run(
                ["git", "worktree", "remove", "--force", str(base_tree)],
                repo,
                ok_codes=(0, 128),
            )

    found = new_violations(head, old, files, ranks, formatting_only)
    for path in sorted(head_dirty):
        base_path = files.get(path)
        if base_path is None or base_path not in base_dirty:
            found.append(Violation(path, "FORMAT", 1, 1, "File is not formatted.", ""))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", required=True, help="the merge-base commit to compare with"
    )
    parser.add_argument("--ruff", default="ruff", help="the ruff executable")
    parser.add_argument(
        "--expect-ruff-version",
        default=RUFF_VERSION,
        help="fail unless `ruff --version` reports this version",
    )
    args = parser.parse_args(argv)
    try:
        repo = Path(
            run(["git", "rev-parse", "--show-toplevel"], Path.cwd()).stdout.strip()
        )
        version = run([args.ruff, "--version"], repo).stdout.strip()
        if (
            version != f"ruff {RUFF_VERSION}"
            or args.expect_ruff_version != RUFF_VERSION
        ):
            raise GateError(
                f"formatting-aware comparison requires ruff {RUFF_VERSION}; "
                f"found {version}, requested {args.expect_ruff_version!r}"
            )
        found = check(repo, args.base, args.ruff)
    except (GateError, OSError, json.JSONDecodeError) as exc:
        print(f"::error title=Python lint gate::{escape_data(str(exc))}")
        return 2

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    errors = [v for v in found if not v.is_warning]
    warnings = [v for v in found if v.is_warning]
    lines = [f"Compared with {args.base[:12]} using {version}."]
    if errors:
        lines.append(
            f"**{len(errors)} new violation(s).** Each annotation says what to write instead."
        )
        lines += [f"- `{v.path}:{v.line}` {v.code} {v.message}" for v in errors]
    else:
        lines.append("No new violations.")
    if warnings:
        lines.append(
            f"{len(warnings)} warning(s) for a reviewer to check (these do not fail the check):"
        )
        lines += [f"- `{v.path}:{v.line}` {v.code} {v.message}" for v in warnings]
    for v in found:
        print(annotation(v))
    print("\n".join(lines))
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(
                "## Python lint (new violations only)\n\n" + "\n".join(lines) + "\n"
            )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
