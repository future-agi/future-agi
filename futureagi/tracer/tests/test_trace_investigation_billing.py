import uuid
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

import pytest
from accounts.models import Organization
from django.test import override_settings

from tracer.ee_boundary import TraceInvestigationUsagePricing
from tracer.models.trace_investigation import (
    TraceInvestigationReport,
    TraceInvestigationUsageReceipt,
    TraceInvestigationUsageStatus,
)
from tracer.services.trace_investigation import (
    canonical_wire_result_digest,
    claim_due_investigations,
    record_trace_notifications,
    update_investigation_attempt,
)
from tracer.services.trace_investigation_billing import (
    _derive_cost_state,
    deliver_trace_investigation_usage,
)
from tracer.tests.test_trace_investigation_control import (
    _configure,
    _delivery,
    _publish,
    _result,
)

pytestmark = pytest.mark.django_db


def _accounted_result(claim, costs, *, reported_total=None):
    result = _result(claim)
    result["gateway_accounting"] = [
        {
            "request_id": f"gateway-request-{index}",
            "model_used": "openai/test",
            "cost": cost,
            "raw": {"x-agentcc-model-used": "openai/test"},
        }
        for index, cost in enumerate(costs, start=1)
    ]
    result["usage"]["model_calls"] = len(costs)
    result["usage"]["cost_usd"] = (
        sum(costs)
        if reported_total is None and all(c is not None for c in costs)
        else reported_total
    )
    result["usage"]["cost_status"] = (
        "priced" if all(cost is not None for cost in costs) else "partially_unpriced"
    )
    result["result_digest"] = canonical_wire_result_digest(result)
    return result


def _claim(observe_project):
    _configure(observe_project)
    record_trace_notifications(deliveries=[_delivery(observe_project)])
    return claim_due_investigations(
        worker_id="node-billing-test", engine_version="omega-v1", limit=1
    )["claims"][0]


