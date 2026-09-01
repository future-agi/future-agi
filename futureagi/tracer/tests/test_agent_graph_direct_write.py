"""Exact direct-write and async-snapshot contracts for Agent Graph."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from clickhouse_driver.errors import ServerException

from tracer.services.clickhouse.query_builders.agent_graph import (
    AGENT_GRAPH_MAX_RESULT_BYTES,
    AGENT_GRAPH_MAX_VISIBLE_NODES,
    AGENT_GRAPH_OTHER_NODE_ID,
    AGENT_GRAPH_RESULT_ROW_SENTINEL,
)
from tracer.services.clickhouse.v2.query_builders.agent_graph import (
    AgentGraphQueryBuilderV2,
)

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
FINAL_STATUS_FILTER = {
    "column_id": "final_status",
    "display_name": "final_status",
    "filter_config": {
        "filter_type": "text",
        "filter_op": "in",
        "filter_value": ["Rechazado"],
        "col_type": "SPAN_ATTRIBUTE",
    },
}
PROFILE_FILTER = {
    "column_id": "profile",
    "filter_config": {
        "filter_type": "map",
        "filter_op": "contains",
        "filter_value": {"tier": "gold", "enabled": True},
        "col_type": "SPAN_ATTRIBUTE",
    },
}


def _complete_payload():
    return {
        "nodes": [],
        "edges": [],
        "path_edges": [],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }


def _call_agent_graph(monkeypatch, *, side_effect=None, refresh=False):
    from tracer.views import trace as trace_view

    project_scope = MagicMock()
    project_scope.filter.return_value.first.return_value = object()
    fetch = MagicMock(return_value=_complete_payload())
    if side_effect is not None:
        fetch.side_effect = side_effect
    monkeypatch.setattr(
        trace_view, "_project_queryset_for_request", lambda _request: project_scope
    )
    monkeypatch.setattr(trace_view, "fetch_agent_graph_ch", fetch)
    monkeypatch.setattr(
        trace_view,
        "bind_request_my_annotations_principal",
        lambda _request, filters: filters,
    )

    request = SimpleNamespace(
        validated_query_data={
            "project_id": PROJECT_ID,
            "filters": [FINAL_STATUS_FILTER],
            "refresh": refresh,
        }
    )
    view = trace_view.TraceView()
    view.request = request
    response = unwrap(trace_view.TraceView.agent_graph)(view, request)
    return response, fetch


@pytest.mark.unit
def test_agent_graph_http_path_schedules_exact_snapshot_without_sync_ch(monkeypatch):
    response, fetch = _call_agent_graph(monkeypatch, refresh=True)

    assert response.status_code == 200
    assert response.data["result"] == _complete_payload()
    fetch.assert_called_once_with(
        project_id=PROJECT_ID,
        filters=[FINAL_STATUS_FILTER],
        refresh=True,
    )


@pytest.mark.unit
def test_agent_graph_cache_identity_invalidates_retired_path_payloads(monkeypatch):
    from tracer.services.clickhouse import graph_dispatch

    schedule = MagicMock(return_value=_complete_payload())
    monkeypatch.setattr(graph_dispatch, "read_or_schedule_exact_snapshot", schedule)

    result = graph_dispatch.fetch_agent_graph_ch(
        project_id=PROJECT_ID,
        filters=[FINAL_STATUS_FILTER],
    )

    assert result == _complete_payload()
    identity = schedule.call_args.args[1]
    assert identity == {
        "project_id": PROJECT_ID,
        "filters": [FINAL_STATUS_FILTER],
        "payload_version": 5,
    }


@pytest.mark.unit
def test_agent_graph_is_one_latest_state_v2_statement():
    builder = AgentGraphQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[FINAL_STATUS_FILTER, PROFILE_FILTER],
    )
    query, params = builder.build()

    assert query.count("FROM spans") == 1
    assert "argMax(" in query
    assert "_version" in query
    assert "AS graph_physical_versions" in query
    assert "WHERE tupleElement(graph_latest_row," in query
    assert "FROM spans FINAL" not in query
    assert "attrs_string" in query
    assert "attributes_extra" in query
    assert "span_attr_str" not in query
    assert "span_attributes_raw" not in query
    assert "arrayJoin(arrayConcat(" in query
    assert "'node'" in query
    assert "'hierarchy'" in query
    assert "'path'" not in query
    assert "arrayFirst(" not in query
    assert "arrayExists(" not in query
    assert "indexOfAssumeSorted(" in query
    assert "arrayFold(" not in query
    assert "graph_execution_groups" not in query
    # Timestamp order must never invent a transition between siblings. Agent
    # Path stays unavailable until producers record chronological transitions;
    # parent_span_id is hierarchy, not execution order.
    assert "graph_chronological_spans" not in query
    assert "range(1, length(graph_chronological_spans))" not in query
    assert "uniqExact(trace_id)" not in query
    assert "graph_trace_events AS" in query
    assert "graph_ranked_events AS" in query
    assert "graph_fold_inputs AS" in query
    assert "graph_mapped_events AS" in query
    assert "groupArrayIf(" in query
    assert "graph_global_rank <= %(graph_visible_keep_count)s" in query
    assert f"max_result_rows = {AGENT_GRAPH_RESULT_ROW_SENTINEL}" in query
    assert f"max_result_bytes = {AGENT_GRAPH_MAX_RESULT_BYTES}" in query
    assert "max_threads = 1" in query
    assert params["project_id"] == PROJECT_ID
    assert params["graph_visible_keep_count"] == AGENT_GRAPH_MAX_VISIBLE_NODES - 1

    # Mutable Map/JSON values are consumed inside argMax. They must not be
    # applied as PREWHERE predicates where an old matching version could hide
    # a newer correction or tombstone.
    prewhere = query.split("PREWHERE", 1)[1].split("GROUP BY", 1)[0]
    assert "attrs_string" not in prewhere
    assert "attributes_extra" not in prewhere
    collapse_suffix = query.split(") AS graph_physical_versions", 1)[1]
    assert "attrs_string" not in collapse_suffix
    assert "attributes_extra" not in collapse_suffix


@pytest.mark.unit
def test_agent_graph_membership_is_global_but_unfiltered_scan_stays_bounded():
    start = datetime(2026, 8, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    date_filter = {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start, end],
        },
    }
    remote_child_filter = {
        "column_id": "span_name",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "remote-child",
        },
    }

    filtered_query, filtered_params = AgentGraphQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[date_filter, remote_child_filter],
    ).build()
    filtered_prewhere = filtered_query.split("PREWHERE", 1)[1].split("GROUP BY", 1)[0]

    assert "graph_witness_start_date" not in filtered_query
    assert "graph_witness_end_date" not in filtered_query
    assert "graph_witness_start_date" not in filtered_params
    assert "graph_witness_end_date" not in filtered_params
    assert "start_time >=" not in filtered_prewhere
    assert "start_time <" not in filtered_prewhere
    assert "graph_root_in_output_window = 1" in filtered_query
    assert "groupArrayIf(" in filtered_query
    assert "start_time >= %(start_date)s" in filtered_query
    assert "start_time < %(end_date)s" in filtered_query

    unfiltered_query, _ = AgentGraphQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[date_filter],
    ).build()
    unfiltered_prewhere = unfiltered_query.split("PREWHERE", 1)[1].split("GROUP BY", 1)[
        0
    ]
    assert "start_time >= %(start_date)s" in unfiltered_prewhere
    assert "start_time < %(end_date)s" in unfiltered_prewhere


@pytest.mark.unit
def test_agent_graph_formatter_never_relabels_parent_edges_as_path_edges():
    builder = AgentGraphQueryBuilderV2(project_id=PROJECT_ID, filters=[])
    payload = builder.format_result(
        [
            {
                "row_kind": "node",
                "source_node": "agent",
                "source_type": "agent",
                "target_node": "",
                "target_type": "",
                "item_count": 4,
                "avg_latency_ms": 12.5,
                "total_tokens": 8,
                "total_cost": 0.25,
                "error_count": 1,
                "trace_count": 3,
            },
            {
                "row_kind": "hierarchy",
                "source_node": "agent",
                "source_type": "agent",
                "target_node": "lookup",
                "target_type": "tool",
                "item_count": 2,
                "avg_latency_ms": 5,
                "total_tokens": 0,
                "total_cost": 0,
                "error_count": 0,
                "trace_count": 2,
            },
            {
                "row_kind": "path",
                "source_node": "agent",
                "source_type": "agent",
                "target_node": "lookup",
                "target_type": "tool",
                "item_count": 2,
                "avg_latency_ms": 5,
                "total_tokens": 0,
                "total_cost": 0,
                "error_count": 0,
                "trace_count": 2,
            },
        ],
        [],
    )

    assert payload["nodes"] == [
        {
            "id": "agent:agent",
            "name": "agent",
            "type": "agent",
            "span_count": 4,
            "avg_latency_ms": 12.5,
            "total_tokens": 8,
            "total_cost": 0.25,
            "error_count": 1,
            "trace_count": 3,
        }
    ]
    assert payload["edges"][0]["source"] == "agent:agent"
    assert payload["edges"][0]["target"] == "tool:lookup"
    assert payload["path_edges"] == []
    assert payload["graph_collapsed"] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "recorded_parent_edges",
    [
        pytest.param(
            [
                ("chain:rag-pipeline", "chain:query-enhancement"),
                ("chain:rag-pipeline", "retriever:retrieval"),
                ("chain:rag-pipeline", "reranker:reranking"),
                ("chain:rag-pipeline", "chain:generation"),
                ("chain:generation", "llm:GenerateContent"),
                ("chain:rag-pipeline", "evaluator:evaluations"),
            ],
            id="rag-pipeline",
        ),
        pytest.param(
            [
                ("agent:invoke_agent weather-agent", "llm:chat gemini-2.5-flash"),
                (
                    "llm:chat gemini-2.5-flash",
                    "unknown:model_step weather-agent",
                ),
                (
                    "unknown:model_step weather-agent",
                    "unknown:model_inference weather-agent",
                ),
                (
                    "unknown:model_step weather-agent",
                    "tool:execute_tool weatherTool",
                ),
            ],
            id="weather-agent",
        ),
    ],
)
def test_agent_graph_real_shapes_never_invent_sibling_chains(recorded_parent_edges):
    builder = AgentGraphQueryBuilderV2(project_id=PROJECT_ID, filters=[])

    rows = []
    for source, target in recorded_parent_edges:
        source_type, source_name = source.split(":", 1)
        target_type, target_name = target.split(":", 1)
        common = {
            "source_node": source_name,
            "source_type": source_type,
            "target_node": target_name,
            "target_type": target_type,
            "item_count": 40,
            "avg_latency_ms": 10,
            "total_tokens": 0,
            "total_cost": 0,
            "error_count": 0,
            "trace_count": 40,
        }
        rows.append({**common, "row_kind": "hierarchy"})

    payload = builder.format_result(rows, [])

    expected = set(recorded_parent_edges)
    assert {(edge["source"], edge["target"]) for edge in payload["edges"]} == expected
    assert payload["path_edges"] == []


@pytest.mark.unit
def test_agent_graph_formatter_collapses_overflow_without_dropping_counts():
    builder = AgentGraphQueryBuilderV2(project_id=PROJECT_ID, filters=[])
    rows = []
    for index in range(AGENT_GRAPH_MAX_VISIBLE_NODES + 6):
        rows.append(
            {
                "row_kind": "node",
                "source_node": f"node-{index:03d}",
                "source_type": "tool",
                "target_node": "",
                "target_type": "",
                "item_count": index + 1,
                "avg_latency_ms": index + 0.5,
                "total_tokens": index,
                "total_cost": index / 100,
                "error_count": index % 2,
                "trace_count": 1,
            }
        )
    rows.append(
        {
            "row_kind": "hierarchy",
            "source_node": "node-000",
            "source_type": "tool",
            "target_node": "node-001",
            "target_type": "tool",
            "item_count": 3,
            "avg_latency_ms": 7,
            "total_tokens": 5,
            "total_cost": 0.1,
            "error_count": 1,
            "trace_count": 2,
        }
    )

    payload = builder.format_result(rows, [])

    assert payload["graph_collapsed"] is True
    assert payload["omitted_node_count"] == 7
    assert len(payload["nodes"]) == AGENT_GRAPH_MAX_VISIBLE_NODES
    other = next(
        node for node in payload["nodes"] if node["id"] == AGENT_GRAPH_OTHER_NODE_ID
    )
    assert other["span_count"] == sum(range(1, 8))
    assert other["trace_count"] is None
    assert payload["path_edges"] == []
    assert payload["edges"][0]["source"] == AGENT_GRAPH_OTHER_NODE_ID
    assert payload["edges"][0]["target"] == AGENT_GRAPH_OTHER_NODE_ID
    assert payload["edges"][0]["transition_count"] == 3


@pytest.mark.unit
def test_agent_graph_formatter_preserves_sql_side_exact_other_fold():
    builder = AgentGraphQueryBuilderV2(project_id=PROJECT_ID, filters=[])
    common = {
        "graph_total_nodes": 70,
        "aggregate_member_count": 7,
        "source_endpoint_exact": 0,
        "target_endpoint_exact": 1,
        "trace_count": None,
        "trace_count_exact": 0,
    }
    payload = builder.format_result(
        [
            {
                **common,
                "row_kind": "node",
                "source_node": "__other_nodes__",
                "source_type": "aggregate",
                "target_node": "",
                "target_type": "",
                "item_count": 99,
                "avg_latency_ms": 8,
                "total_tokens": 4,
                "total_cost": 0.2,
                "error_count": 3,
            },
            {
                **common,
                "row_kind": "hierarchy",
                "source_node": "__other_nodes__",
                "source_type": "aggregate",
                "target_node": "answer",
                "target_type": "llm",
                "item_count": 11,
                "avg_latency_ms": 5,
                "total_tokens": 2,
                "total_cost": 0.1,
                "error_count": 1,
            },
        ],
        [],
    )

    assert payload["graph_collapsed"] is True
    assert payload["omitted_node_count"] == 7
    assert payload["nodes"] == [
        {
            "id": AGENT_GRAPH_OTHER_NODE_ID,
            "name": "Other nodes",
            "type": "aggregate",
            "span_count": 99,
            "avg_latency_ms": 8.0,
            "total_tokens": 4,
            "total_cost": 0.2,
            "error_count": 3,
            "trace_count": None,
            "trace_count_exact": False,
            "is_aggregate": True,
            "member_count": 7,
        }
    ]
    assert payload["edges"][0] == {
        "source": AGENT_GRAPH_OTHER_NODE_ID,
        "target": "llm:answer",
        "transition_count": 11,
        "avg_latency_ms": 5.0,
        "total_tokens": 2,
        "total_cost": 0.1,
        "error_count": 1,
        "trace_count": None,
        "is_self_loop": False,
        "trace_count_exact": False,
        "is_aggregate": True,
    }
    assert payload["path_edges"] == []


@pytest.mark.unit
def test_exact_agent_graph_reader_executes_only_one_statement():
    from tracer.services.clickhouse.exact_graph_reads import read_exact_agent_graph

    analytics = MagicMock()
    analytics.execute_ch_query.return_value = SimpleNamespace(
        data=[], columns=[], row_count=0
    )
    result = read_exact_agent_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[FINAL_STATUS_FILTER],
    )

    assert analytics.execute_ch_query.call_count == 1
    call = analytics.execute_ch_query.call_args
    assert call.kwargs["settings"]["max_threads"] == 1
    assert call.kwargs["settings"]["max_result_rows"] == AGENT_GRAPH_RESULT_ROW_SENTINEL
    assert call.kwargs["settings"]["max_result_bytes"] == AGENT_GRAPH_MAX_RESULT_BYTES
    assert call.args[0].count("FROM spans") == 1
    assert result["query_complete"] is True
    assert result["query_status"] == "complete"
    assert result["query_sampled"] is False


@pytest.mark.unit
def test_exact_aggregation_worker_routes_agent_graph_without_interval(monkeypatch):
    from tracer.services.clickhouse import exact_graph_reads
    from tracer.tasks import exact_aggregation

    analytics = object()
    reader = MagicMock(return_value=_complete_payload())
    monkeypatch.setattr(
        exact_aggregation,
        "_exact_observe_analytics",
        lambda: nullcontext(analytics),
    )
    monkeypatch.setattr(exact_graph_reads, "read_exact_agent_graph", reader)
    monkeypatch.setattr(
        exact_aggregation,
        "_reauthorize_exact_observe_project",
        lambda _identity: None,
    )

    payload = exact_aggregation._observe_payload(
        "observe-agent-graph",
        {"project_id": PROJECT_ID, "filters": [FINAL_STATUS_FILTER]},
    )

    assert payload == _complete_payload()
    reader.assert_called_once_with(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[FINAL_STATUS_FILTER],
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (ServerException("private timeout SQL", code=159), 503),
        (ServerException("private memory SQL", code=241), 503),
        (ServerException("private byte-limit SQL", code=307), 503),
        (ServerException("private heterogeneous SQL", code=386), 503),
        (ServerException("private unknown-column SQL", code=47), 500),
    ],
    ids=["code-159", "code-241", "code-307", "code-386", "code-47"],
)
def test_agent_graph_error_boundary_is_typed_and_sanitized(
    monkeypatch, failure, expected_status
):
    response, _fetch = _call_agent_graph(monkeypatch, side_effect=failure)

    assert response.status_code == expected_status
    assert "private" not in str(response.data)
    if expected_status == 503:
        assert response.data["code"] == "service_unavailable"
    else:
        assert response.data["code"] == "server_error"
