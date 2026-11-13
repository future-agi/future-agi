"""Validate an agent, environment and suite pairing with a run-less certify-only job.

The job certifies the pairing's bundle as a pinned run's miss does, but makes no
TestExecution and places no simulated call. It does start a build sandbox and, through the
harness's qualification, talks to the agent: model turns on the agent's own credentials, with
the simulator's side metered through the platform gateway. Only a check that ran fails a pairing: an exit 78 or a
certify that ended without a bundle. Any other ending leaves the pairing pending.
A newer move of the environment's default agent replaces any move still waiting.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, TypedDict

import structlog
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from simulate.models import (
    AgentVersion,
    HarnessAgentVersionRuntime,
    HarnessAssembledBundle,
    HarnessEnvironmentVersion,
    HarnessNudge,
    HarnessSuiteVersion,
    HostedHarnessJob,
)
from simulate.services.harness_versioning import assembly
from simulate.services.hosted_harness import (
    RUN_DROPPED_METADATA,
    HostedHarnessError,
    ResolvedPins,
    create_hosted_job,
    fail_unscheduled,
    overlay_stage_outputs,
    pinned_stage_outputs,
)

logger = structlog.get_logger(__name__)

Purpose = Literal["agent_move", "recheck"]
Caller = Literal["activation", "hand", "no_change", "land"]

# A check still running this long after it was asked for is stopped. A slow worker or
# sandbox is ours, not the agent's: the check reads as unavailable and runs go ahead;
# the next check starts a fresh job.
CHECK_DEADLINE = timedelta(minutes=45)
CHECK_TIMED_OUT = "validation_timed_out"
# The endings that are a check's verdict on the pairing; any other one is retried.
CHECK_FAILURES = frozenset({"authoring_runtime_validation_failed"})
# A check that ended on our side, not the agent's: runs are not held for it.
CHECK_UNAVAILABLE = "check_unavailable"
# What a run refused after a failed check is told, by the check's ending.
_FAILED_REASONS = {
    "authoring_runtime_validation_failed": "the agent did not pass this environment's checks",
    CHECK_TIMED_OUT: "the check did not finish within 45 minutes",
}
UNAVAILABLE_REASON = (
    "the check could not prepare this environment's test world on our side; runs go"
    " ahead without it"
)
# The processes ALK itself adds to a generated world (agent-learning-kit
# bundle_author_v2: the managed world-db, the generated tool-proxy and mcp-toolbox). Any
# other process is the agent's or one of its services, whatever it is called.
_OWNED_PROCESSES = frozenset({"world-db", "tool-proxy", "mcp-toolbox"})
# Codes ALK raises with an infrastructure domain at every raise site, whatever the process.
_INFRASTRUCTURE_CODES = frozenset(
    {
        "store_statement_failed",
        "resource_capacity_unknown",
        "resource_capacity_unavailable",
    }
)
# A canonical seed that failed only for ALK's own fingerprint check; any other seed
# failure is the agent's migration or seed content.
_CANONICAL_SEED = "canonical World IR could not be compiled or applied: "
_OWNED_SEED_DIAGNOSTICS = frozenset({"source_model_fingerprint_mismatch"})
# ALK's typed failure: ``stage/code (process): detail``.
_TYPED = re.compile(
    r"(?:ProcessRuntimeError: )?[a-z_]+/([a-z_]+)(?: \(([^)]+)\))?: (.*)"
)
# The line ALK prints for a failed validation, before the error and the agent's output.
_PHASE_LINE = re.compile(r"runtime validation: ([a-z_]+): (.*)")
# Only a runtime phase ends the check at once; ALK repairs or retries any other.
_ENDING_PHASE = "runtime"
# ALK ends a failure's own text with this and prints the agent's output lines after it
# (process_runtime ``_output_tail``).
_AGENT_OUTPUT = "; last output:"
# Causes that end every check of the same pairing on the same template the same way.
_REPEATING_CAUSES = frozenset({"seed_failed"})


def _ending(tail: str) -> tuple[str, str] | None:
    """ALK's phase line for the check's ending, as ``(phase, error)``: the first phase
    line, read only before any of the agent's output. ``None`` when that cannot be
    told: the log's start was cut off, the agent's output comes first, or the first
    phase line is one ALK repairs or retries (a later one may be the agent's)."""
    from simulate.services.hosted_harness_gateway import GUEST_LOG_CHARS

    if len(tail) >= GUEST_LOG_CHARS:
        return None
    for line in tail.splitlines():
        phase = _PHASE_LINE.fullmatch(line)
        if phase is not None:
            return phase.groups() if phase.group(1) == _ENDING_PHASE else None
        if _AGENT_OUTPUT in line:
            return None
    return None


def platform_failure(failure: Any, failed_actions: Any = None) -> str | None:
    """Why a failed check ended on our side, or ``None`` when the agent failed it.

    Read only from ALK's phase line for the ending (see ``_ending``), never from the
    agent's output after it. Ours: ALK's typed failure on that line is an ``internal_*``
    code (an ALK bug), an infrastructure-only code, or a failure of a process ALK owns
    (a canonical seed only for ALK's own fingerprint check). A failed action probe, or
    anything else, is the agent's."""
    if failed_actions or not failure:
        return None
    ending = _ending(str((failure.get("details") or {}).get("guest_log_tail") or ""))
    if ending is None:
        return None
    typed = _TYPED.fullmatch(ending[1])
    if typed is None:
        return None
    code, process, detail = typed.groups()
    if code.startswith("internal_") or code in _INFRASTRUCTURE_CODES:
        return code
    if process not in _OWNED_PROCESSES:
        return None
    if code != "seed_failed":
        return code
    if not detail.startswith(_CANONICAL_SEED):
        return None
    diagnostics = set(detail.removeprefix(_CANONICAL_SEED).split(", "))
    return code if diagnostics <= _OWNED_SEED_DIAGNOSTICS else None


def _template() -> str | None:
    """The sandbox template checks run on now, or ``None`` when none is pinned."""
    from simulate.services.hosted_sandbox.factory import sandbox_runtime_reference

    name, build = sandbox_runtime_reference()
    return f"{name}:{build}" if name else None


def repeats_unavailable(bundle: HarnessAssembledBundle) -> bool:
    """Whether this pending pairing's last check ended on our side for a cause that
    would end a new check the same way: same versions (the bundle) and the template the
    last check ran on."""
    last = (bundle.error or {}).get("last_attempt") or {}
    template = _template()
    return (
        bundle.state == HarnessAssembledBundle.State.PENDING
        and last.get("code") == CHECK_UNAVAILABLE
        and template is not None
        and last.get("template") == template
    )


FAILURE_SOURCE = "validation"
# A move whose validation ended before a check did is validated this many times more.
MOVE_RESTARTS = 1
# What a settled validation did with its move, kept apart from its outcome: a move not
# made yet (the worker stopped, or making it raised) is made by the next settle.
MOVE_RESULT = "validation_move_result"
MOVE_TRIES_KEY = "validation_move_tries"
# Making a move that raises is tried this many times in all, then recorded as failed.
MOVE_TRIES = 3
# A move still not made this long after its validation settled is made by the nudge tick.
MOVE_RETRY_AFTER = timedelta(minutes=2)
_LIVE_STATES = frozenset(
    set(HostedHarnessJob.State)
    - {
        HostedHarnessJob.State.COMPLETED,
        HostedHarnessJob.State.FAILED,
        HostedHarnessJob.State.CANCELED,
    }
)


class AgentMove(TypedDict):
    """A default-agent move that waits for its pairing to validate."""

    caller: Caller
    agent_version_id: str
    from_agent_version_id: str | None
    user_id: str | None
    nudge_id: str | None
    explicit: bool
    revalidated: bool
    restarts: int
    # A landing's default environment version before it landed, put back if the
    # move fails.
    from_environment_version_id: str | None


class ValidationView(TypedDict):
    state: str
    job_id: str | None
    purpose: str
    finished_at: str | None
    error: dict[str, Any] | None


@dataclass(frozen=True)
class Validation:
    """A pairing's validation as started: already ``ready``, ``validating`` by ``job``, or
    ``check_unavailable`` when a new check would end on our side as the last one did."""

    state: Literal["ready", "validating", "check_unavailable"]
    bundle: HarnessAssembledBundle
    purpose: Purpose
    job: HostedHarnessJob | None = None

    @property
    def ready(self) -> bool:
        """Nothing to wait for: a move or run goes ahead now."""
        return self.state != "validating"

    def as_view(self) -> ValidationView:
        unavailable = self.state == CHECK_UNAVAILABLE
        last = (self.bundle.error or {}).get("last_attempt") or {}
        return {
            "state": self.state,
            "job_id": (
                str(self.job.id)
                if self.job is not None
                else last.get("job_id")
                if unavailable
                else None
            ),
            "purpose": self.purpose,
            "finished_at": None,
            "error": (
                {"code": CHECK_UNAVAILABLE, "reason": UNAVAILABLE_REASON}
                if unavailable
                else None
            ),
        }


def failed_reason(error: dict[str, Any]) -> str:
    """A failed check's reason in plain words, from its bundle's ``error``."""
    reason = _FAILED_REASONS.get(error.get("code"), "the check failed")
    actions = [
        str(one["action"])
        for one in error.get("failed_actions") or []
        if isinstance(one, dict) and one.get("action")
    ]
    if actions:
        reason += f" (failed actions: {', '.join(actions[:5])})"
    return reason


def certify_only(job: HostedHarnessJob) -> bool:
    """Whether ``job`` is a validation job, which certifies and runs nothing."""
    return ((job.payload or {}).get("metadata") or {}).get("certify_only") is True


def can_validate(environment: HostedHarnessJob, agent_version_id: Any) -> bool:
    """Whether a pairing of this agent version can be validated in ``environment``:
    certifying is on, the environment pins an agent and versions, and the version has a
    side row."""
    return (
        assembly.certify_enabled()
        and environment.agent_version_id is not None
        and environment.environment_version_id is not None
        and environment.suite_version_id is not None
        and HarnessAgentVersionRuntime.no_workspace_objects.filter(
            agent_version_id=agent_version_id
        ).exists()
    )


def live_validation(bundle_id: Any) -> HostedHarnessJob | None:
    """The validation job still certifying this bundle, if any."""
    return (
        HostedHarnessJob.no_workspace_objects.filter(
            assembled_bundle_id=bundle_id,
            state__in=_LIVE_STATES,
            payload__metadata__certify_only=True,
        )
        .order_by("-created_at")
        .first()
    )


def validating(environment_id: Any, agent_version_id: Any) -> bool:
    """Whether a move of ``environment``'s default to this agent version is validating."""
    return HostedHarnessJob.no_workspace_objects.filter(
        environment_id=environment_id,
        state__in=_LIVE_STATES,
        payload__metadata__certify_only=True,
        payload__metadata__validation_move__agent_version_id=str(agent_version_id),
    ).exists()


def agent_move(
    caller: Caller,
    locked: HostedHarnessJob,
    version: AgentVersion,
    *,
    user: Any = None,
    nudge: HarnessNudge | None = None,
    explicit: bool = False,
    from_environment_version_id: Any = None,
) -> AgentMove:
    """The move to make once the pairing of ``version`` with the defaults validates."""
    return {
        "caller": caller,
        "agent_version_id": str(version.id),
        "from_agent_version_id": (
            str(locked.agent_version_id) if locked.agent_version_id else None
        ),
        "user_id": str(user.id) if getattr(user, "id", None) else None,
        "nudge_id": str(nudge.id) if nudge is not None else None,
        "explicit": explicit,
        "revalidated": False,
        "restarts": 0,
        "from_environment_version_id": (
            str(from_environment_version_id) if from_environment_version_id else None
        ),
    }


def start_validation(
    environment: HostedHarnessJob,
    agent_version: AgentVersion,
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
    *,
    purpose: Purpose,
    move: AgentMove | None = None,
) -> Validation | None:
    """Validate this pairing, unless its bundle is already certified or its last check
    ended on our side for a cause that repeats on the same template.

    Returns ``None`` when it cannot be validated (see ``can_validate``). A failed bundle
    is reset to pending; a validation already certifying the bundle is returned, carrying
    ``move`` from now on. A ``move`` replaces every other move still waiting on this
    environment. Takes the environment's lock; the job's workflow starts on commit.
    """
    from simulate.services.harness_versioning.backfill import effective_runtime
    from simulate.services.harness_versioning.versions import _lock_environment

    if not can_validate(environment, agent_version.id):
        return None
    with transaction.atomic():
        locked = _lock_environment(environment)
        if move is not None:
            drop_waiting_moves(locked, move["agent_version_id"])
        runtime = effective_runtime(
            locked,
            HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version_id=agent_version.id
            ).first(),
        )
        key = assembly.bundle_key(runtime, environment_version, suite_version)
        bundle, _ = HarnessAssembledBundle._base_manager.get_or_create(
            job_id=locked.id,
            assembly_key=key,
            defaults={
                "organization_id": locked.organization_id,
                "agent_version_id": agent_version.id,
                "environment_version_id": environment_version.id,
                "suite_version_id": suite_version.id,
                "state": HarnessAssembledBundle.State.PENDING,
            },
        )
        if bundle.state == HarnessAssembledBundle.State.READY and bundle.object_key:
            logger.info(
                "harness_pair_validation_reused",
                environment_id=str(locked.id),
                agent_version_id=str(agent_version.id),
                environment_version_id=str(environment_version.id),
                suite_version_id=str(suite_version.id),
                bundle_id=str(bundle.id),
                assembly_key=bundle.assembly_key,
                purpose=purpose,
                caller=move["caller"] if move else None,
            )
            return Validation("ready", bundle, purpose)
        if purpose != "recheck" and repeats_unavailable(bundle):
            logger.info(
                "harness_pair_check_unavailable_reused",
                environment_id=str(locked.id),
                agent_version_id=str(agent_version.id),
                bundle_id=str(bundle.id),
                cause=bundle.error["last_attempt"].get("cause"),
                purpose=purpose,
                caller=move["caller"] if move else None,
            )
            return Validation(CHECK_UNAVAILABLE, bundle, purpose)
        if bundle.state == HarnessAssembledBundle.State.FAILED:
            assembly.reset_failed(bundle)
        job = live_validation(bundle.id)
        if job is not None and (move is None or _carry_move(job, move)):
            return Validation("validating", bundle, purpose, job)
        job = _create_job(
            locked,
            runtime,
            agent_version,
            environment_version,
            suite_version,
            bundle,
            purpose=purpose,
            move=move,
        )
        job_id = job.id
        transaction.on_commit(lambda: _dispatch(job_id))
    logger.info(
        "harness_pair_validation_started",
        **_check_log(job),
        caller=move["caller"] if move else None,
    )
    return Validation("validating", bundle, purpose, job)