def _publish_accounted(observe_project, costs, *, key="billing-report"):
    claim = _claim(observe_project)
    result = _accounted_result(claim, costs)
    publication = _publish(
        idempotency_key=key,
        lease_token=claim["lease_token"],
        result=result,
    )
    return claim, result, publication


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
    ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED=False,
)
def test_cancelled_paid_report_pins_tenant_cost_and_is_idempotent(observe_project):
    claim = _claim(observe_project)
    update_investigation_attempt(
        attempt_id=claim["attempt_id"],
        organization_id=claim["organization_id"],
        workspace_id=claim["workspace_id"],
        project_id=claim["project_id"],
        job_id=claim["job_id"],
        lease_token=claim["lease_token"],
        action="cancel",
        reason="heartbeat timeout",
    )
    result = _accounted_result(claim, [0, 0.001, 0.002])
    with patch(
        "tracer.services.trace_investigation_billing.price_trace_investigation_usage"
    ) as price:
        accepted = _publish(
            idempotency_key="cancelled-paid-report",
            lease_token=claim["lease_token"],
            result=result,
        )
        duplicate = _publish(
            idempotency_key="cancelled-paid-report",
            lease_token=claim["lease_token"],
            result=deepcopy(result),
        )
    price.assert_not_called()

    assert accepted["active_projection_updated"] is False
    assert duplicate == {**accepted, "status": "duplicate"}
    receipt = TraceInvestigationUsageReceipt.no_workspace_objects.get()
    assert receipt.organization_id == observe_project.organization_id
    assert receipt.workspace_id == observe_project.workspace_id
    assert receipt.project_id == observe_project.id
    assert receipt.raw_cost_usd == Decimal("0.003")
    assert receipt.credit_amount is None
    assert receipt.status == TraceInvestigationUsageStatus.PENDING
    assert receipt.event_id == uuid.uuid5(
        accepted["report_id"], "trace-error-analysis-usage/v1"
    )
    assert receipt.event_payload == {}
    assert receipt.event_properties["attempt_id"] == str(claim["attempt_id"])
    assert TraceInvestigationUsageReceipt.no_workspace_objects.count() == 1


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
    ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED=False,
)
def test_default_off_gate_keeps_pinned_receipt_pending(observe_project):
    pricing = TraceInvestigationUsagePricing(True, Decimal("0.1"), "")
    _publish_accounted(observe_project, [0.001])

    receipt = TraceInvestigationUsageReceipt.no_workspace_objects.get()
    assert receipt.credit_amount is None
    assert receipt.event_payload == {}
    with (
        patch(
            "tracer.services.trace_investigation_billing.price_trace_investigation_usage",
            return_value=pricing,
        ),
        patch(
            "tracer.services.trace_investigation_billing.enqueue_trace_investigation_usage"
        ) as enqueue,
    ):
        summary = deliver_trace_investigation_usage(receipt_limit=1)

    receipt.refresh_from_db()
    pinned_payload = deepcopy(receipt.event_payload)
    assert summary == {
        "emission_enabled": False,
        "backfilled": 0,
        "selected": 1,
        "emitted": 0,
        "unpriced": 0,
        "skipped": 0,
        "gated": 1,
        "deferred": 0,
    }
    enqueue.assert_not_called()
    assert receipt.status == TraceInvestigationUsageStatus.PENDING
    assert receipt.delivery_attempts == 0
    assert receipt.credit_amount == Decimal("0.1")
    assert pinned_payload["event_id"] == str(receipt.event_id)
    assert pinned_payload["org_id"] == str(observe_project.organization_id)
    assert pinned_payload["amount"] == 0.1


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
    ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED=False,
)
def test_pricing_failure_stays_pending_and_logs_tenant_evidence(observe_project):
    _, _, publication = _publish_accounted(observe_project, [0.001])
    receipt = TraceInvestigationUsageReceipt.no_workspace_objects.get()

    with (
        patch(
            "tracer.services.trace_investigation_billing.price_trace_investigation_usage",
            side_effect=RuntimeError("billing config unavailable"),
        ),
        patch("tracer.services.trace_investigation_billing.logger") as logger,
    ):
        summary = deliver_trace_investigation_usage(receipt_limit=1)

    receipt.refresh_from_db()
    assert summary["deferred"] == 1
    assert receipt.status == TraceInvestigationUsageStatus.PENDING
    assert receipt.status_reason == "pricing_deferred"
    assert receipt.credit_amount is None
    assert receipt.event_payload == {}
    logger.exception.assert_called_once_with(
        "trace_investigation_usage_pricing_deferred",
        receipt_id=str(receipt.id),
        report_id=str(publication["report_id"]),
        organization_id=str(observe_project.organization_id),
        project_id=str(observe_project.id),
    )


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
    ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED=True,
)
def test_enqueue_retry_reuses_pinned_event_without_repricing(observe_project):
    pricing = TraceInvestigationUsagePricing(True, Decimal("0.2"), "")
    _publish_accounted(observe_project, [0.002])
    receipt = TraceInvestigationUsageReceipt.no_workspace_objects.get()
    assert receipt.event_payload == {}

    with (
        patch(
            "tracer.services.trace_investigation_billing.price_trace_investigation_usage",
            return_value=pricing,
        ) as price,
        patch(
            "tracer.services.trace_investigation_billing.enqueue_trace_investigation_usage",
            side_effect=ConnectionError("Redis down"),
        ) as failed_enqueue,
    ):
        failed = deliver_trace_investigation_usage(receipt_limit=1)
    receipt.refresh_from_db()
    assert failed["deferred"] == 1
    assert receipt.status == TraceInvestigationUsageStatus.PENDING
    assert receipt.delivery_attempts == 1
    assert receipt.status_reason == "enqueue_deferred"
    original_payload = deepcopy(receipt.event_payload)
    assert failed_enqueue.call_args.args[0] == original_payload
    price.assert_called_once_with(Decimal("0.002000000000000000"))

    with (
        patch(
            "tracer.services.trace_investigation_billing.price_trace_investigation_usage"
        ) as reprice,
        patch(
            "tracer.services.trace_investigation_billing.enqueue_trace_investigation_usage"
        ) as successful_enqueue,
    ):
        successful = deliver_trace_investigation_usage(receipt_limit=1)
    receipt.refresh_from_db()
    assert successful["emitted"] == 1
    assert receipt.status == TraceInvestigationUsageStatus.EMITTED
    assert receipt.delivery_attempts == 2
    assert receipt.emitted_at is not None
    assert successful_enqueue.call_args.args[0] == original_payload
    reprice.assert_not_called()


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
    ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED=True,
)
def test_delivery_fails_closed_when_receipt_tenant_scope_is_tampered(
    observe_project,
):
    _publish_accounted(observe_project, [0.001])
    receipt = TraceInvestigationUsageReceipt.no_workspace_objects.get()
    receipt.organization = Organization.objects.create(name="Other billing tenant")
    receipt.save(update_fields=["organization", "updated_at"])

    with patch(
        "tracer.services.trace_investigation_billing.enqueue_trace_investigation_usage"
    ) as enqueue:
        summary = deliver_trace_investigation_usage(receipt_limit=1)

    receipt.refresh_from_db()
    assert summary["unpriced"] == 1
    assert receipt.status == TraceInvestigationUsageStatus.UNPRICED
    assert receipt.status_reason == "tenant_scope_conflict"
    enqueue.assert_not_called()


