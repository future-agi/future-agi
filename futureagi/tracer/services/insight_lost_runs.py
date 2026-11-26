"""Report runs whose workflow is gone, as after a Temporal restart: found and
failed once, so the report offers Run again instead of running forever. Read
by the report views and swept by the Insights worker."""

from datetime import timedelta

import structlog
from django.core.cache import cache
from django.utils import timezone

from tracer.models.insights import InsightRun

logger = structlog.get_logger(__name__)

# A run in flight this quiet has its workflow looked up (at most every
# LOST_CHECK_TTL seconds): one whose workflow is gone, as after a Temporal
# restart, stopped before it finished and would otherwise run forever.
LOST_AFTER = timedelta(minutes=2)
LOST_CHECK_TTL = 20
LOST_ERROR = "This report stopped before it finished. Run it again."


def _workflow_lost(run) -> bool:
    key = f"insight-wf-lost:{run.id}"
    known = cache.get(key)
    if known is not None:
        return known
    from tfc.temporal.eval_tasks.client import WF_ABSENT, WF_CLOSED
    from tfc.temporal.insights import client

    try:
        lost = client.describe_insight_run_sync(
            run.workflow_id or client.workflow_id_for(run.id)
        ) in (WF_ABSENT, WF_CLOSED)
    except Exception:
        # An unreachable Temporal is not an answer: the run is left running.
        logger.warning("insight_describe_failed", run_id=str(run.id))
        lost = False
    cache.set(key, lost, LOST_CHECK_TTL)
    return lost


def settle_lost_runs(runs) -> None:
    """Mark failed, once, the runs still pending or running whose workflow is
    gone, so the report offers Run again. Runs that changed lately aren't
    looked up; the update only takes a run still in flight."""
    now = timezone.now()
    for run in runs:
        if run.status not in ("pending", "running"):
            continue
        if run.updated_at and now - run.updated_at < LOST_AFTER:
            continue
        if not _workflow_lost(run):
            continue
        if InsightRun.no_workspace_objects.filter(
            id=run.id,
            status__in=["pending", "running"],
            deleted=False,
            updated_at__lte=now - LOST_AFTER,
        ).update(status="failed", error=LOST_ERROR, finished_at=now, updated_at=now):
            logger.info("insight_run_lost", run_id=str(run.id), stage=run.stage)
        run.refresh_from_db(fields=["status", "error", "finished_at", "updated_at"])
