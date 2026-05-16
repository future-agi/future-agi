"""
pact call-graph visualizer.

Renders the violation-annotated call graph as:
  - Mermaid flowchart (GitHub-native rendering, no hosting required)
  - Animated SVG "reduction" sequence — each frame shows one refactor
    suggestion applied, nodes shifting from red to green as violations
    are resolved

Usage
-----
    from tools.pact.visualize import render_mermaid, render_reduction_sequence

    mermaid = render_mermaid(violations, functions, call_sites)
    frames  = render_reduction_sequence(suggestions, violations, functions, call_sites)

    # Embed in a GitHub PR comment:
    print("```mermaid")
    print(mermaid)
    print("```")

    for i, frame in enumerate(frames):
        print(f"<details><summary>After refactor {i+1}</summary>\\n\\n```mermaid")
        print(frame)
        print("```\\n</details>")
"""

from __future__ import annotations

import re
from typing import Optional

from .encoder import Violation
from .extractor import CallSite, FunctionManifest
from .refactor import RefactorSuggestion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_test_file(path: str) -> bool:
    """True if path looks like a test file (test_*.py or conftest.py)."""
    import os
    base = os.path.basename(path)
    return base.startswith("test_") or base == "conftest.py"


# ---------------------------------------------------------------------------
# Colour mapping: violation score → Mermaid style class
# ---------------------------------------------------------------------------

def _score(func_name: str, violations: list[Violation]) -> int:
    return sum(1 for v in violations if _attr_func(v) == func_name)


def _attr_func(v: Violation) -> str:
    """Extract the function name from a violation's call field."""
    return v.call.split("(")[0].strip()


def _style_class(n_violations: int) -> str:
    if n_violations == 0:
        return "clean"
    if n_violations <= 1:
        return "warn"
    if n_violations <= 3:
        return "hot"
    return "fire"


