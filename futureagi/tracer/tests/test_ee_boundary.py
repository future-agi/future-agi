"""OSS-stripped boundary test (``_ee_available=False``).

A self-host / OSS deploy runs with ``ee.*`` absent, so every
``tracer.ee_boundary`` function must degrade to its no-op / passthrough fallback
without touching ``ee.*``. That path is otherwise unexercised — a regression
(e.g. dereferencing an ee symbol before the availability guard) would land
silently on self-host only. Pin all four fallbacks here.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest

from tracer import ee_boundary


def test_oss_path_returns_fallbacks_when_ee_absent():
    with patch.object(ee_boundary, "_ee_available", False):
        # LLM-backed helpers → None (callers default to medium / deterministic).
        assert ee_boundary.generate_scan_cluster_severity("cat", "brief") is None
        assert ee_boundary.generate_eval_cluster_meta("eval", "reasoning") is None

        # Passthroughs → the SAME object back, untouched (identity proves the
        # guard returned before any ee call / CH read).
        key_moments = [{"verbatim": "x"}]
        assert (
            ee_boundary.attribute_key_moments(key_moments, "trace-1", "proj-1")
            is key_moments
        )

        results: list = []
        assert ee_boundary.distill_eval_failure_phrases(results) is results


def test_usage_boundary_short_circuits_through_oss_oracle():
    with patch.object(ee_boundary, "is_oss", return_value=True):
        pricing = ee_boundary.price_trace_investigation_usage(Decimal("0.001"))
        with pytest.raises(RuntimeError, match="emitter is unavailable"):
            ee_boundary.emit_trace_investigation_usage({})

    assert pricing == ee_boundary.TraceInvestigationUsagePricing(
        applicable=False,
        credit_amount=None,
        reason="billing_unavailable",
    )


def test_usage_boundary_preserves_non_cloud_ee_distinction():
    with (
        patch.object(ee_boundary, "is_oss", return_value=False),
        patch(
            "ee.usage.deployment.DeploymentMode.is_cloud",
            return_value=False,
        ),
    ):
        pricing = ee_boundary.price_trace_investigation_usage(Decimal("0.001"))

    assert pricing == ee_boundary.TraceInvestigationUsagePricing(
        applicable=False,
        credit_amount=None,
        reason="non_cloud_deployment",
    )


def test_usage_boundary_uses_existing_emitter_in_cloud():
    payload = {
        "event_id": "ad7855e4-159e-5f21-b855-1c1ab4ff1072",
        "org_id": "org-1",
        "event_type": "trace_error_analysis",
        "amount": 2.0,
    }
    with (
        patch.object(ee_boundary, "is_oss", return_value=False),
        patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        ee_boundary.emit_trace_investigation_usage(payload)
    event = emit.call_args.args[0]
    assert event.event_id == payload["event_id"]
    assert event.org_id == payload["org_id"]
    assert event.amount == payload["amount"]