def _carry_move(job: HostedHarnessJob, move: AgentMove) -> bool:
    """Give a running validation ``move``; ``False`` when it settled meanwhile."""
    locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(id=job.id)
    metadata = (locked.payload or {}).get("metadata") or {}
    if locked.state not in _LIVE_STATES or metadata.get("validation_outcome"):
        return False
    _set_move(locked, move)
    return True


def _set_move(job: HostedHarnessJob, move: AgentMove | None) -> None:
    payload = dict(job.payload or {})
    payload["metadata"] = {**(payload.get("metadata") or {}), "validation_move": move}
    job.payload = payload
    job.save(update_fields=["payload", "updated_at"])


def drop_waiting_moves(locked: HostedHarnessJob, agent_version_id: Any) -> None:
    """A move to ``agent_version_id`` was asked for on the locked environment: every
    move to another version still waiting on a validation never lands."""
    from simulate.services.harness_agent_versions import _record_activation

    waiting = HostedHarnessJob.no_workspace_objects.select_for_update().filter(
        environment_id=locked.id,
        state__in=_LIVE_STATES,
        payload__metadata__certify_only=True,
        payload__metadata__validation_move__isnull=False,
    )
    for job in waiting:
        dropped = (job.payload.get("metadata") or {}).get("validation_move")
        if not dropped or dropped["agent_version_id"] == str(agent_version_id):
            continue
        _set_move(job, None)
        _record_activation(dropped["agent_version_id"], {str(locked.id): "superseded"})
        purpose = (job.payload.get("metadata") or {}).get("validation_purpose")
        if purpose == "agent_move":
            # Nobody else waits on a move's check: stop its sandbox rather than run it out.
            from simulate.services.hosted_harness import request_cancellation

            request_cancellation(job, "superseded_move")
        logger.info(
            "harness_pair_validation_move_dropped",
            environment_id=str(locked.id),
            job_id=str(job.id),
            agent_version_id=dropped["agent_version_id"],
            by_agent_version_id=str(agent_version_id),
        )


