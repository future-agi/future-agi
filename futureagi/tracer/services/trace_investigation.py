from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q
from django.utils import timezone

from tracer.models.project import Project
from tracer.models.trace_investigation import (
    TraceInvestigationAttempt,
    TraceInvestigationAttemptStatus,
    TraceInvestigationAttribution,
    TraceInvestigationAttributionEvidence,
    TraceInvestigationDelivery,
    TraceInvestigationEvidenceReceipt,
    TraceInvestigationFinding,
    TraceInvestigationFindingEvidence,
    TraceInvestigationGatewayCall,
    TraceInvestigationGroupingStatus,
    TraceInvestigationJob,
    TraceInvestigationJobState,
    TraceInvestigationReport,
    TraceInvestigationRequirementCheck,
    TraceInvestigationRequirementEvidence,
    TraceInvestigationSource,
    TraceInvestigationVerificationReceipt,
)
from tracer.models.trace_scan import TraceScanConfig
from tracer.queries.trace_scanner import is_trace_sampled
from tracer.services.trace_investigation_billing import charge_trace_investigation

CONTRACT_VERSION = "omega-investigation/v1"
_DEFAULT_LIMITS = {
    "deadline_seconds": 180,
    "max_model_calls": 12,
    "max_children": 2,
    "max_parallel_children": 2,
    "max_input_tokens_total": 60_000,
    "max_output_tokens_total": 8_000,
    "max_evidence_bytes": 64 * 1024 * 1024,
    "max_tool_result_bytes": 16 * 1024,
}
_LIMIT_CEILINGS = {
    "deadline_seconds": 600,
    "max_model_calls": 50,
    "max_children": 8,
    "max_parallel_children": 4,
    "max_input_tokens_total": 250_000,
    "max_output_tokens_total": 50_000,
    "max_evidence_bytes": 128 * 1024 * 1024,
    "max_tool_result_bytes": 1024 * 1024,
}


class InvestigationControlError(Exception):
    code = "invalid_request"


class InvestigationNotFound(InvestigationControlError):
    code = "not_found"


class InvestigationConflict(InvestigationControlError):
    code = "conflict"


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    return value


