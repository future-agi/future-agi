"""
Main orchestration: parse → graph → FailureMode registry → Z3 → violations.
"""

from pathlib import Path
from typing import Optional

from .encoder import Violation
from .extractor import FunctionManifest, ModelManifest, extract_from_codebase
from .failure_mode import DEFAULT_MODES, FailureEvidence, FailureMode
from .graph import build_call_graph


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
    """
    if modes is None:
        modes = DEFAULT_MODES

    models, functions, call_sites = extract_from_codebase(root)

    model_index: dict[str, ModelManifest] = {m.name: m for m in models}
    func_index: dict[str, FunctionManifest] = {f.name: f for f in functions}

    build_call_graph(functions, call_sites)

    violations: list[Violation] = []
    for call in call_sites:
        for mode in modes:
            for evidence in mode.check(call, model_index, func_index):
                violations.append(_to_violation(evidence))

    return violations
