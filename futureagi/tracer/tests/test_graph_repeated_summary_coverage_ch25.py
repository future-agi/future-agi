"""Literal graph accounting oracles, executed through the actual CH25 queries."""

from datetime import timedelta

import pytest

from tracer.services.clickhouse import exact_graph_reads, graph_dispatch
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserDetailTimeSeriesQueryBuilderV2,
    UserTimeSeriesQueryBuilderV2,
)
from tracer.tests import test_graph_native_cost_coverage as graph_fixtures
from tracer.tests.test_graph_native_cost_coverage import (
    END,
    FILTERS,
    ORG,
    PROJECT,
    START,
    USER,
    NativeAnalytics,
    insert_span,
)

native_graph_db = graph_fixtures.native_graph_db
pytestmark = pytest.mark.integration

EMPTY = {
    "status": "complete",
    "known_subtotal": 0.0,
    "known_fact_count": 0,
    "unknown_fact_count": 0,
}
ONE = {
    "status": "complete",
    "known_subtotal": 1.25,
    "known_fact_count": 1,
    "unknown_fact_count": 0,
}
ZERO = {
    "status": "complete",
    "known_subtotal": 0.0,
    "known_fact_count": 1,
    "unknown_fact_count": 0,
}
TWO = {
    "status": "complete",
    "known_subtotal": 2.5,
    "known_fact_count": 2,
    "unknown_fact_count": 0,
}
DIFFERENT = {
    "status": "complete",
    "known_subtotal": 1.75,
    "known_fact_count": 2,
    "unknown_fact_count": 0,
}
PARENT = {
    "span_id": "parent",
    "trace": "run",
    "kind": "agent",
    "name": "pydantic_ai.agent.run",
    "cost": 1.25,
    "tokens": 120,
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "attrs": {"fi.cost.kind": "provider_reported"},
}
CHILD = {
    "span_id": "child",
    "trace": "run",
    "parent": "parent",
    "cost": 1.25,
    "tokens": 120,
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "attrs": {"fi.cost.kind": "provider_reported"},
}


