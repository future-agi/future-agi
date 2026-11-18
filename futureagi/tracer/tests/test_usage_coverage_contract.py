"""Additive coverage contracts; SQL/capture reducers have separate regressions."""

import json

import pytest
from rest_framework.renderers import JSONRenderer

from tracer.serializers.filters import ObserveGraphDataResultSerializer
from tracer.serializers.project import (
    ProjectGraphDataResultSerializer,
    ProjectUserGraphDataResultSerializer,
)
from tracer.serializers.trace import (
    TraceAgentGraphEdgeSerializer,
    TraceAgentGraphNodeSerializer,
    TraceDetailResultSerializer,
    TraceSessionTableRowSerializer,
    UsersTableRowSerializer,
)
from tracer.serializers.trace_session import TraceSessionDetailResultSerializer
from tracer.serializers.usage_coverage import CostCoverageSerializer

pytestmark = pytest.mark.unit

MIXED = {
    "status": "partial",
    "known_subtotal": 1.25,
    "known_fact_count": 1,
    "unknown_fact_count": 1,
}
TIMESTAMP = "2026-11-03T00:00:00Z"
NODE = {
    "id": "agent",
    "name": "agent",
    "type": "llm",
    "span_count": 2,
    "avg_latency_ms": 1.0,
    "total_tokens": 240,
    "total_cost": 1.25,
    "error_count": 0,
    "trace_count": 2,
}
EDGE = {
    "source": "parent",
    "target": "agent",
    "transition_count": 2,
    "avg_latency_ms": 1.0,
    "total_tokens": 240,
    "total_cost": 1.25,
    "error_count": 0,
    "trace_count": 2,
    "is_self_loop": False,
}


# Existing DRF representation defaults; legacy inputs deliberately omit them.
_ROW_NULL_DEFAULTS = {
    UsersTableRowSerializer: dict.fromkeys(
        (
            "user_id",
            "total_tokens",
            "input_tokens",
            "output_tokens",
            "num_traces",
            "num_sessions",
            "avg_session_duration",
            "avg_trace_latency",
            "num_llm_calls",
            "num_guardrails_triggered",
            "activated_at",
            "last_active",
            "num_active_days",
            "num_traces_with_errors",
            "bool_eval_pass_rate",
            "avg_output_float",
            "project_id",
            "project_name",
            "user_id_type",
            "user_id_hash",
            "end_user_id",
        )
    ),
    TraceSessionTableRowSerializer: dict.fromkeys(
        (
            "session_id",
            "session_name",
            "project_id",
            "start_time",
            "end_time",
            "created_at",
            "duration",
            "total_tokens",
            "total_traces_count",
            "first_message",
            "last_message",
            "user_id",
            "user_id_type",
            "user_id_hash",
        )
    ),
}


@pytest.mark.parametrize(
    "status, subtotal, known, unknown",
    [
        ("partial", 1.25, 1, 1),
        ("partial", 0.0, 1, 1),  # Known zero is still a known fact.
        ("unknown", 0.0, 0, 1),
        ("complete", 0.0, 1, 0),
        ("complete", 0.0, 0, 0),  # Empty observed set, not complete capture.
        ("complete", 1.25, 2, 0),  # 1.25 plus explicit zero.
    ],
)
def test_cost_coverage_states_are_numeric(status, subtotal, known, unknown):
    payload = {
        "status": status,
        "known_subtotal": subtotal,
        "known_fact_count": known,
        "unknown_fact_count": unknown,
    }
    serializer = CostCoverageSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    rendered = json.loads(JSONRenderer().render(serializer.data))
    assert rendered == payload
    assert type(rendered["known_subtotal"]) is float
    assert type(rendered["unknown_fact_count"]) is int


@pytest.mark.parametrize(
    "update",
    [
        {"status": "complete"},
        {"status": "unknown"},
        {"status": "unrecognized"},
        {"known_fact_count": -1},
        {"unknown_fact_count": -1},
        {"known_subtotal": -1},
        {"known_subtotal": float("nan")},
        {"known_subtotal": float("inf")},
        {"known_subtotal": None},
        {"known_fact_count": 0, "status": "unknown"},
    ],
)
def test_inconsistent_or_invalid_coverage_is_rejected(update):
    assert not CostCoverageSerializer(data={**MIXED, **update}).is_valid()


@pytest.mark.parametrize("field", list(MIXED))
def test_present_coverage_requires_every_field(field):
    serializer = CostCoverageSerializer(
        data={key: value for key, value in MIXED.items() if key != field}
    )
    assert not serializer.is_valid()
    assert field in serializer.errors