def _create_job(
    locked: HostedHarnessJob,
    runtime: Any,
    agent_version: AgentVersion,
    environment_version: HarnessEnvironmentVersion,
    suite_version: HarnessSuiteVersion,
    bundle: HarnessAssembledBundle,
    *,
    purpose: Purpose,
    move: AgentMove | None,
) -> HostedHarnessJob:
    """The environment's job, pinned to the pairing; certifying is all it does."""
    from simulate.selectors.held_back import HELD_BACK_METADATA_KEY
    from simulate.services.harness_agent_versions import (
        apply_agent_version,
        own_credential_set,
    )

    payload = json.loads(json.dumps(locked.payload))
    if own_credential_set(runtime):
        apply_agent_version(payload, runtime, environment_id=locked.id)
    metadata = payload.setdefault("metadata", {})
    for name in (*RUN_DROPPED_METADATA, HELD_BACK_METADATA_KEY):
        metadata.pop(name, None)
    metadata.update(
        {
            "environment_job_id": str(locked.id),
            "certify_only": True,
            "validation_purpose": purpose,
            "check_template": _template(),
            "assembly": assembly.RunAssembly(
                bundle=bundle, key=bundle.assembly_key, cached=False, defaults=False
            ).as_metadata(),
        }
    )
    if move is not None:
        metadata["validation_move"] = move
    payload.pop("job_id", None)
    payload.pop("run_id", None)
    # One key per check of this bundle, so two requests for the same check make one job,
    # and so one workflow; the bundle is the environment's for this pairing's hash.
    checks = HostedHarnessJob.no_workspace_objects.filter(
        assembled_bundle_id=bundle.id, payload__metadata__certify_only=True
    ).count()
    job, _ = create_hosted_job(
        locked.organization,
        payload,
        idempotency_key=f"validation:{bundle.id}:{checks + 1}",
        workspace=locked.workspace,
    )
    job.environment = locked
    job.run_test = locked.run_test
    job.agent_version = agent_version
    job.environment_version = environment_version
    job.suite_version = suite_version
    job.assembled_bundle = bundle
    job.bundle_digest = locked.bundle_digest
    job.stage_outputs = overlay_stage_outputs(
        list(locked.stage_outputs or []),
        pinned_stage_outputs(environment_version, suite_version),
    )
    job.save(
        update_fields=[
            "environment",
            "run_test",
            "agent_version",
            "environment_version",
            "suite_version",
            "assembled_bundle",
            "bundle_digest",
            "stage_outputs",
            "updated_at",
        ]
    )
    return job


