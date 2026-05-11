"""pact CLI — run constraint analysis on a codebase."""

import argparse
import json
import sys
from pathlib import Path

from .checker import check_codebase, check_codebase_incremental
from .extractor import extract_from_codebase
from .refactor import suggest_refactors
from .visualize import (
    format_pr_comment, render_mermaid, render_reduction_sequence,
    render_test_coverage_mermaid,
)


class DiffResolutionError(RuntimeError):
    """Raised when diff-scoped analysis cannot resolve the changed file set."""


def _changed_files_on_branch(base: str = "main", cwd: Path = None) -> set[str]:
    """Return absolute paths of files changed vs base branch."""
    import subprocess
    cwd = cwd or Path(".").resolve()
    try:
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
    except (subprocess.CalledProcessError, OSError) as exc:
        stderr = (getattr(exc, "stderr", None) or "").strip()
        stdout = (getattr(exc, "stdout", None) or "").strip()
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
    p.add_argument(
        "--incremental", metavar="BASE", nargs="?", const="main",
        help=(
            "Analyze only the dirty subgraph: files changed vs BASE plus their "
            "transitive callers (default BASE: main). Faster than --diff because "
            "unchanged graph nodes are never analyzed."
        ),
    )
    p.add_argument(
        "--suggest", action="store_true",
        help="Suggest safe refactor targets: functions with high violation density and low coupling",
    )
    p.add_argument(
        "--suggest-min", type=int, default=1, metavar="N",
        help="Minimum violations to include in refactor suggestions (default: 1)",
    )
    p.add_argument(
        "--graph", action="store_true",
        help="Print the violation call graph as a Mermaid flowchart",
    )
    p.add_argument(
        "--graph-tests", action="store_true",
        help="Print a separate test coverage graph (test→production edges)",
    )
    p.add_argument(
        "--pr-comment", action="store_true",
        help="Print a full GitHub PR comment body (call graph + reduction sequence + test coverage)",
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

    incremental_stats: dict = {}
    if args.incremental is not None:
        changed = _changed_files_on_branch(args.incremental, cwd=root)
        violations, incremental_stats = check_codebase_incremental(
            root, changed, _extracted=extracted,
        )
        if args.stats:
            s = incremental_stats
            skipped_pct = int(s["skip_ratio"] * 100)
            print(
                f"incremental vs {args.incremental}: "
                f"{s['dirty_files']}/{s['total_files']} files dirty, "
                f"{s['dirty_call_sites']}/{s['total_call_sites']} call sites analyzed "
                f"({skipped_pct}% skipped)"
            )
    else:
        violations = check_codebase(root, _extracted=extracted)

    if args.diff is not None:
        changed = _changed_files_on_branch(args.diff, cwd=root)
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

    suggestions: list = []
    if args.suggest or args.pr_comment:
        suggestions = suggest_refactors(
            violations, functions, call_sites,
            min_violations=args.suggest_min,
        )
        if args.suggest and not args.json_mode:
            if suggestions:
                print(f"\n⚡ pact: {len(suggestions)} refactor suggestion(s)\n")
                for s in suggestions:
                    print(s.summary())
                    print()
            else:
                print("\n✓  pact: no refactor targets above threshold")

    if args.graph:
        # Use suggestion data for accurate attribution if available; else compute
        if not suggestions and violations:
            _graph_suggestions = suggest_refactors(violations, functions, call_sites, verify=False)
        else:
            _graph_suggestions = suggestions
        vcounts = {s.func_name: s.violation_count for s in _graph_suggestions}
        diagram = render_mermaid(
            violations, functions, call_sites,
            highlight={s.func_name for s in _graph_suggestions},
            violation_counts=vcounts,
        )
        print("\n```mermaid")
        print(diagram)
        print("```\n")

    if args.graph_tests:
        diagram = render_test_coverage_mermaid(functions, call_sites)
        print("\n```mermaid")
        print(diagram)
        print("```\n")

    if args.pr_comment:
        print(format_pr_comment(suggestions, violations, functions, call_sites))

    return 1 if (violations and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
