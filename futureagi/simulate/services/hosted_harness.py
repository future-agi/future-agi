from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from simulate.models import (
    CallExecution,
    HostedHarnessAttempt,
    HostedHarnessCleanupReceipt,
    HostedHarnessJob,
    HostedHarnessReceipt,
    HostedHarnessScenario,
    TestExecution,
)
from simulate.services.alk_simulate_ingestion import (
    ALKSimulateIngestionError,
    create_alk_sim_call_execution_batch,
    create_alk_sim_test_execution,
    provision_alk_sim_run_test,
)

_CAPABILITY_SCHEMA_VERSION = "futureagi.harness-capabilities.v1"
_JOB_SCHEMA_VERSION = "futureagi.harness-job.v1"
_TOKEN_TAIL_SECONDS = 120 + 300


class HostedHarnessError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class AttemptCapability:
    attempt: HostedHarnessAttempt
    token: str
    fence: str
    document: dict[str, Any]


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_hosted_job(
    organization,
    payload: dict[str, Any],
    *,
    idempotency_key: str,
    workspace=None,
) -> tuple[HostedHarnessJob, bool]:
    request_digest = canonical_digest(payload)
    with transaction.atomic():
        existing = (
            HostedHarnessJob.no_workspace_objects.select_for_update()
            .filter(
                organization=organization,
                idempotency_key=idempotency_key,
            )
            .first()
        )
        if existing is not None:
            if existing.workspace_id != getattr(workspace, "id", None):
                raise HostedHarnessError(
                    "idempotency_conflict",
                    "the idempotency key was already used in a different workspace",
                    status_code=409,
                )
            if existing.request_digest != request_digest:
                raise HostedHarnessError(
                    "idempotency_conflict",
                    "the idempotency key was already used for a different request",
                    status_code=409,
                )
            return existing, False

        run_id = payload.get("run_id") or uuid.uuid4()
        seed = payload.get("seed")
        if seed is None:
            seed = secrets.randbits(63)
        normalized = _json_value(payload)
        normalized.update(
            {
                "schema_version": _JOB_SCHEMA_VERSION,
                "job_id": None,
                "run_id": str(run_id),
                "execution": "hosted",
                "seed": seed,
            }
        )
        now = timezone.now()
        duration = normalized["runtime"]["max_duration_seconds"]
        job = HostedHarnessJob.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            run_id=run_id,
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            schema_version=_JOB_SCHEMA_VERSION,
            payload=normalized,
            state=HostedHarnessJob.State.QUEUED,
            seed=seed,
            scenario_count=normalized["scenario_count"],
            artifact_level=normalized["artifacts"]["level"],
            max_artifact_bytes=normalized["artifacts"]["max_artifact_bytes"],
            deadline_at=now + timedelta(seconds=duration),
        )
        normalized["job_id"] = str(job.id)
        job.payload = normalized
        job.save(update_fields=["payload", "updated_at"])
        return job, True


def register_attempt(
    job_id: uuid.UUID | str,
    *,
    endpoint_base_url: str,
    provider_ref: str | None = None,
    snapshot_name: str | None = None,
    snapshot_digest: str | None = None,
) -> AttemptCapability:
    now = timezone.now()
    token = secrets.token_urlsafe(32)
    fence = secrets.token_urlsafe(32)
    with transaction.atomic():
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(id=job_id)
        previous_number = job.current_attempt_number
        attempt_number = previous_number + 1
        if previous_number:
            HostedHarnessAttempt.no_workspace_objects.filter(
                job=job,
                attempt_number__lte=previous_number,
                state__in=(
                    HostedHarnessAttempt.State.REGISTERED,
                    HostedHarnessAttempt.State.PROVISIONING,
                    HostedHarnessAttempt.State.RUNNING,
                    HostedHarnessAttempt.State.FINALIZING,
                    HostedHarnessAttempt.State.CLEANING_UP,
                ),
            ).update(state=HostedHarnessAttempt.State.SUPERSEDED)
        runnable_deadline = now + timedelta(
            seconds=job.payload["runtime"]["max_duration_seconds"]
        )
        expires_at = runnable_deadline + timedelta(seconds=_TOKEN_TAIL_SECONDS)
        attempt = HostedHarnessAttempt.no_workspace_objects.create(
            job=job,
            attempt_number=attempt_number,
            token_hash=hash_secret(token),
            fence_hash=hash_secret(fence),
            expires_at=expires_at,
            provider_ref=provider_ref,
            snapshot_name=snapshot_name,
            snapshot_digest=snapshot_digest,
        )
        job.current_attempt_number = attempt_number
        job.state = HostedHarnessJob.State.PROVISIONING
        # A retry is a fresh active attempt. Do not keep presenting the previous
        # attempt's terminal state while Daytona is launching (or even running)
        # the replacement guest.
        job.current_stage = "queued"
        job.failure = None
        job.terminal_at = None
        job.deadline_at = runnable_deadline
        job.save(
            update_fields=[
                "current_attempt_number",
                "state",
                "current_stage",
                "failure",
                "terminal_at",
                "deadline_at",
                "updated_at",
            ]
        )

    base = endpoint_base_url.rstrip("/")
    prefix = f"{base}/simulate/api/harness/attempts/{attempt.id}"
    document = {
        "schema_version": _CAPABILITY_SCHEMA_VERSION,
        "job_id": str(job.id),
        "attempt_id": str(attempt.id),
        "attempt_number": attempt.attempt_number,
        "fence": fence,
        "expires_at": _rfc3339(attempt.expires_at),
        "token": token,
        "endpoints": {
            "events": f"{prefix}/events/",
            "results": f"{prefix}/results/",
            "artifacts": f"{prefix}/artifacts/",
            "scenarios": f"{prefix}/scenarios/",
            "ingress": f"{prefix}/ingress/",
        },
    }
    return AttemptCapability(
        attempt=attempt, token=token, fence=fence, document=document
    )


