"""Voice filter aliases must match list, suggestion, and graph semantics.

The frontend sends public system-metric column IDs whose values may be derived
from several provider attributes. Raw SPAN_ATTRIBUTE filtering stays separate.
"""

from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.voice_call_list import (
    VoiceCallFilterBuilder,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallFilterBuilderV2,
    VoiceCallListQueryBuilderV2,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
WINDOW_END = datetime(2026, 8, 5, 12, 0)
WINDOW_START = WINDOW_END - timedelta(hours=1)

# FE column_id (from /tracer/dashboard/metrics/) -> stored CH attr key it must read.
VOICE_ALIASES = {
    "talk_ratio": "call.talk_ratio",
    "agent_latency": "avg_agent_latency_ms",
    "ai_interruptions": "ai_interruption_count",
    "user_interruptions": "user_interruption_count",
    "stop_time_after_interruption": "avg_stop_time_after_interruption_ms",
    "llm_cost": "cost_breakdown.llm",
    "stt_cost": "cost_breakdown.stt",
    "tts_cost": "cost_breakdown.tts",
    "total_cost": "cost_breakdown.total",
    "customer_cost": "cost_breakdown.total",
    "llm_latency": "modelLatencyAverage",
    "stt_latency": "transcriberLatencyAverage",
    "tts_latency": "voiceLatencyAverage",
    "response_time": "turnLatencyAverage",
}


@pytest.mark.unit
@pytest.mark.parametrize("col_id,stored_key", list(VOICE_ALIASES.items()))
def test_voice_filter_alias_resolves_to_stored_key(col_id, stored_key):
    where, _ = ClickHouseFilterBuilder().translate(
        [
            {
                "column_id": col_id,
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 0,
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
    )
    assert stored_key in where, f"{col_id} must read '{stored_key}', got: {where[:200]}"


@pytest.mark.unit
def test_call_type_filter_normalizes_vapi_retell_and_collector_rows():
    where, _ = ClickHouseFilterBuilder().translate(
        [
            {
                "column_id": "call_type",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "outbound",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
    )

    assert "multiIf(" in where
    assert "'raw_log', 'direction'" in where
    assert "= 'inboundPhoneCall', 'inbound'" in where
    assert "= 'inboundPhoneCall', 'inbound', 'outbound')" in where
    assert "outboundPhoneCall" not in where
    assert "span_attr_str['call_type']" in where
    assert "'retell'" in where
    assert "'vapi'" in where
    assert "IN ('vapi', 'retell', 'eleven_labs', 'bland', 'twilio')" in where
    # Provider dispatch mirrors process_raw_logs: a recognized hot provider
    # wins, then gen_ai.system, then Vapi. Raw payload shape never overrides it.
    assert "'startedAt'" not in where
    assert "'createdAt'" not in where
    assert "'call_status'" not in where
    assert "CAST(NULL AS Nullable(String))" in where


def _system_filter(column_id, filter_type, filter_op, filter_value):
    return {
        "column_id": column_id,
        "filter_config": {
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": filter_value,
            "col_type": "SYSTEM_METRIC",
        },
    }


def _time_filter():
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [WINDOW_START, WINDOW_END],
        },
    }


def _voice_builder(*filters):
    return VoiceCallListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), *filters],
        page_size=15,
    )


@pytest.mark.unit
def test_voice_call_status_alias_matches_normalized_list_semantics_everywhere():
    where, _ = VoiceCallFilterBuilder().translate(
        [_system_filter("call_status", "text", "equals", "completed")]
    )

    assert "multiIf(" in where
    assert "('ended', 'done', 'complete', 'completed'" in where
    assert "('in-progress', 'in_progress', 'ongoing'" in where
    for transition in (
        "initiated",
        "processing",
        "scheduled",
        "created",
        "dialing",
        "connecting",
    ):
        assert f"'{transition}'" in where
    assert "('failed', 'failure', 'error', 'errored')" in where
    assert "('not-connected', 'not_connected', 'no-answer'" in where
    assert "lowerUTF8(trimBoth(toString(" in where
    assert "CAST(NULL AS Nullable(String)), 'in-progress'" in where
    assert "coalesce(" in where
    assert "JSONExtractRaw(span_attributes_raw, 'raw_log') NOT IN" in where
    assert "span_attr_str['raw_log'] NOT IN ('', '{}', 'null')" in where

    generic_where, generic_params = ClickHouseFilterBuilder().translate(
        [_system_filter("call_status", "text", "equals", "completed")]
    )
    assert generic_where == where
    assert "completed" in generic_params.values()