@pytest.mark.parametrize(
    "spans,coverage,mean,traffic,wrapper_cost,wrapper_facts",
    [
        ([PARENT, CHILD], ONE, 0.625, 2, 0.0, 0),
        (
            [
                {**PARENT, "cost": 0, "attrs": {"fi.cost.kind": "unknown"}},
                {**CHILD, "cost": 0},
            ],
            ZERO,
            0.0,
            2,
            0.0,
            0,
        ),
        ([PARENT], ONE, 1.25, 1, 1.25, 1),
        ([PARENT, {**CHILD, "cost": 0.5}], DIFFERENT, 0.875, 2, 1.25, 1),
        (
            [PARENT, {**CHILD, "prompt_tokens": 90, "completion_tokens": 30}],
            TWO,
            1.25,
            2,
            1.25,
            1,
        ),
        (
            [
                {**CHILD, "parent": "", "span_id": "first"},
                {**CHILD, "parent": "", "span_id": "second"},
            ],
            TWO,
            1.25,
            2,
            0.0,
            0,
        ),
        ([{**PARENT, "kind": "llm"}, CHILD], TWO, 1.25, 2, 1.25, 1),
        (
            [
                {**CHILD, "parent": "", "service": "one"},
                {**CHILD, "parent": "", "service": "two"},
            ],
            TWO,
            1.25,
            2,
            0.0,
            0,
        ),
        ([{**CHILD, "parent": ""}], ONE, 1.25, 1, 0.0, 0),
    ],
    ids=[
        "known-repeat",
        "unknown-wrapper-known-zero",
        "parent-only",
        "different-cost",
        "different-token-components",
        "equal-independent-calls",
        "llm-not-summary",
        "same-id-independent-physical-keys",
        "known-call-default-alias-policy",
    ],
)
def test_repeated_summary_all_graph_consumers(
    native_graph_db,
    spans,
    coverage,
    mean,
    traffic,
    wrapper_cost,
    wrapper_facts,
):
    client = native_graph_db
    # Exercise the failing production alias policy, not a server workaround.
    client.execute("SET prefer_column_name_to_alias = 0")
    for span in spans:
        insert_span(client, **span)
    analytics = NativeAnalytics(client)
    bundle = graph_dispatch.fetch_all_system_metrics_ch(
        analytics=analytics, project_id=PROJECT, filters=FILTERS, interval="hour"
    )
    assert bundle["cost"][0]["value"] == mean
    assert bundle["cost"][0]["cost_coverage"] == coverage
    assert bundle["traffic"][0]["traffic"] == traffic
    # Traffic retains wrappers; usage counts only their additive contribution.
    repeated_wrapper = (
        len(spans) == 2
        and any(span.get("parent") for span in spans)
        and not wrapper_facts
    )
    assert bundle["tokens"][0]["value"] == 120 * (traffic - int(repeated_wrapper))
    for observe_type in ("span", "trace"):
        shared = exact_graph_reads.read_exact_system_graph(
            analytics=analytics,
            project_id=PROJECT,
            filters=FILTERS,
            interval="hour",
            metric_id="cost",
            observe_type=observe_type,
        )
        assert shared["data"][0]["value"] == mean
        assert shared["data"][0]["cost_coverage"] == coverage

    user = exact_graph_reads.read_exact_user_system_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=FILTERS,
        interval="hour",
        metric_id="cost",
    )
    assert user["data"][0]["value"] == coverage["known_subtotal"]
    assert user["data"][0]["cost_coverage"] == coverage
    # The unfiltered whole-row user builder is a separate source from the
    # reader's closed argMax projection; both must retain the proof columns.
    user_builder = UserTimeSeriesQueryBuilderV2(
        project_id=PROJECT, filters=FILTERS, interval="hour"
    )
    query, params = user_builder.build()
    raw_user = analytics.execute_ch_query(query, params).data[0]
    assert raw_user["avg_cost"] == coverage["known_subtotal"]
    assert raw_user["known_cost_fact_count"] == coverage["known_fact_count"]
    assert raw_user["unknown_cost_fact_count"] == 0

    detail = UserDetailTimeSeriesQueryBuilderV2(
        project_id=PROJECT,
        organization_id=ORG,
        end_user_id=USER,
        filters=FILTERS,
        interval="hour",
    )
    query, params = detail.build()
    row = analytics.execute_ch_query(
        query, params, settings={"prefer_column_name_to_alias": 0}
    ).data[0]
    assert row["cost"] == coverage["known_subtotal"]
    assert row["cost_known_subtotal"] == coverage["known_subtotal"]
    assert row["known_cost_fact_count"] == coverage["known_fact_count"]
    assert row["unknown_cost_fact_count"] == 0
    assert row["trace_count"] == 1

    graph = exact_graph_reads.read_exact_agent_graph(
        analytics=analytics, project_id=PROJECT, filters=FILTERS
    )
    assert (
        sum(node["total_cost"] for node in graph["nodes"]) == coverage["known_subtotal"]
    )
    assert (
        sum(node["cost_coverage"]["known_fact_count"] for node in graph["nodes"])
        == coverage["known_fact_count"]
    )
    assert all(
        node["cost_coverage"]["unknown_fact_count"] == 0 for node in graph["nodes"]
    )
    wrappers = [
        node for node in graph["nodes"] if node["name"] == "pydantic_ai.agent.run"
    ]
    assert sum(node["total_cost"] for node in wrappers) == wrapper_cost
    assert (
        sum(node["cost_coverage"]["known_fact_count"] for node in wrappers)
        == wrapper_facts
    )
    if any(span.get("parent") for span in spans):
        assert len(graph["edges"]) == 1
        edge_coverage = (
            ZERO
            if coverage == ZERO
            else (
                {
                    "status": "complete",
                    "known_subtotal": 0.5,
                    "known_fact_count": 1,
                    "unknown_fact_count": 0,
                }
                if coverage == DIFFERENT
                else ONE
            )
        )
        assert graph["edges"][0]["cost_coverage"] == edge_coverage
        assert graph["edges"][0]["total_cost"] == edge_coverage["known_subtotal"]
    else:
        assert graph["edges"] == []
    assert graph["path_edges"] == []