def request_cancellation(job: HostedHarnessJob, reason: str) -> HostedHarnessJob:
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        if locked.state in {
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        }:
            return locked
        locked.cancel_requested_at = timezone.now()
        locked.cancel_reason = reason
        locked.state = HostedHarnessJob.State.CLEANING_UP
        # State and stage are both part of the public read model. Leaving the
        # previous authoring stage here makes a successful cancel request look
        # like it did nothing until remote sandbox cleanup finishes.
        locked.current_stage = HostedHarnessJob.State.CLEANING_UP
        locked.save(
            update_fields=[
                "cancel_requested_at",
                "cancel_reason",
                "state",
                "current_stage",
                "updated_at",
            ]
        )
        return locked


def provision_scenarios(
    attempt: HostedHarnessAttempt, payload: dict[str, Any]
) -> dict[str, Any]:
    job = attempt.job
    # Enforce exactly scenario_count unique personas.
    persona_keys = [p["scenario_key"] for p in payload["personas"]]
    if len(persona_keys) != len(set(persona_keys)):
        raise HostedHarnessError(
            "scenario_key_duplicate",
            "persona scenario_key values must be unique",
            status_code=400,
        )
    if len(persona_keys) != job.scenario_count:
        raise HostedHarnessError(
            "scenario_count_mismatch",
            f"expected exactly {job.scenario_count} personas, got {len(persona_keys)}",
            status_code=400,
        )
    if job.run_test_id:
        registrations = list(
            HostedHarnessScenario.no_workspace_objects.filter(job=job).order_by(
                "created_at"
            )
        )
        requested_keys = [persona["scenario_key"] for persona in payload["personas"]]
        if requested_keys != [item.scenario_key for item in registrations]:
            raise HostedHarnessError(
                "scenario_registration_conflict",
                "the job already has a different sealed scenario registration",
                status_code=409,
            )
        return _provision_response(job, registrations)

    personas = [
        {key: value for key, value in persona.items() if key != "scenario_key"}
        for persona in payload["personas"]
    ]
    try:
        # Older hosted guests did not include ``modality`` in the scenario
        # provision request.  The authoring contract is already persisted on
        # the job before scenario registration, so use it as the authoritative
        # fallback instead of silently defaulting every hosted run to text.
        # Misclassifying a LiveKit run here makes completed voice calls render
        # through the chat schema and effectively disappear from call-details.
        modality = _resolve_scenario_modality(job, payload)
        run_test, scenarios, agent_definition = provision_alk_sim_run_test(
            job.organization,
            workspace=job.workspace,
            name=payload["name"],
            personas=personas,
            agent_definition_id=payload.get("agent_definition_id"),
            agent_name=payload.get("agent_name"),
            description=payload.get("description", ""),
            modality=modality,
        )
    except ALKSimulateIngestionError as exc:
        raise HostedHarnessError("scenario_provision_failed", str(exc)) from exc
    if len(scenarios) != len(payload["personas"]):
        raise HostedHarnessError(
            "scenario_provision_count_mismatch",
            "scenario provisioning did not preserve the requested cardinality",
            status_code=500,
            retryable=True,
        )
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        if locked.run_test_id and locked.run_test_id != run_test.id:
            raise HostedHarnessError(
                "scenario_registration_conflict",
                "another attempt registered scenarios first",
                status_code=409,
            )
        locked.run_test = run_test
        locked.save(update_fields=["run_test", "updated_at"])
        registrations = [
            HostedHarnessScenario.no_workspace_objects.create(
                job=locked,
                scenario_key=persona["scenario_key"],
                scenario=scenario,
            )
            for persona, scenario in zip(payload["personas"], scenarios, strict=True)
        ]
        _record_target_agent_facts(locked, agent_definition, payload)
        _select_platform_evals(locked, run_test, payload, modality)
    return _provision_response(locked, registrations)


