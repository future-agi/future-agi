"""Canonical voice ended-reason parity across list and ClickHouse reads."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tracer.models.observability_provider import ProviderChoices
from tracer.services.clickhouse.filter_value_reads import (
    read_span_system_filter_values,
)
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.voice_filter_expressions import (
    VOICE_ENDED_REASON_FILTER_EXPRESSION,
)
from tracer.services.observability_providers import ObservabilityService

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("provider", "raw_log", "expected"),
    [
        (
            ProviderChoices.VAPI,
            {"id": "vapi-1", "endedReason": "assistant-ended-call", "messages": []},
            "assistant-ended-call",
        ),
        (
            ProviderChoices.RETELL,
            {
                "call_id": "retell-1",
                "disconnection_reason": "user_hangup",
                "call_cost": {},
            },
            "user_hangup",
        ),
    ],
)
def test_voice_list_uses_provider_ended_reason(provider, raw_log, expected):
    result = ObservabilityService.process_raw_logs(raw_log, provider)

    assert result["ended_reason"] == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("provider", "raw_log"),
    [
        (ProviderChoices.VAPI, {"id": "vapi-1", "messages": []}),
        (
            ProviderChoices.VAPI,
            {"id": "vapi-1", "endedReason": None, "messages": []},
        ),
        (ProviderChoices.RETELL, {"call_id": "retell-1", "call_cost": {}}),
        (
            ProviderChoices.RETELL,
            {
                "call_id": "retell-1",
                "disconnection_reason": None,
                "call_cost": {},
            },
        ),
    ],
)
def test_voice_list_falls_back_to_stored_reason_when_provider_reason_is_missing(
    provider, raw_log
):
    attrs = {"ended_reason": "collector-ended-call"}

    result = ObservabilityService.process_raw_logs(
        raw_log,
        provider,
        span_attributes=attrs,
    )

    assert result["ended_reason"] == "collector-ended-call"


@pytest.mark.unit
def test_voice_list_uses_stored_ended_reason_without_raw_log():
    attrs = {"ended_reason": "collector-ended-call"}

    assert (
        ObservabilityService.process_raw_logs(
            {}, ProviderChoices.VAPI, span_attributes=attrs
        )["ended_reason"]
        == "collector-ended-call"
    )


@pytest.mark.unit
def test_voice_ended_reason_expression_matches_provider_list_contract():
    expression = VOICE_ENDED_REASON_FILTER_EXPRESSION

    assert "'raw_log', 'endedReason'" in expression
    assert "'raw_log', 'disconnection_reason'" in expression
    assert "span_attr_str['ended_reason']" in expression
    assert "NOT ((JSONHas(span_attributes_raw, 'raw_log')" in expression
    assert "= 'retell'" in expression
    assert "= 'vapi'" in expression
    assert "coalesce(" in expression
    assert expression.endswith("span_attr_str['ended_reason'], ''), null))")


@pytest.mark.unit
def test_system_ended_reason_filter_is_provider_aware_and_root_scoped():
    filter_item = {
        "column_id": "ended_reason",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "assistant-ended-call",
            "col_type": "SYSTEM_METRIC",
        },
    }
    where, _ = ClickHouseFilterBuilder().translate([filter_item])
    graph_plan = compile_exact_graph_row_predicates(
        [filter_item],
        project_id=PROJECT_ID,
        observe_type="trace",
    )

    for predicate in (where, " AND ".join(graph_plan.predicates)):
        assert "'raw_log', 'endedReason'" in predicate
        assert "'raw_log', 'disconnection_reason'" in predicate
        assert "ended_reason']" in predicate
        assert "parent_span_id IS NULL OR parent_span_id = ''" in predicate
        assert "observation_type = 'conversation'" in predicate


@pytest.mark.unit
def test_system_ended_reason_value_sql_uses_the_same_canonical_expression():
    class Analytics:
        call = None

        def execute_ch_query(self, query, params, **kwargs):
            self.call = (query, params, kwargs)
            return SimpleNamespace(data=[])

    analytics = Analytics()
    read_span_system_filter_values(
        analytics,
        project_ids=[PROJECT_ID],
        metric_name="ended_reason",
        now=NOW,
    )

    query, _, _ = analytics.call
    assert "'raw_log', 'endedReason'" in query
    assert "'raw_log', 'disconnection_reason'" in query
    assert "attrs_string['ended_reason']" in query
    assert "latest_parent_span_id IS NULL OR latest_parent_span_id = ''" in query
    assert "latest_observation_type = 'conversation'" in query


@pytest.mark.unit
def test_raw_ended_reason_attribute_keeps_exact_attribute_semantics():
    where, _ = ClickHouseFilterBuilder().translate(
        [
            {
                "column_id": "ended_reason",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "assistant-ended-call",
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]
    )

    assert "span_attr_str['ended_reason']" in where
    assert "'endedReason'" not in where
    assert "'disconnection_reason'" not in where
    assert "observation_type = 'conversation'" not in where