# Safe Mermaid node ID: alphanumeric + underscore only
def _node_id(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


# ---------------------------------------------------------------------------
# Core: build a graph of which functions call which
# ---------------------------------------------------------------------------

def _call_edges(
    functions: list[FunctionManifest],
    call_sites: list[CallSite],
) -> list[tuple[str, str]]:
    """Return (caller, callee) pairs for known functions only."""
    func_names = {f.name for f in functions}
    short_to_qual = {}
    for f in functions:
        short_to_qual.setdefault(f.name.split(".")[-1], f.name)

    edges = []
    for cs in call_sites:
        if not cs.caller_name:
            continue
        caller = cs.caller_name
        callee = cs.callee_name
        if callee not in func_names:
            callee = short_to_qual.get(callee.split(".")[-1], callee)
        if caller in func_names or callee in func_names:
            edges.append((caller, callee))
    return edges


# ---------------------------------------------------------------------------
# Mermaid renderer
# ---------------------------------------------------------------------------

def render_mermaid(
    violations: list[Violation],
    functions: list[FunctionManifest],
    call_sites: list[CallSite],
    *,
    highlight: Optional[set[str]] = None,
    resolved: Optional[set[str]] = None,
    title: str = "",
    direction: str = "TD",
    skip_test_files: bool = True,
    violation_counts: Optional[dict[str, int]] = None,
) -> str:
    """
    Render the call graph as a Mermaid flowchart string.

    highlight         — names shown with a dashed "extraction candidate" border
    resolved          — names shown as green regardless of violation count
    violation_counts  — override per-function counts (use from RefactorSuggestion
                        data to get accurate attribution)
    """
    if highlight is None:
        highlight = set()
    if resolved is None:
        resolved = set()

    # Optionally filter out test files so tests don't pollute the graph
    if skip_test_files:
        functions = [f for f in functions if not _is_test_file(f.file)]
        call_sites = [cs for cs in call_sites
                      if not _is_test_file(cs.file)]

    # Per-function violation counts — prefer caller-attributed data
    if violation_counts is not None:
        vcount = {f.name: violation_counts.get(f.name, 0) for f in functions}
    else:
        vcount = {f.name: 0 for f in functions}
        for v in violations:
            fname = _attr_func(v)
            if fname in vcount:
                vcount[fname] += 1

    edges = _call_edges(functions, call_sites)
    # Build adjacency so we can do 1-hop expansion
    known = {f.name for f in functions}
    neighbors: dict[str, set[str]] = {f.name: set() for f in functions}
    for a, b in edges:
        if a in known and b in known:
            neighbors.setdefault(a, set()).add(b)
            neighbors.setdefault(b, set()).add(a)

    # Seed with nodes that have violations or are in highlight/resolved
    hot = {name for name in known if vcount.get(name, 0) > 0}
    hot |= (highlight or set()) & known
    hot |= (resolved or set()) & known

    # Expand 1 hop so we can see what calls into/out of the hot nodes
    involved = set(hot)
    for name in hot:
        involved |= neighbors.get(name, set())

    if not involved:
        # Nothing hot — show the whole known graph (small codebase case)
        involved = known

    lines = [f"flowchart {direction}"]
    if title:
        lines.append(f"  %% {title}")

    # Node definitions
    for name in sorted(involved):
        nid = _node_id(name)
        label = name.split(".")[-1]  # short name for readability
        count = vcount.get(name, 0)
        if count > 0:
            label = f"{label}\\n({count}✗)"
        cls = "resolved" if name in resolved else _style_class(count)
        shape_open, shape_close = ("([", "])") if name in highlight else ("[", "]")
        lines.append(f"  {nid}{shape_open}\"{label}\"{shape_close}:::{cls}")

    # Edges
    seen_edges: set[tuple[str, str]] = set()
    for caller, callee in edges:
        if caller not in involved or callee not in involved:
            continue
        key = (_node_id(caller), _node_id(callee))
        if key in seen_edges:
            continue
        seen_edges.add(key)
        arrow = "-.->" if caller in highlight or callee in highlight else "-->"
        lines.append(f"  {_node_id(caller)} {arrow} {_node_id(callee)}")

    # Style classes
    lines += [
        "  classDef clean fill:#d4edda,stroke:#28a745,color:#155724",
        "  classDef warn  fill:#fff3cd,stroke:#ffc107,color:#856404",
        "  classDef hot   fill:#ffe5b4,stroke:#fd7e14,color:#7d3501",
        "  classDef fire  fill:#f8d7da,stroke:#dc3545,color:#721c24",
        "  classDef resolved fill:#c3e6cb,stroke:#155724,color:#155724,stroke-dasharray:4",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test coverage graph
# ---------------------------------------------------------------------------

def render_test_coverage_mermaid(
    functions: list[FunctionManifest],
    call_sites: list[CallSite],
    *,
    direction: str = "LR",
) -> str:
    """
    Render a separate graph showing which test functions call which production
    functions. Tests as rectangular nodes (left), production functions as
    rounded nodes (right). Useful for spotting undertested areas.
    """
    test_funcs = {f.name for f in functions if _is_test_file(f.file)}
    prod_funcs = {f.name for f in functions if not _is_test_file(f.file)}

    # Edges from test → production only
    edges = []
    for cs in call_sites:
        caller = cs.caller_name or ""
        callee = cs.callee_name
        if caller in test_funcs and (callee in prod_funcs or callee.split(".")[-1] in {
            n.split(".")[-1] for n in prod_funcs
        }):
            # Resolve short name
            if callee not in prod_funcs:
                for pf in prod_funcs:
                    if pf.split(".")[-1] == callee.split(".")[-1]:
                        callee = pf
                        break
            edges.append((caller, callee))

    involved_tests = {a for a, _ in edges}
    involved_prod = {b for _, b in edges}

    if not involved_tests:
        return f"flowchart {direction}\n  %% no test→production edges found"

    lines = [f"flowchart {direction}", "  %% test coverage — left: tests, right: production"]

    # Count how many tests cover each prod function
    cover_count: dict[str, int] = {}
    for _, b in edges:
        cover_count[b] = cover_count.get(b, 0) + 1

    for name in sorted(involved_tests):
        nid = _node_id(name)
        label = name.split(".")[-1].replace("test_", "")
        lines.append(f"  {nid}[\"{label}\"]:::test")

    for name in sorted(involved_prod):
        nid = _node_id(name)
        label = name.split(".")[-1]
        n = cover_count.get(name, 0)
        label_full = f"{label}\\n({n} test{'s' if n != 1 else ''})"
        cls = "well_covered" if n >= 3 else "covered" if n >= 1 else "uncovered"
        lines.append(f"  {nid}([\"{label_full}\"]):::{cls}")

    seen: set[tuple[str, str]] = set()
    for a, b in edges:
        key = (_node_id(a), _node_id(b))
        if key not in seen:
            seen.add(key)
            lines.append(f"  {_node_id(a)} --> {_node_id(b)}")

    lines += [
        "  classDef test fill:#e8f4fd,stroke:#2196f3,color:#1565c0",
        "  classDef well_covered fill:#c8e6c9,stroke:#388e3c,color:#1b5e20",
        "  classDef covered fill:#fff9c4,stroke:#f9a825,color:#6d4c00",
        "  classDef uncovered fill:#ffcdd2,stroke:#c62828,color:#b71c1c",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Reduction sequence: animate refactors one by one
# ---------------------------------------------------------------------------

def render_reduction_sequence(
    suggestions: list[RefactorSuggestion],
    violations: list[Violation],
    functions: list[FunctionManifest],
    call_sites: list[CallSite],
) -> list[tuple[str, str]]:
    """
    Build an ordered sequence of (label, mermaid_diagram) pairs.

    Frame 0  — baseline: all violations, candidates highlighted
    Frame N  — after applying suggestion N: that function shown as resolved

    Returns list of (label, mermaid_str).
    """
    if not suggestions:
        return []

    candidate_names = {s.func_name for s in suggestions}
    # Build accurate violation counts from suggestion data
    all_vcounts = {s.func_name: s.violation_count for s in suggestions}
    frames = []

    # Frame 0: baseline
    frames.append((
        "Baseline — all violations",
        render_mermaid(
            violations, functions, call_sites,
            highlight=candidate_names,
            violation_counts=all_vcounts,
            title="pact refactor targets (dashed = extraction candidate)",
        ),
    ))

    resolved: set[str] = set()
    for s in suggestions:
        resolved.add(s.func_name)
        remaining_vcounts = {k: v for k, v in all_vcounts.items() if k not in resolved}
        frames.append((
            f"After extracting `{s.func_name.split('.')[-1]}` "
            f"({s.violation_count} violation{'s' if s.violation_count != 1 else ''} resolved)",
            render_mermaid(
                violations, functions, call_sites,
                highlight=candidate_names - resolved,
                resolved=resolved,
                violation_counts=remaining_vcounts,
                title=f"resolved: {', '.join(sorted(resolved))}",
            ),
        ))

    return frames


# ---------------------------------------------------------------------------
# GitHub PR comment formatter
# ---------------------------------------------------------------------------

def format_pr_comment(
    suggestions: list[RefactorSuggestion],
    violations: list[Violation],
    functions: list[FunctionManifest],
    call_sites: list[CallSite],
    *,
    include_test_coverage: bool = True,
) -> str:
    """
    Build a full GitHub PR comment body with the call graph and
    animated reduction sequence as collapsible Mermaid blocks.
    """
    if not violations and not suggestions:
        return "✓ **pact**: no violations found."

    n_v = len(violations)
    n_s = len(suggestions)
    header = (
        f"## pact analysis\n\n"
        f"**{n_v} violation{'s' if n_v != 1 else ''}** found"
        + (f" · **{n_s} refactor target{'s' if n_s != 1 else ''}** identified" if n_s else "")
        + "\n"
    )

    if not functions or not call_sites:
        # No graph to draw — just the header
        return header

    frames = render_reduction_sequence(suggestions, violations, functions, call_sites)
    if not frames:
        # Just the static graph
        static = render_mermaid(violations, functions, call_sites)
        return (
            header
            + "\n```mermaid\n" + static + "\n```\n"
        )

    parts = [header]

    # First frame inline (baseline)
    label0, diagram0 = frames[0]
    parts.append(f"### {label0}\n\n```mermaid\n{diagram0}\n```\n")

    # Remaining frames collapsible
    if len(frames) > 1:
        parts.append("### Reduction sequence\n")
        for label, diagram in frames[1:]:
            parts.append(
                f"<details><summary>{label}</summary>\n\n"
                f"```mermaid\n{diagram}\n```\n\n</details>\n"
            )

    # Test coverage graph
    if include_test_coverage:
        test_diagram = render_test_coverage_mermaid(functions, call_sites)
        if "no test→production edges found" not in test_diagram:
            parts.append(
                "\n<details><summary>Test coverage graph</summary>\n\n"
                f"```mermaid\n{test_diagram}\n```\n\n</details>\n"
            )

    return "\n".join(parts)
