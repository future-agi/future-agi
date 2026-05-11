"""
Main orchestration: parse → graph → FailureMode registry → Z3 → violations.
"""

from pathlib import Path
from typing import Optional

from .encoder import Violation
from .extractor import (
    CallSite,
    FunctionManifest,
    ModelManifest,
    extract_from_codebase,
    iter_python_files,
)
from .failure_mode import DEFAULT_MODES, FailureEvidence, FailureMode


def _to_violation(e: FailureEvidence) -> Violation:
    return Violation(
        file=e.file,
        line=e.line,
        call=e.call,
        missing=e.missing,
        context=e.mode_name,
    )


def check_codebase(
    root: Path,
    modes: Optional[list[FailureMode]] = None,
    *,
    _extracted=None,  # (models, functions, call_sites) if already extracted
) -> list[Violation]:
    """
    Run all FailureModes against every call site in the codebase.

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
    scanned_files = {str(path) for path in iter_python_files(root)}
    call_files = {call.file for call in call_sites}
    file_sentinels = [
        CallSite(callee_name="__file__", file=file, line=1)
        for file in sorted(scanned_files - call_files)
    ]

    seen: set[tuple] = set()
    violations: list[Violation] = []
    for call in [*call_sites, *file_sentinels]:
        for mode in modes:
            for evidence in mode.check(call, model_index, func_index):
                key = (evidence.file, evidence.line, evidence.mode_name, evidence.call)
                if key not in seen:
                    seen.add(key)
                    violations.append(_to_violation(evidence))

    return violations