@pytest.mark.unit
def test_voice_cost_cents_alias_normalizes_every_supported_provider():
    where, params = VoiceCallFilterBuilder().translate(
        [_system_filter("cost_cents", "number", "equals", 12.2)]
    )

    assert "'call_cost', 'combined_cost'" in where  # Retell: already cents
    assert "'metadata', 'cost'" in where  # ElevenLabs: already cents
    assert "'cost_breakdown.total'" in where  # VAPI dollars -> cents
    assert "'price'" in where  # Bland/Twilio dollars -> cents
    assert "toFloat64OrNull(JSONExtractString" in where
    assert "JSONType(" in where
    assert "IN ('Int64', 'UInt64', 'Float64')" in where
    assert "IN ('vapi', 'retell', 'eleven_labs', 'bland', 'twilio')" in where
    assert "toFloat64(cost)" not in where  # missing hot-column cost stays null
    assert 12.2 in params.values()

    generic_where, generic_params = ClickHouseFilterBuilder().translate(
        [_system_filter("cost_cents", "number", "equals", 12.2)]
    )
    assert generic_where == where
    assert 12.2 in generic_params.values()


@pytest.mark.unit
def test_voice_zero_cost_is_a_filterable_value():
    where, params = VoiceCallFilterBuilder().translate(
        [_system_filter("cost_cents", "number", "equals", 0)]
    )

    assert "'raw_log', 'cost'" in where
    assert "nullIf((coalesce(" not in where
    assert "toFloat64(cost)" not in where
    assert 0 in params.values()


@pytest.mark.unit
@pytest.mark.parametrize("column_id", ["talk_ratio", "agent_talk_percentage"])
def test_zero_talk_ratio_is_filterable_in_list_and_graph(column_id):
    filter_item = _system_filter(column_id, "number", "equals", 0)
    where, params = VoiceCallFilterBuilderV2().translate([filter_item])
    graph_plan = compile_exact_graph_row_predicates(
        [filter_item], project_id=PROJECT_ID, observe_type="trace"
    )
    graph_where = " AND ".join(graph_plan.predicates)

    for predicate in (where, graph_where):
        assert "attrs_number['call.talk_ratio'] >= 0" in predicate
        assert "attrs_number['call.talk_ratio'] > 0" not in predicate
    assert 0 in params.values()


@pytest.mark.unit
def test_voice_call_id_alias_matches_the_processed_list_value_for_every_provider():
    where, params = VoiceCallFilterBuilder().translate(
        [_system_filter("call_id", "text", "equals", "provider-call-123")]
    )

    # process_raw_logs provider dispatch: Vapi / Retell / ElevenLabs / Bland /
    # Twilio read these exact provider payload keys.  OTLP roots without a
    # raw_log use metadata.call_execution_id.
    assert "'raw_log', 'id'" in where
    assert "'raw_log', 'call_id'" in where
    assert "'raw_log', 'conversation_id'" in where
    assert "'raw_log', 'sid'" in where
    assert "'metadata', 'call_execution_id'" in where
    assert "gen_ai.system" in where
    assert "'vapi', 'retell', 'eleven_labs', 'bland', 'twilio'" in where
    assert "provider-call-123" in params.values()

    generic_where, generic_params = ClickHouseFilterBuilder().translate(
        [_system_filter("call_id", "text", "equals", "provider-call-123")]
    )
    assert generic_where == where
    assert "provider-call-123" in generic_params.values()


@pytest.mark.unit
@pytest.mark.parametrize(
    "filter_item",
    [
        _system_filter("call_status", "text", "in", ["completed", "failed"]),
        _system_filter("cost_cents", "number", "greater_than", 4.2),
        _system_filter("call_id", "text", "equals", "provider-call-123"),
        _system_filter("call_type", "text", "equals", "inbound"),
    ],
)
def test_explicit_voice_metrics_compile_identically_for_list_and_graph_builders(
    filter_item,
):
    generic_where, generic_params = ClickHouseFilterBuilderV2().translate([filter_item])
    voice_where, voice_params = VoiceCallFilterBuilderV2().translate([filter_item])

    assert generic_where == voice_where
    assert generic_params == voice_params


