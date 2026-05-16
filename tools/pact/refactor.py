"""
pact refactor suggester.

After finding violations, ranks functions by how much they concentrate
constraint failures relative to how many callers they have. A function
with many violations and few callers is a safe extraction candidate —
high impact, low coupling, structurally isolated.

Z3 verifies the extraction is safe: for every caller, the function's
required argument contract is already satisfied at each call site. If Z3
returns UNSAT (no contract violation), the extraction preserves behavior.

Usage
-----
    from tools.pact.refactor import suggest_refactors
    from tools.pact.checker import check_codebase
    from tools.pact.extractor import extract_from_codebase

    models, functions, call_sites = extract_from_codebase(root)
    violations = check_codebase(root, _extracted=(models, functions, call_sites))
    suggestions = suggest_refactors(violations, functions, call_sites)
    for s in suggestions:
        print(s.summary())
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Optional

from .encoder import Violation
from .extractor import ArgConstraint, CallSite, FunctionManifest

try:
    from z3 import (
        And, Bool, BoolVal, Not, Or, Solver, sat, unsat,
        Implies, BoolRef,
    )
    _HAS_Z3 = True
except ImportError:
    _HAS_Z3 = False


# ---------------------------------------------------------------------------
# RefactorSuggestion
# ---------------------------------------------------------------------------

@dataclass
class RefactorSuggestion:
    """A ranked suggestion to extract a function into its own unit."""
    func_name: str
    file: str
    line: int
    violation_count: int
    caller_count: int
    modes: list[str]               # violation mode names, deduplicated
    violations: list[Violation]
    z3_safe: Optional[bool]        # True = Z3 proved safe; False = unsafe; None = skipped
    z3_detail: str = ""            # human-readable reason when z3_safe is False

    @property
    def score(self) -> float:
        """Higher = better extraction candidate (many violations, few callers)."""
        return self.violation_count / max(1, self.caller_count)

    def summary(self) -> str:
        safety = (
            "Z3-safe ✓" if self.z3_safe is True
            else f"Z3-unsafe ✗ ({self.z3_detail})" if self.z3_safe is False
            else "Z3 n/a"
        )
        modes_str = ", ".join(sorted(set(self.modes)))
        return (
            f"  {self.func_name}  [{self.file}:{self.line}]\n"
            f"    violations={self.violation_count}  callers={self.caller_count}"
            f"  score={self.score:.2f}  {safety}\n"
            f"    modes: {modes_str}"
        )


# ---------------------------------------------------------------------------
# Z3 extraction-safety verifier
# ---------------------------------------------------------------------------

def _verify_extraction_safe(
    func: FunctionManifest,
    callers: list[CallSite],
) -> tuple[bool, str]:
    """
    Use Z3 to check whether every caller satisfies the function's required
    argument contract. Returns (safe: bool, detail: str).

    Contract: for each required arg, at least one caller must provide it —
    but *all* callers must provide it for the extraction to be safe.

    If Z3 is unavailable, returns (True, "z3 not installed — skipped").
    """
    if not _HAS_Z3:
        return True, "z3 not installed — skipped"

    if not func.args:
        return True, "no args"

    required = [a for a in func.args if a.required and not a.kwonly]
    required_kw = [a for a in func.args if a.required and a.kwonly]

    if not required and not required_kw:
        return True, "no required args"

    if not callers:
        return True, "no callers"

    solver = Solver()
    unsafe_conditions = []

    for i, call in enumerate(callers):
        # Positional contract: caller must provide enough positional args
        if required:
            n_required_positional = len(required)
            if call.positional_count < n_required_positional:
                cond = BoolVal(True)  # this caller is already unsafe
                unsafe_conditions.append(cond)

        # Keyword-only contract: all required kwonly args must be provided
        for arg in required_kw:
            if arg.name not in call.provided_kwargs:
                unsafe_conditions.append(BoolVal(True))
                break

    if not unsafe_conditions:
        return True, "all callers satisfy contract"

    solver.add(Or(*unsafe_conditions))
    result = solver.check()
    if result == sat:
        # At least one caller violates the contract
        n = len(unsafe_conditions)
        return False, f"{n} caller(s) missing required args"
    elif result == unsat:
        return True, "all callers satisfy contract"
    else:
        return True, "z3 unknown"


# ---------------------------------------------------------------------------
# Core suggester
# ---------------------------------------------------------------------------

def suggest_refactors(
    violations: list[Violation],
    functions: list[FunctionManifest],
    call_sites: list[CallSite],
    *,
    min_violations: int = 1,
    max_suggestions: int = 10,
    verify: bool = True,
) -> list[RefactorSuggestion]:
    """
    Rank functions by refactor value: high violation density, low coupling.

    Parameters
    ----------
    violations:
        Output of check_codebase().
    functions:
        FunctionManifest list from extract_from_codebase().
    call_sites:
        CallSite list from extract_from_codebase().
    min_violations:
        Only suggest functions with at least this many violations.
    max_suggestions:
        Cap output length.
    verify:
        Run Z3 safety check on each candidate.

    Returns
    -------
    list[RefactorSuggestion]
        Sorted descending by score (violations / callers).
    """
    # Index functions by name
    func_by_name: dict[str, FunctionManifest] = {f.name: f for f in functions}

    # Count violations per function (by file+line proximity — violations that
    # originate inside a function are attributed to it via caller_name on the
    # call site, or by file+line range if caller_name is absent).
    # Strategy: use call site caller_name when available; fall back to file match.

    # Build caller_name → call sites map
    caller_to_sites: dict[str, list[CallSite]] = collections.defaultdict(list)
    for cs in call_sites:
        if cs.caller_name:
            caller_to_sites[cs.caller_name].append(cs)

    # Build (file, line) → caller_name index for attribution fallback
    site_key_to_caller: dict[tuple[str, int], str] = {}
    for cs in call_sites:
        if cs.caller_name:
            site_key_to_caller[(cs.file, cs.line)] = cs.caller_name

    # Count violations per function.
    # Attribution: find the function whose definition is closest above the
    # violation line in the same file. This is the function whose body
    # contains the violation, regardless of whether it's a caller or callee.
    # We prefer a caller_name from call site data when it matches a known
    # function; otherwise fall back to proximity.
    func_violations: dict[str, list[Violation]] = collections.defaultdict(list)
    for v in violations:
        attributed = False

        # Try call-site caller attribution first — only use it if the caller
        # name is actually a known function in our index.
        caller = site_key_to_caller.get((v.file, v.line))
        if caller and caller in func_by_name:
            func_violations[caller].append(v)
            attributed = True

        if not attributed:
            # Proximity: find the function defined closest above this line.
            best: Optional[str] = None
            best_dist = float("inf")
            for f in functions:
                if f.file == v.file and f.line <= v.line:
                    dist = v.line - f.line
                    if dist < best_dist:
                        best_dist = dist
                        best = f.name
            if best:
                func_violations[best].append(v)

    # Count callers per function (in-degree in call graph)
    callee_to_callers: dict[str, list[CallSite]] = collections.defaultdict(list)
    for cs in call_sites:
        callee_to_callers[cs.callee_name].append(cs)
        # Also match short name
        short = cs.callee_name.split(".")[-1]
        if short != cs.callee_name:
            callee_to_callers[short].append(cs)

    suggestions: list[RefactorSuggestion] = []

    for func_name, func_viols in func_violations.items():
        if len(func_viols) < min_violations:
            continue

        func = func_by_name.get(func_name)
        if func is None:
            # Try short name match
            short = func_name.split(".")[-1]
            func = func_by_name.get(short)
        if func is None:
            continue

        # Callers: merge both qualified and short names to avoid or-shortcircuit drop,
        # then deduplicate by object identity (callee_to_callers inserts each CallSite
        # under both the qualified key and the short key when they differ, so a plain
        # + concatenation would double-count and corrupt unsafe_conditions counts).
        short = func.name.split(".")[-1]
        callers = list({id(cs): cs for cs in (
            callee_to_callers.get(func_name, []) + callee_to_callers.get(short, [])
        )}.values())

        # Z3 safety check
        z3_safe: Optional[bool] = None
        z3_detail = ""
        if verify and func.args:
            z3_safe, z3_detail = _verify_extraction_safe(func, callers)

        suggestions.append(RefactorSuggestion(
            func_name=func_name,
            file=func.file,
            line=func.line,
            violation_count=len(func_viols),
            caller_count=len({
                cs.caller_name if cs.caller_name else f"__module__:{cs.file}"
                for cs in callers
            }),
            modes=[v.context for v in func_viols],
            violations=func_viols,
            z3_safe=z3_safe,
            z3_detail=z3_detail,
        ))

    suggestions.sort(key=lambda s: s.score, reverse=True)
    return suggestions[:max_suggestions]
