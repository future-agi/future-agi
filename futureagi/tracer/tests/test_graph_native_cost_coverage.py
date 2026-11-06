"""Monetary coverage through actual graph queries, reducers and API boundaries.

Real SQL cases require the parent's owned CH25 sidecar; no embedded fallback,
provider keys, SQL rewriting, or external model calls are involved.
"""

from datetime import UTC, datetime, timedelta
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services.clickhouse import exact_graph_reads, graph_dispatch
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.v2.query_builders.user_time_series import (
    UserDetailTimeSeriesQueryBuilderV2,
)
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (
    _REMAP_DDL,
    _SPANS_DDL,
)

START = datetime(2026, 10, 20, 12, tzinfo=UTC)
END = START + timedelta(hours=2)
PROJECT = "00000000-0000-4000-8000-000000000061"
ORG = "00000000-0000-4000-8000-000000000062"
USER = "00000000-0000-4000-8000-000000000063"
FILTERS = [
    {
        "column_id": "start_time",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START, END],
        },
    }
]
PARTIAL = {
    "status": "partial",
    "known_subtotal": 1.25,
    "known_fact_count": 1,
    "unknown_fact_count": 1,
}
UNKNOWN = {
    "status": "unknown",
    "known_subtotal": 0.0,
    "known_fact_count": 0,
    "unknown_fact_count": 1,
}


@pytest.mark.unit
def test_partition_merge_sums_counts_and_subtotal_before_mean():
    rows, columns = exact_graph_reads._merge_exact_trace_contribution_rows(
        [
            (
                [
                    {
                        "time_bucket": START,
                        "cost_sum": 1.25,
                        "traffic_count": 1,
                        "cost_known_subtotal": 1.25,
                        "known_cost_fact_count": 1,
                        "unknown_cost_fact_count": 0,
                    }
                ],
                [],
            ),
            (
                [
                    {
                        "time_bucket": START,
                        "cost_sum": 0.0,
                        "traffic_count": 1,
                        "cost_known_subtotal": 0.0,
                        "known_cost_fact_count": 0,
                        "unknown_cost_fact_count": 1,
                    }
                ],
                [],
            ),
        ]
    )
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=FILTERS,
        interval="hour",
        exact_snapshot=True,
        start_date=START,
        end_date=END,
    )
    metrics = builder.format_result(rows, columns)
    point = graph_dispatch.format_system_metric_graph(metrics, "cost")["data"][0]
    assert point["value"] == 0.625  # numeric compatibility, not a complete mean
    assert point["cost_coverage"] == PARTIAL
    assert metrics["cost"][1]["cost_coverage"] == {
        "status": "complete",
        "known_subtotal": 0.0,
        "known_fact_count": 0,
        "unknown_fact_count": 0,
    }
    assert (
        "cost_coverage"
        not in graph_dispatch.format_system_metric_graph(metrics, "tokens")["data"][0]
    )


@pytest.mark.unit
def test_user_detail_http_publishes_typed_companion_without_retyping_cost(monkeypatch):
    from contextlib import nullcontext

    from tracer.views import project as views

    analytics = SimpleNamespace(
        supports_per_query_read_settings=True,
        execute_ch_query=Mock(
            return_value=SimpleNamespace(
                data=[
                    {
                        "time_bucket": START,
                        "cost": 1.25,
                        "session_count": 0,
                        "trace_count": 2,
                        "input_tokens": 200,
                        "output_tokens": 40,
                        "cost_known_subtotal": 1.25,
                        "known_cost_fact_count": 1,
                        "unknown_cost_fact_count": 1,
                    }
                ],
                columns=[],
            )
        ),
    )
    monkeypatch.setattr(views, "V2AnalyticsQueryService", lambda: analytics)
    monkeypatch.setattr(views, "graph_action_postgres_budget", lambda _: nullcontext())
    monkeypatch.setattr(views, "graph_action_remaining_ms", lambda _: 1000)
    monkeypatch.setattr(
        views, "bind_request_my_annotations_principal", lambda _, filters: filters
    )
    monkeypatch.setattr(
        views, "get_request_organization", lambda _: SimpleNamespace(id=ORG)
    )
    view = views.ProjectView()
    view._get_project_in_scope = Mock(return_value=SimpleNamespace(organization_id=ORG))
    request = SimpleNamespace(
        validated_query_data={"project_id": PROJECT, "end_user_id": USER},
        validated_data={"interval": "hour", "filters": FILTERS},
    )
    response = unwrap(views.ProjectView.get_user_graph_data)(
        view, request, _graph_action_deadline=object()
    )
    assert response.status_code == 200
    result = response.data["result"]
    assert result["cost"][0]["cost"] == 1.25
    assert result["cost_coverage"][0] == {
        "timestamp": result["cost"][0]["timestamp"],
        **PARTIAL,
    }
    analytics.execute_ch_query.assert_called_once()


