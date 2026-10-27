"""Shared resolution of a non-terminal / skipped eval cell state.

The span / trace / voice-call eval pivots share one precedence for a
``(row, eval_config)`` pair that has *no completed result and no error*:
``skipped > running > pending``. The completed (score) and errored states are
resolved by each caller (they own the score/error shapes); this helper only
decides the lifecycle marker when neither applies, from the per-status counts
the eval query selects.
"""

from __future__ import annotations

from typing import Any


def non_terminal_eval_marker(row: dict[str, Any]) -> dict[str, Any] | None:
    """Cell marker for a (row, config) pair with no completed score and no error.

    Returns ``{"status": "skipped"|"running"|"pending"}`` (with ``skipped_reason``
    when present) per the precedence ``skipped > running > pending``, or ``None``
    when no eval has run for the pair (renders blank — "no eval run").
    """
    if (row.get("skipped_count", 0) or 0) > 0:
        marker: dict[str, Any] = {"status": "skipped"}
        reason = row.get("skipped_reason")
        if reason:
            marker["skipped_reason"] = reason
        return marker
    if (row.get("running_count", 0) or 0) > 0:
        return {"status": "running"}
    if (row.get("pending_count", 0) or 0) > 0:
        return {"status": "pending"}
    return None