@pytest.mark.unit
@pytest.mark.parametrize(
    "filter_item",
    [
        _system_filter("call_status", "text", "equals", "completed"),
        _system_filter("cost_cents", "number", "greater_than", 4.2),
        _system_filter("call_id", "text", "equals", "provider-call-123"),
        _system_filter("call_type", "text", "equals", "inbound"),
    ],
)
def test_legacy_omitted_col_type_still_uses_normalized_voice_alias(filter_item):
    legacy_filter = {
        **filter_item,
        "filter_config": {
            key: value
            for key, value in filter_item["filter_config"].items()
            if key != "col_type"
        },
    }

    where, _ = ClickHouseFilterBuilderV2().translate([legacy_filter])
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), legacy_filter],
        page_size=15,
    )
    plans, residual_filters = builder._partition_trace_filter_plans(builder.filters)

    assert "observation_type = 'conversation'" in where
    assert residual_filters == []
    assert len(plans) == 1
    assert plans[0].scope == "root"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("column_id", "filter_type", "expected_marker", "forbidden_marker"),
    [
        ("call.status", "text", "attrs_string['call.status']", "'in-progress'"),
        ("cost_cents", "number", "attrs_number['cost_cents']", "combined_cost"),
        ("call_id", "text", "attrs_string['call_id']", "conversation_id"),
        ("call_type", "text", "attrs_string['call_type']", "raw_log"),
        ("ended_reason", "text", "attrs_string['ended_reason']", "conversation"),
        (
            "gen_ai.usage.total_tokens",
            "number",
            "attrs_number['gen_ai.usage.total_tokens']",
            "parent_span_id",
        ),
    ],
)
def test_raw_voice_attributes_remain_available_as_span_attributes(
    column_id,
    filter_type,
    expected_marker,
    forbidden_marker,
):
    filter_item = {
        "column_id": column_id,
        "filter_config": {
            "filter_type": filter_type,
            "filter_op": "equals",
            "filter_value": "ended" if filter_type == "text" else 12.2,
            "col_type": "SPAN_ATTRIBUTE",
        },
    }
    where, _ = ClickHouseFilterBuilderV2().translate([filter_item])
    graph_plan = compile_exact_graph_row_predicates(
        [filter_item],
        project_id=PROJECT_ID,
        observe_type="trace",
    )

    for predicate in (where, " AND ".join(graph_plan.predicates)):
        assert expected_marker in predicate
        assert forbidden_marker not in predicate


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filter_item", "sql_markers"),
    [
        (
            _system_filter("call_status", "text", "equals", "completed"),
            ("('ended', 'done', 'complete', 'completed'", "'in-progress'"),
        ),
        (
            _system_filter("cost_cents", "number", "greater_than", 4.2),
            ("'call_cost', 'combined_cost'", "'cost_breakdown.total'"),
        ),
        (
            _system_filter("call_id", "text", "equals", "provider-call-123"),
            ("'conversation_id'", "'metadata', 'call_execution_id'"),
        ),
        (
            _system_filter("call_type", "text", "equals", "inbound"),
            ("'raw_log', 'direction'", "'inboundPhoneCall', 'inbound', 'outbound'"),
        ),
        (
            _system_filter("duration", "number", "equals", 30),
            ("attrs_number['call.duration']", "observation_type = 'conversation'"),
        ),
        (
            _system_filter("ended_reason", "text", "equals", "customer-ended-call"),
            ("attrs_string['ended_reason']", "observation_type = 'conversation'"),
        ),
    ],
)
def test_exact_graph_predicates_use_normalized_voice_values(filter_item, sql_markers):
    plan = compile_exact_graph_row_predicates(
        [_time_filter(), filter_item],
        project_id=PROJECT_ID,
        observe_type="trace",
    )
    where = " AND ".join(plan.predicates)

    assert "parent_span_id IS NULL OR parent_span_id = ''" in where
    assert "observation_type = 'conversation'" in where
    for marker in sql_markers:
        assert marker in where


@pytest.mark.unit
def test_call_status_default_is_inside_the_conversation_root_guard():
    plan = compile_exact_graph_row_predicates(
        [_system_filter("call_status", "text", "equals", "completed")],
        project_id=PROJECT_ID,
        observe_type="trace",
    )
    predicate = " AND ".join(plan.predicates)
    root_guard = (
        "if((parent_span_id IS NULL OR parent_span_id = '') "
        "AND observation_type = 'conversation'"
    )

    # A non-conversation root fails the observation guard; an ordinary child
    # fails the parent guard. In both cases the expression is NULL before the
    # voice-only no-raw-log fallback can produce completed.
    assert root_guard in predicate
    assert predicate.index(root_guard) < predicate.index("'completed'")


