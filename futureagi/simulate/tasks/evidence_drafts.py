from __future__ import annotations

from django.db import close_old_connections

from tfc.temporal.drop_in import temporal_activity

__all__ = ["forget_evidence_drafting", "sweep_stale_evidence_drafts"]


@temporal_activity(time_limit=300, max_retries=3, queue="default")
def forget_evidence_drafting(evidence_import_id: str) -> str:
    """Delete a settled evidence drafting chat: its sandbox, checkpoints, rows."""
    from simulate.services.evidence_drafts import forget

    close_old_connections()
    try:
        forget(evidence_import_id)
        return "forgotten"
    finally:
        close_old_connections()


# No retry: the next pass picks up whatever this one could not finish.
@temporal_activity(time_limit=300, max_retries=0, queue="default")
def sweep_stale_evidence_drafts() -> dict[str, int]:
    """Settle drafting chats that never finished, and finish any deletion that
    stopped part way, so no customer text outlives its import in a hidden job."""
    from simulate.services.evidence_drafts import sweep

    close_old_connections()
    try:
        return sweep()
    finally:
        close_old_connections()


def schedule_forget(evidence_import_id: str):
    from temporalio.common import WorkflowIDConflictPolicy

    return forget_evidence_drafting.apply_async(
        args=[str(evidence_import_id)],
        task_id=f"evidence-forget-{evidence_import_id}",
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