@pytest.mark.unit
def test_project_http_moves_companion_beside_legacy_bundle(monkeypatch):
    from tracer.views import project as views

    timestamp = START.isoformat()
    payload = {
        "cost": [
            {
                "timestamp": timestamp,
                "value": 0.625,
                "cost": 0.625,
                "cost_coverage": PARTIAL,
            }
        ],
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }
    monkeypatch.setattr(views, "get_all_system_metrics", lambda **_: payload)
    monkeypatch.setattr(
        views, "bind_request_my_annotations_principal", lambda _, filters: filters
    )
    view = views.ProjectView()
    view._get_project_in_scope = Mock(return_value=SimpleNamespace(organization_id=ORG))
    request = SimpleNamespace(
        validated_query_data={
            "project_id": PROJECT,
            "interval": "hour",
            "filters": FILTERS,
        }
    )
    response = unwrap(views.ProjectView.get_graph_data)(view, request)
    assert response.status_code == 200
    result = response.data["result"]
    assert result["cost_coverage"] == [{"timestamp": timestamp, **PARTIAL}]
    assert result["system_metrics"]["cost"] == [
        {"timestamp": timestamp, "value": 0.625, "cost": 0.625}
    ]


@pytest.fixture
def native_graph_db():
    with _ch_test_owned_database("test_graph_cost_") as database:
        with _ch_test_native_client(database=database) as client:
            client.execute(_SPANS_DDL)
            client.execute(_REMAP_DDL)
            client.execute(
                _REMAP_DDL.replace("trace_session_id_remap", "end_user_id_remap")
            )
            client.execute("""CREATE TABLE end_users (
                project_id UUID, end_user_id UUID, organization_id UUID,
                version UInt64, is_deleted UInt8,
                user_id String DEFAULT 'synthetic', user_id_type String DEFAULT 'string',
                user_id_hash UInt64 DEFAULT 1, first_seen DateTime64(6, 'UTC') DEFAULT now64(6)
            ) ENGINE=ReplacingMergeTree(version) ORDER BY (project_id, end_user_id)""")
            client.execute(
                "INSERT INTO end_users (project_id, end_user_id, organization_id, version, is_deleted) VALUES",
                [(PROJECT, USER, ORG, 1, 0)],
            )
            yield client


class NativeAnalytics:
    supports_per_query_read_settings = True

    def __init__(self, client):
        self.client = client
        self.queries = []

    def execute_ch_query(self, query, params, *, timeout_ms=None, settings=None):
        self.queries.append(query)
        rows, columns = self.client.execute(
            query, params, with_column_types=True, settings=settings or {}
        )
        names = [name for name, _ in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows], columns=names
        )


