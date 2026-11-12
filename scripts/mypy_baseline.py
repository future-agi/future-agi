"""Compare whole-repo mypy output with futureagi/mypy-baseline.txt (PostHog-style).

Run from futureagi/ (or use `make mypy` and `make mypy-baseline-sync`):

    mypy . > /tmp/mypy.txt; python3 -I ../scripts/mypy_baseline.py filter \
        --mypy-exit-code $? --input /tmp/mypy.txt
    mypy . | python3 -I ../scripts/mypy_baseline.py sync

Each error "path:line[:column]: error: message  [code]" is stored as
"path:0: error: message  [code]", so moving code does not change it. "on line
N" inside a message (as in "already defined on line N") becomes "on line 0" for
the same reason. Notes are ignored. The baseline is a sorted multiset: an entry
appears once per occurrence.

filter exits 1 when an entry occurs more often than the baseline allows (a new
error) and prints every occurrence with its real line number. Baseline entries
that no longer occur are reported but do not fail: sync removes them.

sync rewrites the baseline from the output.

Both refuse the output of a run that did not finish (a crash, a run cut short,
"errors prevented further checking", or output this script does not
understand) and exit 2. A run that did not finish is never a clean result.

Exit codes: 0 no new errors, 1 new errors, 2 the comparison could not run.
Standard library only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# "path: error: msg", "path:12: error: msg", "path:12:5: error: msg"; end
# positions too, if show_error_end is ever turned on. Paths contain no colon.
DIAGNOSTIC = re.compile(
    r"^(?P<path>[^:\s][^:]*?)(?::(?P<line>\d+)(?::\d+){0,3})?"
    r": (?P<severity>error|note): (?P<message>.+)$"
)
FOUND = re.compile(
    r"^Found (?P<errors>\d+) errors? in \d+ files? \((?P<detail>[^()]*)\)$"
)
CHECKED = re.compile(r"^checked \d+ source files?$")
SUCCESS = re.compile(r"^Success: no issues found in \d+ source files?$")
ENTRY = re.compile(r"^[^:\s][^:]*:0: error: .+$")
ON_LINE = re.compile(r"\bon line \d+\b")
CODE = re.compile(r"  \[(?P<code>[a-z0-9-]+)\]$")

SYNC_HINT = "run `make mypy-baseline-sync` in futureagi/"
SHOW = 200  # longest list printed before "... and N more"


class Incomplete(Exception):
    """mypy did not finish, or the output or baseline cannot be read."""


@dataclass(frozen=True)
class Error:
    path: str
    line: int | None
    raw: str
    entry: str


@dataclass(frozen=True)
class Comparison:
    errors: list[Error]
    baseline: Counter[str]
    new: dict[str, list[Error]]  # entry -> every occurrence of it now
    fixed: dict[str, int]  # entry -> occurrences the baseline has that are gone

    @property
    def new_count(self) -> int:
        return sum(len(found) - self.baseline[e] for e, found in self.new.items())

    @property
    def fixed_count(self) -> int:
        return sum(self.fixed.values())


def entry_for(path: str, message: str) -> str:
    return f"{path}:0: error: {ON_LINE.sub('on line 0', message)}"


def parse_output(text: str, exit_code: int | None = None) -> list[Error]:
    """The errors of a finished mypy run; Incomplete if it did not finish."""
    errors: list[Error] = []
    summary = None
    summary_errors = 0
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not line:
            continue
        if summary is not None:
            raise Incomplete(
                f"output line {number} follows mypy's summary line: {line!r}"
            )
        if SUCCESS.match(line):
            summary = line
            continue
        found = FOUND.match(line)
        if found:
            if not CHECKED.match(found["detail"]):
                raise Incomplete(f"mypy stopped before checking everything: {line}")
            summary, summary_errors = line, int(found["errors"])
            continue
        diagnostic = DIAGNOSTIC.match(line)
        if not diagnostic:
            raise Incomplete(f"output line {number} is not a mypy diagnostic: {line!r}")
        if diagnostic["severity"] == "error":
            path = diagnostic["path"].removeprefix("./")
            errors.append(
                Error(
                    path=path,
                    line=int(diagnostic["line"]) if diagnostic["line"] else None,
                    raw=line,
                    entry=entry_for(path, diagnostic["message"]),
                )
            )
    if summary is None:
        raise Incomplete(
            "mypy printed no summary line, so it did not finish "
            "(a crash, a killed process or truncated output)"
        )
    if summary_errors != len(errors):
        raise Incomplete(
            f"mypy's summary says {summary_errors} error(s) but its output has "
            f"{len(errors)}: {summary}"
        )
    if exit_code is not None and exit_code != (1 if errors else 0):
        raise Incomplete(f"mypy exited {exit_code} after printing: {summary}")
    return errors


def parse_baseline(text: str, source: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        if not ENTRY.match(raw):
            raise Incomplete(
                f"{source}:{number} is not a baseline entry (a merge conflict? "
                f"{SYNC_HINT} to rebuild it): {raw!r}"
            )
        counts[raw] += 1
    return counts


def read_baseline(path: Path) -> Counter[str]:
    if not path.is_file():
        raise Incomplete(f"no baseline at {path}; {SYNC_HINT} to create it")
    return parse_baseline(path.read_text(encoding="utf-8"), str(path))


def compare(errors: list[Error], baseline: Counter[str]) -> Comparison:
    by_entry: dict[str, list[Error]] = {}
    for error in errors:
        by_entry.setdefault(error.entry, []).append(error)
    new = {
        entry: found
        for entry, found in sorted(by_entry.items())
        if len(found) > baseline[entry]
    }
    fixed = {
        entry: count - len(by_entry.get(entry, ()))
        for entry, count in sorted(baseline.items())
        if count > len(by_entry.get(entry, ()))
    }
    return Comparison(errors, baseline, new, fixed)


def render(entries: list[str]) -> str:
    return "\n".join(entries) + "\n" if entries else ""


def code_of(text: str) -> str:
    found = CODE.search(text)
    return found["code"] if found else "(no code)"


def escape(text: str, *, property_value: bool = False) -> str:
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if property_value else text


def annotate(result: Comparison) -> None:
    """GitHub annotations on each occurrence of an entry above its baseline count."""
    workspace = Path(os.environ.get("GITHUB_WORKSPACE") or Path.cwd()).resolve()
    for entry, found in result.new.items():
        allowed = result.baseline[entry]
        title = "New mypy error"
        if allowed:
            title = f"mypy error over baseline count ({len(found)}, baseline {allowed})"
        for error in found:
            path = os.path.relpath(Path.cwd() / error.path, workspace)
            line = f",line={error.line}" if error.line else ""
            message = error.raw.split(": error: ", 1)[1]
            print(
                f"::error file={escape(path, property_value=True)}{line},"
                f"title={escape(title, property_value=True)}::{escape(message)}"
            )


def write_step_summary(result: Comparison) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    lines = [
        "## Mypy (baseline)",
        "",
        f"- Errors now: {len(result.errors)}",
        f"- Baseline entries: {sum(result.baseline.values())}",
        f"- New errors: {result.new_count}",
        f"- Fixed but still in the baseline: {result.fixed_count}",
    ]
    if result.new:
        lines += [
            "",
            "New errors (every occurrence of each entry over its count):",
            "",
            "```",
        ]
        lines += [e.raw for found in result.new.values() for e in found][:SHOW]
        lines += ["```"]
    with open(summary, "a", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")


def report_filter(result: Comparison) -> int:
    if result.new:
        by_code = Counter(code_of(entry) for entry in result.new)
        print(
            f"{result.new_count} new mypy error(s) beyond the baseline, "
            f"codes: {', '.join(f'{c} {n}' for c, n in sorted(by_code.items()))}."
        )
        print("Every occurrence of each over-count entry is listed; fix the new one.\n")
        for entry, found in result.new.items():
            allowed = result.baseline[entry]
            print(f"{len(found)} now, baseline allows {allowed}: {entry}")
            for error in found:
                print(f"    {error.raw}")
        print()
        if os.environ.get("GITHUB_ACTIONS") == "true":
            annotate(result)
    if result.fixed:
        print(
            f"Fixed: {result.fixed_count} baseline line(s) no longer occur. Not "
            f"a failure; {SYNC_HINT} so they cannot come back unnoticed."
        )
        for index, (entry, gone) in enumerate(result.fixed.items()):
            if index == SHOW:
                print(f"fixed: ... and {len(result.fixed) - SHOW} more entries")
                break
            print(f"fixed: {entry}" + (f"  (x{gone})" if gone > 1 else ""))
        print()
    print(
        f"mypy-baseline: {len(result.errors)} error(s) now, "
        f"{sum(result.baseline.values())} in the baseline, "
        f"{result.new_count} new, {result.fixed_count} fixed."
    )
    write_step_summary(result)
    return 1 if result.new else 0


def sync(errors: list[Error], path: Path) -> int:
    old: Counter[str] = Counter()
    if path.is_file():
        # Sync is how a broken baseline (a merge conflict) gets rebuilt, so
        # unreadable lines are dropped here instead of stopping the run.
        for raw in path.read_text(encoding="utf-8").splitlines():
            if ENTRY.match(raw):
                old[raw] += 1
    entries = sorted(error.entry for error in errors)
    new = Counter(entries)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(render(entries), encoding="utf-8")
    os.replace(tmp, path)
    added, removed = new - old, old - new
    print(
        f"Wrote {path}: {len(entries)} entries (was {sum(old.values())}), "
        f"{sum(removed.values())} removed, {sum(added.values())} added."
    )
    if added:
        print("Added (new errors; fixing them is better than baselining them):")
        for index, entry in enumerate(sorted(added.elements())):
            if index == SHOW:
                print(f"    ... and {sum(added.values()) - SHOW} more")
                break
            print(f"    {entry}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (
        ("filter", "fail on errors beyond the baseline"),
        ("sync", "rewrite the baseline from mypy's output"),
    ):
        command = commands.add_parser(name, help=text)
        command.add_argument(
            "--baseline",
            type=Path,
            default=Path("mypy-baseline.txt"),
            help="baseline file (default: mypy-baseline.txt in the current directory)",
        )
        command.add_argument(
            "--input", type=Path, help="mypy's output (default: standard input)"
        )
        command.add_argument(
            "--mypy-exit-code",
            type=int,
            help="mypy's exit status, checked against its summary line",
        )
    args = parser.parse_args(argv)
    try:
        if args.input:
            text = args.input.read_text(encoding="utf-8")
        else:
            text = sys.stdin.buffer.read().decode("utf-8")
        errors = parse_output(text, args.mypy_exit_code)
        if args.command == "sync":
            return sync(errors, args.baseline)
        return report_filter(compare(errors, read_baseline(args.baseline)))
    except (Incomplete, OSError, UnicodeDecodeError) as exc:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::error title=mypy baseline check did not run::{escape(str(exc))}")
        print(f"mypy-baseline: could not compare: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
