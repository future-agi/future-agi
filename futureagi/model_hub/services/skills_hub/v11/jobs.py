"""Durable admissions, call reservations, outbox recovery and retention.

Quota -> job -> attempt/call is the lock order. No transport runs in a transaction.
Unknown calls are never replayed; finished_at is local-sender settlement, not proof
that a provider stopped computing or billing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from django.db import transaction
from django.db.models import Exists, F, Max, OuterRef, Q
from django.utils import timezone

from model_hub.models.skills_hub_v11 import (
    HubSkillExecutionQuota,
    HubSkillJob,
    HubSkillModelCall,
    HubSkillTestAttempt,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.inventory import canonical_json, fingerprint
from model_hub.services.skills_hub.v11.limits import effective_limits, retention_days
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
    require_skill_admin,
)
from model_hub.services.skills_hub.v11.types import (
    ACTIVE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    SkillPrincipal,
    SkillScope,
)

if TYPE_CHECKING:
    from model_hub.services.skills_hub.v11.types import CompletionResult

logger = logging.getLogger(__name__)
LIVE_CALL = Q(state__in=["prepared", "in_flight"]) | Q(
    state="unknown", finished_at__isnull=True
)


def principal_for_job(job: HubSkillJob) -> SkillPrincipal:
    return SkillPrincipal(
        SkillScope(job.organization_id, job.workspace_id),
        job.owner_user_id,
        job.actor_api_key_id,
        job.principal_kind,
    )


def check_job_access(job: HubSkillJob) -> None:
    principal = principal_for_job(job)
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    if (
        job.skill_id
        and not job.skill.__class__.no_workspace_objects.filter(
            id=job.skill_id,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
        ).exists()
    ):
        raise SkillsHubError(
            "access_revoked", "The skill is no longer available.", status=403
        )


def lock_quota(organization_id: UUID) -> HubSkillExecutionQuota:
    HubSkillExecutionQuota.objects.get_or_create(organization_id=organization_id)
    return HubSkillExecutionQuota.objects.select_for_update().get(
        organization_id=organization_id
    )


def existing_job(
    *, principal: SkillPrincipal, kind: str, idempotency_key: UUID, request_digest: str
) -> HubSkillJob | None:
    job = HubSkillJob.objects.filter(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        owner_user_id=principal.user_id,
        kind=kind,
        idempotency_key=idempotency_key,
    ).first()
    if job and job.request_digest != request_digest:
        raise SkillsHubError(
            "idempotency_conflict",
            "This key already identifies a different request.",
            status=409,
        )
    return job


def create_job_locked(
    *,
    principal: SkillPrincipal,
    kind: str,
    skill_id: UUID | None,
    idempotency_key: UUID,
    request_digest: str,
    deadline_at: datetime,
    limits: dict,
) -> HubSkillJob:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("create_job_locked requires the acceptance transaction")
    # Caller must acquire this quota before skill/suite/draft locks.
    quota = lock_quota(principal.scope.organization_id)
    existing = existing_job(
        principal=principal,
        kind=kind,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
    )
    if existing:
        return existing
    if kind not in {"test", "assist", "github"} or (
        skill_id is None and kind != "github"
    ):
        raise SkillsHubError(
            "invalid_test_config", "Invalid job kind or parent.", status=400
        )
    if skill_id is not None:
        from model_hub.models.skills_hub import HubSkill

        if not HubSkill.no_workspace_objects.filter(
            pk=skill_id,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
        ).exists():
            raise SkillsHubError("resource_not_found", "Skill not found.", status=404)
    kinds = ["github"] if kind == "github" else ["test", "assist"]
    maximum = (
        quota.max_active_github_jobs
        if kind == "github"
        else min(
            quota.max_active_model_jobs,
            effective_limits()["max_active_model_jobs_per_organization"],
        )
    )
    active = HubSkillJob.objects.filter(
        organization_id=principal.scope.organization_id,
        kind__in=kinds,
        reservation_released_at__isnull=True,
    ).count()
    if active >= maximum:
        raise SkillsHubError(
            "skills_capacity_exceeded",
            "Organization execution capacity is full.",
            status=429,
            detail={"retry_at": None},
        )
    job_id = uuid4()
    return HubSkillJob.objects.create(
        id=job_id,
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        owner_user_id=principal.user_id,
        actor_api_key_id=principal.api_key_id,
        principal_kind=principal.kind,
        kind=kind,
        skill_id=skill_id,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        workflow_id=f"skills-job-{job_id}",
        deadline_at=deadline_at,
        max_total_tokens=limits.get("max_total_tokens"),
        max_estimated_cost_usd=limits.get("max_estimated_cost_usd"),
    )


def dispatch_job(*, job_id: UUID, scope: SkillScope) -> None:
    from tfc.temporal.skills_hub.client import start_skills_job

    query = HubSkillJob.objects.filter(
        id=job_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
    )
    job = query.first()
    if job is None or job.status not in ACTIVE_JOB_STATUSES:
        return
    query.update(dispatch_attempts=F("dispatch_attempts") + 1)
    try:
        start_skills_job(job_id=job.id, scope=scope, deadline_at=job.deadline_at)
    except (TimeoutError, ConnectionError, OSError):
        query.update(dispatch_state="unknown")
        logger.warning(
            "Skills dispatch acceptance unknown", extra={"job_id": str(job_id)}
        )
    else:
        query.update(dispatch_state="accepted")


def cancel_job(*, principal: SkillPrincipal, job_id: UUID) -> HubSkillJob:
    require_private_principal(principal=principal)
    require_scope_access(principal=principal, write=True)
    with transaction.atomic():
        job = (
            HubSkillJob.objects.select_for_update(of=("self",))
            .filter(
                id=job_id,
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
            )
            .select_related("skill")
            .first()
        )
        if job is None:
            raise SkillsHubError("resource_not_found", "Job not found.", status=404)
        if job.owner_user_id != principal.user_id:
            if job.skill is None:
                # Initial preview has no target skill: workspace admin still requires current scope.
                from model_hub.services.skills_hub.v11.principals import (
                    effective_scope_level,
                )
                from tfc.constants.levels import Level

                if effective_scope_level(principal=principal) < Level.WORKSPACE_ADMIN:
                    raise SkillsHubError(
                        "resource_not_found", "Job not found.", status=404
                    )
            else:
                require_skill_admin(principal=principal, skill=job.skill)
        if job.status not in TERMINAL_JOB_STATUSES:
            job.cancel_requested_at = job.cancel_requested_at or timezone.now()
            job.status = "cancelling"
            job.save(update_fields=["cancel_requested_at", "status"])
    return job


def check_job_running(job: HubSkillJob) -> None:
    check_job_access(job)
    if job.status not in {"queued", "running"} or job.cancel_requested_at:
        raise SkillsHubError(
            "execution_cancelled",
            "Execution stopped; no new calls are allowed.",
            status=409,
        )
    if timezone.now() >= job.deadline_at:
        raise SkillsHubError("provider_timeout", "Job deadline reached.", status=422)


def reserve_call(
    *,
    job_id: UUID,
    attempt_id: UUID | None,
    kind: str,
    request_digest: str,
    token_allowance: int,
    estimated_allowance: Decimal,
) -> HubSkillModelCall:
    parent = HubSkillJob.objects.get(id=job_id)
    with transaction.atomic():
        quota = lock_quota(parent.organization_id)
        job = HubSkillJob.objects.select_for_update().get(id=job_id)
        check_job_running(job)
        if kind not in {"task", "judge", "assist"} or (kind == "assist") != (
            job.kind == "assist"
        ):
            raise SkillsHubError(
                "invalid_test_config", "Call kind does not match job.", status=400
            )
        generation = job.claim_generation
        if attempt_id is not None:
            attempt = (
                HubSkillTestAttempt.objects.select_for_update()
                .filter(id=attempt_id, run_id=job_id)
                .first()
            )
            if attempt is None or attempt.status != "running":
                raise SkillsHubError(
                    "execution_unknown",
                    "Attempt is not owned by a live execution.",
                    status=409,
                )
            generation = attempt.claim_generation
            cap = 6 if kind == "task" else 1
            if (
                HubSkillModelCall.objects.filter(attempt=attempt, kind=kind).count()
                >= cap
            ):
                raise SkillsHubError(
                    "budget_exhausted", "Attempt call limit reached.", status=422
                )
        elif kind != "assist":
            raise SkillsHubError(
                "invalid_test_config",
                "A task or judge call requires an attempt.",
                status=400,
            )
        elif HubSkillModelCall.objects.filter(job=job).exists():
            raise SkillsHubError(
                "execution_unknown",
                "One-shot assistance cannot replay a provider call.",
                status=409,
            )
        maximum = min(
            quota.max_active_provider_calls,
            effective_limits()["max_active_provider_calls_per_organization"],
        )
        if (
            HubSkillModelCall.objects.filter(
                LIVE_CALL, job__organization_id=job.organization_id
            ).count()
            >= maximum
        ):
            raise SkillsHubError(
                "skills_capacity_exceeded",
                "Provider concurrency is full.",
                status=429,
                detail={"retry_at": None},
            )
        if (
            token_allowance <= 0
            or estimated_allowance <= 0
            or job.max_total_tokens is None
            or job.max_estimated_cost_usd is None
        ):
            raise SkillsHubError(
                "budget_exhausted",
                "Explicit token and spend allowances are required.",
                status=422,
            )
        if (
            job.reserved_tokens
            + job.known_input_tokens
            + job.known_output_tokens
            + token_allowance
            > job.max_total_tokens
            or job.reserved_estimate_usd + job.known_estimate_usd + estimated_allowance
            > job.max_estimated_cost_usd
        ):
            raise SkillsHubError(
                "budget_exhausted",
                "Next request exceeds the confirmed remaining budget.",
                status=422,
            )
        sequence = (
            HubSkillModelCall.objects.filter(job=job).aggregate(value=Max("sequence"))[
                "value"
            ]
            or 0
        ) + 1
        call = HubSkillModelCall.objects.create(
            job=job,
            attempt_id=attempt_id,
            kind=kind,
            sequence=sequence,
            claim_generation=generation,
            request_digest=request_digest,
            reserved_tokens=token_allowance,
            reserved_estimate_usd=estimated_allowance,
        )
        job.reserved_tokens += token_allowance
        job.reserved_estimate_usd += estimated_allowance
        job.save(update_fields=["reserved_tokens", "reserved_estimate_usd"])
        return call


def mark_in_flight(*, call_id: UUID, generation: int, connection: dict) -> None:
    parent = HubSkillModelCall.objects.select_related("job").get(id=call_id)
    with transaction.atomic():
        job = HubSkillJob.objects.select_for_update().get(id=parent.job_id)
        check_job_running(job)
        call = HubSkillModelCall.objects.select_for_update().get(id=call_id)
        if call.claim_generation != generation or call.state != "prepared":
            raise SkillsHubError(
                "execution_unknown", "Call has already been claimed.", status=409
            )
        if (
            call.attempt_id
            and not HubSkillTestAttempt.objects.filter(
                id=call.attempt_id, claim_generation=generation, status="running"
            ).exists()
        ):
            raise SkillsHubError(
                "execution_unknown", "Attempt claim is stale.", status=409
            )
        call.state, call.started_at = "in_flight", timezone.now()
        call.connection_reference, call.connection_revision = (
            connection["connection_id"],
            connection["revision"],
        )
        call.requested_model, call.pricing_source = (
            connection["model_id"],
            connection["pricing_source"],
        )
        call.save(
            update_fields=[
                "state",
                "started_at",
                "connection_reference",
                "connection_revision",
                "requested_model",
                "pricing_source",
            ]
        )


def settle_call(
    *,
    call_id: UUID,
    generation: int,
    result: CompletionResult | None,
    error: dict | None,
) -> None:
    parent = HubSkillModelCall.objects.select_related("job").get(id=call_id)
    with transaction.atomic():
        lock_quota(parent.job.organization_id)
        job = HubSkillJob.objects.select_for_update().get(id=parent.job_id)
        call = HubSkillModelCall.objects.select_for_update().get(id=call_id)
        if generation != call.claim_generation or call.finished_at is not None:
            return
        stale = job.status in TERMINAL_JOB_STATUSES
        if call.attempt_id:
            stale |= not HubSkillTestAttempt.objects.filter(
                id=call.attempt_id, claim_generation=generation, status="running"
            ).exists()
        # Local transport has settled. Unknown usage still retains its entire spend reservation.
        call.finished_at = timezone.now()
        call.state = (
            "completed"
            if result
            else "unknown"
            if call.started_at is not None
            else "failed"
        )
        if result:
            call.provider_request_id, call.returned_model = (
                result.provider_request_id,
                result.returned_model,
            )
            call.usage, call.estimated_cost_usd = (
                result.usage,
                result.estimated_cost_usd,
            )
            call.latency_ms, call.finish_reason = (
                result.latency_ms,
                result.finish_reason,
            )
            call.pricing_source = result.pricing_source
            if not stale:
                call.assistant_message = result.assistant_message
        call.error = error
        known = (
            result is not None
            and result.usage is not None
            and result.estimated_cost_usd is not None
        )
        if known:
            job.reserved_tokens = max(0, job.reserved_tokens - call.reserved_tokens)
            job.reserved_estimate_usd = max(
                Decimal(0), job.reserved_estimate_usd - call.reserved_estimate_usd
            )
            job.known_input_tokens += result.usage["input_tokens"]
            job.known_output_tokens += result.usage["output_tokens"]
            job.known_estimate_usd += result.estimated_cost_usd
        elif call.started_at is None:
            job.reserved_tokens = max(0, job.reserved_tokens - call.reserved_tokens)
            job.reserved_estimate_usd = max(
                Decimal(0), job.reserved_estimate_usd - call.reserved_estimate_usd
            )
            call.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            call.estimated_cost_usd = Decimal(0)
        else:
            job.unknown_call_count += 1
        call.save()
        job.save(
            update_fields=[
                "reserved_tokens",
                "reserved_estimate_usd",
                "known_input_tokens",
                "known_output_tokens",
                "known_estimate_usd",
                "unknown_call_count",
            ]
        )


def check_attempt_payload(*, attempt_id: UUID | None, assistant_message: dict) -> None:
    if attempt_id is None:
        return
    attempt = HubSkillTestAttempt.objects.select_related("run").get(id=attempt_id)
    if attempt.run.snapshot is None:
        raise SkillsHubError(
            "evidence_expired", "Detailed evidence expired.", status=410
        )
    case = next(
        case
        for case in attempt.run.snapshot["cases"]
        if case["id"] == str(attempt.case_id)
    )
    messages = list(
        HubSkillModelCall.objects.filter(attempt=attempt)
        .exclude(assistant_message=None)
        .values_list("assistant_message", flat=True)
    )
    # Reserve bounded envelope/metrics/receipts/judge-excerpt overhead. The next
    # answer can also appear as a call message, so count it twice before storing.
    used = len(
        canonical_json(
            {
                "case": case,
                "expected_checks": [check["config"] for check in case["checks"]],
                "calls": messages,
                "answer": attempt.answer,
            }
        ).encode()
    )
    used += 2 * len(canonical_json(assistant_message).encode()) + 64 * 1024
    if used > 256 * 1024:
        raise SkillsHubError(
            "evidence_limit",
            "Attempt evidence would exceed its 256 KiB response allowance.",
            status=422,
        )


async def perform_model_call(
    *,
    scope: SkillScope,
    job_id: UUID,
    attempt_id: UUID | None,
    kind: str,
    configuration: dict,
    frozen_connection: dict,
    messages: list[dict],
    tools: list[dict],
    timeout_ms: int,
) -> CompletionResult:
    from asgiref.sync import sync_to_async

    from model_hub.services.skills_hub.v11.model_calls import (
        call_allowance,
        complete_skill_turn,
        resolve_test_connection,
    )

    job = await sync_to_async(HubSkillJob.objects.get)(
        id=job_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
    )
    await sync_to_async(check_job_running)(job)
    connection = await sync_to_async(resolve_test_connection)(
        scope=scope, model_id=configuration["model_id"]
    )
    if connection.identity() != frozen_connection:
        raise SkillsHubError(
            "test_plan_changed",
            "Workspace model connection or pricing changed after acceptance.",
            status=409,
        )
    remaining_ms = int((job.deadline_at - timezone.now()).total_seconds() * 1000)
    timeout_ms = min(timeout_ms, remaining_ms, 45000)
    if timeout_ms <= 0:
        raise SkillsHubError(
            "provider_timeout", "No execution time remains.", status=422
        )
    tokens, estimate = call_allowance(
        connection=connection,
        messages=messages,
        tools=tools,
        max_output_tokens=configuration["max_output_tokens"],
    )
    await sync_to_async(check_attempt_payload)(
        attempt_id=attempt_id, assistant_message={}
    )
    call = await sync_to_async(reserve_call)(
        job_id=job_id,
        attempt_id=attempt_id,
        kind=kind,
        request_digest=fingerprint(
            {"messages": messages, "tools": tools, "configuration": configuration}
        ),
        token_allowance=tokens,
        estimated_allowance=estimate,
    )
    try:
        await sync_to_async(mark_in_flight)(
            call_id=call.id,
            generation=call.claim_generation,
            connection=connection.identity(),
        )
        result = await complete_skill_turn(
            connection=connection,
            messages=messages,
            tools=tools,
            parameters={
                "temperature": configuration["temperature"],
                "max_output_tokens": configuration["max_output_tokens"],
            },
            timeout_ms=timeout_ms,
        )
    except SkillsHubError as error:
        await sync_to_async(settle_call)(
            call_id=call.id,
            generation=call.claim_generation,
            result=None,
            error={"code": error.code, "message": error.message, "detail": {}},
        )
        raise
    except asyncio.CancelledError:
        # wait_for/transport cancellation closes this local sender, not the provider's work.
        await asyncio.shield(
            sync_to_async(settle_call)(
                call_id=call.id,
                generation=call.claim_generation,
                result=None,
                error={
                    "code": "execution_unknown",
                    "message": "Request cancelled with unknown provider outcome.",
                    "detail": {},
                },
            )
        )
        raise
    try:
        await sync_to_async(check_attempt_payload)(
            attempt_id=attempt_id, assistant_message=result.assistant_message
        )
    except SkillsHubError as error:
        await sync_to_async(settle_call)(
            call_id=call.id,
            generation=call.claim_generation,
            result=replace(result, assistant_message={}),
            error={"code": error.code, "message": error.message, "detail": {}},
        )
        raise
    await sync_to_async(settle_call)(
        call_id=call.id, generation=call.claim_generation, result=result, error=None
    )
    return result


def start_job(*, scope: SkillScope, job_id: UUID) -> dict:
    with transaction.atomic():
        job = (
            HubSkillJob.objects.select_for_update()
            .filter(
                id=job_id,
                organization_id=scope.organization_id,
                workspace_id=scope.workspace_id,
            )
            .first()
        )
        if job is None:
            raise SkillsHubError("resource_not_found", "Job not found.", status=404)
        if job.status in TERMINAL_JOB_STATUSES:
            return {
                "id": str(job.id),
                "kind": job.kind,
                "status": job.status,
                "attempts": [],
                "remaining_seconds": 0,
            }
        check_job_running(job)
        if job.started_at is None and timezone.now() > job.created_at + timedelta(
            seconds=effective_limits()["max_queue_wait_seconds"]
        ):
            raise SkillsHubError(
                "queue_timeout",
                "Execution did not begin within the queue allowance.",
                status=422,
            )
        if job.started_at is None:
            job.started_at, job.claim_generation = (
                timezone.now(),
                job.claim_generation + 1,
            )
        job.status, job.heartbeat_at = "running", timezone.now()
        job.save(
            update_fields=["started_at", "claim_generation", "status", "heartbeat_at"]
        )
        attempts = list(
            HubSkillTestAttempt.objects.filter(run_id=job_id, status="not_started")
            .order_by("schedule_index")
            .values_list("id", flat=True)
        )
        concurrency = min(2, effective_limits()["max_active_attempts_per_run"])
        if job.kind == "test":
            concurrency = min(
                concurrency,
                job.hub_skill_test_run.snapshot["limits"][
                    "max_active_attempts_per_run"
                ],
            )
        return {
            "id": str(job.id),
            "kind": job.kind,
            "status": job.status,
            "attempts": [str(value) for value in attempts],
            "max_active_attempts": concurrency,
            "remaining_seconds": max(
                0, int((job.deadline_at - timezone.now()).total_seconds())
            ),
        }


def finish_job(
    *, scope: SkillScope, job_id: UUID, failure_code: str | None = None
) -> dict:
    parent = HubSkillJob.objects.get(
        id=job_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
    )
    if parent.kind == "test":
        from model_hub.services.skills_hub.v11.grading import aggregate_test_run

        aggregate_test_run(scope=scope, run_id=job_id)
    with transaction.atomic():
        lock_quota(parent.organization_id)
        job = HubSkillJob.objects.select_for_update().get(id=job_id)
        stopping = (
            failure_code
            or job.cancel_requested_at
            or timezone.now() >= job.deadline_at
            or job.status in TERMINAL_JOB_STATUSES
        )
        if stopping:
            # A prepared request provably never crossed mark_in_flight. Holding the
            # job fence prevents its sender from transmitting after this settlement.
            for prepared in HubSkillModelCall.objects.select_for_update().filter(
                job=job, state="prepared"
            ):
                prepared.state, prepared.finished_at = "failed", timezone.now()
                prepared.usage = {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                }
                prepared.estimated_cost_usd = Decimal(0)
                prepared.error = {
                    "code": "execution_cancelled",
                    "message": "Request stopped before transmission.",
                    "detail": {},
                }
                prepared.save(
                    update_fields=[
                        "state",
                        "finished_at",
                        "usage",
                        "estimated_cost_usd",
                        "error",
                    ]
                )
                job.reserved_tokens = max(
                    0, job.reserved_tokens - prepared.reserved_tokens
                )
                job.reserved_estimate_usd = max(
                    Decimal(0),
                    job.reserved_estimate_usd - prepared.reserved_estimate_usd,
                )
        live = HubSkillModelCall.objects.filter(LIVE_CALL, job=job).exists()
        if job.status not in TERMINAL_JOB_STATUSES:
            if job.cancel_requested_at:
                job.status = "cancelled" if not live else "cancelling"
            elif timezone.now() >= job.deadline_at or failure_code == "queue_timeout":
                job.status = "timed_out"
            elif failure_code:
                job.status = (
                    "interrupted" if failure_code == "execution_unknown" else "failed"
                )
            elif HubSkillTestAttempt.objects.filter(
                run_id=job_id, status="running"
            ).exists():
                return {"id": str(job.id), "status": job.status}
            else:
                job.status = "completed"
            job.failure_code = failure_code
            if job.status in TERMINAL_JOB_STATUSES:
                job.finished_at = timezone.now()
                job.claim_generation += 1
                HubSkillTestAttempt.objects.filter(
                    run_id=job_id, status="not_started"
                ).update(
                    status="cancelled" if job.cancel_requested_at else "incomplete",
                    finished_at=job.finished_at,
                )
                HubSkillTestAttempt.objects.filter(
                    run_id=job_id, status="running"
                ).update(
                    status="unknown" if live else "incomplete",
                    finished_at=job.finished_at,
                    claim_generation=F("claim_generation") + 1,
                )
                if job.kind == "test":
                    from model_hub.models.skills_hub_v11 import HubSkillTestRun

                    run = HubSkillTestRun.objects.get(job=job)
                    days = (
                        run.snapshot.get("retention_days", retention_days())
                        if run.snapshot
                        else retention_days()
                    )
                    HubSkillTestRun.objects.filter(job=job).update(
                        payload_expires_at=job.finished_at + timedelta(days=days)
                    )
                elif job.kind == "assist":
                    from model_hub.models.skills_hub_v11 import HubSkillAssistProposal

                    proposal = HubSkillAssistProposal.objects.get(job=job)
                    days = (
                        proposal.input.get("retention_days", retention_days())
                        if proposal.input
                        else retention_days()
                    )
                    HubSkillAssistProposal.objects.filter(
                        job=job, payload_expires_at__isnull=True
                    ).update(payload_expires_at=job.finished_at + timedelta(days=days))
        if job.status in TERMINAL_JOB_STATUSES and not live:
            job.reservation_released_at = job.reservation_released_at or timezone.now()
        job.save(
            update_fields=[
                "status",
                "failure_code",
                "finished_at",
                "claim_generation",
                "reservation_released_at",
                "reserved_tokens",
                "reserved_estimate_usd",
            ]
        )
    if parent.kind == "test":
        aggregate_test_run(scope=scope, run_id=job_id)
    return {"id": str(job.id), "status": job.status}


def heartbeat_job(*, scope: SkillScope, job_id: UUID) -> None:
    HubSkillJob.objects.filter(
        id=job_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
        status__in=ACTIVE_JOB_STATUSES,
    ).update(heartbeat_at=timezone.now())


def reconcile_jobs(*, batch_size: int = 100) -> dict:
    now = timezone.now()
    needs_attention = (
        Q(status__in=ACTIVE_JOB_STATUSES, dispatch_state__in=["pending", "unknown"])
        | Q(status__in=ACTIVE_JOB_STATUSES, deadline_at__lte=now)
        | Q(status="queued", created_at__lte=now - timedelta(seconds=60))
        | Q(status="running", heartbeat_at__lt=now - timedelta(seconds=170))
        | Q(status__in=ACTIVE_JOB_STATUSES, cancel_requested_at__isnull=False)
        | Q(status__in=TERMINAL_JOB_STATUSES, has_live_sender=False)
    )
    # Quarantined terminal senders must not permanently starve later tenants out
    # of the first batch. A late local settlement makes them eligible again.
    jobs = list(
        HubSkillJob.objects.filter(reservation_released_at__isnull=True)
        .annotate(
            has_live_sender=Exists(
                HubSkillModelCall.objects.filter(LIVE_CALL, job_id=OuterRef("pk"))
            )
        )
        .filter(needs_attention)
        .order_by("created_at")[: min(max(batch_size, 1), 100)]
    )
    result = {"examined": len(jobs), "dispatched": 0, "finalized": 0, "quarantined": 0}
    for job in jobs:
        scope = SkillScope(job.organization_id, job.workspace_id)
        expired_queue = job.started_at is None and now >= job.created_at + timedelta(
            seconds=60
        )
        lost_worker = (
            job.status == "running"
            and job.heartbeat_at is not None
            and now > job.heartbeat_at + timedelta(seconds=170)
        )
        if (
            expired_queue
            or now >= job.deadline_at
            or job.cancel_requested_at
            or lost_worker
            or job.status in TERMINAL_JOB_STATUSES
        ):
            if lost_worker:
                # Fences lost executions without declaring that their local senders died.
                with transaction.atomic():
                    locked = HubSkillJob.objects.select_for_update().get(id=job.id)
                    if (
                        locked.heartbeat_at != job.heartbeat_at
                        or locked.status != "running"
                    ):
                        continue
                    HubSkillModelCall.objects.filter(
                        job=locked, state="in_flight"
                    ).update(state="unknown")
                    HubSkillTestAttempt.objects.filter(
                        run_id=job.id, status="running"
                    ).update(
                        status="unknown", claim_generation=F("claim_generation") + 1
                    )
            terminal = finish_job(
                scope=scope,
                job_id=job.id,
                failure_code="queue_timeout"
                if expired_queue
                else "execution_unknown"
                if lost_worker
                else None,
            )
            result["finalized"] += terminal["status"] in TERMINAL_JOB_STATUSES
            result["quarantined"] += HubSkillModelCall.objects.filter(
                LIVE_CALL, job=job
            ).exists()
        elif job.dispatch_state in {"pending", "unknown"}:
            dispatch_job(job_id=job.id, scope=scope)
            result["dispatched"] += 1
    return result


def purge_expired_payloads(*, batch_size: int = 100) -> dict:
    from model_hub.models.skills_hub_v11 import (
        HubSkillAssistProposal,
        HubSkillGitHubPreview,
        HubSkillTestRun,
    )

    now, limit = timezone.now(), min(max(batch_size, 1), 100)
    result = {"runs": 0, "proposals": 0, "previews": 0}
    for run_id in list(
        HubSkillTestRun.objects.filter(
            payload_expires_at__lte=now, payload_purged_at__isnull=True
        ).values_list("job_id", flat=True)[:limit]
    ):
        with transaction.atomic():
            job = HubSkillJob.objects.select_for_update().get(id=run_id)
            if HubSkillModelCall.objects.filter(LIVE_CALL, job=job).exists():
                continue
            run = HubSkillTestRun.objects.select_for_update().get(job_id=run_id)
            run.snapshot, run.payload_purged_at = None, now
            run.summary = {
                key: value
                for key, value in run.summary.items()
                if key != "case_results"
            }
            run.save(update_fields=["snapshot", "payload_purged_at", "summary"])
            for attempt in HubSkillTestAttempt.objects.filter(run=run):
                attempt.answer, attempt.receipts = None, []
                if attempt.error:
                    attempt.error = {
                        "code": attempt.error["code"],
                        "message": "Detailed error payload expired.",
                        "detail": {},
                    }
                attempt.check_results = [
                    {
                        "check_id": check["check_id"],
                        "category": check["category"],
                        "verdict": check["verdict"],
                    }
                    for check in attempt.check_results
                ]
                attempt.save(
                    update_fields=["answer", "receipts", "error", "check_results"]
                )
            HubSkillModelCall.objects.filter(job=job).update(
                assistant_message=None, tool_receipts=[]
            )
            result["runs"] += 1
    for proposal_id in list(
        HubSkillAssistProposal.objects.filter(
            payload_expires_at__lte=now, payload_purged_at__isnull=True
        ).values_list("job_id", flat=True)[:limit]
    ):
        with transaction.atomic():
            job = HubSkillJob.objects.select_for_update().get(id=proposal_id)
            if HubSkillModelCall.objects.filter(LIVE_CALL, job=job).exists():
                continue
            HubSkillAssistProposal.objects.filter(job=job).update(
                input=None,
                proposal=None,
                missing_information=[],
                checks=None,
                payload_purged_at=now,
            )
            HubSkillModelCall.objects.filter(job=job).update(
                assistant_message=None, tool_receipts=[]
            )
            result["proposals"] += 1
    expired_previews = list(
        HubSkillGitHubPreview.objects.filter(expires_at__lte=now)
        .exclude(bundle=None, diff=None, license={})
        .values_list("job_id", flat=True)[:limit]
    )
    result["previews"] = HubSkillGitHubPreview.objects.filter(
        job_id__in=expired_previews
    ).update(bundle=None, diff=None, license={})
    return result