def _dispatch(job_id: Any) -> None:
    """Start the job's workflow; if it cannot start, the validation ends at once."""
    from simulate.temporal.client import start_hosted_harness_gateway_workflow

    job = HostedHarnessJob.no_workspace_objects.get(id=job_id)
    retry = job.payload["retry"]
    try:
        start_hosted_harness_gateway_workflow(
            str(job.id),
            str(settings.HARNESS_PUBLIC_BASE_URL).rstrip("/"),
            retry["max_infrastructure_attempts"],
            retry["initial_backoff_seconds"],
            retry["max_backoff_seconds"],
        )
    except Exception:
        logger.exception("harness_pair_validation_dispatch_failed", job_id=str(job_id))
        if fail_unscheduled(job, "the pairing's validation could not be scheduled"):
            finish_validation(job_id)


def on_terminal(job: HostedHarnessJob) -> None:
    """End a validation job's validation once its terminal state commits."""
    if certify_only(job):
        job_id = job.id
        transaction.on_commit(lambda: finish_validation(job_id))


def check_deadline(job: HostedHarnessJob) -> Any:
    """When a validation job is stopped: ``CHECK_DEADLINE`` after its latest sandbox
    launched, so time queued for a runner never counts; from its creation before one."""
    launched = (
        job.attempts.order_by("-attempt_number")
        .values_list("created_at", flat=True)
        .first()
    )
    return (launched or job.created_at) + CHECK_DEADLINE


