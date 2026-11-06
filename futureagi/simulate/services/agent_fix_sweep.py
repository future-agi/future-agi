"""End "Fix my agent" runs that nothing drives any more.

A fix's workflow ends it in every ordinary case: at its ending, at its deadline, when its
engine keeps dying, or when the user stops it. This sweep catches the rest, a workflow
that never started or one that died without closing its fix, so no fix stays active and
holds its environment's one fix slot.
"""

from datetime import datetime, timedelta

import structlog
from django.conf import settings
from django.utils import timezone

from simulate.models import AgentFixRun
from simulate.selectors.agent_fix import fix_deadline
from simulate.services.agent_fix_runs import close_fix_run
from simulate.services.agent_fix_sandbox import release
from simulate.temporal.client import agent_fix_workflow_running

logger = structlog.get_logger(__name__)

# A new fix's workflow starts once its row commits; give that time to happen.
START_GRACE = timedelta(minutes=5)
_BATCH_LIMIT = 100

_NOT_SCHEDULED = {
    "code": "fix_dispatch_failed",
    "message": "the fix could not be scheduled; try again",
}
_LOST = {
    "code": "fix_workflow_lost",
    "message": "the platform stopped tracking this fix; start it again",
}
_LATE = {
    "code": "fix_deadline",
    "message": "the fix ran out of the time it was given",
}

Closing = tuple[AgentFixRun.State, dict[str, str] | None]


def sweep_lost_fix_runs() -> dict[str, int]:
    """Close every active fix whose workflow is gone, or that outlived its deadline."""
    now = timezone.now()
    fix_runs = AgentFixRun.no_workspace_objects.filter(
        state__in=AgentFixRun.ACTIVE_STATES, created_at__lte=now - START_GRACE
    ).order_by("created_at")[:_BATCH_LIMIT]
    counts = {"checked": 0, "closed": 0}
    for fix_run in fix_runs:
        counts["checked"] += 1
        # One fix the sweep cannot settle (Temporal or the sandbox API refusing) must
        # not keep it from the others; the next pass tries that one again.
        try:
            ending = _ending(fix_run, now)
            if ending is None:
                continue
            state, failure = ending
            close_fix_run(fix_run.id, state, failure=failure)
            counts["closed"] += 1
            logger.warning(
                "agent_fix_run_swept",
                fix_run=str(fix_run.id),
                state=state,
                code=(failure or {}).get("code"),
            )
            release(str(fix_run.id))
        except Exception:
            logger.exception("agent_fix_sweep_failed", fix_run=str(fix_run.id))
    return counts


def _ending(fix_run: AgentFixRun, now: datetime) -> Closing | None:
    """How a fix that nothing drives ends, or None while its workflow still drives it."""
    grace = timedelta(seconds=settings.AGENT_FIX_TOKEN_GRACE_SECONDS)
    # Past its deadline and the engine's grace, a fix is over even when a stuck
    # workflow still counts as running.
    overdue = now >= fix_deadline(fix_run) + grace
    if not overdue and agent_fix_workflow_running(str(fix_run.id)):
        return None
    if fix_run.state == AgentFixRun.State.STOPPING:
        return AgentFixRun.State.STOPPED, None
    if fix_run.state == AgentFixRun.State.QUEUED:
        return AgentFixRun.State.FAILED, _NOT_SCHEDULED
    return AgentFixRun.State.FAILED, _LATE if overdue else _LOST
