"""Which rerun action an edit is allowed to use, given what changed.

Every edit is either "Edit & rerun" (incremental reconcile, reuse valid
completed results) or "Delete & rerun" (wipe live entries, redo everything).
Which of the two is offered depends on what the edit touched:

- evals only, or rows only -> both allowed (UI defaults to Edit & rerun)
- evals AND rows together   -> Delete & rerun only (a partial delta across both
  axes at once is surprising on a large edit, so force a clean full re-run)
- historical -> continuous  -> Edit & rerun only (Delete would discard still-valid
  history for no reason)
- continuous -> historical  -> both allowed (the new window also needs a row limit)
- metadata only (e.g. name) -> no reprocessing; either action is a harmless no-op

The reconciler is idempotent, so this is a guard against offering the wrong
button, not a correctness requirement of the engine.
"""

from __future__ import annotations

from tracer.models.eval_task import RunType

FRESH_RUN = "fresh_run"  # Delete & rerun
EDIT_RERUN = "edit_rerun"  # Edit & rerun


def validate_edit_action(
    edit_type: str,
    *,
    original_run_type: str,
    new_run_type: str | None,
    evals_changed: bool,
    rows_changed: bool,
) -> str | None:
    """Return an error message if ``edit_type`` isn't allowed for what changed,
    or ``None`` if it is."""
    switching = new_run_type is not None and new_run_type != original_run_type
    if switching:
        if (
            original_run_type == RunType.HISTORICAL
            and new_run_type == RunType.CONTINUOUS
            and edit_type == FRESH_RUN
        ):
            return (
                "Delete & rerun isn't available when switching a historical task "
                "to continuous — it would discard valid history. Use Edit & rerun."
            )
        return None

    if evals_changed and rows_changed and edit_type == EDIT_RERUN:
        return (
            "Changing evaluations and rows together requires Delete & rerun; "
            "Edit & rerun isn't offered for this combination."
        )
    return None
