"""Charge a completed Omega investigation through the existing usage emitter."""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

import structlog

from tracer.ee_boundary import (
    emit_trace_investigation_usage,
    price_trace_investigation_usage,
)
from tracer.models.trace_investigation import TraceInvestigationReport

logger = structlog.get_logger(__name__)
_TOTAL_TOLERANCE = Decimal("0.000000001")
_EVENT_PURPOSE = "trace-error-analysis-usage/v1"


def _cost(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def _validated_cost(result: object) -> tuple[Decimal, list[str]] | None:
    if not isinstance(result, dict):
        return None
    accounting = result.get("gateway_accounting")
    usage = result.get("usage")
    if not isinstance(accounting, list) or not isinstance(usage, dict):
        return None
    if isinstance(usage.get("model_calls"), bool) or usage.get("model_calls") != len(
        accounting
    ):
        return None
    costs = []
    models = set()
    for row in accounting:
        if not isinstance(row, dict):
            return None
        cost = _cost(row.get("cost"))
        if cost is None:
            return None
        costs.append(cost)
        model = row.get("model_used")
        if isinstance(model, str) and model:
            models.add(model)
    total = sum(costs, Decimal("0"))
    reported = _cost(usage.get("cost_usd"))
    if reported is None or abs(total - reported) > _TOTAL_TOLERANCE:
        return None
    return total, sorted(models)


def charge_trace_investigation(report: TraceInvestigationReport) -> None:
    """Emit one tenant-scoped, idempotent credit event after report commit.

    The shared emitter is fire-and-forget. A Redis failure is logged there, not
    retried here; this matches the existing scanner's billing behavior.
    """
    calls = list(report.gateway_calls.all())
    validated = _validated_cost(
        {
            "usage": {
                "model_calls": report.model_calls,
                "cost_usd": report.cost_usd,
            },
            "gateway_accounting": [
                {"cost": call.cost_usd, "model_used": call.model_used} for call in calls
            ],
        }
    )
    if validated is None:
        logger.warning("trace_investigation_cost_unpriced", report_id=str(report.id))
        return
    raw_cost, models = validated
    if raw_cost == 0:
        return
    try:
        pricing = price_trace_investigation_usage(raw_cost)
        if not pricing.applicable or pricing.credit_amount is None:
            return
        if pricing.credit_amount <= 0 or not pricing.credit_amount.is_finite():
            logger.warning(
                "trace_investigation_invalid_credit_amount", report_id=str(report.id)
            )
            return
        event_id = uuid.uuid5(report.id, _EVENT_PURPOSE)
        emit_trace_investigation_usage(
            {
                "event_id": str(event_id),
                "org_id": str(report.organization_id),
                "event_type": "trace_error_analysis",
                "amount": float(pricing.credit_amount),
                "properties": {
                    "source": "omega_trace_investigation",
                    "source_id": str(report.id),
                    "project_id": str(report.project_id),
                    "workspace_id": str(report.workspace_id or ""),
                    "raw_cost_usd": format(raw_cost, "f"),
                    "models_used": ",".join(models),
                },
            }
        )
    except Exception:
        logger.exception("trace_investigation_billing_failed", report_id=str(report.id))
