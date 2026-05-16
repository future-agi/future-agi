"""
Main orchestration: parse → graph → FailureMode registry → Z3 → violations.
"""

from pathlib import Path
from typing import Optional

from .encoder import Violation
from .extractor import FunctionManifest, ModelManifest, extract_from_codebase, iter_python_files
from .failure_mode import DEFAULT_MODES, FailureEvidence, FailureMode


def _to_violation(e: FailureEvidence) -> Violation:
    return Violation(
        file=e.file,
        line=e.line,
        call=e.call,
        missing=e.missing if e.missing else [e.message],
        context=e.mode_name,
    )


def _compute_dirty_set(
    changed_files: set[str],
    functions: list[FunctionManifest],
    call_sites,
) -> tuple[set[str], set[str]]:
    """
    BFS upward through the call graph from changed files.

    If F is defined in a dirty file, every call site that CALLS F is dirty too
    (a callee contract change can invalidate a caller's violation verdict).
    Returns (dirty_files, dirty_function_names).
    """
    dirty_files: set[str] = set(changed_files)
    dirty_funcs: set[str] = {f.name for f in functions if f.file in dirty_files}

    # callee_name → set of files that contain a call to it
    callee_to_caller_files: dict[str, set[str]] = {}
    for cs in call_sites:
        callee_to_caller_files.setdefault(cs.callee_name, set()).add(cs.file)

    frontier = set(dirty_funcs)
    while frontier:
        next_frontier: set[str] = set()
        for func_name in frontier:
            for caller_file in callee_to_caller_files.get(func_name, set()):
                if caller_file not in dirty_files:
                    dirty_files.add(caller_file)
                    new_funcs = {f.name for f in functions if f.file == caller_file} - dirty_funcs
                    dirty_funcs |= new_funcs
                    next_frontier |= new_funcs
        frontier = next_frontier

    return dirty_files, dirty_funcs


def check_codebase(
    root: Path,
    modes: Optional[list[FailureMode]] = None,
    *,
    _extracted=None,  # (models, functions, call_sites) if already extracted
) -> list[Violation]:
    """
    Run all FailureModes against every call site (and every file) in the codebase.

    Parameters
    ----------
    root:
        Directory to analyze.
    modes:
        FailureMode list to run. Defaults to DEFAULT_MODES.
        Pass a custom list to add or replace constraint classes without
        touching any other code.
    _extracted:
        Pre-extracted (models, functions, call_sites) tuple. If provided,
        skips the extraction step to avoid double-parsing.
    """
    if modes is None:
        modes = DEFAULT_MODES

    if _extracted is not None:
        models, functions, call_sites = _extracted
    else:
        models, functions, call_sites = extract_from_codebase(root)

    model_index: dict[str, ModelManifest] = {m.name: m for m in models}
    func_index: dict[str, FunctionManifest] = {f.name: f for f in functions}

    seen: set[tuple] = set()
    violations: list[Violation] = []

    def _add(evidence: FailureEvidence) -> None:
        key = (evidence.file, evidence.line, evidence.mode_name, evidence.call)
        if key not in seen:
            seen.add(key)
            violations.append(_to_violation(evidence))

    # Per-call-site checks (modes with check=None are file-level only)
    for call in call_sites:
        for mode in modes:
            if mode.check is None:
                continue
            for evidence in mode.check(call, model_index, func_index):
                _add(evidence)

    # File-level checks — run on every Python file under root (via the same
    # iterator the extractor uses), so modes that scan definitions catch files
    # with zero call sites, models, or functions.
    file_modes = [m for m in modes if m.file_check is not None]
    if file_modes:
        for path in iter_python_files(root):
            for mode in file_modes:
                for evidence in mode.file_check(str(path)):  # type: ignore[misc]
                    _add(evidence)

    return violations


def check_codebase_incremental(
    root: Path,
    changed_files: set[str],
    modes: Optional[list[FailureMode]] = None,
    *,
    _extracted=None,
) -> tuple[list[Violation], dict]:
    """
    Like check_codebase, but skips call sites whose enclosing file AND whose
    callees' files are all unchanged.

    Propagation: if any function F is defined in a dirty file, every call site
    that calls F is also dirty — a callee change can alter whether a caller
    is flagged.  The BFS terminates when no new files are reached.

    Returns
    -------
    violations : list[Violation]
        Only violations reachable from the dirty subgraph.
    stats : dict
        Diagnosis keys: total_files, dirty_files, total_call_sites,
        dirty_call_sites, skip_ratio.
    """
    if modes is None:
        modes = DEFAULT_MODES

    if _extracted is not None:
        models, functions, call_sites = _extracted
    else:
        models, functions, call_sites = extract_from_codebase(root)

    dirty_files, _ = _compute_dirty_set(changed_files, functions, call_sites)
    dirty_call_sites = [cs for cs in call_sites if cs.file in dirty_files]

    model_index: dict[str, ModelManifest] = {m.name: m for m in models}
    func_index: dict[str, FunctionManifest] = {f.name: f for f in functions}

    seen: set[tuple] = set()
    violations: list[Violation] = []

    def _add(evidence: FailureEvidence) -> None:
        key = (evidence.file, evidence.line, evidence.mode_name, evidence.call)
        if key not in seen:
            seen.add(key)
            violations.append(_to_violation(evidence))

    for call in dirty_call_sites:
        for mode in modes:
            if mode.check is None:
                continue
            for evidence in mode.check(call, model_index, func_index):
                _add(evidence)

    file_modes = [m for m in modes if m.file_check is not None]
    if file_modes:
        for path in iter_python_files(root):
            if str(path) not in dirty_files:
                continue
            for mode in file_modes:
                for evidence in mode.file_check(str(path)):  # type: ignore[misc]
                    _add(evidence)

    total_cs = len(call_sites)
    all_py_files = {str(p) for p in iter_python_files(root)}
    stats = {
        "total_files": len(all_py_files),
        "dirty_files": len(dirty_files & all_py_files),
        "total_call_sites": total_cs,
        "dirty_call_sites": len(dirty_call_sites),
        "skip_ratio": round(1.0 - len(dirty_call_sites) / max(total_cs, 1), 3),
    }
    return violations, stats
