"""End "Fix my agent" runs that nothing drives any more.

A fix's workflow ends it in every ordinary case: at its ending, at its deadline, when its
engine keeps dying, or when the user stops it. This sweep catches the rest, a workflow
that never started or one that died without closing its fix, so no fix stays active and
holds its environment's one fix slot. It also finishes a closed fix's cleanup: the
dataset rows its variations run added, which the close drops but fails open on.
"""

from datetime import datetime, timedelta

import structlog
from django.conf import settings
from django.db.models import Exists, OuterRef
from django.utils import timezone

from simulate.models import AgentFixRun
from simulate.models.harness_versioning import VersionOrigin
from simulate.selectors.agent_fix import fix_deadline, queue_deadline
from simulate.services.agent_fix_runs import close_fix_run, drop_variation_rows
from simulate.services.agent_fix_sandbox import release
from simulate.temporal.client import agent_fix_workflow_running

logger = structlog.get_logger(__name__)

# A new fix's workflow starts once its row commits; give that time to happen.
START_GRACE = timedelta(minutes=5)
_BATCH_LIMIT = 100
# How long a closed fix's variation rows are retried; its close drops them as a rule.
CLEANUP_WINDOW = timedelta(days=7)

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
    try:
        counts["variation_rows_left"] = sweep_variation_rows(now)
    except Exception:
        logger.exception("agent_fix_variation_rows_sweep_failed")
    return counts


def sweep_variation_rows(now: datetime) -> int:
    """Drop the dataset rows a closed fix's variations run left bound, for each recent fix
    that is the newest on its private suite (a retry runs the same variations and owns
    them). The number of fixes whose rows are still bound."""
    newer = AgentFixRun.no_workspace_objects.filter(
        suite_version_id=OuterRef("suite_version_id"),
        created_at__gt=OuterRef("created_at"),
    )
    fix_runs = (
        AgentFixRun.no_workspace_objects.exclude(state__in=AgentFixRun.ACTIVE_STATES)
        .filter(
            suite_version__origin=VersionOrigin.FIX,
            finished_at__gte=now - CLEANUP_WINDOW,
        )
        .exclude(Exists(newer))
        .select_related("suite_version", "environment")
        .order_by("-finished_at")[:_BATCH_LIMIT]
    )
    return sum(1 for fix_run in fix_runs if not drop_variation_rows(fix_run))


def _ending(fix_run: AgentFixRun, now: datetime) -> Closing | None:
    """How a fix that nothing drives ends, or None while its workflow still drives it."""
    grace = timedelta(seconds=settings.AGENT_FIX_TOKEN_GRACE_SECONDS)
    # Past its deadline and the engine's grace, a fix is over even when a stuck
    # workflow still counts as running. A queued fix's deadline is the end of its wait.
    queued = fix_run.state == AgentFixRun.State.QUEUED
    deadline = queue_deadline(fix_run) if queued else fix_deadline(fix_run)
    overdue = now >= deadline + grace
    if not overdue and agent_fix_workflow_running(str(fix_run.id)):
        return None
    if fix_run.state == AgentFixRun.State.STOPPING:
        return AgentFixRun.State.STOPPED, None
    if fix_run.state == AgentFixRun.State.QUEUED:
        return AgentFixRun.State.FAILED, _NOT_SCHEDULED
    return AgentFixRun.State.FAILED, _LATE if overdue else _LOST
