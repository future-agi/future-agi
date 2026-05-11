"""
pact Go checker bridge.

Invokes the compiled pact-go binary (or `go run .` fallback) on a directory
or file list and converts the resulting JSON violations into FailureEvidence
objects compatible with pact's FailureMode plugin layer.

The Go checker is kept as a separate binary so it can be distributed and
used independently of the Python stack — this module is the integration
point for combined Python + Go analysis in a single pact run.

Usage (programmatic)
--------------------
    from tools.pact.go_checker import run_go_checker

    evidence = run_go_checker(paths=["/path/to/pkg"])
    for ev in evidence:
        print(ev)

Usage (CLI)
-----------
    python -m tools.pact.go_checker path/to/package/
    python -m tools.pact.go_checker --file a.go --file b.go
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .failure_mode import FailureEvidence

# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------

_CHECKER_DIR = Path(__file__).parent / "go" / "checker"
_BINARY_NAME = "pact-go"


def _find_binary() -> Optional[str]:
    """Return path to pact-go binary, or None if not found."""
    # 1. Pre-compiled binary next to main.go
    local = _CHECKER_DIR / _BINARY_NAME
    if local.exists():
        return str(local)

    # 2. On PATH
    found = shutil.which(_BINARY_NAME)
    if found:
        return found

    return None


def _can_go_run() -> bool:
    """Return True if `go` is available to use `go run`."""
    return shutil.which("go") is not None


# ---------------------------------------------------------------------------
# Core runner
# ---------------------------------------------------------------------------

def run_go_checker(
    paths: list[str | Path],
    *,
    binary: Optional[str] = None,
    extra_args: Optional[list[str]] = None,
) -> list[FailureEvidence]:
    """
    Run pact-go on the given paths and return a list of FailureEvidence.

    Each path can be a .go file or a directory (analyzed recursively).

    Parameters
    ----------
    paths:
        Files or directories to analyze.
    binary:
        Explicit path to pact-go binary. If None, auto-resolves.
    extra_args:
        Additional CLI args forwarded to pact-go.

    Returns
    -------
    list[FailureEvidence]
        One entry per violation; empty list if none found or Go not available.
    """
    if not paths:
        return []

    # Build command
    resolved_binary = binary or _find_binary()

    if resolved_binary:
        cmd = [resolved_binary]
    elif _can_go_run():
        cmd = ["go", "run", str(_CHECKER_DIR)]
    else:
        # No Go toolchain or binary — skip silently so Python analysis still runs
        return []

    for p in paths:
        p = Path(p)
        if p.is_dir():
            cmd += ["--dir", str(p)]
        elif p.is_file():
            cmd += ["--file", str(p)]

    if extra_args:
        cmd += extra_args

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []

    # Exit codes: 0 = success (violations or clean), 1 = some tools signal violations,
    # 2 = usage error (no files given). Any non-zero with no stdout → no results.
    raw = result.stdout.strip()
    if not raw:
        return []

    try:
        records = json.loads(raw)
    except json.JSONDecodeError:
        return []

    if not isinstance(records, list):
        return []

    evidence = []
    for rec in records:
        evidence.append(FailureEvidence(
            mode_name=rec.get("mode", "go_unknown"),
            file=rec.get("file", ""),
            line=rec.get("line", 0),
            call=rec.get("call", ""),
            message=rec.get("message", ""),
        ))
    return evidence


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="pact-go-checker",
        description="Run pact Go failure-mode analysis and emit violations as JSON.",
    )
    p.add_argument("paths", nargs="*", help="Go files or directories to analyze")
    p.add_argument("--file", dest="files", action="append", default=[],
                   metavar="FILE", help="Go source file (repeatable)")
    p.add_argument("--dir", dest="dirs", action="append", default=[],
                   metavar="DIR", help="Directory to analyze recursively (repeatable)")
    p.add_argument("--json", action="store_true", help="Emit raw JSON array (default: human-readable)")
    p.add_argument("--binary", help="Path to pact-go binary")
    args = p.parse_args(argv)

    all_paths = list(args.paths) + list(args.files) + list(args.dirs)
    if not all_paths:
        p.print_help()
        return 2

    evidence = run_go_checker(all_paths, binary=args.binary)

    if args.json:
        import dataclasses
        print(json.dumps([dataclasses.asdict(e) for e in evidence], indent=2))
    else:
        if not evidence:
            print("pact-go: no violations found")
        for ev in evidence:
            print(ev)

    return 0 if not evidence else 1


if __name__ == "__main__":
    sys.exit(main())
