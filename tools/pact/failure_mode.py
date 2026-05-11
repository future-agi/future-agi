"""
FailureMode — declarative constraint objects for pact.

A FailureMode defines a class of bug: what facts trigger it, what Z3 asserts
about those facts, and what message to show when violated.

This is the plugin layer. New constraint classes are new FailureMode instances —
no code changes to the checker. The LLM authors FailureModes; Z3 verifies them.

Inspired by ~/src/z3_spelunking/formal_failure_analysis.py.
"""

from __future__ import annotations

import ast as pyast
import functools
from dataclasses import dataclass, field
from typing import Callable, Optional

try:
    from z3 import BoolVal, Solver, sat, unsat, Bool, And, Or, Not, Implies
    _HAS_Z3 = True
except ImportError:
    _HAS_Z3 = False

from .encoder import check_model_create
from .extractor import CallSite, FieldConstraint, FunctionManifest, ModelManifest


@dataclass
class FailureEvidence:
    """Concrete evidence of a FailureMode violation at a specific site."""
    mode_name: str
    file: str
    line: int
    call: str
    message: str
    missing: list[str] = field(default_factory=list)
    context: str = "failure_mode"

    def __str__(self) -> str:
        return f"{self.file}:{self.line}  [{self.mode_name}]  {self.call}  — {self.message}"


# ---------------------------------------------------------------------------
# The FailureMode type
# ---------------------------------------------------------------------------

@dataclass
class FailureMode:
    """
    Declarative specification of a constraint class.

    Parameters
    ----------
    name:
        Short identifier, e.g. "required_field_missing".
    description:
        Human-readable explanation of what this catches.
    check:
        Callable(call_site, models, functions) → list[FailureEvidence].
        Pure function — no side effects. Called once per call site.
    """
    name: str
    description: str
    check: Callable[
        [CallSite, dict[str, ModelManifest], dict[str, FunctionManifest]],
        list[FailureEvidence],
    ]


# ---------------------------------------------------------------------------
# Built-in FailureModes
# (these replace the hardcoded checks in encoder.py — encoder.py stays for
#  direct use; failure_mode.py is the extensible plugin layer on top)
# ---------------------------------------------------------------------------

def _z3_missing(required: list[str], provided: set[str]) -> list[str]:
    return [f for f in required if f not in provided]


# --- 1. Universal model constraint check (presence + range + choices + max_length) ---

def _check_model_constraints(
    call: CallSite,
    models: dict[str, ModelManifest],
    functions: dict[str, FunctionManifest],
) -> list[FailureEvidence]:
    if not call.is_create_call or not call.model_name:
        return []
    model = models.get(call.model_name)
    if not model:
        return []
    violations = check_model_create(call, model)
    return [
        FailureEvidence(
            mode_name="model_constraint",
            file=v.file, line=v.line,
            call=v.call,
            message="; ".join(v.missing),
            missing=v.missing,
        )
        for v in violations
    ]


REQUIRED_FIELD_MISSING = FailureMode(
    name="model_constraint",
    description=(
        "Model.objects.create() violates one or more field constraints: "
        "presence, choices, max_length, integer range."
    ),
    check=_check_model_constraints,
)


# --- 2. Optional dereference — x.attr where x may be None -----------------
# Extracted from the AST: detects `x.something` where x is assigned from a
# call that returns Optional (common Django patterns: .first(), .get_or_none(),
# dict.get(), os.environ.get()).

_OPTIONAL_SOURCES = frozenset({
    "first", "last", "get_or_none", "filter().first",
    "get", "environ.get", "os.environ.get",
})


_OPTIONAL_RETURNING = frozenset({"first", "last", "get_or_none", "one_or_none"})
_SAFE_CHECKS = frozenset({"is None", "is not None", "if not", "if "})

