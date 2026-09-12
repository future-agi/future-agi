"""Durable tenant attribution and gated billing for Omega gateway cost.

AgentCC authenticates the Omega daemon with one dedicated internal service key.
That key is intentionally not distributed per organization: gateway log
ingestion resolves the key's owner before request metadata, so the immutable
investigation report is the customer attribution authority.

The gateway meters request counts under ``GATEWAY_REQUEST``. This outbox emits
the distinct ``TRACE_ERROR_ANALYSIS`` AI-credit event derived from reported LLM
cost; it does not duplicate a gateway cost-credit event.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from tracer.ee_boundary import (
    enqueue_trace_investigation_usage,
    price_trace_investigation_usage,
)
from tracer.models.trace_investigation import (
    TraceInvestigationReport,
    TraceInvestigationUsageReceipt,
    TraceInvestigationUsageStatus,
)

logger = structlog.get_logger(__name__)

DEFAULT_RECEIPT_LIMIT = 100
MAX_RECEIPT_LIMIT = 500
_EVENT_PURPOSE = "trace-error-analysis-usage/v1"
_MAX_STORED_DECIMAL = Decimal("999999999999.999999999999999999")
_STORAGE_QUANTUM = Decimal("0.000000000000000001")
# Node totals integer micro-USD values, then serializes the quotient as JSON.
# One nano-USD covers binary-float string noise without making the allowed
# discrepancy grow with the amount.
_TOTAL_TOLERANCE = Decimal("0.000000001")


class TraceInvestigationBillingError(Exception):
    pass


@dataclass(frozen=True)
class _CostState:
    status: str
    reason: str
    raw_cost_usd: Decimal | None
    gateway_request_count: int
    models_used: str


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0 or parsed > _MAX_STORED_DECIMAL:
        return None
    return parsed


def _stored_decimal(value: Decimal) -> Decimal | None:
    try:
        stored = value.quantize(_STORAGE_QUANTUM, rounding=ROUND_HALF_EVEN)
    except InvalidOperation:
        return None
    if stored < 0 or stored > _MAX_STORED_DECIMAL:
        return None
    return stored


def _derive_cost_state(result: object) -> _CostState:
    if not isinstance(result, dict):
        return _CostState(
            TraceInvestigationUsageStatus.UNPRICED,
            "invalid_result",
            None,
            0,
            "",
        )
    accounting = result.get("gateway_accounting")
    usage = result.get("usage")
    if not isinstance(accounting, list) or not isinstance(usage, dict):
        return _CostState(
            TraceInvestigationUsageStatus.UNPRICED,
            "missing_accounting",
            None,
            0,
            "",
        )
    model_calls = usage.get("model_calls")
    if (
        isinstance(model_calls, bool)
        or not isinstance(model_calls, int)
        or model_calls < 0
        or model_calls != len(accounting)
    ):
        return _CostState(
            TraceInvestigationUsageStatus.UNPRICED,
            "inconsistent_call_count",
            None,
            len(accounting),
            "",
        )

    models: set[str] = set()
    costs: list[Decimal] = []
    for row in accounting:
        if not isinstance(row, dict):
            return _CostState(
                TraceInvestigationUsageStatus.UNPRICED,
                "invalid_accounting",
                None,
                len(accounting),
                "",
            )
        model = row.get("model_used")
        if isinstance(model, str) and model:
            models.add(model)
        if "cost" not in row or row["cost"] is None:
            return _CostState(
                TraceInvestigationUsageStatus.UNPRICED,
                "missing_cost",
                None,
                len(accounting),
                ",".join(sorted(models)),
            )
        cost = _decimal(row["cost"])
        if cost is None:
            return _CostState(
                TraceInvestigationUsageStatus.UNPRICED,
                "invalid_cost",
                None,
                len(accounting),
                ",".join(sorted(models)),
            )
        costs.append(cost)

    summed = sum(costs, Decimal("0"))
    reported_total = _decimal(usage.get("cost_usd"))
    if reported_total is None:
        return _CostState(
            TraceInvestigationUsageStatus.UNPRICED,
            "missing_total_cost",
            None,
            len(accounting),
            ",".join(sorted(models)),
        )
    if abs(reported_total - summed) > _TOTAL_TOLERANCE:
        return _CostState(
            TraceInvestigationUsageStatus.UNPRICED,
            "inconsistent_total_cost",
            None,
            len(accounting),
            ",".join(sorted(models)),
        )
    if summed == 0:
        return _CostState(
            TraceInvestigationUsageStatus.SKIPPED,
            "zero_cost",
            Decimal("0"),
            len(accounting),
            ",".join(sorted(models)),
        )
    return _CostState(
        TraceInvestigationUsageStatus.PENDING,
        "",
        summed,
        len(accounting),
        ",".join(sorted(models)),
    )


def _properties(report: TraceInvestigationReport, cost: _CostState) -> dict:
    result = report.result if isinstance(report.result, dict) else {}
    return {
        "source": "omega_trace_investigation",
        "source_id": str(report.id),
        "report_id": str(report.id),
        "attempt_id": str(report.attempt_id),
        "job_id": str(report.job_id),
        "project_id": str(report.project_id),
        "workspace_id": str(report.workspace_id or ""),
        "engine_version": str(result.get("engine_version") or ""),
        "execution_status": str(result.get("execution_status") or ""),
        "outcome": str(result.get("outcome") or ""),
        "active_projection_updated": report.active_projection_updated,
        "gateway_request_count": cost.gateway_request_count,
        "models_used": cost.models_used,
        "raw_cost_usd": (
            format(cost.raw_cost_usd, "f")
            if cost.raw_cost_usd is not None
            else "unknown"
        ),
        "gateway_metering": "request_count_only",
    }


def _event_payload(
    receipt: TraceInvestigationUsageReceipt, report: TraceInvestigationReport
) -> dict:
    if receipt.credit_amount is None:
        return {}
    return {
        "event_id": str(receipt.event_id),
        "org_id": str(receipt.organization_id),
        "event_type": "trace_error_analysis",
        "timestamp": report.created_at.isoformat(),
        "amount": float(receipt.credit_amount),
        "properties": receipt.event_properties,
    }


def _pin_pricing(receipt: TraceInvestigationUsageReceipt) -> None:
    if (
        receipt.status != TraceInvestigationUsageStatus.PENDING
        or receipt.credit_amount is not None
    ):
        return
    if receipt.raw_cost_usd is None or receipt.raw_cost_usd <= 0:
        receipt.status = TraceInvestigationUsageStatus.UNPRICED
        receipt.status_reason = "invalid_positive_cost"
        return
    try:
        pricing = price_trace_investigation_usage(receipt.raw_cost_usd)
    except Exception:
        logger.exception(
            "trace_investigation_usage_pricing_deferred",
            receipt_id=str(receipt.id),
            report_id=str(receipt.report_id),
            organization_id=str(receipt.organization_id),
            project_id=str(receipt.project_id),
        )
        receipt.status_reason = "pricing_deferred"
        return
    if not pricing.applicable:
        receipt.status = TraceInvestigationUsageStatus.SKIPPED
        receipt.status_reason = pricing.reason
        return
    if pricing.credit_amount is None or pricing.credit_amount < 0:
        receipt.status = TraceInvestigationUsageStatus.UNPRICED
        receipt.status_reason = "invalid_credit_amount"
        return
    if pricing.credit_amount > _MAX_STORED_DECIMAL:
        receipt.status = TraceInvestigationUsageStatus.UNPRICED
        receipt.status_reason = "credit_amount_too_large"
        return
    credit_amount = _stored_decimal(pricing.credit_amount)
    if credit_amount is None:
        receipt.status = TraceInvestigationUsageStatus.UNPRICED
        receipt.status_reason = "invalid_credit_amount"
        return
    if credit_amount == 0:
        receipt.status = TraceInvestigationUsageStatus.SKIPPED
        receipt.status_reason = "below_credit_precision"
        return
    receipt.credit_amount = credit_amount
    receipt.status_reason = ""


def record_trace_investigation_usage(
    report: TraceInvestigationReport,
) -> TraceInvestigationUsageReceipt:
    """Create the report's immutable usage outbox row in its DB transaction."""
    if (
        report.organization_id != report.job.organization_id
        or report.workspace_id != report.job.workspace_id
        or report.project_id != report.job.project_id
        or report.attempt.job_id != report.job_id
        or report.project.organization_id != report.organization_id
        or report.project.workspace_id != report.workspace_id
    ):
        raise TraceInvestigationBillingError("report billing scope is inconsistent")

    event_id = uuid.uuid5(report.id, _EVENT_PURPOSE)
    cost = _derive_cost_state(report.result)
    raw_cost_usd = (
        _stored_decimal(cost.raw_cost_usd) if cost.raw_cost_usd is not None else None
    )
    if cost.raw_cost_usd is not None and raw_cost_usd is None:
        cost = _CostState(
            TraceInvestigationUsageStatus.UNPRICED,
            "cost_outside_storage_precision",
            None,
            cost.gateway_request_count,
            cost.models_used,
        )
    elif cost.raw_cost_usd is not None and raw_cost_usd == 0 and cost.raw_cost_usd > 0:
        cost = _CostState(
            TraceInvestigationUsageStatus.SKIPPED,
            "below_cost_precision",
            raw_cost_usd,
            cost.gateway_request_count,
            cost.models_used,
        )
    elif raw_cost_usd is not None:
        cost = _CostState(
            cost.status,
            cost.reason,
            raw_cost_usd,
            cost.gateway_request_count,
            cost.models_used,
        )

    draft = TraceInvestigationUsageReceipt(
        report=report,
        organization_id=report.organization_id,
        workspace_id=report.workspace_id,
        project_id=report.project_id,
        event_id=event_id,
        raw_cost_usd=cost.raw_cost_usd,
        event_properties=_properties(report, cost),
        status=cost.status,
        status_reason=cost.reason,
    )
    receipt, created = (
        TraceInvestigationUsageReceipt.no_workspace_objects.get_or_create(
            report=report,
            defaults={
                "id": draft.id,
                "organization_id": draft.organization_id,
                "workspace_id": draft.workspace_id,
                "project_id": draft.project_id,
                "event_id": draft.event_id,
                "raw_cost_usd": draft.raw_cost_usd,
                "credit_amount": draft.credit_amount,
                "event_properties": draft.event_properties,
                "event_payload": draft.event_payload,
                "status": draft.status,
                "status_reason": draft.status_reason,
            },
        )
    )
    if not created and (
        receipt.event_id != event_id
        or receipt.organization_id != report.organization_id
        or receipt.workspace_id != report.workspace_id
        or receipt.project_id != report.project_id
    ):
        raise TraceInvestigationBillingError(
            "report usage receipt conflicts with tenant scope"
        )
    return receipt