def insert_span(
    client,
    *,
    span_id,
    cost=0.0,
    attrs=None,
    tokens=120,
    kind="llm",
    version=1,
    deleted=0,
    start=START,
    project=PROJECT,
    parent="",
    trace=None,
    user=USER,
    name="chat",
    prompt_tokens=None,
    completion_tokens=0,
    service="",
):
    row = {
        "project_id": project,
        "end_user_id": user,
        "trace_id": trace or span_id,
        "id": span_id,
        "parent_span_id": parent,
        "name": name,
        "observation_type": kind,
        "service_name": service,
        "start_time": start,
        "cost": cost,
        "total_tokens": tokens,
        "prompt_tokens": tokens if prompt_tokens is None else prompt_tokens,
        "completion_tokens": completion_tokens,
        "attrs_string": attrs or {},
        "_version": version,
        "is_deleted": deleted,
    }
    client.execute(
        f"INSERT INTO spans ({', '.join(row)}) VALUES", [tuple(row.values())]
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "cost,attrs,kind,tokens,expected",
    [
        (0, {"fi.cost.kind": "unknown"}, "llm", 120, PARTIAL),
        (
            0,
            {"fi.cost.kind": "provider_reported"},
            "llm",
            120,
            {
                "status": "complete",
                "known_subtotal": 1.25,
                "known_fact_count": 2,
                "unknown_fact_count": 0,
            },
        ),
        (
            0,
            {},
            "llm",
            120,
            {
                "status": "complete",
                "known_subtotal": 1.25,
                "known_fact_count": 2,
                "unknown_fact_count": 0,
            },
        ),
        (
            0,
            {"fi.cost.kind": "unknown"},
            "tool",
            0,
            {
                "status": "complete",
                "known_subtotal": 1.25,
                "known_fact_count": 1,
                "unknown_fact_count": 0,
            },
        ),
        (
            0,
            {
                "fi.cost.kind": "unknown",
                "fi.usage.contribution": "none",
                "fi.usage.scope": "turn",
            },
            "agent",
            0,
            {
                "status": "complete",
                "known_subtotal": 1.25,
                "known_fact_count": 1,
                "unknown_fact_count": 0,
            },
        ),
        (
            17,
            {
                "fi.cost.kind": "provider_reported",
                "fi.usage.contribution": "none",
                "fi.source.summary.cost": "17",
            },
            "agent",
            0,
            {
                "status": "complete",
                "known_subtotal": 1.25,
                "known_fact_count": 1,
                "unknown_fact_count": 0,
            },
        ),
    ],
)
def test_actual_project_user_shared_and_agent_queries(
    native_graph_db, cost, attrs, kind, tokens, expected
):
    client = native_graph_db
    insert_span(
        client, span_id="known", cost=1.25, attrs={"fi.cost.kind": "provider_reported"}
    )
    insert_span(
        client, span_id="other", cost=cost, attrs=attrs, kind=kind, tokens=tokens
    )
    insert_span(client, span_id="foreign", cost=99, project=str(uuid4()))
    analytics = NativeAnalytics(client)
    bundle = graph_dispatch.fetch_all_system_metrics_ch(
        analytics=analytics, project_id=PROJECT, filters=FILTERS, interval="hour"
    )
    assert bundle["cost"][0]["value"] == 0.625
    assert bundle["cost"][0]["cost_coverage"] == expected
    assert bundle["query_exact"] is True
    shared = exact_graph_reads.read_exact_system_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=FILTERS,
        interval="hour",
        metric_id="cost",
        observe_type="span",
    )
    assert shared["data"][0]["cost_coverage"] == expected
    user = exact_graph_reads.read_exact_user_system_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=FILTERS,
        interval="hour",
        metric_id="cost",
    )
    assert user["data"][0]["value"] == 1.25
    assert user["data"][0]["cost_coverage"] == expected
    detail = UserDetailTimeSeriesQueryBuilderV2(
        project_id=PROJECT,
        organization_id=ORG,
        end_user_id=USER,
        filters=FILTERS,
        interval="hour",
    )
    sql, params = detail.build()
    detail_rows = analytics.execute_ch_query(sql, params).data
    assert detail_rows[0]["cost"] == 1.25
    assert detail_rows[0]["known_cost_fact_count"] == expected["known_fact_count"]
    assert detail_rows[0]["unknown_cost_fact_count"] == expected["unknown_fact_count"]
    graph = exact_graph_reads.read_exact_agent_graph(
        analytics=analytics, project_id=PROJECT, filters=FILTERS
    )
    assert (
        sum(node["cost_coverage"]["known_fact_count"] for node in graph["nodes"])
        == expected["known_fact_count"]
    )
    assert (
        sum(node["cost_coverage"]["unknown_fact_count"] for node in graph["nodes"])
        == expected["unknown_fact_count"]
    )
    if kind == "llm":
        assert graph["nodes"][0]["cost_coverage"] == expected
        assert graph["nodes"][0]["total_cost"] == 1.25
    assert sum(node["total_cost"] for node in graph["nodes"]) == 1.25
    assert graph["path_edges"] == []