@pytest.mark.parametrize(
    "new_child,coverage,mean,traffic",
    [
        ({"cost": 0.5}, DIFFERENT, 0.875, 2),
        ({"deleted": 1}, ONE, 1.25, 1),
        ({"cost": 1.25, "attrs": {"fi.cost.kind": "provider_reported"}}, ONE, 0.625, 2),
    ],
)
def test_summary_proof_uses_latest_live_child(
    native_graph_db, new_child, coverage, mean, traffic
):
    client = native_graph_db
    client.execute("SYSTEM STOP MERGES spans")
    try:
        insert_span(client, **PARENT)
        insert_span(client, **{**CHILD, "attrs": {"fi.cost.kind": "unknown"}})
        insert_span(client, **{**CHILD, **new_child, "version": 2})
        analytics = NativeAnalytics(client)
        result = exact_graph_reads.read_exact_all_system_metrics(
            analytics=analytics, project_id=PROJECT, filters=FILTERS, interval="hour"
        )
        assert result["cost"][0]["value"] == mean
        assert result["cost"][0]["cost_coverage"] == coverage
        assert result["traffic"][0]["traffic"] == traffic
        graph = exact_graph_reads.read_exact_agent_graph(
            analytics=analytics, project_id=PROJECT, filters=FILTERS
        )
        assert (
            sum(n["cost_coverage"]["known_fact_count"] for n in graph["nodes"])
            == coverage["known_fact_count"]
        )
        assert (
            sum(n["total_cost"] for n in graph["nodes"]) == coverage["known_subtotal"]
        )
    finally:
        client.execute("SYSTEM START MERGES spans")


def test_summary_proof_crosses_bucket_and_partition_boundaries(native_graph_db):
    client = native_graph_db
    insert_span(client, **PARENT)
    insert_span(client, **{**CHILD, "start": START + timedelta(hours=1)})
    analytics = NativeAnalytics(client)
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=FILTERS,
        interval="hour",
        exact_snapshot=True,
        observe_type="span",
        start_date=START,
        end_date=END,
    )
    batches = []
    for offset in (0, 1):
        query, params = builder.build_exact_span_partition(
            partition_start=START + timedelta(hours=offset),
            partition_end=START + timedelta(hours=offset + 1),
        )
        result = analytics.execute_ch_query(query, params)
        batches.append((result.data, result.columns))
    rows, columns = exact_graph_reads._merge_exact_trace_contribution_rows(batches)
    metrics = builder.format_result(rows, columns)
    # Graph series retain the pre-existing inclusive zero-fill end bucket.
    # The half-open row filter keeps that boundary bucket empty (not billable).
    assert [p["value"] for p in metrics["cost"]] == [0.0, 1.25, 0]
    assert [p["cost_coverage"] for p in metrics["cost"]] == [EMPTY, ONE, EMPTY]
    assert [p["traffic"] for p in metrics["traffic"]] == [1, 1, 0]
    trace_builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=FILTERS,
        interval="hour",
        exact_snapshot=True,
        observe_type="trace",
        start_date=START,
        end_date=END,
    )
    query, params = trace_builder.build_exact_trace_contribution_batch(["run"])
    result = analytics.execute_ch_query(query, params)
    rows, columns = exact_graph_reads._merge_exact_trace_contribution_rows(
        [(result.data, result.columns)]
    )
    trace_metrics = trace_builder.format_result(rows, columns)
    assert [p["cost_coverage"] for p in trace_metrics["cost"]] == [EMPTY, ONE, EMPTY]
    assert [p["value"] for p in trace_metrics["cost"]] == [0.0, 1.25, 0]


