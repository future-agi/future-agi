"""
FailureMode — declarative constraint objects for pact.

A FailureMode defines a class of bug: what facts trigger it, what Z3 asserts
about those facts, and what message to show when violated.

This is the plugin layer. New constraint classes are new FailureMode instances —
no code changes to the checker. The LLM authors FailureModes; Z3 verifies them.

Inspired by ~/src/z3_spelunking/formal_failure_analysis.py.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Callable

from .encoder import check_model_create
from .extractor import CallSite, FunctionManifest, ModelManifest


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
            file=v.file,
            line=v.line,
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

_OPTIONAL_SOURCES = frozenset(
    {
        "first",
        "last",
        "get_or_none",
        "filter().first",
        "get",
        "environ.get",
        "os.environ.get",
    }
)


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

        def _visit_scope(self, node):
            outer_optional = self.optional_vars
            outer_guarded = self.guarded
            self.optional_vars = {}
            self.guarded = set()
            for child in node.body:
                self.visit(child)
            self.optional_vars = outer_optional
            self.guarded = outer_guarded

        def visit_FunctionDef(self, node):
            self._visit_scope(node)

        def visit_AsyncFunctionDef(self, node):
            self._visit_scope(node)

        def visit_Assign(self, node):
            targets = [target for target in node.targets if isinstance(target, _ast.Name)]
            for target in targets:
                self.guarded.discard(target.id)
                self.optional_vars.pop(target.id, None)
                if (
                    isinstance(node.value, _ast.Call)
                    and isinstance(node.value.func, _ast.Attribute)
                    and node.value.func.attr in _OPTIONAL_RETURNING
                ):
                    self.optional_vars[target.id] = node.lineno
            self.generic_visit(node)

        def visit_If(self, node):
            src = _ast.unparse(node.test) if hasattr(_ast, "unparse") else ""
            guarded_here = {var for var in self.optional_vars if var in src}
            original_guarded = self.guarded
            self.visit(node.test)
            self.guarded = original_guarded | guarded_here
            for child in node.body:
                self.visit(child)
            self.guarded = original_guarded
            for child in node.orelse:
                self.visit(child)

        def visit_Attribute(self, node):
            # var.something — flag if var is unguarded optional
            if (
                isinstance(node.value, _ast.Name)
                and node.value.id in self.optional_vars
                and node.value.id not in self.guarded
            ):
                var = node.value.id
                assign_line = self.optional_vars[var]
                evidence.append(
                    FailureEvidence(
                        mode_name="optional_dereference",
                        file=path,
                        line=node.lineno,
                        call=f"{var}.{node.attr}",
                        message=(
                            f"'{var}' assigned from optional source at line {assign_line} "
                            f"but used without None check"
                        ),
                    )
                )
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
    func = functions.get(f"{call.file}:{call.callee_name}") or functions.get(
        call.callee_name
    )
    if not func or not func.required_args:
        return []
    # Only non-kwonly required args can be covered by positional args.
    # Enumerate positional-only required args separately so a kwonly arg at
    # index i is never falsely marked covered because positional_count > i.
    positional_required = [a for a in func.required_args if not a.kwonly]
    positional_covered = {
        arg.name
        for i, arg in enumerate(positional_required)
        if i < call.positional_count
    }
    provided = call.provided_kwargs | positional_covered
    missing = _z3_missing([a.name for a in func.required_args], provided)
    if missing:
        return [
            FailureEvidence(
                mode_name="required_arg_missing",
                file=call.file,
                line=call.line,
                call=call.callee_name,
                message=f"missing required arg(s): {', '.join(missing)}",
                missing=missing,
            )
        ]
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


@functools.lru_cache(maxsize=None)
def _scan_file_bare_except(path: str) -> list[FailureEvidence]:
    """File-level scan for bare except: and silent except Exception: pass."""
    import ast as _ast
    from pathlib import Path as _Path

    try:
        source = _Path(path).read_text(encoding="utf-8", errors="replace")
        tree = _ast.parse(source, filename=path)
    except (SyntaxError, OSError):
        return []

    evidence = []
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.ExceptHandler):
            continue
        if node.type is None:
            # bare `except:` — catches KeyboardInterrupt, SystemExit, everything
            evidence.append(
                FailureEvidence(
                    mode_name="bare_except",
                    file=path,
                    line=node.lineno,
                    call="except:",
                    message="bare `except:` catches all exceptions including KeyboardInterrupt",
                )
            )
        elif isinstance(node.type, _ast.Name) and node.type.id == "Exception":
            # `except Exception: pass` or `except Exception: ...` — silent swallow
            body = node.body
            is_silent = len(body) == 1 and (
                isinstance(body[0], _ast.Pass)
                or (
                    isinstance(body[0], _ast.Expr)
                    and isinstance(body[0].value, _ast.Constant)
                    and body[0].value.value is ...
                )
            )
            if is_silent:
                evidence.append(
                    FailureEvidence(
                        mode_name="bare_except",
                        file=path,
                        line=node.lineno,
                        call="except Exception: pass",
                        message="`except Exception: pass` silently swallows all errors",
                    )
                )
    return evidence


def _check_bare_except(
    call: CallSite,
    models: dict[str, ModelManifest],
    functions: dict[str, FunctionManifest],
) -> list[FailureEvidence]:
    return _scan_file_bare_except(call.file)


BARE_EXCEPT = FailureMode(
    name="bare_except",
    description=(
        "Bare `except:` or `except Exception: pass` silently swallows all errors. "
        "Makes bugs invisible."
    ),
    check=_check_bare_except,
)


# --- 5. save() without update_fields ---------------------------------------
# Django model .save() without update_fields re-writes every column,
# clobbering concurrent partial updates.

_SAFE_SAVE_RECEIVER_KINDS = frozenset({"form", "serializer", "fs", "storage", "file"})


def _check_save_without_update_fields(
    call: CallSite,
    models: dict[str, ModelManifest],
    functions: dict[str, FunctionManifest],
) -> list[FailureEvidence]:
    if not call.callee_name.endswith(".save"):
        return []
    if "update_fields" in call.provided_kwargs:
        return []
    # Skip form/serializer/storage saves — intentional full saves.
    # Match on snake_case receiver kind so `image_dataset_serializer` is safe
    # but `profile.save()` is not misclassified as a file save.
    receiver = call.callee_name.rsplit(".", 1)[0].split(".")[-1].lower()
    if receiver.rsplit("_", 1)[-1] in _SAFE_SAVE_RECEIVER_KINDS:
        return []
    return [
        FailureEvidence(
            mode_name="save_without_update_fields",
            file=call.file,
            line=call.line,
            call=call.callee_name,
            message=(
                ".save() without update_fields re-writes every column; "
                "use save(update_fields=[...]) to prevent clobbering concurrent writes"
            ),
        )
    ]


SAVE_WITHOUT_UPDATE_FIELDS = FailureMode(
    name="save_without_update_fields",
    description=(
        "Django model .save() called without update_fields. "
        "Re-writes every column, clobbering concurrent partial updates."
    ),
    check=_check_save_without_update_fields,
)


# ---------------------------------------------------------------------------
# Registry — all active failure modes
# ---------------------------------------------------------------------------

DEFAULT_MODES: list[FailureMode] = [
    REQUIRED_FIELD_MISSING,
    REQUIRED_ARG_MISSING,
    OPTIONAL_DEREF,
    BARE_EXCEPT,
    SAVE_WITHOUT_UPDATE_FIELDS,
]
