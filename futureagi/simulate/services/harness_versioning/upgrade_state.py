"""Value-free, organization-scoped views of automatic backfill progress."""

from datetime import timedelta

from django.utils import timezone

from simulate.models import (
    HarnessVersioningBackfillOutcome,
    HarnessVersioningBackfillProgress,
)
from simulate.models.harness_versioning_backfill import BackfillState
from simulate.services.harness_versioning.versions import versioning_enabled

HEARTBEAT_SECONDS = 30
STALE_AFTER = timedelta(minutes=5)
OUTCOME_CODES = frozenset(
    {
        "versioned",
        "appended",
        "default_moved",
        "revision_noted",
        "current",
        "error",
        "changed_during_backfill",
        "skipped_unreadable",
        "not_found",
        "not_environment",
        "no_archive",
        "interrupted",
        "off",
    }
)


def live_attempt(progress, now):
    """A running claim is live only with a recent persisted heartbeat."""
    return bool(
        progress is not None
        and progress.state == BackfillState.RUNNING
        and progress.heartbeat_at is not None
        and progress.heartbeat_at >= now - STALE_AFTER
    )


def _time(value):
    return value.isoformat() if value is not None else None


def _progress_view(progress, now):
    if progress is None:
        return None
    state = (
        progress.state
        if progress.state in BackfillState.values
        else BackfillState.FAILED
    )
    if state == BackfillState.RUNNING and not live_attempt(progress, now):
        state = BackfillState.FAILED
    return {
        "state": state,
        "total": progress.total,
        "processed": progress.processed,
        "succeeded": progress.succeeded,
        "failed": progress.failed,
        "skipped": progress.skipped,
        "heartbeat_at": _time(progress.heartbeat_at),
        "updated_at": _time(progress.updated_at),
    }


def backfill_state(environment):
    """Return only this environment's outcome and its organization's counts.

    This is not the pins-based readiness flag. Reads never persist a state
    transition, and an old worker's missing heartbeat cannot look live forever.
    A run job refers to its environment's outcome, not a separate run backfill.
    """
    empty = {
        "state": BackfillState.NOT_STARTED,
        "outcome_code": None,
        "updated_at": None,
        "progress": None,
    }
    if not versioning_enabled():
        return {**empty, "state": BackfillState.OFF}
    now = timezone.now()
    environment_id = environment.environment_id or environment.id
    organization_id = environment.organization_id
    progress = HarnessVersioningBackfillProgress.objects.filter(
        organization_id=organization_id
    ).first()
    outcome = HarnessVersioningBackfillOutcome.objects.filter(
        environment_id=environment_id,
        environment__organization_id=organization_id,
    ).first()
    result = {**empty, "progress": _progress_view(progress, now)}
    if outcome is None:
        return result
    code = outcome.outcome_code
    # Only the vocabulary is public, never arbitrary stored diagnostic text.
    code = (code if code in OUTCOME_CODES else "error") if code else None
    state = outcome.state
    if state == BackfillState.RUNNING and (
        not live_attempt(progress, now) or outcome.run_token != progress.run_token
    ):
        state, code = BackfillState.FAILED, "interrupted"
    if state not in BackfillState.values:
        state, code = BackfillState.FAILED, "error"
    result.update(
        state=state,
        outcome_code=code,
        updated_at=_time(outcome.updated_at),
    )
    return result
