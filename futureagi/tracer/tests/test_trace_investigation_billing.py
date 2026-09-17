import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings

from tracer.ee_boundary import TraceInvestigationUsagePricing
from tracer.services.trace_investigation import (
    canonical_wire_result_digest,
    claim_due_investigations,
    record_trace_notifications,
)
from tracer.services.trace_investigation_billing import (
    _validated_cost,
    charge_trace_investigation,
)
from tracer.tests.test_trace_investigation_control import (
    _configure,
    _delivery,
    _publish,
    _result,
)


def test_gateway_accounting_must_match_reported_cost():
    result = {
        "gateway_accounting": [
            {"model_used": "gemini", "cost": 0.001},
            {"model_used": "gemini", "cost": 0.002},
        ],
        "usage": {"model_calls": 2, "cost_usd": 0.003},
    }
    assert _validated_cost(result) == (Decimal("0.003"), ["gemini"])
    result["usage"]["cost_usd"] = 0.004
    assert _validated_cost(result) is None
    result["usage"]["cost_usd"] = 0.003
    result["gateway_accounting"][0]["cost"] = None
    assert _validated_cost(result) is None
    result["gateway_accounting"] = [{"model_used": "gemini", "cost": 0.003}]
    result["usage"]["model_calls"] = True
    assert _validated_cost(result) is None


def test_charge_uses_existing_emitter_with_stable_tenant_event():
    report = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        workspace_id=None,
        model_calls=1,
        cost_usd=Decimal("0.01"),
        gateway_calls=SimpleNamespace(
            all=lambda: [SimpleNamespace(model_used="gemini", cost_usd=Decimal("0.01"))]
        ),
    )
    with (
        patch(
            "tracer.services.trace_investigation_billing.price_trace_investigation_usage",
            return_value=TraceInvestigationUsagePricing(True, Decimal("2"), ""),
        ),
        patch(
            "tracer.services.trace_investigation_billing.emit_trace_investigation_usage"
        ) as emit,
    ):
        charge_trace_investigation(report)
        charge_trace_investigation(report)
    first, second = [call.args[0] for call in emit.call_args_list]
    assert first == second
    assert first["org_id"] == str(report.organization_id)
    assert first["amount"] == 2.0
    assert first["properties"]["source_id"] == str(report.id)


@pytest.mark.django_db
@override_settings(ERROR_FEED_OMEGA_DELAY_SECONDS=0)
def test_publish_and_duplicate_schedule_same_billing_event(
    observe_project, django_capture_on_commit_callbacks
):
    _configure(observe_project)
    record_trace_notifications(deliveries=[_delivery(observe_project)])
    claim = claim_due_investigations(
        worker_id="billing-test", engine_version="omega-v1", limit=1
    )["claims"][0]
    result = _result(claim)
    result["gateway_accounting"][0]["cost"] = 0.01
    result["usage"]["cost_usd"] = 0.01
    result["usage"]["cost_status"] = "priced"
    result["result_digest"] = canonical_wire_result_digest(result)
    with (
        patch(
            "tracer.services.trace_investigation_billing.price_trace_investigation_usage",
            return_value=TraceInvestigationUsagePricing(True, Decimal("2"), ""),
        ),
        patch(
            "tracer.services.trace_investigation_billing.emit_trace_investigation_usage"
        ) as emit,
        django_capture_on_commit_callbacks(execute=True),
    ):
        accepted = _publish(
            idempotency_key="billing-report",
            lease_token=claim["lease_token"],
            result=result,
        )
        duplicate = _publish(
            idempotency_key="billing-report",
            lease_token=claim["lease_token"],
            result=result,
        )
    assert accepted["status"] == "accepted"
    assert duplicate["status"] == "duplicate"
    assert emit.call_count == 2
    assert (
        emit.call_args_list[0].args[0]["event_id"]
        == emit.call_args_list[1].args[0]["event_id"]
    )