@pytest.mark.integration
@pytest.mark.parametrize("new_unknown", [False, True])
def test_winner_reversal_tombstone_partition_and_direct_dispatch(
    native_graph_db, monkeypatch, new_unknown
):
    client = native_graph_db
    client.execute("SYSTEM STOP MERGES spans")
    try:
        for version, unknown in [(1, not new_unknown), (2, new_unknown)]:
            insert_span(
                client,
                span_id="revised",
                version=version,
                cost=0 if unknown else 1.25,
                attrs={"fi.cost.kind": "unknown" if unknown else "provider_reported"},
            )
        insert_span(client, span_id="deleted", attrs={"fi.cost.kind": "unknown"})
        insert_span(
            client,
            span_id="deleted",
            attrs={"fi.cost.kind": "unknown"},
            deleted=1,
            version=2,
        )
        insert_span(
            client,
            span_id="next-hour",
            start=START + timedelta(hours=1),
            attrs={"fi.cost.kind": "unknown"},
        )
        expected = (
            UNKNOWN
            if new_unknown
            else {
                "status": "complete",
                "known_subtotal": 1.25,
                "known_fact_count": 1,
                "unknown_fact_count": 0,
            }
        )
        analytics = NativeAnalytics(client)
        monkeypatch.setattr(
            graph_dispatch, "_read_or_refresh_exact_graph", lambda **_: None
        )
        monkeypatch.setattr(
            graph_dispatch, "_affordable_raw_graph_seed", lambda **_: (None, 0)
        )
        result = graph_dispatch.fetch_system_metric_graph_ch(
            analytics=analytics,
            project_id=PROJECT,
            filters=FILTERS,
            interval="hour",
            metric_id="cost",
            observe_type="span",
        )
        assert result["data"][0]["cost_coverage"] == expected
        assert result["data"][1]["cost_coverage"] == UNKNOWN
        assert len(analytics.queries) == 1
        assert "graph_latest_row" in analytics.queries[0]
        assert "sumMerge" not in analytics.queries[0]
        builder = TimeSeriesQueryBuilder(
            project_id=PROJECT,
            filters=FILTERS,
            interval="hour",
            exact_snapshot=True,
            observe_type="span",
        )
        # The filter parser is the source of the half-open naive-UTC bounds;
        # partition cuts must use that same caller datetime contract.
        builder.start_date, builder.end_date = builder.parse_time_range(builder.filters)
        batches = []
        for offset in (0, 1):
            sql, params = builder.build_exact_span_partition(
                partition_start=builder.start_date + timedelta(hours=offset),
                partition_end=builder.start_date + timedelta(hours=offset + 1),
            )
            result = analytics.execute_ch_query(sql, params)
            batches.append((result.data, result.columns))
        rows, columns = exact_graph_reads._merge_exact_trace_contribution_rows(batches)
        # The existing formatter includes the empty end-boundary bucket;
        # it carries no monetary facts or subtotal from the half-open query.
        assert [
            p["cost_coverage"] for p in builder.format_result(rows, columns)["cost"]
        ] == [
            expected,
            UNKNOWN,
            {
                "status": "complete",
                "known_subtotal": 0.0,
                "known_fact_count": 0,
                "unknown_fact_count": 0,
            },
        ]
    finally:
        client.execute("SYSTEM START MERGES spans")


@pytest.mark.integration
def test_agent_edge_coverage_counts_only_child_contributions(native_graph_db):
    client = native_graph_db
    insert_span(client, span_id="parent", kind="agent", tokens=0, trace="run")
    insert_span(client, span_id="known", cost=1.25, trace="run", parent="parent")
    insert_span(
        client,
        span_id="unknown",
        attrs={"fi.cost.kind": "unknown"},
        trace="run",
        parent="parent",
    )
    graph = exact_graph_reads.read_exact_agent_graph(
        analytics=NativeAnalytics(client), project_id=PROJECT, filters=FILTERS
    )
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["total_cost"] == 1.25
    assert graph["edges"][0]["cost_coverage"] == PARTIAL
    llm = next(node for node in graph["nodes"] if node["type"] == "llm")
    assert llm["cost_coverage"] == PARTIAL
    assert next(node for node in graph["nodes"] if node["type"] == "agent")[
        "cost_coverage"
    ] == {
        "status": "complete",
        "known_subtotal": 0.0,
        "known_fact_count": 0,
        "unknown_fact_count": 0,
    }


@pytest.mark.integration
@pytest.mark.parametrize(
    "with_zero,expected",
    [
        (False, UNKNOWN),
        (
            True,
            {
                "status": "partial",
                "known_subtotal": 0.0,
                "known_fact_count": 1,
                "unknown_fact_count": 1,
            },
        ),
    ],
)
def test_unpriced_is_not_free_even_after_user_remap(
    native_graph_db, with_zero, expected
):
    client = native_graph_db
    old_user = str(uuid4())
    client.execute(
        "INSERT INTO end_user_id_remap (old_id, new_id) VALUES", [(old_user, USER)]
    )
    insert_span(
        client, span_id="unknown", user=old_user, attrs={"fi.cost.kind": "unknown"}
    )
    if with_zero:
        insert_span(client, span_id="zero", attrs={"fi.cost.kind": "provider_reported"})
    analytics = NativeAnalytics(client)
    result = exact_graph_reads.read_exact_all_system_metrics(
        analytics=analytics, project_id=PROJECT, filters=FILTERS, interval="hour"
    )
    assert result["cost"][0]["value"] == 0
    assert result["cost"][0]["cost_coverage"] == expected
    result = exact_graph_reads.read_exact_user_system_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=FILTERS,
        interval="hour",
        metric_id="total_cost",
    )
    assert result["data"][0]["value"] == 0
    assert result["data"][0]["cost_coverage"] == expected
    # The graph's own user membership selector cannot classify this user free.
    cost_filter = {
        "column_id": "total_cost",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": "equals",
            "filter_value": 0,
        },
    }
    filtered = exact_graph_reads.read_exact_user_system_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=[*FILTERS, cost_filter],
        interval="hour",
        metric_id="active_users",
    )
    assert all(point["value"] == 0 for point in filtered["data"])
    graph = exact_graph_reads.read_exact_agent_graph(
        analytics=analytics, project_id=PROJECT, filters=FILTERS
    )
    assert graph["nodes"][0]["total_cost"] == 0
    assert graph["nodes"][0]["cost_coverage"] == expected


