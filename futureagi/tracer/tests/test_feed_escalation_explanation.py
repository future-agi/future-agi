"""The persisted status and the API explanation have one source of truth."""

import pytest

from tracer.models.trace_error_analysis import TraceErrorGroup
from tracer.queries.feed import _cluster_escalation
from tracer.serializers.feed import FeedEscalationSerializer

pytestmark = pytest.mark.unit


def test_manual_status_does_not_expose_stale_automatic_reason():
    issue = TraceErrorGroup(
        status="escalating",
        status_origin="manual",
        escalation_state={"reason": {"current_count": 100}},
    )
    assert _cluster_escalation(issue) is None


def test_current_reason_serializes_exact_units_counts_and_version():
    reason = {
        "current_count": 20,
        "affected_trace_count": 8,
        "baseline": 5,
        "entry_threshold": 20,
        "recovery_threshold": 8,
        "unit": "findings",
        "window_start": "2026-10-14T11:00:00+00:00",
        "window_end": "2026-10-14T12:00:00+00:00",
        "evaluated_at": "2026-10-14T12:10:00+00:00",
        "algorithm_version": "finding-volume-v1",
        "policy_revision": 3,
        "input_revision": 4,
        "effective_settings": {
            "entry_confirmation_minutes": 15,
            "recovery_confirmation_minutes": 10,
        },
    }
    issue = TraceErrorGroup(
        status="escalating", status_origin="system", escalation_state={"reason": reason}
    )
    output = FeedEscalationSerializer(_cluster_escalation(issue)).data
    assert output["current_count"] == 20
    assert output["affected_trace_count"] == 8
    assert output["policy_revision"] == 3
    assert output["entry_confirmation_minutes"] == 15
    assert output["unit"] == "findings"