@pytest.mark.parametrize(
    "serializer_class, legacy",
    [
        (UsersTableRowSerializer, {"total_cost": 1.25}),
        (TraceAgentGraphNodeSerializer, NODE),
        (TraceAgentGraphEdgeSerializer, EDGE),
    ],
)
def test_numeric_total_contracts_remain_nonnullable(serializer_class, legacy):
    old = serializer_class(data=legacy)
    assert old.is_valid(), old.errors
    expected = {**_ROW_NULL_DEFAULTS.get(serializer_class, {}), **legacy}
    assert old.data == expected
    assert "cost_coverage" not in old.data
    assert not serializer_class(data={**legacy, "total_cost": None}).is_valid()

    new = serializer_class(data={**legacy, "cost_coverage": MIXED})
    assert new.is_valid(), new.errors
    assert new.data["total_cost"] == 1.25
    assert new.data["cost_coverage"] == MIXED
    assert not serializer_class(data={**legacy, "cost_coverage": None}).is_valid()


@pytest.mark.parametrize(
    "serializer_class, legacy",
    [
        (
            ProjectGraphDataResultSerializer,
            {
                "system_metrics": {
                    "cost": [{"timestamp": TIMESTAMP, "cost": 0.625}],
                    "query_exact": True,
                },
                "evaluations": {},
            },
        ),
        (
            ProjectUserGraphDataResultSerializer,
            {
                "session": [],
                "trace": [],
                "cost": [{"timestamp": TIMESTAMP, "cost": 1.25}],
                "input_tokens": [],
                "output_tokens": [],
            },
        ),
    ],
)
def test_project_envelopes_add_typed_bucket_coverage_without_retyping_series(
    serializer_class, legacy
):
    old = serializer_class(data=legacy)
    assert old.is_valid(), old.errors
    assert old.data == legacy
    bucket = {"timestamp": TIMESTAMP, **MIXED}
    new = serializer_class(data={**legacy, "cost_coverage": [bucket]})
    assert new.is_valid(), new.errors
    assert new.data == {**legacy, "cost_coverage": [bucket]}
    invalid = serializer_class(
        data={**legacy, "cost_coverage": [{**bucket, "unknown_fact_count": -1}]}
    )
    assert not invalid.is_valid()


def test_shared_graph_exact_query_is_not_monetary_completeness():
    payload = {
        "metric_name": "cost",
        "metric_statistic": "mean",
        "query_complete": True,
        "query_exact": True,
        "data": [
            {
                "timestamp": TIMESTAMP,
                "value": 0.625,
                "primary_traffic": None,
                "cost_coverage": MIXED,
            }
        ],
    }
    serializer = ObserveGraphDataResultSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert serializer.data == payload
    assert serializer.data["data"][0]["cost_coverage"]["known_subtotal"] == 1.25


@pytest.mark.parametrize(
    "serializer_class, legacy",
    [
        (TraceSessionTableRowSerializer, {"total_cost": None}),
        (
            TraceDetailResultSerializer,
            {
                "trace": {},
                "observation_spans": [],
                "summary": {"total_cost": 0},
                "graph": {},
            },
        ),
        (
            TraceSessionDetailResultSerializer,
            {"session_metadata": {"total_cost": 0}, "response": [], "next": False},
        ),
    ],
)
def test_capture_coverage_is_optional_and_independent_of_pricing(
    serializer_class, legacy
):
    old = serializer_class(data=legacy)
    assert old.is_valid(), old.errors
    expected = {**_ROW_NULL_DEFAULTS.get(serializer_class, {}), **legacy}
    assert old.data == expected
    assert "cost_coverage" not in old.data
    assert "capture_coverage" not in old.data
    coverage = {
        "scope": "observed_requests",
        "source_summary_present": True,
        "request_count": 0,
        "coverage_unknown": True,
    }
    empty_observed_cost = {
        "status": "complete",
        "known_subtotal": 0.0,
        "known_fact_count": 0,
        "unknown_fact_count": 0,
    }
    payload = {
        **legacy,
        "cost_coverage": empty_observed_cost,
        "capture_coverage": coverage,
    }
    new = serializer_class(data=payload)
    assert new.is_valid(), new.errors
    assert new.data == {**expected, **payload}
    invalid = serializer_class(
        data={**payload, "capture_coverage": {**coverage, "request_count": -1}}
    )
    assert not invalid.is_valid()
