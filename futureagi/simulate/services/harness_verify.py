"""Hold back a third of an environment's scenarios from a coding agent, and verify an
agent version on them in a run the user's reads never show."""

from collections.abc import Sequence
from hashlib import sha256
from uuid import UUID

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from simulate.models import HarnessSuiteMember, HostedHarnessJob, TestExecution
from simulate.selectors.held_back import (
    HELD_BACK_METADATA_KEY,
    HeldBack,
    held_back,
    version_verify,
)
from simulate.services.agent_fix_runs import HELD_BACK_SHARE
from simulate.services.harness_agent_versions import advisory_xact_lock
from simulate.services.harness_usage import require_harness_call_usage
from simulate.services.hosted_harness import (
    HostedHarnessError,
    VersionPicks,
    create_selected_harness_run,
)
from simulate.temporal.client import start_hosted_harness_gateway_workflow

logger = structlog.get_logger(__name__)


def pick_held_back(seed: str, keys: Sequence[str]) -> list[str]:
    """About a third of ``keys``, picked by a stable hash of ``seed`` and each key: the
    same suite and seed always hold back the same cases. None of a single case."""
    ranked = sorted(keys, key=lambda key: sha256(f"{seed}:{key}".encode()).hexdigest())
    return sorted(ranked[: round(len(ranked) / HELD_BACK_SHARE)])


def ensure_held_back(environment: HostedHarnessJob) -> HeldBack | None:
    """The environment's held-back cases, chosen from its current suite the first time
    they are needed and kept from then on. ``None`` for an environment without a suite
    version, which has nothing to choose from."""
    if (chosen := held_back(environment)) is not None:
        return chosen
    if environment.suite_version_id is None:
        return None
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=environment.id
        )
        chosen = held_back(locked)
        if chosen is None:
            keys = HarnessSuiteMember.no_workspace_objects.filter(
                suite_version_id=locked.suite_version_id
            ).values_list("scenario_key", flat=True)
            chosen = HeldBack(
                suite_version_id=locked.suite_version_id,
                scenario_keys=tuple(pick_held_back(str(locked.id), list(keys))),
            )
            metadata = dict(locked.payload.get("metadata") or {})
            metadata[HELD_BACK_METADATA_KEY] = {
                "suite_version_id": str(chosen.suite_version_id),
                "scenario_keys": list(chosen.scenario_keys),
            }
            locked.payload = {**locked.payload, "metadata": metadata}
            locked.save(update_fields=["payload", "updated_at"])
    environment.payload = locked.payload
    return chosen


def start_verify(
    environment: HostedHarnessJob, *, agent_version_id: UUID, trials: int
) -> HostedHarnessJob:
    """Run ``agent_version_id`` on the held-back cases, pinned to the suite version they
    were chosen from. A version is verified once: asking again returns that verify,
    whatever its trials or state, so the held-back cases cannot be sampled twice."""
    chosen = ensure_held_back(environment)
    if chosen is None or not chosen.scenario_keys:
        raise HostedHarnessError(
            "held_back_empty",
            "this environment has too few scenarios to hold any back",
            status_code=409,
        )
    if (existing := version_verify(environment, agent_version_id)) is not None:
        return existing
    # Outside the transaction: the check may call the usage service.
    require_harness_call_usage(environment)
    with transaction.atomic():
        # Same-version verifies take turns, so a concurrent one finds the first.
        advisory_xact_lock(f"harness-verify:{environment.id}:{agent_version_id}")
        if (existing := version_verify(environment, agent_version_id)) is not None:
            return existing
        run, created = create_selected_harness_run(
            environment,
            scenario_keys=list(chosen.scenario_keys),
            trials=trials,
            idempotency_key=f"verify:{agent_version_id}",
            picks=VersionPicks(
                agent_version_id=agent_version_id,
                suite_version_id=chosen.suite_version_id,
            ),
            verify=True,
        )
        if created:
            transaction.on_commit(lambda: _dispatch(run))
    return run


def _dispatch(run: HostedHarnessJob) -> None:
    """Start the verify run's workflow; if it cannot start, the run fails at once instead
    of waiting queued for a workflow that will never come."""
    retry = run.payload["retry"]
    try:
        start_hosted_harness_gateway_workflow(
            str(run.id),
            str(settings.HARNESS_PUBLIC_BASE_URL).rstrip("/"),
            retry["max_infrastructure_attempts"],
            retry["initial_backoff_seconds"],
            retry["max_backoff_seconds"],
        )
    except Exception:
        logger.exception("harness_verify_dispatch_failed", run=str(run.id))
        _fail_unscheduled(run)


def _fail_unscheduled(run: HostedHarnessJob) -> None:
    message = "the verify run could not be scheduled"
    now = timezone.now()
    with transaction.atomic():
        marked = HostedHarnessJob.no_workspace_objects.filter(
            id=run.id, state=HostedHarnessJob.State.QUEUED
        ).update(
            state=HostedHarnessJob.State.FAILED,
            current_stage="failed",
            terminal_at=now,
            failure={
                "domain": "infrastructure",
                "stage": "queued",
                "code": "scheduler_unavailable",
                "message": message,
            },
        )
        if marked:
            TestExecution.no_workspace_objects.filter(id=run.test_execution_id).update(
                status=TestExecution.ExecutionStatus.FAILED,
                completed_at=now,
                error_reason=message,
            )