def overdue(job: HostedHarnessJob) -> bool:
    """Whether a live validation job has run past its deadline and is to be stopped."""
    return (
        certify_only(job)
        and job.state in _LIVE_STATES
        and check_deadline(job) <= timezone.now()
        and (job.cancel_requested_at is None or job.cancel_reason == CHECK_TIMED_OUT)
    )


def _check_log(job: HostedHarnessJob) -> dict[str, Any]:
    """The fields every log line of a check carries: its pairing, hash and duration."""
    bundle = job.assembled_bundle
    end = job.terminal_at or timezone.now()
    return {
        "job_id": str(job.id),
        "environment_id": str(job.environment_id),
        "agent_version_id": str(job.agent_version_id),
        "environment_version_id": str(job.environment_version_id),
        "suite_version_id": str(job.suite_version_id),
        "bundle_id": str(job.assembled_bundle_id),
        "assembly_key": bundle.assembly_key if bundle is not None else None,
        "purpose": ((job.payload or {}).get("metadata") or {}).get(
            "validation_purpose"
        ),
        "duration_seconds": round((end - job.created_at).total_seconds(), 1),
    }


def finish_validation(job_id: Any) -> str | None:
    """Settle the pairing a terminal validation job checked, then act on the outcome.

    Returns ``ready``, ``failed`` (the agent failed a check: the pairing is failed),
    ``unavailable`` (the check failed on our side: the pairing stays pending and holds
    nothing back) or ``retry`` (it ended before a check did: the pairing stays pending),
    or ``None`` when the job is not terminal, or was settled before with its move, if it
    carries one, made. A settled job whose move was not made yet makes it now.
    """
    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(id=job_id)
        metadata = dict((job.payload or {}).get("metadata") or {})
        if job.state in _LIVE_STATES:
            return None
        outcome = metadata.get("validation_outcome")
        settled = outcome is None
        if settled:
            outcome = _settle_bundle(job)
            payload = dict(job.payload or {})
            payload["metadata"] = {**metadata, "validation_outcome": outcome}
            job.payload = payload
            job.save(update_fields=["payload", "updated_at"])
        elif not _move_pending(metadata):
            return None
    if settled:
        logger.info(
            "harness_pair_validation_finished",
            **_check_log(job),
            outcome=outcome,
            code=(
                CHECK_TIMED_OUT
                if job.cancel_reason == CHECK_TIMED_OUT
                else (job.failure or {}).get("code")
            ),
        )
    _finish_move(job_id)
    return outcome