@functools.lru_cache(maxsize=None)
def _scan_file_optional_deref(path: str) -> list[FailureEvidence]:
    """
    File-level scan: find variables assigned from .first()/.last() etc
    that are then attribute-accessed without a None guard in between.
    Uses the AST control flow visitor from ast_z3_analysis lineage.
    """
    import ast as _ast
    from pathlib import Path as _Path

    try:
        source = _Path(path).read_text(encoding="utf-8", errors="replace")
        tree = _ast.parse(source, filename=path)
    except (SyntaxError, OSError):
        return []

    evidence = []

    class _Visitor(_ast.NodeVisitor):
        def __init__(self):
            # var_name -> line where it was assigned from optional source
            self.optional_vars: dict[str, int] = {}
            self.guarded: set[str] = set()

        def visit_Assign(self, node):
            if (len(node.targets) == 1 and
                    isinstance(node.targets[0], _ast.Name) and
                    isinstance(node.value, _ast.Call) and
                    isinstance(node.value.func, _ast.Attribute) and
                    node.value.func.attr in _OPTIONAL_RETURNING):
                self.optional_vars[node.targets[0].id] = node.lineno
            self.generic_visit(node)

        def visit_If(self, node):
            # If the test references an optional var, mark it guarded
            src = _ast.unparse(node.test) if hasattr(_ast, "unparse") else ""
            for var in list(self.optional_vars):
                if var in src:
                    self.guarded.add(var)
            self.generic_visit(node)

        def visit_Attribute(self, node):
            # var.something — flag if var is unguarded optional
            if (isinstance(node.value, _ast.Name) and
                    node.value.id in self.optional_vars and
                    node.value.id not in self.guarded):
                var = node.value.id
                assign_line = self.optional_vars[var]
                evidence.append(FailureEvidence(
                    mode_name="optional_dereference",
                    file=path,
                    line=node.lineno,
                    call=f"{var}.{node.attr}",
                    message=(
                        f"'{var}' assigned from optional source at line {assign_line} "
                        f"but used without None check"
                    ),
                ))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return evidence


def _check_optional_deref(
    call: CallSite,
    models: dict[str, ModelManifest],
    functions: dict[str, FunctionManifest],
) -> list[FailureEvidence]:
    return _scan_file_optional_deref(call.file)


OPTIONAL_DEREF = FailureMode(
    name="optional_dereference",
    description=(
        "Attribute access on a value that may be None (e.g. from .first(), "
        "dict.get(), os.environ.get()). Will raise AttributeError at runtime."
    ),
    check=_check_optional_deref,
)


# --- 3. Missing required function argument ---------------------------------

def _check_required_arg(
    call: CallSite,
    models: dict[str, ModelManifest],
    functions: dict[str, FunctionManifest],
) -> list[FailureEvidence]:
    if call.is_create_call:
        return []
    func = functions.get(call.callee_name)
    if not func or not func.required_args:
        return []
    positional_covered = {
        arg.name for i, arg in enumerate(func.required_args)
        if i < call.positional_count
    }
    provided = call.provided_kwargs | positional_covered
    missing = _z3_missing([a.name for a in func.required_args], provided)
    if missing:
        return [FailureEvidence(
            mode_name="required_arg_missing",
            file=call.file, line=call.line,
            call=call.callee_name,
            message=f"missing required arg(s): {', '.join(missing)}",
            missing=missing,
        )]
    return []


REQUIRED_ARG_MISSING = FailureMode(
    name="required_arg_missing",
    description=(
        "Function called without all required positional arguments. "
        "Will raise TypeError at runtime."
    ),
    check=_check_required_arg,
)


# --- 4. Bare except that swallows all exceptions ---------------------------
# Detects `except:` or `except Exception: pass` — silent failure patterns.

def _check_bare_except(
    call: CallSite,
    models: dict[str, ModelManifest],
    functions: dict[str, FunctionManifest],
) -> list[FailureEvidence]:
    return []  # file-level scan, not call-site; handled in flow_scanner.py


BARE_EXCEPT = FailureMode(
    name="bare_except",
    description=(
        "Bare `except:` or `except Exception: pass` silently swallows all errors. "
        "Makes bugs invisible."
    ),
    check=_check_bare_except,
)


# ---------------------------------------------------------------------------
# Registry — all active failure modes
# ---------------------------------------------------------------------------

DEFAULT_MODES: list[FailureMode] = [
    REQUIRED_FIELD_MISSING,
    REQUIRED_ARG_MISSING,
    OPTIONAL_DEREF,
]