@pytest.mark.unit
@pytest.mark.parametrize(
    "filter_item",
    [
        _system_filter("call_status", "text", "equals", "completed"),
        _system_filter("cost_cents", "number", "greater_than", 4.2),
        _system_filter("call_id", "text", "equals", "provider-call-123"),
        _system_filter("call_type", "text", "equals", "inbound"),
        _system_filter("duration", "number", "equals", 30),
        _system_filter("ended_reason", "text", "equals", "customer-ended-call"),
    ],
)
def test_bounded_trace_voice_metrics_are_conversation_root_plans(filter_item):
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), filter_item],
        page_size=15,
    )
    plans, residual_filters = builder._partition_trace_filter_plans(builder.filters)

    assert residual_filters == []
    assert len(plans) == 1
    assert plans[0].scope == "root"
    assert "parent_span_id IS NULL OR parent_span_id = ''" in plans[0].aggregates[0]
    assert "observation_type = 'conversation'" in plans[0].aggregates[0]


@pytest.mark.unit
def test_global_token_alias_keeps_existing_root_semantics():
    filter_item = _system_filter(
        "gen_ai.usage.total_tokens", "number", "greater_than", 100
    )
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[_time_filter(), filter_item],
        page_size=15,
    )
    plans, residual_filters = builder._partition_trace_filter_plans(builder.filters)
    graph_plan = compile_exact_graph_row_predicates(
        [filter_item], project_id=PROJECT_ID, observe_type="trace"
    )

    assert residual_filters == []
    assert len(plans) == 1
    assert plans[0].scope == "root"
    assert "total_tokens" in plans[0].aggregates[0]
    assert "parent_span_id IS NULL OR parent_span_id = ''" in " AND ".join(
        graph_plan.predicates
    )


@pytest.mark.unit
def test_voice_normalized_aliases_rewrite_to_ch25_columns():
    where, _ = VoiceCallFilterBuilderV2().translate(
        [
            _system_filter("call_status", "text", "equals", "completed"),
            _system_filter("cost_cents", "number", "greater_than", 1),
            _system_filter("call_id", "text", "equals", "provider-call-123"),
            _system_filter("call_type", "text", "equals", "inbound"),
        ]
    )

    assert "attrs_string['call.status']" in where
    assert "attrs_number['combined_cost']" in where
    assert "attributes_extra" in where
    assert "attrs_string['gen_ai.system']" in where
    assert "'metadata', 'call_execution_id'" in where
    assert "'conversation_id'" in where
    assert "'direction'" in where
    assert "attrs_string['call_type']" in where
    assert "span_attr_str" not in where
    assert "span_attr_num" not in where
    assert "span_attributes_raw" not in where


@pytest.mark.unit
@pytest.mark.parametrize("column_id", ["latency", "latency_ms"])
def test_voice_root_latency_contract_is_preserved_before_pagination(column_id):
    """Legacy and canonical root latency keep their historical meaning."""

    where, direct_params = VoiceCallFilterBuilderV2().translate(
        [_system_filter(column_id, "number", "greater_than", 578)]
    )
    assert "latency_ms" in where
    assert "attrs_number['avg_agent_latency_ms']" not in where
    assert 578.0 in direct_params.values()

    builder = _voice_builder(_system_filter(column_id, "number", "greater_than", 578))
    seed_query, seed_params = builder.build_filter_seed_page(
        slice_start=WINDOW_START,
        slice_end=WINDOW_END,
        limit=50,
    )
    match_query, match_params = (
        builder.build_filter_identity_match_query_from_seed_rows(
            [
                {
                    "project_id": PROJECT_ID,
                    "trace_id": "trace-a",
                    "root_span_id": "root-a",
                    "start_time": WINDOW_END - timedelta(minutes=1),
                }
            ]
        )
    )

    for query in (seed_query, match_query):
        assert "latency_ms" in query
        assert "attrs_number['avg_agent_latency_ms']" not in query
    assert 578.0 in seed_params.values()
    assert 578.0 in match_params.values()


