"""Typed, sanitized sampling failures.

Deterministic contract failures subclass ``EvalTaskSelectionRejected`` so the
task activity fails fast with the message below instead of retrying a query
that cannot succeed. Transient read pressure and concurrent writers subclass
``EvalTaskReadBudgetExceeded`` so the bounded retry/deferral policy applies.
No message carries SQL, parameters or attribute values.
"""

from __future__ import annotations

import re

from tracer.selectors.eval_tasks.row_resolver import (
    EvalTaskReadBudgetExceeded,
    EvalTaskSelectionRejected,
)

OVERSIZED_UNIT_MARKER = "EVAL_SAMPLING_OVERSIZED_UNIT"
OVERSIZED_GROUP_MARKER = "EVAL_SAMPLING_OVERSIZED_GROUP_"
MAX_UNIT_ID_BYTES = 2048
MAX_GROUP_VALUE_BYTES = 1024
# ClickHouse error codes: TOO_MANY_ROWS, TOO_MANY_BYTES, TOO_MANY_ROWS_OR_BYTES.
READ_CEILING_CODES = frozenset({158, 307, 396})
# TIMEOUT_EXCEEDED, TOO_SLOW, MEMORY_LIMIT_EXCEEDED.
READ_PRESSURE_CODES = frozenset({159, 160, 241})


class SamplingFilterRejected(EvalTaskSelectionRejected):
    """Task filters cannot be compiled into a sampling population."""


class SamplingValueTooLarge(EvalTaskSelectionRejected):
    """A unit identity or group-by value exceeds its documented byte bound."""


class SamplingReadCeilingExceeded(EvalTaskSelectionRejected):
    """A sampling read would exceed its configured row/byte ceiling."""


class SamplingProofBoundExceeded(SamplingReadCeilingExceeded):
    """A live first-admission proof exceeds its configured bound."""


class SamplingReadPressure(EvalTaskReadBudgetExceeded):
    """A bounded read timed out or hit its memory budget; retry later."""


class SamplingReconcileBusy(EvalTaskReadBudgetExceeded):
    """Another reconcile holds this cohort's lease."""


class SamplingLeaseLost(EvalTaskReadBudgetExceeded):
    """This reconcile's lease or task revision changed; nothing more is written."""


def oversized_message(policy: dict | None, text: str) -> str | None:
    """Name the bound a query rejected, using only the policy's own keys."""

    if OVERSIZED_UNIT_MARKER in text:
        unit = (policy or {}).get("sampling_unit", "sampling")
        detail = ""
        if unit == "attribute":
            key = ((policy or {}).get("unit_attribute") or {}).get("key", "")
            detail = f" (contact attribute {key!r})"
        return (
            f"A {unit} sampling unit ID{detail} exceeds {MAX_UNIT_ID_BYTES} bytes "
            "in this window. Exclude those rows with a task filter or choose "
            "another sampling unit."
        )
    match = re.search(re.escape(OVERSIZED_GROUP_MARKER) + r"(\d+)", text)
    if match is None:
        return None
    index = int(match.group(1))
    groups = (policy or {}).get("group_by") or []
    key = groups[index]["key"] if index < len(groups) else f"#{index + 1}"
    return (
        f"Group-by attribute {key!r} has a value longer than "
        f"{MAX_GROUP_VALUE_BYTES} bytes in this window. Remove it from group-by "
        "or exclude those rows with a task filter."
    )


def classify_read_error(
    exc: BaseException, *, policy: dict | None = None, proof: bool = False
) -> Exception | None:
    """Map a driver failure to a typed sampling error, or ``None`` to re-raise."""

    if isinstance(exc, EvalTaskReadBudgetExceeded):
        return None
    text = str(exc)
    message = oversized_message(policy, text)
    if message is not None:
        return SamplingValueTooLarge(message)
    code = getattr(exc, "code", None)
    if code in READ_CEILING_CODES:
        if proof:
            return SamplingProofBoundExceeded(
                "The live first-admission proof for new contacts exceeds its read "
                "ceiling. Start a new live policy with a later start or narrow the "
                "task filters; no partial decision was saved."
            )
        return SamplingReadCeilingExceeded(
            "This sampling read exceeds its row/byte ceiling. Narrow the window, "
            "task filters or group-by attributes."
        )
    if code in READ_PRESSURE_CODES or isinstance(exc, TimeoutError):
        return SamplingReadPressure(
            "The sampling read exceeded its time or memory budget. Retry later or "
            "narrow the window."
        )
    return None