@pytest.mark.parametrize(
    ("costs", "expected_status", "expected_reason", "expected_raw"),
    [
        ([None], TraceInvestigationUsageStatus.UNPRICED, "missing_cost", None),
        ([0], TraceInvestigationUsageStatus.SKIPPED, "zero_cost", Decimal("0")),
    ],
)
@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
)
def test_unknown_cost_is_not_free_and_explicit_zero_is_distinct(
    observe_project,
    costs,
    expected_status,
    expected_reason,
    expected_raw,
):
    with patch(
        "tracer.services.trace_investigation_billing.price_trace_investigation_usage"
    ) as price:
        _publish_accounted(observe_project, costs)

    receipt = TraceInvestigationUsageReceipt.no_workspace_objects.get()
    assert receipt.status == expected_status
    assert receipt.status_reason == expected_reason
    assert receipt.raw_cost_usd == expected_raw
    assert receipt.credit_amount is None
    assert receipt.event_payload == {}
    price.assert_not_called()


@pytest.mark.parametrize(
    "result",
    [
        {
            "usage": {"model_calls": 1, "cost_usd": 1},
            "gateway_accounting": [{"cost": -1}],
        },
        {
            "usage": {"model_calls": 1, "cost_usd": "NaN"},
            "gateway_accounting": [{"cost": "NaN"}],
        },
        {
            "usage": {"model_calls": 1, "cost_usd": 2},
            "gateway_accounting": [{"cost": 1}],
        },
        {
            "usage": {"model_calls": 2, "cost_usd": 1_000_000_000.00001},
            "gateway_accounting": [{"cost": 1_000_000_000}, {"cost": 0}],
        },
    ],
)
def test_invalid_or_inconsistent_costs_fail_closed(result):
    cost = _derive_cost_state(result)

    assert cost.status == TraceInvestigationUsageStatus.UNPRICED
    assert cost.raw_cost_usd is None


def test_node_float_rounding_stays_within_fixed_cost_tolerance():
    cost = _derive_cost_state(
        {
            "usage": {"model_calls": 2, "cost_usd": 0.30000000000000004},
            "gateway_accounting": [{"cost": 0.1}, {"cost": 0.2}],
        }
    )

    assert cost.status == TraceInvestigationUsageStatus.PENDING
    assert cost.raw_cost_usd == Decimal("0.3")


@pytest.mark.parametrize("receipt_limit", [True, 0, 501])
def test_recovery_rejects_unbounded_limits(receipt_limit):
    with pytest.raises(ValueError, match="between 1 and 500"):
        deliver_trace_investigation_usage(receipt_limit=receipt_limit)


@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True,
    ERROR_FEED_OMEGA_DELAY_SECONDS=0,
    ERROR_FEED_OMEGA_BILLING_EMIT_ENABLED=False,
)
def test_bounded_recovery_backfills_legacy_report_without_emitting(observe_project):
    pricing = TraceInvestigationUsagePricing(True, Decimal("0.1"), "")
    _, _, publication = _publish_accounted(observe_project, [0.001])
    TraceInvestigationUsageReceipt.no_workspace_objects.all().delete()
    report = TraceInvestigationReport.no_workspace_objects.get(
        id=publication["report_id"]
    )

    with (
        patch(
            "tracer.services.trace_investigation_billing.price_trace_investigation_usage",
            return_value=pricing,
        ),
        patch(
            "tracer.services.trace_investigation_billing.enqueue_trace_investigation_usage"
        ) as enqueue,
    ):
        summary = deliver_trace_investigation_usage(receipt_limit=1)

    receipt = TraceInvestigationUsageReceipt.no_workspace_objects.get(report=report)
    assert summary["backfilled"] == 1
    assert summary["gated"] == 1
    assert receipt.status == TraceInvestigationUsageStatus.PENDING
    enqueue.assert_not_called()
