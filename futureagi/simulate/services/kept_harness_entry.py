"""A harness verdict kept under the placeholder of an eval graded again.

When the platform grades again an eval the harness already filled during the
call, the cell reads "pending" until the new verdict lands. The harness's
verdict travels inside that placeholder, so a grading that ends with no
verdict of its own (skipped, failed, never started, timed out) puts it back
instead of leaving the call without the check it had.

Only these functions build or read the kept field.
"""

from __future__ import annotations

from typing import Any

KEPT_KEY = "kept_harness_entry"

_PENDING = "pending"


def pending_placeholder(previous: Any) -> dict:
    """The "pending" cell written over ``previous`` when its eval is graded again.

    A harness verdict is kept inside it, as is one an earlier placeholder
    already keeps, so a second request before the first grading ends does not
    lose it. It is the same object, unedited. Anything else is replaced, as it
    always was: a platform verdict is superseded by the new grading.
    """
    if isinstance(previous, dict):
        if previous.get("source") == "harness":
            return {"status": _PENDING, KEPT_KEY: previous}
        kept = _kept_entry(previous)
        if kept is not None:
            return {"status": _PENDING, KEPT_KEY: kept}
    return {"status": _PENDING}


def end_without_verdict(current: Any, replacement: dict) -> dict:
    """What a writer that ends ``current`` with no fresh verdict stores.

    The harness verdict ``current`` keeps, when it is a placeholder keeping
    one; ``current`` itself, when it is a harness verdict, which a grader the
    platform could not confirm was stopped may still reach after a failed
    dispatch put it back; ``replacement`` otherwise. Every writer calls it on
    whatever the column holds, so it never raises and changes neither argument.
    """
    if isinstance(current, dict) and current.get("source") == "harness":
        return current
    kept = _kept_entry(current)
    return replacement if kept is None else kept


def without_kept_entries(outputs: Any) -> Any:
    """``outputs`` as an API shows it: no pending cell carries a kept verdict.

    The kept verdict is the put-back's to read, and a reader of the raw cell
    would take it for a result the call already has. Returns a copy; the
    argument is never edited.
    """
    if not isinstance(outputs, dict):
        return outputs
    return {
        key: (
            {k: v for k, v in cell.items() if k != KEPT_KEY}
            if _kept_entry(cell) is not None
            else cell
        )
        for key, cell in outputs.items()
    }


def _kept_entry(cell: Any) -> dict | None:
    if not isinstance(cell, dict) or cell.get("status") != _PENDING:
        return None
    kept = cell.get(KEPT_KEY)
    return kept if isinstance(kept, dict) else None