def _pin_pending_receipt(receipt_id: uuid.UUID) -> TraceInvestigationUsageReceipt:
    with transaction.atomic():
        receipt = (
            TraceInvestigationUsageReceipt.no_workspace_objects.select_for_update()
            .select_related("report__job", "report__attempt", "report__project")
            .get(id=receipt_id)
        )
        report = receipt.report
        if (
            receipt.organization_id != report.organization_id
            or receipt.workspace_id != report.workspace_id
            or receipt.project_id != report.project_id
            or report.organization_id != report.job.organization_id
            or report.workspace_id != report.job.workspace_id
            or report.project_id != report.job.project_id
            or report.attempt.job_id != report.job_id
            or report.project.organization_id != report.organization_id
            or report.project.workspace_id != report.workspace_id
        ):
            receipt.status = TraceInvestigationUsageStatus.UNPRICED
            receipt.status_reason = "tenant_scope_conflict"
        else:
            _pin_pricing(receipt)
            if receipt.credit_amount is not None and not receipt.event_payload:
                receipt.event_payload = _event_payload(receipt, report)
        receipt.save(
            update_fields=[
                "credit_amount",
                "event_payload",
                "status",
                "status_reason",
                "updated_at",
            ]
        )
        return receipt


def _deliver_receipt(receipt_id: uuid.UUID) -> str:
    receipt = _pin_pending_receipt(receipt_id)
    if receipt.status != TraceInvestigationUsageStatus.PENDING:
        return receipt.status
    if not receipt.event_payload or receipt.credit_amount is None:
        return "deferred"
    if not getattr(settings, "ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED", False):
        return "gated"

    with transaction.atomic():
        current = (
            TraceInvestigationUsageReceipt.no_workspace_objects.select_for_update().get(
                id=receipt.id
            )
        )
        if current.status != TraceInvestigationUsageStatus.PENDING:
            return current.status
        current.delivery_attempts += 1
        current.save(update_fields=["delivery_attempts", "updated_at"])
        event_payload = dict(current.event_payload)

    try:
        enqueue_trace_investigation_usage(event_payload)
    except Exception as error:
        TraceInvestigationUsageReceipt.no_workspace_objects.filter(
            id=receipt.id,
            status=TraceInvestigationUsageStatus.PENDING,
        ).update(status_reason="enqueue_deferred", updated_at=timezone.now())
        raise TraceInvestigationBillingError("usage enqueue failed") from error

    with transaction.atomic():
        current = (
            TraceInvestigationUsageReceipt.no_workspace_objects.select_for_update().get(
                id=receipt.id
            )
        )
        if current.status == TraceInvestigationUsageStatus.PENDING:
            current.status = TraceInvestigationUsageStatus.EMITTED
            current.status_reason = ""
            current.emitted_at = timezone.now()
            current.save(
                update_fields=[
                    "status",
                    "status_reason",
                    "emitted_at",
                    "updated_at",
                ]
            )
    return current.status