def _target_agent_prompt(job: HostedHarnessJob, payload: dict[str, Any]) -> str:
    """The target agent's instructions, from the guest or from the authored contract.

    The contract excerpt is a fallback rather than the source of record: it is an excerpt, and a
    guest that sends the real prompt should win.
    """
    supplied = str(payload.get("agent_prompt") or "").strip()
    if supplied:
        return supplied
    return str(_authored_contract_data(job).get("system_prompt_excerpt") or "").strip()


def _authored_contract_data(job: HostedHarnessJob) -> dict[str, Any]:
    """The authored contract body, or empty before the contract stage has landed."""
    for output in job.stage_outputs or []:
        if not isinstance(output, dict) or output.get("kind") != "contract":
            continue
        data = output.get("data")
        if isinstance(data, dict):
            return data
    return {}


def _record_target_agent_facts(
    job: HostedHarnessJob, agent_definition, payload: dict[str, Any]
) -> None:
    """Record the target agent's prompt and identity where the platform already looks.

    `agent_prompt` in an eval mapping resolves from the agent version's configuration snapshot,
    and that snapshot takes its ``description`` from the agent definition, so the definition's
    description is the platform's own home for an agent's instructions. Without a version row
    there is nothing for the mapping to read and nothing for the UI to show.
    """
    changed: list[str] = []
    prompt = _target_agent_prompt(job, payload)
    if prompt and agent_definition.description != prompt:
        agent_definition.description = prompt
        changed.append("description")
    # What the job genuinely knows about the target, so the definition stops reading as an
    # anonymous stub. `model` and `language` are deliberately left alone: the authored contract
    # carries neither, and the simulated caller's model is not the agent's.
    connector = str((job.payload.get("agent") or {}).get("connector") or "").lower()
    if connector in {"livekit", "vapi", "retell"} and not agent_definition.provider:
        agent_definition.provider = connector
        changed.append("provider")
    authored = _authored_contract_data(job)
    named = str(authored.get("agent") or "").strip()
    if named and agent_definition.agent_name == "alk-sdk-agent":
        agent_definition.agent_name = named[:255]
        changed.append("agent_name")
    direction = str(authored.get("call_direction") or "").strip().lower()
    if direction in {"inbound", "outbound"}:
        inbound = direction == "inbound"
        if agent_definition.inbound != inbound:
            agent_definition.inbound = inbound
            changed.append("inbound")
    if changed:
        agent_definition.save(update_fields=[*changed, "updated_at"])
    if prompt and agent_definition.latest_version is None:
        agent_definition.create_version(
            description=prompt,
            commit_message="hosted harness target agent prompt",
            status="active",
        )


def _select_platform_evals(
    job: HostedHarnessJob,
    run_test,
    payload: dict[str, Any],
    modality: str,
) -> None:
    """Bind the guest's chosen platform evals to this run, or refuse an unknown name."""
    from simulate.services.harness_evals import (
        UnknownEvalSelection,
        create_selected_eval_configs,
    )

    chosen = payload.get("chosen_evals") or []
    if not chosen:
        return
    try:
        create_selected_eval_configs(run_test, list(chosen), modality)
    except UnknownEvalSelection as unknown:
        raise HostedHarnessError(
            "eval_selection_unknown",
            f"eval template(s) not available to this organization: {unknown}",
            status_code=400,
        ) from unknown


def _resolve_scenario_modality(
    job: HostedHarnessJob, payload: dict[str, Any]
) -> str:
    """Resolve text/voice without requiring a lock-step guest rollout."""
    explicit = str(payload.get("modality") or "").lower()
    if explicit in {"text", "voice"}:
        return explicit
    for output in job.stage_outputs or []:
        if not isinstance(output, dict) or output.get("kind") != "contract":
            continue
        data = output.get("data")
        if not isinstance(data, dict):
            continue
        authored = str(data.get("modality") or "").lower()
        if authored in {"text", "voice"}:
            return authored
    return "text"


