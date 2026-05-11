"""
Main orchestration: parse → graph → FailureMode registry → Z3 → violations.
"""

from pathlib import Path
from typing import Optional

from .encoder import Violation
from .extractor import FunctionManifest, ModelManifest, extract_from_codebase
from .failure_mode import DEFAULT_MODES, FailureEvidence, FailureMode


def _to_violation(e: FailureEvidence) -> Violation:
    return Violation(
        file=e.file,
        line=e.line,
        call=e.call,
        missing=e.missing if e.missing else [e.message],
        context=e.mode_name,
    )


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

    # Per-call-site checks
    for call in call_sites:
        for mode in modes:
            for evidence in mode.check(call, model_index, func_index):
                _add(evidence)

    # File-level checks — run on every file the extractor touched, so modes
    # that scan for definitions (not calls) catch files with zero call sites.
    file_modes = [m for m in modes if m.file_check is not None]
    if file_modes:
        all_files: set[str] = set()
        all_files.update(c.file for c in call_sites)
        all_files.update(m.file for m in models)
        all_files.update(f.file for f in functions)
        for path in all_files:
            for mode in file_modes:
                for evidence in mode.file_check(path):  # type: ignore[misc]
                    _add(evidence)

    return violations
