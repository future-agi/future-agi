"""Validate an agent, environment and suite pairing with a run-less certify-only job.

The job certifies the pairing's bundle as a pinned run's miss does, but makes no
TestExecution and places no call. Only a check that ran fails a pairing: an exit 78 or a
certify that ended without a bundle. Any other ending leaves the pairing pending.
A newer move of the environment's default agent replaces any move still waiting.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
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

# The endings that are a check's verdict on the pairing; any other one is retried.
CHECK_FAILURES = frozenset(
    {"authoring_runtime_validation_failed", "certify_incomplete"}
)
FAILURE_SOURCE = "validation"
# A move whose validation ended before a check did is validated this many times more.
MOVE_RESTARTS = 1
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
    """A pairing's validation as started: already ``ready``, or ``validating`` by ``job``."""

    state: Literal["ready", "validating"]
    bundle: HarnessAssembledBundle
    purpose: Purpose
    job: HostedHarnessJob | None = None

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    def as_view(self) -> ValidationView:
        return {
            "state": self.state,
            "job_id": str(self.job.id) if self.job is not None else None,
            "purpose": self.purpose,
            "finished_at": None,
            "error": None,
        }


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
    """Validate this pairing, unless its bundle is already certified.

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
            return Validation("ready", bundle, purpose)
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
        environment_id=str(locked.id),
        job_id=str(job.id),
        bundle_id=str(bundle.id),
        purpose=purpose,
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
    from simulate.services.harness_agent_versions import (
        apply_agent_version,
        own_credential_set,
    )

    payload = json.loads(json.dumps(locked.payload))
    if own_credential_set(runtime):
        apply_agent_version(payload, runtime, environment_id=locked.id)
    metadata = payload.setdefault("metadata", {})
    for name in RUN_DROPPED_METADATA:
        metadata.pop(name, None)
    metadata.update(
        {
            "environment_job_id": str(locked.id),
            "certify_only": True,
            "validation_purpose": purpose,
            "assembly": assembly.RunAssembly(
                bundle=bundle, key=bundle.assembly_key, cached=False, defaults=False
            ).as_metadata(),
        }
    )
    if move is not None:
        metadata["validation_move"] = move
    payload.pop("job_id", None)
    payload.pop("run_id", None)
    job, _ = create_hosted_job(
        locked.organization,
        payload,
        idempotency_key=f"validation:{locked.id}:{uuid.uuid4().hex}",
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


def finish_validation(job_id: Any) -> str | None:
    """Settle the pairing a terminal validation job checked, then act on the outcome.

    Returns ``ready``, ``failed`` (a check failed: the pairing is failed) or ``retry``
    (it ended before a check did: the pairing stays pending), or ``None`` when the job
    is not terminal or was settled before.
    """
    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(id=job_id)
        metadata = dict((job.payload or {}).get("metadata") or {})
        if job.state in _LIVE_STATES or metadata.get("validation_outcome"):
            return None
        outcome = _settle_bundle(job)
        payload = dict(job.payload or {})
        payload["metadata"] = {**metadata, "validation_outcome": outcome}
        job.payload = payload
        job.save(update_fields=["payload", "updated_at"])
    logger.info(
        "harness_pair_validation_finished",
        job_id=str(job.id),
        environment_id=str(job.environment_id),
        bundle_id=str(job.assembled_bundle_id),
        outcome=outcome,
        code=(job.failure or {}).get("code"),
    )
    move = metadata.get("validation_move")
    if move:
        _after_move_validated(job, move, outcome)
    return outcome


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


def _after_move_validated(job: HostedHarnessJob, move: AgentMove, outcome: str) -> None:
    from simulate.services.harness_agent_versions import _record_activation

    environment = HostedHarnessJob.no_workspace_objects.filter(
        id=job.environment_id
    ).first()
    if environment is None:
        result = "environment_gone"
    elif outcome == "ready":
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
            )
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
        environment_id=str(job.environment_id),
        agent_version_id=move["agent_version_id"],
        caller=move["caller"],
        outcome=result,
    )


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
    if live_validation(bundle.id) is not None:
        raise HostedHarnessError(
            "pair_validating",
            "These versions are being checked together; run them when the check ends",
            status_code=409,
        )
    if (
        bundle.state == HarnessAssembledBundle.State.FAILED
        and (bundle.error or {}).get("source") == FAILURE_SOURCE
    ):
        raise HostedHarnessError(
            "pair_failed",
            "These versions failed their check together; fix them and check again",
            status_code=409,
        )