def begin_scenarios(
    attempt: HostedHarnessAttempt, payload: dict[str, Any]
) -> dict[str, Any]:
    job = attempt.job
    if not job.run_test_id or str(job.run_test_id) != str(payload["run_test_id"]):
        raise HostedHarnessError(
            "run_test_mismatch", "run test is not registered", status_code=403
        )
    registrations = list(
        HostedHarnessScenario.no_workspace_objects.filter(job=job).select_related(
            "scenario", "call_execution"
        )
    )
    by_key = {item.scenario_key: item for item in registrations}
    if set(payload["scenario_keys"]) != set(by_key):
        raise HostedHarnessError(
            "scenario_key_mismatch",
            "begin must name the complete sealed scenario set",
            status_code=409,
        )
    if job.test_execution_id:
        return _begin_response(job, registrations)

    test_execution = create_alk_sim_test_execution(
        job.run_test,
        scenario_ids=[item.scenario_id for item in registrations],
    )
    try:
        batch = create_alk_sim_call_execution_batch(
            test_execution, count=len(registrations)
        )
    except ALKSimulateIngestionError as exc:
        raise HostedHarnessError("scenario_begin_failed", str(exc)) from exc
    calls = CallExecution.no_workspace_objects.filter(
        id__in=batch.call_execution_ids
    ).select_related("scenario")
    calls_by_scenario: dict[uuid.UUID, CallExecution] = {}
    for call in calls:
        calls_by_scenario.setdefault(call.scenario_id, call)
    if set(calls_by_scenario) != {item.scenario_id for item in registrations}:
        raise HostedHarnessError(
            "scenario_call_mapping_incomplete",
            "execution pre-allocation did not create exactly one scenario call",
            status_code=500,
            retryable=True,
        )
    with transaction.atomic():
        locked = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=job.id
        )
        if locked.test_execution_id and locked.test_execution_id != test_execution.id:
            raise HostedHarnessError(
                "scenario_begin_conflict",
                "another attempt began the execution first",
                status_code=409,
            )
        locked.test_execution = test_execution
        locked.state = HostedHarnessJob.State.RUNNING
        locked.save(update_fields=["test_execution", "state", "updated_at"])
        for registration in registrations:
            registration.call_execution = calls_by_scenario[registration.scenario_id]
            registration.save(update_fields=["call_execution", "updated_at"])
    return _begin_response(locked, registrations)


