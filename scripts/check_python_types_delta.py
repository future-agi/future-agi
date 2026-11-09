#!/usr/bin/env python3
"""Report mypy errors added to changed backend files since a PR's merge base.

Findings match by (file, error code, normalized source line), counted as a
multiset. Old errors and line-number shifts do not fail the advisory job.
Exit codes: 0 no new errors, 1 new errors, 2 the comparison could not run.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tokenize
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

BACKEND = "futureagi"


class GateError(Exception):
    """The comparison is incomplete, not a clean type-check result."""


@dataclass(frozen=True)
class Finding:
    path: str
    code: str
    line: int
    column: int
    message: str
    source: str


@dataclass(frozen=True)
class CheckResult:
    findings: list[Finding]
    seconds: float
    files: int


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=60
    )
    if result.returncode:
        raise GateError(
            f"{command[0]} exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def changed_files(repo: Path, base: str) -> dict[str, str | None]:
    fields = run(
        ["git", "diff", "--name-status", "-M", "-z", base, "HEAD", "--", BACKEND], repo
    ).split("\0")
    paths: dict[str, str | None] = {}
    i = 0
    while i < len(fields) and fields[i]:
        status, old = fields[i : i + 2]
        i += 2
        new = old
        if status.startswith("R"):
            new = fields[i]
            i += 1
        if status != "D" and new.startswith(f"{BACKEND}/") and new.endswith(".py"):
            paths[new] = None if status == "A" else old
    return paths


def normalized_lines(path: Path) -> list[str]:
    with tokenize.open(path) as source:
        text = source.read()
    lines = text.splitlines()
    # Tokenize the whole file so a # inside a multiline string is not a comment.
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                row, column = token.start
                lines[row - 1] = lines[row - 1][:column]
    except (tokenize.TokenError, IndentationError):
        # Mypy reports malformed syntax; keep the source available for that error.
        return [line.strip() for line in lines]
    return [" ".join(line.split()) for line in lines]


def parse_findings(output: str, tree: Path, paths: list[str]) -> list[Finding]:
    sources = {path: normalized_lines(tree / path) for path in paths}
    findings = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        diagnostic = json.loads(raw)
        if diagnostic["severity"] != "error":
            continue
        filename = (tree / BACKEND / diagnostic["file"]).resolve()
        if not filename.is_relative_to(tree.resolve()):
            continue
        path = filename.relative_to(tree.resolve()).as_posix()
        if path not in sources:
            continue
        line = diagnostic["line"]
        code = diagnostic["code"]
        if not code or not 0 < line <= len(sources[path]):
            raise GateError(f"Unlocated mypy error: {raw}")
        findings.append(
            Finding(
                path,
                code,
                line,
                diagnostic["column"] + 1,
                diagnostic["message"],
                sources[path][line - 1],
            )
        )
    return findings


def mypy_check(tree: Path, paths: list[str], label: str) -> CheckResult:
    if not paths:
        print(f"mypy {label}: no existing changed Python files; not run.")
        return CheckResult([], 0.0, 0)
    command = [
        sys.executable,
        "-m",
        "mypy",
        "--config-file=pyproject.toml",
        "--output=json",
        "--no-incremental",
        f"--cache-dir={os.devnull}",
        "--",
        *(str(Path(path).relative_to(BACKEND)) for path in paths),
    ]
    started = time.monotonic()
    result = subprocess.run(
        command, cwd=tree / BACKEND, capture_output=True, text=True, timeout=600
    )
    seconds = time.monotonic() - started
    print(
        f"mypy {label}: {len(paths)} file(s), {seconds:.2f}s, exit {result.returncode}."
    )
    if result.returncode not in (0, 1):
        raise GateError(
            f"mypy {label} did not complete (exit {result.returncode}): "
            f"{(result.stdout + result.stderr)[-6000:]}"
        )
    if result.stderr:
        raise GateError(
            f"mypy {label} wrote unexpected stderr: {result.stderr[-6000:]}"
        )
    if result.returncode == 1 and not result.stdout.strip():
        raise GateError(f"mypy {label} failed without diagnostics")
    findings = parse_findings(result.stdout, tree, paths)
    return CheckResult(findings, seconds, len(paths))


def added_lines(repo: Path, base: str, path: str) -> set[int]:
    diff = run(["git", "diff", "-U0", base, "HEAD", "--", path], repo)
    lines: set[int] = set()
    for start, count in re.findall(
        r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", diff, re.M
    ):
        lines.update(range(int(start), int(start) + int(count or 1)))
    return lines


def new_findings(
    head: list[Finding],
    base: list[Finding],
    paths: dict[str, str | None],
    added: dict[str, set[int]],
) -> list[Finding]:
    to_head = {old: new for new, old in paths.items() if old}
    counts = Counter((to_head.get(f.path, f.path), f.code, f.source) for f in base)
    groups: dict[tuple[str, str, str], list[Finding]] = {}
    for finding in head:
        groups.setdefault((finding.path, finding.code, finding.source), []).append(
            finding
        )
    found = []
    for key, items in groups.items():
        excess = max(0, len(items) - counts[key])
        items.sort(key=lambda f: (f.line not in added.get(f.path, set()), f.line))
        found.extend(items[:excess])
    return sorted(found, key=lambda f: (f.path, f.line, f.code))


def escape(text: str, *, property_value: bool = False) -> str:
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if property_value else text


def check(repo: Path, base: str) -> tuple[list[Finding], CheckResult, CheckResult]:
    paths = changed_files(repo, base)
    head = mypy_check(repo, sorted(paths), "head")
    base_paths = sorted({old for old in paths.values() if old})
    old = CheckResult([], 0.0, 0)
    if base_paths:
        with tempfile.TemporaryDirectory(prefix="mypy-delta-") as directory:
            tree = Path(directory) / "base"
            run(
                ["git", "worktree", "add", "--detach", "--quiet", str(tree), base], repo
            )
            try:
                old = mypy_check(tree, base_paths, "base")
            finally:
                run(["git", "worktree", "remove", "--force", str(tree)], repo)
    else:
        print("mypy base: no existing changed Python files; not run.")
    added = {path: added_lines(repo, base, path) for path in paths}
    return new_findings(head.findings, old.findings, paths, added), head, old


def report(
    base: str, version: str, result: tuple[list[Finding], CheckResult, CheckResult]
) -> int:
    findings, head, old = result
    lines = [
        f"Compared with merge base {base[:12]} using {version}.",
        f"Head: {head.files} file(s), {len(head.findings)} error(s), {head.seconds:.2f}s.",
        f"Base: {old.files} file(s), {len(old.findings)} error(s), {old.seconds:.2f}s.",
        f"{len(findings)} new type error(s). Advisory only; Backend Tests Pass is unchanged.",
    ]
    for finding in findings:
        print(
            f"::warning file={escape(finding.path, property_value=True)},"
            f"line={finding.line},col={finding.column},title=New mypy error::"
            f"{escape(finding.code + ': ' + finding.message)}"
        )
    print("\n".join(lines))
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as output:
            output.write("## Python types (advisory)\n\n" + "\n\n".join(lines) + "\n")
    return int(bool(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base", required=True, help="PR base commit or ref; its merge base is used"
    )
    parser.add_argument("--expect-mypy-version", required=True)
    args = parser.parse_args()
    try:
        repo = Path(run(["git", "rev-parse", "--show-toplevel"], Path.cwd()))
        base = run(["git", "merge-base", args.base, "HEAD"], repo)
        version = run([sys.executable, "-m", "mypy", "--version"], repo)
        if version.split()[1] != args.expect_mypy_version:
            raise GateError(
                f"Expected mypy {args.expect_mypy_version}, found {version}"
            )
        return report(base, version, check(repo, base))
    except (
        GateError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"::error title=Python types comparison incomplete::{escape(str(exc))}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