def _digest(value: object) -> str:
    encoded = json.dumps(
        _json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _json_number(value: int | float) -> str:
    """Render finite JSON numbers the way JSON.stringify does for our wire shape."""
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise InvestigationConflict("report contains an unsafe JSON integer")
        return str(value)
    if value != value or value in {float("inf"), float("-inf")}:
        raise InvestigationConflict("report contains a non-finite JSON number")
    if value == 0:
        return "0"

    negative = value < 0
    representation = repr(abs(value)).lower()
    mantissa, separator, exponent_text = representation.partition("e")
    exponent = int(exponent_text) if separator else 0
    integer, dot, fraction = mantissa.partition(".")
    raw_digits = integer + (fraction if dot else "")
    decimal_position = len(integer) + exponent
    leading_zeroes = len(raw_digits) - len(raw_digits.lstrip("0"))
    digits = raw_digits.lstrip("0") or "0"
    decimal_position -= leading_zeroes
    digits = digits.rstrip("0") or "0"

    if 0 < decimal_position <= 21:
        if decimal_position >= len(digits):
            rendered = digits + ("0" * (decimal_position - len(digits)))
        else:
            rendered = f"{digits[:decimal_position]}.{digits[decimal_position:]}"
    elif -6 < decimal_position <= 0:
        rendered = f"0.{('0' * -decimal_position)}{digits}"
    else:
        rendered = digits[0]
        if len(digits) > 1:
            rendered += f".{digits[1:]}"
        rendered += f"e{'+' if decimal_position - 1 >= 0 else ''}{decimal_position - 1}"
    return f"-{rendered}" if negative else rendered


def _canonical_wire_json(value: object) -> str:
    """Stable JSON shared with the Node worker for report content identity."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _json_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (datetime, date, uuid.UUID, Decimal)):
        return _canonical_wire_json(_json_value(value))
    if isinstance(value, (list, tuple)):
        return f"[{','.join(_canonical_wire_json(item) for item in value)}]"
    if isinstance(value, Mapping):
        entries = []
        for key in sorted(value, key=lambda item: str(item)):
            entries.append(
                f"{_canonical_wire_json(str(key))}:{_canonical_wire_json(value[key])}"
            )
        return f"{{{','.join(entries)}}}"
    raise InvestigationConflict("report contains a non-JSON value")


def canonical_wire_result_digest(result: Mapping[str, object]) -> str:
    """Hash the report wire object, excluding its self-referential digest field."""
    content = {key: value for key, value in result.items() if key != "result_digest"}
    encoded = _canonical_wire_json(content).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _workspace_matches(actual: object, expected: object) -> bool:
    return (str(actual) if actual is not None else None) == (
        str(expected) if expected is not None else None
    )


def _project_for_event(value: Mapping[str, object]) -> Project:
    try:
        return Project.no_workspace_objects.get(
            id=value["project_id"],
            organization_id=value["organization_id"],
            workspace_id=value["workspace_id"],
            trace_type="observe",
        )
    except Project.DoesNotExist as exc:
        raise InvestigationNotFound("project scope was not found") from exc


def _pending(job: TraceInvestigationJob) -> dict[str, object]:
    return {
        "organization_id": job.organization_id,
        "workspace_id": job.workspace_id,
        "project_id": job.project_id,
        "trace_id": job.trace_id,
        "job_id": job.id,
        "generation": job.generation,
        "state": job.state,
        "not_before": job.not_before,
    }


def record_trace_notifications(
    *, deliveries: list[Mapping[str, object]]
) -> dict[str, object]:
    """Deduplicate one bounded broker batch and advance trace generations."""
    accepted = 0
    duplicates = 0
    pending: dict[uuid.UUID, dict[str, object]] = {}
    not_before = timezone.now() + timedelta(
        seconds=settings.ERROR_FEED_OMEGA_DELAY_SECONDS
    )

    with transaction.atomic():
        for delivery in deliveries:
            value = delivery["value"]
            project = _project_for_event(value)
            # Serialize notifications for one project so two new event IDs cannot
            # race the same trace generation.
            Project.no_workspace_objects.select_for_update().get(id=project.id)
            payload_digest = _digest(value)
            # Event IDs survive broker/topic recreation; offsets do not. Keep
            # broker coordinates as provenance, never as application identity.
            receipts = list(
                TraceInvestigationDelivery.no_workspace_objects.filter(
                    organization_id=value["organization_id"],
                    event_id=value["event_id"],
                )[:1]
            )
            if receipts:
                if len(receipts) != 1 or receipts[0].payload_digest != payload_digest:
                    raise InvestigationConflict(
                        "notification identity was reused with a different payload"
                    )
                duplicates += 1
            else:
                TraceInvestigationDelivery.no_workspace_objects.create(
                    organization_id=value["organization_id"],
                    workspace_id=value["workspace_id"],
                    project=project,
                    topic=delivery["topic"],
                    partition=delivery["partition"],
                    offset=delivery["offset"],
                    event_id=value["event_id"],
                    payload_digest=payload_digest,
                )
                accepted += 1

            config = TraceScanConfig.no_workspace_objects.filter(
                project=project, enabled=True
            ).first()
            if config is None:
                continue
            for trace in value["traces"]:
                if not is_trace_sampled(str(trace["trace_id"]), config.sampling_rate):
                    continue
                job = (
                    TraceInvestigationJob.no_workspace_objects.select_for_update()
                    .filter(project=project, trace_id=trace["trace_id"])
                    .first()
                )
                if job is None:
                    if receipts:
                        # An event sampled out on first delivery stays a no-op
                        # on retry, even if the configured rate later increases.
                        continue
                    job = TraceInvestigationJob.no_workspace_objects.create(
                        organization_id=value["organization_id"],
                        workspace_id=value["workspace_id"],
                        project=project,
                        trace_id=trace["trace_id"],
                        root_span_id=trace["root_span_id"],
                        root_end_time=trace["root_end_time"],
                        not_before=not_before,
                    )
                elif not receipts:
                    job.generation += 1
                    job.root_span_id = trace["root_span_id"]
                    job.root_end_time = trace["root_end_time"]
                    job.not_before = not_before
                    if job.state != TraceInvestigationJobState.RUNNING:
                        job.state = TraceInvestigationJobState.WAITING
                    job.save(
                        update_fields=[
                            "generation",
                            "root_span_id",
                            "root_end_time",
                            "not_before",
                            "state",
                            "updated_at",
                        ]
                    )
                pending[job.id] = _pending(job)

    return {
        "accepted_events": accepted,
        "duplicate_events": duplicates,
        "pending": list(pending.values()),
    }


def validate_investigation_memory(memory: object) -> tuple[list[dict], str]:
    """Normalize a bounded project memory value and return its content digest."""
    if not isinstance(memory, list) or len(memory) > 20:
        raise InvestigationConflict(
            "project Omega memory is invalid or exceeds 20 entries"
        )
    normalized_memory = []
    total_text = 0
    ids = set()
    for entry in memory:
        if not isinstance(entry, dict):
            raise InvestigationConflict("project Omega memory entry is invalid")
        entry_id = entry.get("id")
        text = entry.get("text")
        if (
            not isinstance(entry_id, str)
            or not entry_id
            or len(entry_id) > 128
            or entry_id in ids
            or not isinstance(text, str)
            or not text
            or len(text) > 4000
        ):
            raise InvestigationConflict("project Omega memory entry is invalid")
        ids.add(entry_id)
        total_text += len(text.encode())
        normalized = {"id": entry_id, "text": text}
        source_feedback_id = entry.get("source_feedback_id")
        if source_feedback_id is not None:
            normalized["source_feedback_id"] = str(source_feedback_id)
        normalized_memory.append(normalized)
    if total_text > 32 * 1024:
        raise InvestigationConflict("project Omega memory exceeds 32 KiB")

    return normalized_memory, _digest(normalized_memory)


def _pinned_context(config: TraceScanConfig) -> tuple[str, str, list, dict]:
    memory, memory_digest = validate_investigation_memory(config.omega_memory)
    snapshot_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"omega-memory:{config.project_id}:{config.updated_at.isoformat()}:{memory_digest}",
    )
    configured_limits = config.omega_limits
    if not isinstance(configured_limits, dict) or set(configured_limits) - set(
        _DEFAULT_LIMITS
    ):
        raise InvestigationConflict("project Omega limits are invalid")
    limits = {**_DEFAULT_LIMITS, **configured_limits}
    for key, value in limits.items():
        minimum = {
            "max_children": 0,
            "max_parallel_children": 0,
            "max_model_calls": 2,
            "max_tool_result_bytes": 512,
        }.get(key, 1)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < minimum
            or value > _LIMIT_CEILINGS[key]
        ):
            raise InvestigationConflict(f"project Omega limit {key} is invalid")
    if limits["max_children"] > 0 and limits["max_parallel_children"] == 0:
        raise InvestigationConflict(
            "max_parallel_children must be positive when children are enabled"
        )
    if limits["max_parallel_children"] > limits["max_children"]:
        raise InvestigationConflict("max_parallel_children cannot exceed max_children")

    return (
        str(snapshot_id),
        memory_digest,
        memory,
        limits,
    )


def _expire_claims(now: datetime) -> None:
    with transaction.atomic():
        expired = list(
            TraceInvestigationAttempt.no_workspace_objects.select_for_update(
                skip_locked=True
            )
            .filter(
                status=TraceInvestigationAttemptStatus.CLAIMED,
                lease_expires_at__lte=now,
            )
            .order_by("lease_expires_at")[:500]
        )
        for attempt in expired:
            attempt.status = TraceInvestigationAttemptStatus.EXPIRED
            attempt.completed_at = now
            attempt.save(update_fields=["status", "completed_at", "updated_at"])
            job = TraceInvestigationJob.no_workspace_objects.select_for_update().get(
                id=attempt.job_id
            )
            if job.state == TraceInvestigationJobState.RUNNING:
                job.state = (
                    TraceInvestigationJobState.WAITING
                    if job.generation > attempt.generation
                    else TraceInvestigationJobState.CANCELLED
                )
                job.save(update_fields=["state", "updated_at"])


def _claim_payload(attempt: TraceInvestigationAttempt, token: str) -> dict[str, object]:
    job = attempt.job
    return {
        "organization_id": job.organization_id,
        "workspace_id": job.workspace_id,
        "project_id": job.project_id,
        "job_id": job.id,
        "trace_id": job.trace_id,
        "generation": attempt.generation,
        "attempt_id": attempt.id,
        "lease_token": token,
        "lease_expires_at": attempt.lease_expires_at,
        "read_cutoff": attempt.read_cutoff,
        "engine_version": attempt.engine_version,
        "contract_version": CONTRACT_VERSION,
        "memory": {
            "snapshot_id": attempt.memory_snapshot_id,
            "digest": attempt.memory_digest,
            "entries": attempt.memory,
        },
        "limits": attempt.limits,
        "verification_capabilities": [],
        "feature_enabled": True,
    }


def claim_due_investigations(
    *, worker_id: str, engine_version: str, limit: int
) -> dict[str, object]:
    """Claim due generations while transactionally enforcing project capacity."""
    now = timezone.now()
    _expire_claims(now)
    due_jobs = TraceInvestigationJob.no_workspace_objects.filter(
        project_id=OuterRef("project_id"),
        state=TraceInvestigationJobState.WAITING,
        not_before__lte=now,
    )
    saturated_projects = (
        TraceInvestigationAttempt.no_workspace_objects.filter(
            status=TraceInvestigationAttemptStatus.CLAIMED,
            lease_expires_at__gt=now,
        )
        .values("job__project_id")
        .annotate(active_count=Count("id"))
        .filter(active_count__gte=settings.ERROR_FEED_OMEGA_PROJECT_CONCURRENCY)
        .values("job__project_id")
    )
    # Rotate projects, not traces: one large old backlog must not monopolize
    # every free slot. The expensive pending ledger is probed with EXISTS.
    candidate_ids = list(
        TraceScanConfig.no_workspace_objects.filter(
            enabled=True,
            sampling_rate__gt=0,
            scan_version=engine_version,
            project__trace_type="observe",
        )
        .filter(Exists(due_jobs))
        .exclude(project_id__in=saturated_projects)
        .order_by(F("omega_last_claimed_at").asc(nulls_first=True), "project_id")
        .values_list("id", flat=True)[: min(limit * 2, 100)]
    )
    claims = []
    for config_id in candidate_ids:
        if len(claims) >= limit:
            break
        with transaction.atomic():
            config = (
                TraceScanConfig.no_workspace_objects.select_for_update(skip_locked=True)
                .filter(id=config_id)
                .first()
            )
            if config is None:
                continue
            project_id = config.project_id
            job = (
                TraceInvestigationJob.no_workspace_objects.select_for_update(
                    skip_locked=True
                )
                .filter(
                    project_id=project_id,
                    state=TraceInvestigationJobState.WAITING,
                    not_before__lte=now,
                )
                .order_by("not_before", "created_at", "id")
                .first()
            )
            if job is None:
                continue
            if (
                job.state != TraceInvestigationJobState.WAITING
                or job.not_before > now
                or not config.enabled
                or config.sampling_rate <= 0
                or config.scan_version != engine_version
            ):
                continue
            if not is_trace_sampled(str(job.trace_id), config.sampling_rate):
                # A rate reduction must not leave an excluded head-of-queue job
                # blocking eligible work. No inference or success result is made.
                job.state = TraceInvestigationJobState.CANCELLED
                job.save(update_fields=["state", "updated_at"])
                config.omega_last_claimed_at = now
                config.save(update_fields=["omega_last_claimed_at"])
                continue
            active = TraceInvestigationAttempt.no_workspace_objects.filter(
                job__project_id=project_id,
                status=TraceInvestigationAttemptStatus.CLAIMED,
                lease_expires_at__gt=now,
            ).count()
            if active >= settings.ERROR_FEED_OMEGA_PROJECT_CONCURRENCY:
                continue
            snapshot_id, memory_digest, memory, limits = _pinned_context(config)
            token = secrets.token_urlsafe(32)
            attempt = TraceInvestigationAttempt.no_workspace_objects.create(
                job=job,
                generation=job.generation,
                worker_id=worker_id,
                engine_version=engine_version,
                lease_token_digest=_token_digest(token),
                lease_expires_at=now
                + timedelta(seconds=settings.ERROR_FEED_OMEGA_LEASE_SECONDS),
                read_cutoff=now,
                memory_snapshot_id=snapshot_id,
                memory_digest=memory_digest,
                memory=memory,
                limits=limits,
            )
            job.state = TraceInvestigationJobState.RUNNING
            job.save(update_fields=["state", "updated_at"])
            config.omega_last_claimed_at = now
            config.save(update_fields=["omega_last_claimed_at"])
            claims.append(_claim_payload(attempt, token))
    return {"claims": claims}


def _locked_attempt(
    *,
    attempt_id: uuid.UUID,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    lease_token: str,
) -> tuple[TraceInvestigationAttempt, TraceInvestigationJob]:
    try:
        attempt = (
            TraceInvestigationAttempt.no_workspace_objects.select_for_update().get(
                id=attempt_id
            )
        )
        job = TraceInvestigationJob.no_workspace_objects.select_for_update().get(
            id=attempt.job_id
        )
    except (
        TraceInvestigationAttempt.DoesNotExist,
        TraceInvestigationJob.DoesNotExist,
    ) as exc:
        raise InvestigationNotFound("attempt scope was not found") from exc
    if (
        job.id != job_id
        or job.organization_id != organization_id
        or job.project_id != project_id
        or not _workspace_matches(job.workspace_id, workspace_id)
        or not secrets.compare_digest(
            attempt.lease_token_digest, _token_digest(lease_token)
        )
    ):
        raise InvestigationNotFound("attempt scope was not found")
    return attempt, job


def update_investigation_attempt(
    *,
    attempt_id: uuid.UUID,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    lease_token: str,
    action: str,
    reason: str,
) -> dict[str, object]:
    now = timezone.now()
    expired = False
    with transaction.atomic():
        attempt, job = _locked_attempt(
            attempt_id=attempt_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            project_id=project_id,
            job_id=job_id,
            lease_token=lease_token,
        )
        if attempt.status != TraceInvestigationAttemptStatus.CLAIMED:
            raise InvestigationConflict(f"attempt is already {attempt.status}")
        if attempt.lease_expires_at <= now:
            attempt.status = TraceInvestigationAttemptStatus.EXPIRED
            attempt.completed_at = now
            attempt.save(update_fields=["status", "completed_at", "updated_at"])
            if job.state == TraceInvestigationJobState.RUNNING:
                job.state = (
                    TraceInvestigationJobState.WAITING
                    if job.generation > attempt.generation
                    else TraceInvestigationJobState.CANCELLED
                )
                job.save(update_fields=["state", "updated_at"])
            expired = True
        elif action == "renew":
            attempt.lease_expires_at = now + timedelta(
                seconds=settings.ERROR_FEED_OMEGA_LEASE_SECONDS
            )
            attempt.save(update_fields=["lease_expires_at", "updated_at"])
        else:
            attempt.status = TraceInvestigationAttemptStatus.CANCELLED
            attempt.completed_at = now
            attempt.cancellation_reason = reason
            attempt.save(
                update_fields=[
                    "status",
                    "completed_at",
                    "cancellation_reason",
                    "updated_at",
                ]
            )
            job.state = (
                TraceInvestigationJobState.WAITING
                if job.generation > attempt.generation
                else TraceInvestigationJobState.CANCELLED
            )
            job.save(update_fields=["state", "updated_at"])

    if expired:
        raise InvestigationConflict("attempt lease has expired")

    return {
        "attempt_id": attempt.id,
        "status": attempt.status,
        "lease_expires_at": attempt.lease_expires_at,
        "cancellation_requested": attempt.status
        == TraceInvestigationAttemptStatus.CANCELLED,
        "job_state": job.state,
    }


def _publication_receipt(
    report: TraceInvestigationReport, *, duplicate: bool
) -> dict[str, object]:
    return {
        "status": "duplicate" if duplicate else "accepted",
        "report_id": report.id,
        "occurrence_ids": (
            list(report.findings.order_by("ordinal").values_list("id", flat=True))
            if report.grouping_status != TraceInvestigationGroupingStatus.STALE
            else []
        ),
        "grouping_status": report.grouping_status,
    }


def _persist_investigation_details(
    report: TraceInvestigationReport, result: Mapping[str, object]
) -> None:
    """Persist the validated wire result as relational, report-scoped rows."""
    checks = {}
    for ordinal, row in enumerate(result["requirement_checks"]):
        requirement_id = row["requirement_id"]
        if requirement_id in checks:
            raise InvestigationConflict("duplicate requirement_id")
        checks[requirement_id] = TraceInvestigationRequirementCheck(
            report=report,
            requirement_id=requirement_id,
            ordinal=ordinal,
            requirement=row["requirement"],
            status=row["status"],
        )
    TraceInvestigationRequirementCheck.objects.bulk_create(checks.values())

    evidence = {}
    for ordinal, row in enumerate(result["evidence_receipts"]):
        evidence_id = row["evidence_id"]
        if evidence_id in evidence:
            raise InvestigationConflict("duplicate evidence_id")
        evidence[evidence_id] = TraceInvestigationEvidenceReceipt(
            report=report,
            evidence_id=evidence_id,
            ordinal=ordinal,
            span_id=row["span_id"],
            parent_span_id=row.get("parent_span_id"),
            excerpt=row["excerpt"],
            end_time=row.get("end_time"),
        )
    TraceInvestigationEvidenceReceipt.objects.bulk_create(evidence.values())

    def cited_ids(row: Mapping[str, object]) -> list[str]:
        ids = row["evidence_ids"]
        if len(ids) != len(set(ids)) or any(item not in evidence for item in ids):
            raise InvestigationConflict("invalid evidence citation")
        return ids

    TraceInvestigationRequirementEvidence.objects.bulk_create(
        TraceInvestigationRequirementEvidence(
            requirement=check, evidence=evidence[evidence_id]
        )
        for row in result["requirement_checks"]
        for check in [checks[row["requirement_id"]]]
        for evidence_id in cited_ids(row)
    )

    findings = {}
    for ordinal, row in enumerate(result["findings"]):
        finding_id = row["finding_id"]
        if finding_id in findings:
            raise InvestigationConflict("duplicate finding_id")
        requirement_id = row.get("requirement_id")
        if requirement_id is not None and requirement_id not in checks:
            raise InvestigationConflict("unknown finding requirement_id")
        findings[finding_id] = TraceInvestigationFinding(
            id=uuid.uuid5(report.id, finding_id),
            report=report,
            finding_id=finding_id,
            ordinal=ordinal,
            kind=row["kind"],
            statement=row["statement"],
            recovery=row["recovery"],
            requirement=checks.get(requirement_id),
        )
    TraceInvestigationFinding.objects.bulk_create(findings.values())
    TraceInvestigationFindingEvidence.objects.bulk_create(
        TraceInvestigationFindingEvidence(
            finding=findings[row["finding_id"]], evidence=evidence[evidence_id]
        )
        for row in result["findings"]
        for evidence_id in cited_ids(row)
    )

    attributions = []
    attribution_citations = []
    for row in result["findings"]:
        finding = findings[row["finding_id"]]
        for role in ("origin", "decisive", "symptom"):
            value = row["attribution"][role]
            attribution = TraceInvestigationAttribution(
                finding=finding,
                role=role,
                status=value["status"],
                span_id=value.get("span_id"),
            )
            attributions.append(attribution)
            attribution_citations.append((attribution, cited_ids(value)))
    TraceInvestigationAttribution.objects.bulk_create(attributions)
    TraceInvestigationAttributionEvidence.objects.bulk_create(
        TraceInvestigationAttributionEvidence(
            attribution=attribution, evidence=evidence[evidence_id]
        )
        for attribution, ids in attribution_citations
        for evidence_id in ids
    )

    receipts = {}
    for ordinal, row in enumerate(result["verification_receipts"]):
        receipt_id = row["receipt_id"]
        if receipt_id in receipts:
            raise InvestigationConflict("duplicate receipt_id")
        receipts[receipt_id] = TraceInvestigationVerificationReceipt(
            report=report,
            receipt_id=receipt_id,
            ordinal=ordinal,
            executed=row["executed"],
        )
    TraceInvestigationVerificationReceipt.objects.bulk_create(receipts.values())

    calls = []
    for ordinal, row in enumerate(result["gateway_accounting"]):
        raw = row.get("raw") or {}
        if not isinstance(raw, dict):
            raise InvestigationConflict("invalid gateway diagnostics")
        usage = raw.get("usage") or {}
        if not isinstance(usage, dict):
            raise InvestigationConflict("invalid gateway usage")
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        if not isinstance(prompt_details, dict) or not isinstance(
            completion_details, dict
        ):
            raise InvestigationConflict("invalid gateway token details")
        calls.append(
            TraceInvestigationGatewayCall(
                report=report,
                ordinal=ordinal,
                request_id=row.get("request_id"),
                model_used=row["model_used"],
                cost_usd=row.get("cost"),
                input_tokens=(
                    row["input_tokens"]
                    if row.get("input_tokens") is not None
                    else usage.get("prompt_tokens")
                ),
                output_tokens=(
                    row["output_tokens"]
                    if row.get("output_tokens") is not None
                    else usage.get("completion_tokens")
                ),
                total_tokens=usage.get("total_tokens"),
                cached_input_tokens=prompt_details.get("cached_tokens"),
                reasoning_output_tokens=completion_details.get("reasoning_tokens"),
                cache_status=raw.get("cache_status"),
                status=raw.get("status"),
                http_status=raw.get("http_status"),
                retry_of=raw.get("retry_of"),
                retry_delay_ms=raw.get("retry_delay_ms"),
            )
        )
    TraceInvestigationGatewayCall.objects.bulk_create(calls)


def publish_investigation(
    *,
    idempotency_key: str,
    lease_token: str,
    result: Mapping[str, object],
    wire_result_digest: str,
) -> dict[str, object]:
    """Accept one immutable report and update only its current job generation."""
    now = timezone.now()
    with transaction.atomic():
        attempt, job = _locked_attempt(
            attempt_id=result["attempt_id"],
            organization_id=result["organization_id"],
            workspace_id=result["workspace_id"],
            project_id=result["project_id"],
            job_id=result["job_id"],
            lease_token=lease_token,
        )
        if (
            job.trace_id != result["trace_id"]
            or attempt.generation != result["generation"]
            or attempt.engine_version != result["engine_version"]
            or attempt.read_cutoff != result["read_cutoff"]
            or attempt.memory_snapshot_id != result["memory_snapshot_id"]
            or attempt.memory_digest != result["memory_digest"]
            or result["contract_version"] != CONTRACT_VERSION
        ):
            raise InvestigationConflict("report does not match its claimed attempt")
        if result["result_digest"] != wire_result_digest:
            raise InvestigationConflict("result_digest does not match the wire result")

        existing = (
            TraceInvestigationReport.no_workspace_objects.filter(
                Q(attempt=attempt)
                | Q(
                    organization_id=job.organization_id,
                    idempotency_key=idempotency_key,
                )
            )
            .order_by("id")
            .first()
        )
        if existing:
            if (
                existing.attempt_id != attempt.id
                or existing.result_digest != result["result_digest"]
            ):
                raise InvestigationConflict(
                    "publication identity was reused with a different result"
                )
            transaction.on_commit(
                lambda report=existing: charge_trace_investigation(report)
            )
            return _publication_receipt(existing, duplicate=True)
        report_id = uuid.uuid4()
        active = (
            job.generation == attempt.generation
            and job.state == TraceInvestigationJobState.RUNNING
            and attempt.status == TraceInvestigationAttemptStatus.CLAIMED
            and attempt.lease_expires_at > now
        )
        findings = result["findings"]
        grouping_status = (
            TraceInvestigationGroupingStatus.STALE
            if not active
            else (
                TraceInvestigationGroupingStatus.PENDING
                if findings
                else TraceInvestigationGroupingStatus.NOT_REQUIRED
            )
        )
        coverage = result["coverage"]
        usage = result["usage"]
        if active:
            TraceInvestigationReport.no_workspace_objects.filter(
                project_id=job.project_id,
                trace_id=job.trace_id,
                is_current=True,
            ).update(is_current=False)
        report = TraceInvestigationReport.no_workspace_objects.create(
            id=report_id,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            project_id=job.project_id,
            trace_id=job.trace_id,
            source=TraceInvestigationSource.OMEGA,
            recorded_at=now,
            is_current=active,
            job=job,
            attempt=attempt,
            idempotency_key=idempotency_key,
            result_digest=result["result_digest"],
            contract_version=result["contract_version"],
            evidence_digest=result["evidence_digest"],
            execution_status=result["execution_status"],
            outcome=result["outcome"],
            coverage_scope=coverage["scope"],
            observed_span_count=coverage["observed_span_count"],
            read_complete=coverage["read_complete"],
            future_arrivals_known=coverage["future_arrivals_known"],
            model_calls=usage["model_calls"],
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cost_usd=usage.get("cost_usd"),
            cost_status=usage["cost_status"],
            grouping_status=grouping_status,
        )
        _persist_investigation_details(report, result)
        transaction.on_commit(lambda report=report: charge_trace_investigation(report))
        if active:
            job.current_report = report

        if attempt.status == TraceInvestigationAttemptStatus.CLAIMED:
            attempt.status = (
                TraceInvestigationAttemptStatus.COMPLETED
                if attempt.lease_expires_at > now
                else TraceInvestigationAttemptStatus.EXPIRED
            )
            attempt.completed_at = now
            attempt.save(update_fields=["status", "completed_at", "updated_at"])
        if job.state == TraceInvestigationJobState.RUNNING:
            if job.generation > attempt.generation:
                job.state = TraceInvestigationJobState.WAITING
            elif active:
                job.state = TraceInvestigationJobState.COMPLETED
            else:
                job.state = TraceInvestigationJobState.CANCELLED
            job.save(update_fields=["state", "current_report", "updated_at"])
        return _publication_receipt(report, duplicate=False)