def record_cleanup(
    attempt_id: uuid.UUID | str,
    *,
    provider_ref: str,
    verified_absent: bool,
    retry_pending: bool = False,
    details: dict[str, Any] | None = None,
) -> HostedHarnessJob:
    if not verified_absent:
        raise HostedHarnessError(
            "sandbox_cleanup_unverified",
            "provider did not verify that the sandbox is absent",
            status_code=409,
            retryable=True,
        )
    with transaction.atomic():
        attempt = (
            HostedHarnessAttempt.no_workspace_objects.select_for_update()
            .select_related("job")
            .get(id=attempt_id)
        )
        if attempt.provider_ref and attempt.provider_ref != provider_ref:
            raise HostedHarnessError(
                "provider_ref_mismatch",
                "cleanup provider reference does not match the registered lease",
                status_code=409,
            )
        HostedHarnessCleanupReceipt.no_workspace_objects.get_or_create(
            attempt=attempt,
            defaults={
                "provider_ref": provider_ref,
                "verified_absent": True,
                "details": details or {},
            },
        )
        now = timezone.now()
        attempt.cleanup_verified_at = now
        if attempt.state != HostedHarnessAttempt.State.SUPERSEDED:
            attempt.state = _attempt_terminal_state(attempt)
        attempt.save(update_fields=["cleanup_verified_at", "state", "updated_at"])
        job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
            id=attempt.job_id
        )
        if attempt.attempt_number < job.current_attempt_number:
            return job
        if retry_pending:
            job.state = HostedHarnessJob.State.RETRY_WAIT
            job.save(update_fields=["state", "updated_at"])
            return job
        if attempt.terminal_stage == "completed":
            job.state = HostedHarnessJob.State.COMPLETED
        elif attempt.terminal_stage == "canceled":
            job.state = HostedHarnessJob.State.CANCELED
        else:
            job.state = HostedHarnessJob.State.FAILED
        # Cleanup is an intermediate lifecycle stage. Once absence has been verified, expose the
        # guest's terminal stage so a completed job cannot remain visually stuck on cleaning_up.
        job.current_stage = attempt.terminal_stage or job.state
        job.terminal_at = now
        # Copy terminal stage/failure onto the job atomically so
        # status.failure and status.stage are authoritative in the read DTO.
        job.current_stage = attempt.terminal_stage or job.current_stage
        job.failure = attempt.terminal_failure
        job.save(
            update_fields=[
                "state",
                "terminal_at",
                "current_stage",
                "failure",
                "updated_at",
            ]
        )
        if job.test_execution_id:
            execution_status = {
                HostedHarnessJob.State.COMPLETED: TestExecution.ExecutionStatus.COMPLETED,
                HostedHarnessJob.State.CANCELED: TestExecution.ExecutionStatus.CANCELLED,
                HostedHarnessJob.State.FAILED: TestExecution.ExecutionStatus.FAILED,
            }[job.state]
            TestExecution.no_workspace_objects.filter(id=job.test_execution_id).update(
                status=execution_status,
                completed_at=now,
                error_reason=(
                    (attempt.terminal_failure or {}).get("message")
                    if job.state == HostedHarnessJob.State.FAILED
                    else None
                ),
            )
            remaining_status = (
                CallExecution.CallStatus.CANCELLED
                if job.state == HostedHarnessJob.State.CANCELED
                else CallExecution.CallStatus.FAILED
            )
            CallExecution.no_workspace_objects.filter(
                test_execution_id=job.test_execution_id,
                status__in=(
                    CallExecution.CallStatus.PENDING,
                    CallExecution.CallStatus.REGISTERED,
                    CallExecution.CallStatus.ONGOING,
                ),
            ).update(status=remaining_status, completed_at=now)
        return job


def update_execution_counts(job: HostedHarnessJob) -> None:
    if not job.test_execution_id:
        return
    receipts = HostedHarnessReceipt.no_workspace_objects.filter(job=job)
    # Harness receipt outcomes answer "did the scenario satisfy its checks?";
    # TestExecution counters answer "did the call transport complete?".  Keep
    # those dimensions separate so a completed, playable call with a failed
    # behavioural/evidence verdict is not reported as a failed call.
    scenario_completed = receipts.filter(status="passed").count()
    scenario_failed = receipts.filter(status__in=("failed", "errored")).count()
    calls = CallExecution.no_workspace_objects.filter(
        test_execution_id=job.test_execution_id
    )
    calls_completed = calls.filter(status=CallExecution.CallStatus.COMPLETED).count()
    calls_failed = calls.filter(
        status__in=(
            CallExecution.CallStatus.FAILED,
            CallExecution.CallStatus.CANCELLED,
        )
    ).count()
    TestExecution.no_workspace_objects.filter(id=job.test_execution_id).update(
        completed_calls=calls_completed,
        failed_calls=calls_failed,
    )
    HostedHarnessJob.no_workspace_objects.filter(id=job.id).update(
        completed_count=scenario_completed,
        failed_count=scenario_failed,
    )


def _attempt_terminal_state(attempt: HostedHarnessAttempt) -> str:
    if attempt.terminal_stage == "completed":
        return HostedHarnessAttempt.State.COMPLETED
    if attempt.terminal_stage == "canceled":
        return HostedHarnessAttempt.State.CANCELED
    return HostedHarnessAttempt.State.FAILED


def _provision_response(
    job: HostedHarnessJob, registrations: list[HostedHarnessScenario]
) -> dict[str, Any]:
    return {
        "result": {
            "run_test_id": str(job.run_test_id),
            "scenarios": [
                {
                    "scenario_key": item.scenario_key,
                    "scenario_id": str(item.scenario_id),
                }
                for item in registrations
            ],
        }
    }


def _begin_response(
    job: HostedHarnessJob, registrations: list[HostedHarnessScenario]
) -> dict[str, Any]:
    return {
        "result": {
            "test_execution_id": str(job.test_execution_id),
            "scenarios": [
                {
                    "scenario_key": item.scenario_key,
                    "scenario_id": str(item.scenario_id),
                    "call_execution_id": str(item.call_execution_id),
                }
                for item in registrations
            ],
        }
    }


def _json_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return _rfc3339(value)
    return value


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    value = value.astimezone(UTC)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