def deliver_trace_investigation_usage(
    *, receipt_limit: int = DEFAULT_RECEIPT_LIMIT
) -> dict[str, int | bool]:
    """Backfill and drain a bounded page of report usage receipts."""
    if (
        isinstance(receipt_limit, bool)
        or receipt_limit < 1
        or receipt_limit > MAX_RECEIPT_LIMIT
    ):
        raise ValueError(f"receipt_limit must be between 1 and {MAX_RECEIPT_LIMIT}")

    receipt_exists = TraceInvestigationUsageReceipt.all_objects.filter(
        report_id=OuterRef("pk")
    )
    missing_reports = list(
        TraceInvestigationReport.no_workspace_objects.annotate(
            has_usage_receipt=Exists(receipt_exists)
        )
        .filter(has_usage_receipt=False)
        .select_related("job", "attempt", "project")
        .order_by("created_at", "id")[:receipt_limit]
    )
    for report in missing_reports:
        record_trace_investigation_usage(report)

    receipt_ids = list(
        TraceInvestigationUsageReceipt.no_workspace_objects.filter(
            status=TraceInvestigationUsageStatus.PENDING
        )
        .order_by("updated_at", "id")
        .values_list("id", flat=True)[:receipt_limit]
    )
    summary: dict[str, int | bool] = {
        "emission_enabled": getattr(
            settings, "ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED", False
        ),
        "backfilled": len(missing_reports),
        "selected": len(receipt_ids),
        "emitted": 0,
        "unpriced": 0,
        "skipped": 0,
        "gated": 0,
        "deferred": 0,
    }
    for receipt_id in receipt_ids:
        try:
            status = _deliver_receipt(receipt_id)
        except TraceInvestigationBillingError:
            summary["deferred"] += 1
            logger.exception(
                "trace_investigation_usage_deferred", receipt_id=str(receipt_id)
            )
            continue
        if status in summary:
            summary[status] += 1
        elif status == TraceInvestigationUsageStatus.PENDING:
            summary["deferred"] += 1
    return summary
