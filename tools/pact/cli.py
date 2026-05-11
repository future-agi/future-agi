"""pact CLI — run constraint analysis on a codebase."""

import argparse
import json
import sys
from pathlib import Path

from .checker import check_codebase
from .extractor import extract_from_codebase


class DiffResolutionError(RuntimeError):
    """Raised when diff-scoped analysis cannot resolve the changed file set."""


def _changed_files_on_branch(base: str = "main", cwd: Path = None) -> set[str]:
    """Return absolute paths of files changed vs base branch."""
    import subprocess
    cwd = cwd or Path(".").resolve()
    try:
        # Find git root first so paths from `git diff` resolve correctly
        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True, cwd=cwd,
        ).stdout.strip()
        git_root_path = Path(git_root)
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True, text=True, check=True, cwd=git_root_path,
        )
        return {
            str(git_root_path / p.strip())
            for p in result.stdout.splitlines()
            if p.strip().endswith(".py")
        }
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        detail = stderr or stdout or str(exc)
        raise DiffResolutionError(detail) from exc


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="pact",
        description="Python AST Constraint Tool — verify constraints across a codebase using Z3.",
    )
    p.add_argument(
        "root", nargs="?", default=".", metavar="DIR",
        help="Root directory to analyze (default: current directory)",
    )
    p.add_argument(
        "--json", action="store_true", dest="json_mode",
        help="Emit results as JSON array",
    )
    p.add_argument(
        "--strict", action="store_true",
        help="Exit 1 if any violation found",
    )
    p.add_argument(
        "--stats", action="store_true",
        help="Print extraction statistics before results",
    )
    p.add_argument(
        "--diff", metavar="BASE", nargs="?", const="main",
        help="Only report violations in files changed vs BASE branch (default: main)",
    )
    args = p.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    extracted = extract_from_codebase(root)
    models, functions, call_sites = extracted

    if args.stats:
        print(f"models: {len(models)}  functions: {len(functions)}  call sites: {len(call_sites)}")

    violations = check_codebase(root, _extracted=extracted)

    if args.diff is not None:
        try:
            changed = _changed_files_on_branch(args.diff, cwd=root)
        except DiffResolutionError as exc:
            print(f"error: pact --diff failed: {exc}", file=sys.stderr)
            return 2
        violations = [v for v in violations if v.file in changed]
        if args.stats:
            print(f"files changed vs {args.diff}: {len(changed)}")

    if args.json_mode:
        print(json.dumps(
            [{"file": v.file, "line": v.line, "call": v.call,
              "missing": v.missing, "context": v.context}
             for v in violations],
            indent=2,
        ))
    else:
        if not violations:
            label = f"(diff vs {args.diff})" if args.diff else ""
            print(f"✓  pact: no constraint violations {label}".strip())
        else:
            label = f"(diff vs {args.diff})" if args.diff else ""
            print(f"✗  pact: {len(violations)} violation(s) {label}\n".strip())
            for v in violations:
                print(f"  {v}")
            print()

    return 1 if (violations and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