def test_candidate_filter_does_not_hide_summary_children(native_graph_db):
    client = native_graph_db
    insert_span(client, **{**PARENT, "attrs": {"selected": "yes"}})
    insert_span(client, **CHILD)
    filters = [
        *FILTERS,
        {
            "column_id": "selected",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "yes",
            },
        },
    ]
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=filters,
        interval="hour",
        exact_snapshot=True,
        observe_type="span",
        start_date=START,
        end_date=END,
    )
    assert builder._exact_span_candidate_plan() is not None
    query, params = builder.build_exact_span_partition(
        partition_start=START, partition_end=END
    )
    result = NativeAnalytics(client).execute_ch_query(query, params)
    assert result.data[0]["cost_sum"] == 0.0
    assert result.data[0]["cost_known_subtotal"] == 0.0
    assert result.data[0]["known_cost_fact_count"] == 0
    assert result.data[0]["unknown_cost_fact_count"] == 0
    assert result.data[0]["traffic_count"] == 1


def test_equal_siblings_are_summed_before_summary_proof(native_graph_db):
    client = native_graph_db
    insert_span(
        client,
        **{
            **PARENT,
            "cost": 2.5,
            "tokens": 240,
            "prompt_tokens": 200,
            "completion_tokens": 40,
        },
    )
    insert_span(client, **CHILD)
    insert_span(client, **{**CHILD, "span_id": "second-child"})
    analytics = NativeAnalytics(client)
    bundle = exact_graph_reads.read_exact_all_system_metrics(
        analytics=analytics, project_id=PROJECT, filters=FILTERS, interval="hour"
    )
    assert bundle["cost"][0]["value"] == pytest.approx(0.833333333)
    assert bundle["cost"][0]["cost_coverage"] == TWO
    assert bundle["traffic"][0]["traffic"] == 3
    graph = exact_graph_reads.read_exact_agent_graph(
        analytics=analytics, project_id=PROJECT, filters=FILTERS
    )
    wrapper = next(node for node in graph["nodes"] if node["type"] == "agent")
    assert wrapper["cost_coverage"] == EMPTY
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["cost_coverage"] == TWO
    assert graph["edges"][0]["total_cost"] == 2.5


def test_summary_does_not_use_a_different_traces_child(native_graph_db):
    client = native_graph_db
    insert_span(client, **PARENT)
    insert_span(client, **{**CHILD, "trace": "another-run"})
    bundle = exact_graph_reads.read_exact_all_system_metrics(
        analytics=NativeAnalytics(client),
        project_id=PROJECT,
        filters=FILTERS,
        interval="hour",
    )
    assert bundle["cost"][0]["value"] == 1.25
    assert bundle["cost"][0]["cost_coverage"] == TWO
    assert bundle["traffic"][0]["traffic"] == 2


def test_nested_summary_edges_keep_topology_but_not_wrapper_money(native_graph_db):
    client = native_graph_db
    insert_span(client, **PARENT)
    insert_span(client, **{**PARENT, "span_id": "middle", "parent": "parent"})
    insert_span(client, **{**CHILD, "parent": "middle"})
    graph = exact_graph_reads.read_exact_agent_graph(
        analytics=NativeAnalytics(client), project_id=PROJECT, filters=FILTERS
    )
    wrapper = next(node for node in graph["nodes"] if node["type"] == "agent")
    assert wrapper["cost_coverage"] == EMPTY
    assert wrapper["total_cost"] == 0.0
    assert wrapper["span_count"] == 2
    assert len(graph["edges"]) == 2
    assert sorted(edge["total_cost"] for edge in graph["edges"]) == [0.0, 1.25]
    assert sorted(
        edge["cost_coverage"]["known_fact_count"] for edge in graph["edges"]
    ) == [0, 1]
    assert all(
        edge["cost_coverage"]["unknown_fact_count"] == 0 for edge in graph["edges"]
    )
