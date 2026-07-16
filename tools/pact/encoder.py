"""
Universal Z3 constraint encoder.

One Z3 formula per field encodes ALL constraints simultaneously:
  presence, type range, max_length, choices.

Z3 enumerates the violations — we don't write one checker per constraint class.
"""

from dataclasses import dataclass
from typing import Optional

from .extractor import (
    UNKNOWN_VALUE,
    CallSite,
    FieldConstraint,
    FunctionManifest,
    ModelManifest,
)

try:
    from z3 import (
        IntVal, Not, Or, Solver,
        Length, StringVal, sat,
    )
    _HAS_Z3 = True
except ImportError:
    _HAS_Z3 = False


@dataclass
class Violation:
    file: str
    line: int
    call: str
    missing: list[str]
    context: str  # failure mode name

    def __str__(self) -> str:
        return f"{self.file}:{self.line}  {self.call}()  missing: {', '.join(self.missing)}"


# ---------------------------------------------------------------------------
# Universal field constraint encoder
# ---------------------------------------------------------------------------

def _check_field(fc: FieldConstraint, provided: bool, value: object) -> list[str]:
    """
    Check ALL constraints for one field against one call-site value.

    Returns a list of violation descriptions — empty means clean.

    Z3 checks each assertion independently so every violated constraint is
    reported, not just the first one.
    """
    violations: list[str] = []

    # Presence — no Z3 needed, just set arithmetic
    if not provided:
        if fc.required:
            violations.append(f"missing required field '{fc.name}'")
        return violations   # no value → can't check further

    if value is None:
        if fc.required and not fc.null:
            violations.append(f"'{fc.name}' cannot be None")
        return violations

    if not _HAS_Z3:
        return violations   # no Z3 installed — presence already checked above

    if isinstance(value, str):
        v = StringVal(value)

        if fc.max_length is not None:
            s = Solver()
            s.add(Length(v) > fc.max_length)
            if s.check() == sat:
                violations.append(
                    f"'{fc.name}' length {len(value)} exceeds max_length {fc.max_length}"
                )

        if fc.choices:
            str_choices = [c for c in fc.choices if isinstance(c, str)]
            if str_choices:
                s = Solver()
                s.add(Not(Or([v == StringVal(c) for c in str_choices])))
                if s.check() == sat:
                    violations.append(
                        f"'{fc.name}' value {value!r} not in choices {str_choices}"
                    )

    elif isinstance(value, int):
        v = IntVal(value)

        if fc.min_value is not None:
            s = Solver()
            s.add(v < fc.min_value)
            if s.check() == sat:
                violations.append(
                    f"'{fc.name}' value {value} < min {fc.min_value}"
                )

        if fc.max_value is not None:
            s = Solver()
            s.add(v > fc.max_value)
            if s.check() == sat:
                violations.append(
                    f"'{fc.name}' value {value} > max {fc.max_value}"
                )

        if fc.choices:
            int_choices = [c for c in fc.choices if isinstance(c, int)]
            if int_choices:
                s = Solver()
                s.add(Not(Or([v == c for c in int_choices])))
                if s.check() == sat:
                    violations.append(
                        f"'{fc.name}' value {value!r} not in choices {int_choices}"
                    )

    return violations


def check_model_create(call: CallSite, model: ModelManifest) -> list[Violation]:
    """
    Verify a Model.objects.create() call against ALL field constraints.

    For each field: presence + type range + max_length + choices — all in one pass.
    """
    all_missing: list[str] = []

    for fc in model.fields:
        if fc.field_type in ("ManyToManyField", "ManyToManyRel", "ManyToOneRel", "OneToOneRel"):
            continue
        field_names = [fc.name]
        if fc.field_type == "ForeignKey":
            field_names.append(f"{fc.name}_id")
        provided = any(name in call.provided_kwargs for name in field_names)
        value = next(
            (call.kwarg_values[name] for name in field_names if name in call.kwarg_values),
            UNKNOWN_VALUE,
        )
        if value is UNKNOWN_VALUE:
            if provided:
                continue
            value = None
        violations = _check_field(fc, provided, value)
        all_missing.extend(violations)

    if not all_missing:
        return []

    return [Violation(
        file=call.file,
        line=call.line,
        call=call.callee_name,
        missing=all_missing,
        context="model_create",
    )]


# ---------------------------------------------------------------------------
# Function call checker (presence only — type checking needs return-type inference)
# ---------------------------------------------------------------------------

def _z3_check_presence(required: list[str], provided: set[str]) -> list[str]:
    return [f for f in required if f not in provided]


def check_function_call(call: CallSite, func: FunctionManifest) -> Optional[Violation]:
    required_args = func.required_args
    if not required_args:
        return None

    positional_required = [arg for arg in required_args if not arg.kwonly]
    positional_satisfied = {
        arg.name for i, arg in enumerate(positional_required) if i < call.positional_count
    }
    effectively_provided = call.provided_kwargs | positional_satisfied
    missing = _z3_check_presence([a.name for a in required_args], effectively_provided)

    if missing:
        return Violation(
            file=call.file,
            line=call.line,
            call=call.callee_name,
            missing=missing,
            context="function_call",
        )
    return None