def _move_pending(metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("validation_move")) and MOVE_RESULT not in metadata


def _finish_move(job_id: Any) -> None:
    """Make a settled validation's move, once: the move and the record that it was made
    commit together, so a worker stopped in between leaves it for the next settle."""
    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(id=job_id)
        metadata = dict((job.payload or {}).get("metadata") or {})
        if not _move_pending(metadata):
            return
        tries = int(metadata.get(MOVE_TRIES_KEY) or 0) + 1
        result = _after_move_validated(
            job,
            metadata["validation_move"],
            metadata["validation_outcome"],
            last_try=tries >= MOVE_TRIES,
        )
        metadata[MOVE_TRIES_KEY] = tries
        if result is not None:
            metadata[MOVE_RESULT] = result
        job.payload = {**(job.payload or {}), "metadata": metadata}
        job.save(update_fields=["payload", "updated_at"])


def unfinished_moves(*, limit: int) -> list[Any]:
    """Environments with a settled validation whose move was not made, up to ``limit``,
    longest waiting first. A validation settled within ``MOVE_RETRY_AFTER`` is left to
    the settle still running."""
    jobs = (
        HostedHarnessJob.no_workspace_objects.filter(
            payload__metadata__certify_only=True,
            payload__metadata__validation_outcome__isnull=False,
            payload__metadata__validation_move__isnull=False,
            updated_at__lte=timezone.now() - MOVE_RETRY_AFTER,
        )
        .exclude(state__in=_LIVE_STATES)
        .exclude(payload__metadata__validation_move=None)
        .exclude(**{f"payload__metadata__{MOVE_RESULT}__isnull": False})
        .order_by("updated_at")
        .values_list("environment_id", flat=True)
    )
    return list(dict.fromkeys(jobs[:limit]))


def finish_unfinished_moves(environment_id: Any) -> None:
    """Make every move the environment's settled validations left unmade."""
    jobs = (
        HostedHarnessJob.no_workspace_objects.filter(
            environment_id=environment_id,
            payload__metadata__certify_only=True,
            payload__metadata__validation_outcome__isnull=False,
            payload__metadata__validation_move__isnull=False,
        )
        .exclude(state__in=_LIVE_STATES)
        .exclude(payload__metadata__validation_move=None)
        .exclude(**{f"payload__metadata__{MOVE_RESULT}__isnull": False})
        .order_by("updated_at")
        .values_list("id", flat=True)
    )
    for job_id in jobs:
        finish_validation(job_id)


def _settle_bundle(job: HostedHarnessJob) -> str:
    bundle = (
        HarnessAssembledBundle._base_manager.select_for_update()
        .filter(id=job.assembled_bundle_id)
        .first()
    )
    if bundle is None:
        return "retry"
    if bundle.state == HarnessAssembledBundle.State.READY and bundle.object_key:
        return "ready"
    failure = job.failure or {}
    code = failure.get("code") or job.state
    if job.cancel_reason == CHECK_TIMED_OUT:
        code = CHECK_TIMED_OUT
        failure = {"message": _FAILED_REASONS[CHECK_TIMED_OUT]}
    cause = (
        code
        if code in ("certify_incomplete", CHECK_TIMED_OUT)
        else (
            platform_failure(failure, (bundle.error or {}).get("failed_actions"))
            if code == "authoring_runtime_validation_failed"
            else None
        )
    )
    if cause is not None:
        if bundle.state == HarnessAssembledBundle.State.PENDING:
            last: dict[str, Any] = {
                "code": CHECK_UNAVAILABLE,
                "cause": cause,
                "at": timezone.now().isoformat(),
                "job_id": str(job.id),
            }
            if cause in _REPEATING_CAUSES or cause.startswith("internal_"):
                last["template"] = (job.payload.get("metadata") or {}).get(
                    "check_template"
                )
            bundle.error = {"last_attempt": last}
            bundle.save(update_fields=["error", "updated_at"])
        logger.warning("harness_pair_check_unavailable", **_check_log(job), cause=cause)
        return "unavailable"
    if code in CHECK_FAILURES:
        if bundle.state == HarnessAssembledBundle.State.PENDING:
            bundle.state = HarnessAssembledBundle.State.FAILED
            bundle.error = {
                "code": code,
                "message": str(failure.get("message") or "")[:1000],
                "run_id": str(job.id),
                "source": FAILURE_SOURCE,
            }
            bundle.save(update_fields=["state", "error", "updated_at"])
        return "failed"
    if bundle.state == HarnessAssembledBundle.State.PENDING:
        bundle.error = {
            "last_attempt": {
                "code": code,
                "at": timezone.now().isoformat(),
                "job_id": str(job.id),
            }
        }
        bundle.save(update_fields=["error", "updated_at"])
    return "retry"