@pytest.mark.integration
def test_trace_contribution_batches_keep_additive_monetary_counts(native_graph_db):
    client = native_graph_db
    insert_span(client, span_id="known", cost=1.25)
    insert_span(client, span_id="unknown", attrs={"fi.cost.kind": "unknown"})
    analytics = NativeAnalytics(client)
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT,
        filters=FILTERS,
        interval="hour",
        exact_snapshot=True,
        observe_type="trace",
    )
    batches = []
    for trace in ("known", "unknown"):
        query, params = builder.build_exact_trace_contribution_batch([trace])
        result = analytics.execute_ch_query(query, params)
        batches.append((result.data, result.columns))
    rows, columns = exact_graph_reads._merge_exact_trace_contribution_rows(batches)
    result = builder.format_result(rows, columns)
    assert result["cost"][0]["value"] == 0.625
    assert result["cost"][0]["cost_coverage"] == PARTIAL


@pytest.mark.integration
def test_timestamp_correction_cannot_revive_old_unknown_price(native_graph_db):
    client = native_graph_db
    filters = [
        {
            "column_id": "start_time",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START, START + timedelta(minutes=30)],
            },
        }
    ]
    client.execute("SYSTEM STOP MERGES spans")
    try:
        insert_span(
            client,
            span_id="corrected",
            start=START + timedelta(minutes=10),
            attrs={"fi.cost.kind": "unknown"},
        )
        insert_span(
            client,
            span_id="corrected",
            start=START + timedelta(minutes=40),
            version=2,
            cost=1.25,
            attrs={"fi.cost.kind": "provider_reported"},
        )
        analytics = NativeAnalytics(client)
        bundle = exact_graph_reads.read_exact_all_system_metrics(
            analytics=analytics, project_id=PROJECT, filters=filters, interval="hour"
        )
        assert all(
            point["cost_coverage"]["unknown_fact_count"] == 0
            for point in bundle["cost"]
        )
        assert all(point["value"] == 0 for point in bundle["cost"])
        graph = exact_graph_reads.read_exact_agent_graph(
            analytics=analytics, project_id=PROJECT, filters=filters
        )
        assert graph["nodes"] == []
        detail = UserDetailTimeSeriesQueryBuilderV2(
            project_id=PROJECT,
            organization_id=ORG,
            end_user_id=USER,
            filters=filters,
            interval="hour",
        )
        query, params = detail.build()
        assert analytics.execute_ch_query(query, params).data == []
    finally:
        client.execute("SYSTEM START MERGES spans")


@pytest.mark.unit
@pytest.mark.parametrize(
    "namespace,metric,key",
    [
        ("observe-system-graph", "cost", "data"),
        ("observe-user-system-graph", "total_cost", "data"),
        ("observe-agent-graph", None, "nodes"),
        ("observe-all-system-graphs", None, "cost"),
    ],
)
def test_rolling_worker_cache_cannot_drop_monetary_companion(
    monkeypatch, namespace, metric, key
):
    schedule = Mock(return_value={})
    monkeypatch.setattr(graph_dispatch, "read_or_schedule_exact_snapshot", schedule)
    graph_dispatch._read_or_refresh_exact_graph(
        namespace=namespace,
        identity={"project_id": PROJECT, "metric_id": metric},
        refresh=False,
        pending_payload={},
    )
    accept = schedule.call_args.kwargs["accept_snapshot"]
    assert not accept({key: [{"value": 0.625, "total_cost": 1.25}]})
    assert accept(
        {key: [{"value": 0.625, "total_cost": 1.25, "cost_coverage": PARTIAL}]}
    )