@pytest.mark.unit
def test_voice_avg_agent_latency_filter_is_applied_before_pagination():
    """The displayed Avg Agent Latency uses its explicit metric id."""

    builder = _voice_builder(
        _system_filter("avg_agent_latency_ms", "number", "greater_than", 578)
    )
    seed_query, seed_params = builder.build_filter_seed_page(
        slice_start=WINDOW_START,
        slice_end=WINDOW_END,
        limit=50,
    )
    match_query, match_params = (
        builder.build_filter_identity_match_query_from_seed_rows(
            [
                {
                    "project_id": PROJECT_ID,
                    "trace_id": "trace-a",
                    "root_span_id": "root-a",
                    "start_time": WINDOW_END - timedelta(minutes=1),
                }
            ]
        )
    )

    for query in (seed_query, match_query):
        assert "attrs_number['avg_agent_latency_ms']" in query
    assert 578.0 in seed_params.values()
    assert 578.0 in match_params.values()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("filter_item", "sql_markers", "forbidden_marker"),
    [
        (
            _system_filter("call_status", "text", "equals", "completed"),
            (
                "('ended', 'done', 'complete', 'completed'",
                "('in-progress', 'in_progress', 'ongoing'",
                "IN ('vapi', 'retell', 'eleven_labs', 'bland', 'twilio')",
                "'raw_log', 'call_status'",
            ),
            "attrs_string['call_status']",
        ),
        (
            _system_filter("cost_cents", "number", "equals", 12.2),
            (
                "'call_cost', 'combined_cost'",
                "'metadata', 'cost'",
                "'cost_breakdown.total'",
            ),
            "attrs_number['cost_cents']",
        ),
        (
            _system_filter(
                "call_id", "text", "in", ["provider-call-123", "provider-call-456"]
            ),
            (
                "'raw_log', 'id'",
                "'raw_log', 'call_id'",
                "'raw_log', 'conversation_id'",
                "'raw_log', 'sid'",
                "'metadata', 'call_execution_id'",
            ),
            "attrs_string['call_id']",
        ),
        (
            _system_filter("call_type", "text", "in", ["inbound", "outbound"]),
            (
                "'raw_log', 'direction'",
                "'inboundPhoneCall', 'inbound', 'outbound'",
                "attrs_string['call_type']",
            ),
            "attrs_string['raw_call_type']",
        ),
    ],
)
def test_bounded_voice_seed_uses_normalized_alias_expression(
    filter_item,
    sql_markers,
    forbidden_marker,
):
    builder = _voice_builder(filter_item)
    if filter_item["column_id"] == "call_type":
        query, _ = builder.build_filter_unindexed_micro_seed_page(
            slice_start=WINDOW_END - timedelta(minutes=5),
            slice_end=WINDOW_END,
            limit=50,
        )
    else:
        query, _ = builder.build_filter_seed_page(
            slice_start=WINDOW_START,
            slice_end=WINDOW_END,
            limit=50,
        )

    for marker in sql_markers:
        assert marker in query
    assert forbidden_marker not in query


@pytest.mark.unit
def test_bounded_voice_match_combines_normalized_status_and_cost():
    query, params = _voice_builder(
        _system_filter("call_status", "text", "equals", "completed"),
        _system_filter("cost_cents", "number", "greater_than", 5),
    ).build_filter_match_query(["trace-a", "trace-b"])

    assert "('ended', 'done', 'complete', 'completed'" in query
    assert "'call_cost', 'combined_cost'" in query
    assert "'metadata', 'cost'" in query
    assert "'cost_breakdown.total'" in query
    assert "toFloat64(cost)" not in query
    assert "attrs_string['call_status']" not in query
    assert "attrs_number['cost_cents']" not in query
    assert "completed" in params.values()
    assert 5.0 in params.values()


@pytest.mark.unit
def test_generic_bounded_trace_uses_shared_voice_normalized_aliases():
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            _system_filter("call_status", "text", "equals", "completed"),
            _system_filter("cost_cents", "number", "greater_than", 5),
        ],
        page_size=15,
    )

    query, params = builder.build_filter_match_query(["trace-a"])

    assert "('in-progress', 'in_progress', 'ongoing'" in query
    assert "'call_cost', 'combined_cost'" in query
    assert "'cost_breakdown.total'" in query
    assert "completed" in params.values()
    assert 5.0 in params.values()