def _after_move_validated(
    job: HostedHarnessJob, move: AgentMove, outcome: str, *, last_try: bool = True
) -> str | None:
    """Act on a settled validation's move; its result, or ``None`` when making the
    move raised and it is to be tried again."""
    from simulate.services.harness_agent_versions import _record_activation

    environment = HostedHarnessJob.no_workspace_objects.filter(
        id=job.environment_id
    ).first()
    if environment is None:
        result = "environment_gone"
    elif outcome in ("ready", "unavailable"):
        # A check that ended on our side holds no move back, as before checks.
        try:
            result = complete_move(
                environment,
                move,
                pair=(job.environment_version_id, job.suite_version_id),
            )
        except Exception:  # noqa: BLE001 - recorded, as an activation's failure is
            logger.exception(
                "harness_pair_validation_move_failed",
                environment_id=str(job.environment_id),
                agent_version_id=move["agent_version_id"],
                last_try=last_try,
            )
            if not last_try:
                return None
            result = "failed"
    elif outcome == "retry" and move.get("restarts", 0) < MOVE_RESTARTS:
        result = _restart(environment, job, move)
    else:
        result = "pair_failed" if outcome == "failed" else "validation_retry"
        if move["caller"] == "land":
            _put_back_landing(environment, job, move)
    if result != "activated":
        # A move's own replayed steps record its activation.
        _record_activation(move["agent_version_id"], {str(job.environment_id): result})
    logger.info(
        "harness_pair_validation_move",
        **{**_check_log(job), "agent_version_id": move["agent_version_id"]},
        from_agent_version_id=move["from_agent_version_id"],
        caller=move["caller"],
        check=outcome,
        outcome=result,
    )
    return result


def _restart(
    environment: HostedHarnessJob, job: HostedHarnessJob, move: AgentMove
) -> str:
    """Validate a move's pairing once more after an ending that was not a check."""
    version = AgentVersion.no_workspace_objects.filter(
        id=move["agent_version_id"]
    ).first()
    if version is None:
        return "discarded"
    again: AgentMove = {**move, "restarts": move.get("restarts", 0) + 1}
    started = start_validation(
        environment,
        version,
        job.environment_version,
        job.suite_version,
        purpose="agent_move",
        move=again,
    )
    if started is None:
        return "validation_retry"
    if started.ready:
        return complete_move(
            environment, again, pair=(job.environment_version_id, job.suite_version_id)
        )
    return "validating"


def _put_back_landing(
    environment: HostedHarnessJob, job: HostedHarnessJob, move: AgentMove
) -> None:
    """A landing whose agent will not move: its environment version was built for that
    agent, so the default environment version goes back to the one before it."""
    from simulate.services.harness_versioning.versions import move_default

    before = move.get("from_environment_version_id")
    if not before:
        return
    try:
        move_default(
            environment,
            "environment",
            before,
            expected_default_id=job.environment_version_id,
        )
    except HostedHarnessError as exc:
        logger.info(
            "harness_pair_validation_landing_kept",
            environment_id=str(environment.id),
            environment_version_id=str(job.environment_version_id),
            reason=exc.code,
        )
        return
    logger.info(
        "harness_pair_validation_landing_put_back",
        environment_id=str(environment.id),
        environment_version_id=before,
    )


def complete_move(
    environment: HostedHarnessJob,
    move: AgentMove,
    *,
    pair: tuple[Any, Any],
) -> str:
    """Make the validated agent version the default, then replay its caller's steps.

    ``pair`` is the environment and suite versions validated with it. Re-checks what an
    activation checks. A default agent that moved meanwhile was a newer choice, which
    stands: ``superseded``. When only the environment or suite default moved, one
    validation of the same agent version against the new defaults starts. Returns the
    outcome.
    """
    from simulate.services.harness_agent_versions import (
        _busy,
        _side_row,
        _turned_down,
        hand_move,
    )
    from simulate.services.harness_versioning.versions import (
        _lock_environment,
        move_agent,
    )
    from simulate.services.hosted_harness import agent_version_pickable

    with transaction.atomic():
        locked = _lock_environment(environment)
        version = AgentVersion.no_workspace_objects.filter(
            id=move["agent_version_id"]
        ).first()
        if version is None:
            return "discarded"
        if locked.agent_version_id == version.id:
            return "already_default"
        if not agent_version_pickable(locked, version.id):
            return "discarded"
        if _busy(locked):
            return "environment_busy"
        current = str(locked.agent_version_id) if locked.agent_version_id else None
        if current != move["from_agent_version_id"]:
            return "superseded"
        if move["caller"] == "activation" and not move["explicit"]:
            runtime = _side_row(version.id)
            reason = (
                _turned_down(locked, version, runtime) if runtime is not None else None
            )
            if reason is not None:
                return reason
        if (locked.environment_version_id, locked.suite_version_id) != tuple(pair):
            return _revalidate(locked, version, move)
        outgoing = locked.agent_version_id
        hand = move["caller"] in ("hand", "no_change") or move["explicit"]
        try:
            with transaction.atomic():
                move_agent(
                    locked,
                    version,
                    moved=(
                        hand_move(_user(move["user_id"]), version.id) if hand else None
                    ),
                )
        except HostedHarnessError as exc:
            return exc.code
        _replay_after_move(locked, version, move, outgoing=outgoing)
    return "activated"


