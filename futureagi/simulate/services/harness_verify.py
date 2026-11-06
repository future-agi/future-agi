"""Hold back a third of an environment's scenarios from a coding agent, and verify an
agent version on them in a run the user's reads never show."""

from collections.abc import Sequence
from hashlib import sha256
from uuid import UUID, uuid4

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from simulate.authentication import CodingAgentNeedsSuiteVersion
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
from simulate.services.harness_versioning.read import version_not_found
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
    """The environment's held-back cases, chosen from its default suite the first time
    they are needed and carried to each new default suite after that. ``None`` for an
    environment without a suite version, which has nothing to choose from."""
    chosen = held_back(environment)
    if not _behind(chosen, environment.suite_version_id):
        return chosen
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=environment.id
        )
        chosen = held_back(locked)
        if _behind(chosen, locked.suite_version_id):
            chosen = _carried(locked, chosen)
            metadata = dict(locked.payload.get("metadata") or {})
            metadata[HELD_BACK_METADATA_KEY] = {
                "suite_version_id": str(chosen.suite_version_id),
                "scenario_keys": list(chosen.scenario_keys),
                "seen_keys": list(chosen.seen_keys),
            }
            locked.payload = {**locked.payload, "metadata": metadata}
            locked.save(update_fields=["payload", "updated_at"])
    environment.payload = locked.payload
    return chosen


def _behind(chosen: HeldBack | None, suite_version_id: UUID | None) -> bool:
    """Whether the set is still to be chosen from, or carried to, the default suite."""
    return suite_version_id is not None and (
        chosen is None or chosen.suite_version_id != suite_version_id
    )


def _suite_keys(suite_version_id: UUID) -> list[str]:
    return list(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=suite_version_id
        ).values_list("scenario_key", flat=True)
    )


def _carried(environment: HostedHarnessJob, chosen: HeldBack | None) -> HeldBack:
    """The held-back cases for the environment's default suite. A case held back stays
    held back, even once a suite drops it. A case of any suite the set was carried to
    stays shown, even when a revert brings it back. A third of the cases in neither
    join the held-back ones."""
    held = set(chosen.scenario_keys) if chosen is not None else set()
    seen = set(chosen.seen_keys) if chosen is not None else set()
    keys = _suite_keys(environment.suite_version_id)
    new = [key for key in keys if key not in held and key not in seen]
    held.update(pick_held_back(str(environment.id), new))
    return HeldBack(
        suite_version_id=environment.suite_version_id,
        scenario_keys=tuple(sorted(held)),
        seen_keys=tuple(sorted(seen.union(keys) - held)),
    )


def require_held_back(environment: HostedHarnessJob) -> HeldBack:
    """``ensure_held_back`` for a coding agent's call. Without a suite version nothing
    can be hidden, so the call is refused rather than served every case."""
    chosen = ensure_held_back(environment)
    if chosen is None:
        raise no_held_back()
    return chosen


def require_default_suite(
    environment: HostedHarnessJob, suite_version_id: UUID | None
) -> HeldBack:
    """``require_held_back`` for a coding agent's read of, or pick from, a suite
    version. It may use only the default suite, so every case it ever saw is in a
    suite the set was carried to; any other version reads as one that does not exist.
    """
    chosen = require_held_back(environment)
    if suite_version_id is not None and suite_version_id != chosen.suite_version_id:
        raise version_not_found()
    return chosen


def no_held_back() -> HostedHarnessError:
    """The refusal of a coding agent's call when nothing can be held back from it; the
    same code its key gets at authentication."""
    return HostedHarnessError(
        CodingAgentNeedsSuiteVersion.default_code,
        str(CodingAgentNeedsSuiteVersion.default_detail),
        status_code=CodingAgentNeedsSuiteVersion.status_code,
    )


def start_verify(
    environment: HostedHarnessJob, *, agent_version_id: UUID, trials: int
) -> HostedHarnessJob:
    """Run ``agent_version_id`` on the held-back cases of the default suite, pinned to
    that suite. A live or completed verify is final: asking again returns it, whatever
    its trials, so the held-back cases cannot be sampled twice. After one that failed
    or was canceled, asking again starts a new one.

    The advisory lock and ``version_verify`` keep a repeated request to one verify;
    the idempotency key is unique per verify, so no run a caller names can stand in
    for one."""
    chosen = ensure_held_back(environment)
    if (existing := version_verify(environment, agent_version_id)) is not None:
        return existing
    # A case its suite dropped stays held back but can no longer run.
    cases = (
        sorted(
            set(chosen.scenario_keys).intersection(_suite_keys(chosen.suite_version_id))
        )
        if chosen is not None
        else []
    )
    if not cases:
        raise HostedHarnessError(
            "held_back_empty",
            "this environment's scenarios include no held-back case to verify on",
            status_code=409,
        )
    # Fewer trials keep every held-back case inside a run's execution cap; the
    # result reports the trials that ran.
    cap = int(getattr(settings, "HARNESS_MAX_EXECUTIONS_PER_RUN", 200))
    trials = max(1, min(trials, cap // len(cases)))
    # Outside the transaction: the check may call the usage service.
    require_harness_call_usage(environment)
    with transaction.atomic():
        # Same-version verifies take turns, so a concurrent one finds the first.
        advisory_xact_lock(f"harness-verify:{environment.id}:{agent_version_id}")
        if (existing := version_verify(environment, agent_version_id)) is not None:
            return existing
        run, created = create_selected_harness_run(
            environment,
            scenario_keys=cases,
            trials=trials,
            idempotency_key=f"verify:{agent_version_id}:{uuid4().hex}",
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