def _revalidate(
    locked: HostedHarnessJob, version: AgentVersion, move: AgentMove
) -> str:
    """The environment or suite default moved while validating: validate once more
    against the new ones."""
    if move["revalidated"]:
        return "superseded"
    again: AgentMove = {**move, "revalidated": True}
    validation = start_validation(
        locked,
        version,
        locked.environment_version,
        locked.suite_version,
        purpose="agent_move",
        move=again,
    )
    if validation is None:
        return "superseded"
    if not validation.ready:
        return "validating"
    return complete_move(
        locked, again, pair=(locked.environment_version_id, locked.suite_version_id)
    )


def _replay_after_move(
    locked: HostedHarnessJob,
    version: AgentVersion,
    move: AgentMove,
    *,
    outgoing: Any,
) -> None:
    """The steps each caller takes after a move of its own."""
    from simulate.services.harness_agent_versions import _record_activation
    from simulate.services.harness_versioning.nudges import schedule_environment_nudge

    caller = move["caller"]
    if caller in ("activation", "hand"):
        schedule_environment_nudge(
            locked.id,
            version.id,
            default_moved=True,
            outgoing_agent_version_id=outgoing,
        )
    if caller == "land":
        from simulate.services.harness_versioning.rebuild import _record_agent_move

        nudge = HarnessNudge.no_workspace_objects.filter(id=move["nudge_id"]).first()
        if nudge is not None:
            _record_agent_move(locked, nudge)
            return
    if caller == "no_change" and locked.environment_version_id:
        HarnessEnvironmentVersion.no_workspace_objects.filter(
            id=locked.environment_version_id
        ).update(agent_version_id=version.id, updated_at=timezone.now())
    _record_activation(version.id, {str(locked.id): "activated"})


def _user(user_id: str | None) -> Any:
    if not user_id:
        return None
    return get_user_model().objects.filter(id=user_id).first()


def failed_check(bundle: HarnessAssembledBundle | None) -> bool:
    """Whether a validation failed the pairing: runs of it are refused until it is
    checked again."""
    return (
        bundle is not None
        and bundle.state == HarnessAssembledBundle.State.FAILED
        and (bundle.error or {}).get("source") == FAILURE_SOURCE
    )


def require_pair_runnable(
    environment: HostedHarnessJob, resolved: ResolvedPins
) -> None:
    """Refuse a run of a pairing that is validating or whose validation failed.

    A pairing failed by an ordinary run is not refused: the next run certifies again.
    """
    if resolved.runtime is None:
        return
    key = resolved.assembly_key or assembly.bundle_key(
        resolved.runtime, resolved.environment_version, resolved.suite_version
    )
    bundle = HarnessAssembledBundle._base_manager.filter(
        job_id=environment.id, assembly_key=key
    ).first()
    if bundle is None:
        return
    live = live_validation(bundle.id)
    if live is not None:
        deadline = check_deadline(live)
        retry_after = max(60, int((deadline - timezone.now()).total_seconds()))
        raise HostedHarnessError(
            "pair_validating",
            "These versions are being checked together. Run them again when the "
            f"check ends, at the latest in {-(-retry_after // 60)} minutes.",
            status_code=409,
            retryable=True,
            details={
                "retry_after": retry_after,
                "check": {"job_id": str(live.id), "deadline_at": deadline.isoformat()},
            },
        )
    if failed_check(bundle):
        error = bundle.error or {}
        reason = failed_reason(error)
        raise HostedHarnessError(
            "pair_failed",
            f"These versions failed their check together: {reason}. Fix them, or check "
            "again from Ready to run.",
            status_code=409,
            details={"reason_code": error.get("code"), "reason": reason},
        )
