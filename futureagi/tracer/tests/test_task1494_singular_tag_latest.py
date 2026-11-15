"""Direct non-cost effective tags must not witness superseded span versions.

Unit cases exercise the real compiler/caller with an explicit empty overlay.
Native cases use a disposable task-owned CH25 database; no API qualification is
implied by either layer.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.services.clickhouse import graph_dispatch
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.v2 import effective_tags
from tracer.tests.tag_read_fixtures import empty_tag_assignments  # noqa: F401

PROJECT = "11111111-1111-1111-1111-111111111111"
OTHER_PROJECT = "22222222-2222-2222-2222-222222222222"
TRACE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
START = datetime(2026, 8, 8, 12, 10, tzinfo=UTC)
END = START + timedelta(minutes=30)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture(autouse=True)
def empty_curated_overlay(monkeypatch):
    def query(*args, **kwargs):
        return SimpleNamespace(data=[], complete=True, row_count=0, query_time_ms=0)

    monkeypatch.setattr(effective_tags, "graph_overlay_executor", lambda: query)


def leaf(column="tag", operation="contains", col_type="SYSTEM_METRIC"):
    return {
        "column_id": column,
        "filter_config": {
            "col_type": col_type,
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": "wanted",
        },
    }


def window(*items):
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START.isoformat(), END.isoformat()],
            },
        },
        *items,
    ]


def build(item, observe_type="trace", **kwargs):
    return TimeSeriesQueryBuilder(
        project_id=kwargs.pop("project_id", PROJECT),
        filters=window(item),
        observe_type=observe_type,
        exact_snapshot=True,
        resolve_span_versions=False,
        start_date=START,
        end_date=END,
        **kwargs,
    ).build()


@pytest.mark.parametrize("observe_type", ["trace", "span"])
@pytest.mark.parametrize("column", ["tag", "tags"])
@pytest.mark.parametrize(
    "operation", ["contains", "not_contains", "is_null", "is_not_null"]
)
def test_effective_tag_reads_physical_winner(observe_type, column, operation):
    sql, params = build(leaf(column, operation), observe_type)
    assert "AS graph_physical_versions" in sql
    assert sql.count("FROM spans") == 1
    assert " FINAL" not in sql
    assert " SAMPLE" not in sql
    assert params["graph_scalar_scan_start"].minute == 0
    assert params["graph_scalar_scan_end"].minute == 0
    assert params["project_id"] == PROJECT
    if observe_type == "trace":
        assert ("graph_root_order" in sql) == (column == "tags")


@pytest.mark.parametrize("column", ["tag", "tags", "unrelated"])
def test_raw_attribute_does_not_opt_into_effective_tag_latest_route(column):
    sql, _ = build(leaf(column, col_type="SPAN_ATTRIBUTE"))
    assert "AS graph_raw_spans" in sql
    assert "AS graph_physical_versions" not in sql
    assert "etag_graph" not in sql


@pytest.mark.parametrize("metric", ["traffic", "tokens", "latency", "cost"])
def test_direct_caller_keeps_bounded_status_and_query_budget(
    monkeypatch, settings, metric
):
    settings.DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER = ""
    settings.DASHBOARD_TRACE_REPLICA_SHARD_COUNT = 1
    calls = []

    def execute(sql, params, **kwargs):
        calls.append((sql, params, kwargs))
        return SimpleNamespace(
            data=[], columns=list(graph_dispatch._TRACE_ROLLUP_RESULT_COLUMNS)
        )

    response = graph_dispatch._fetch_direct_raw_system_metric_graph(
        analytics=SimpleNamespace(execute_ch_query=execute),
        project_id=PROJECT,
        filters=window(leaf()),
        interval="hour",
        metric_id=metric,
        observe_type="trace",
        timeout_ms=1000,
        seed=(None, 0),
    )
    assert len(calls) == 1
    sql, params, options = calls[0]
    assert "AS graph_physical_versions" in sql
    assert options["settings"] == graph_dispatch.GRAPH_READ_SETTINGS
    assert 0 < options["timeout_ms"] <= 1000
    assert params["project_id"] == PROJECT
    assert response["query_provenance"] == "bounded_candidates"
    assert response["query_exact"] is False


@pytest.fixture
def native_store(monkeypatch):
    # Uses the repository's explicit test-endpoint safety checks, never a default
    # application database. The caller must supply the task1494 endpoint.
    from conftest import _open_ch_test_native_client

    database = "test_task1494_singular_" + uuid4().hex
    client = _open_ch_test_native_client(owned_database=database)
    client.execute(f"CREATE DATABASE {database}")
    client.execute(f"USE {database}")
    try:
        assert client.execute("SELECT version()")[0][0].startswith("25.")
        client.execute("""CREATE TABLE spans (
            project_id UUID, observation_type LowCardinality(String),
            service_name LowCardinality(String), start_time DateTime64(6, 'UTC'),
            trace_id String, id String, parent_span_id Nullable(String),
            name String, status Nullable(String), latency_ms Int64 DEFAULT 1,
            cost Float64 DEFAULT 0, total_tokens Int64 DEFAULT 1,
            prompt_tokens Int64 DEFAULT 0, completion_tokens Int64 DEFAULT 0,
            project_version_id Nullable(UUID),
            attrs_string Map(String, String), attrs_number Map(String, Float64),
            attrs_bool Map(String, UInt8), attributes_extra String DEFAULT '{}',
            tags String DEFAULT '[]', is_deleted UInt8 DEFAULT 0, _version UInt64
        ) ENGINE=ReplacingMergeTree(_version, is_deleted)
          PARTITION BY toDate(start_time)
          ORDER BY (project_id, observation_type, service_name,
                    toStartOfHour(start_time), trace_id, id)""")
        client.execute("SYSTEM STOP MERGES spans")
        client.execute("""CREATE TABLE traces (
            project_id UUID, id UUID, tags String, _version UInt64, is_deleted UInt8
        ) ENGINE=ReplacingMergeTree(_version) ORDER BY (project_id, id)""")

        def insert(**changes):
            row = {
                "project_id": PROJECT,
                "trace_id": TRACE,
                "id": "root",
                "name": "root",
                "observation_type": "span",
                "service_name": "svc",
                "parent_span_id": "",
                "start_time": START + timedelta(minutes=5),
                "_version": 1,
                "tags": '["wanted"]',
                "status": "OK",
            }
            row.update(changes)
            client.execute(
                f"INSERT INTO spans ({', '.join(row)}) VALUES", [tuple(row.values())]
            )

        def execute(sql, params=None, **kwargs):
            rows, columns = client.execute(sql, params, with_column_types=True)
            return SimpleNamespace(data=rows, columns=[name for name, _ in columns])

        yield SimpleNamespace(insert=insert, execute=execute)
    finally:
        client.execute(f"DROP DATABASE IF EXISTS {database}")
        client.disconnect()


def native_traffic(store, item, observe_type="trace", project_id=PROJECT):
    sql, params = build(item, observe_type, project_id=project_id)
    result = store.execute(sql, params)
    index = result.columns.index("traffic_count")
    return sum(row[index] for row in result.data)


@pytest.mark.parametrize("observe_type", ["trace", "span"])
@pytest.mark.parametrize(
    "operation,expected",
    [("contains", 0), ("not_contains", 1), ("is_null", 1), ("is_not_null", 0)],
)
def test_native_superseded_sdk_tag(native_store, observe_type, operation, expected):
    native_store.insert()
    native_store.insert(_version=2, tags="[]")
    assert (
        native_traffic(native_store, leaf(operation=operation), observe_type)
        == expected
    )


@pytest.mark.parametrize("correction", ["tombstone", "outside_window"])
def test_native_replacement_cannot_witness_old_tag(native_store, correction):
    native_store.insert()
    changes = (
        {"is_deleted": 1}
        if correction == "tombstone"
        else {"start_time": END + timedelta(minutes=5)}
    )
    native_store.insert(_version=2, **changes)
    assert native_traffic(native_store, leaf()) == 0


def test_native_singular_child_and_plural_selected_root_are_distinct(native_store):
    native_store.insert(tags="[]")
    native_store.insert(id="child", parent_span_id="root")
    assert native_traffic(native_store, leaf("tag")) == 2
    assert native_traffic(native_store, leaf("tags")) == 0
    assert native_traffic(native_store, leaf("tag"), "span") == 1


def test_native_negative_tag_rejects_a_current_matching_sibling(native_store):
    native_store.insert(tags="[]")
    native_store.insert(id="child", parent_span_id="root", tags="[]")
    native_store.insert(id="child", parent_span_id="root", _version=2)
    assert native_traffic(native_store, leaf()) == 2
    assert native_traffic(native_store, leaf(operation="not_contains")) == 0
    assert native_traffic(native_store, leaf(operation="is_null")) == 0


def test_native_raw_tag_attribute_is_not_sdk_tag_membership(native_store):
    native_store.insert(tags="[]", attrs_string={"tag": "wanted"})
    assert native_traffic(native_store, leaf(col_type="SPAN_ATTRIBUTE")) == 1
    assert native_traffic(native_store, leaf()) == 0


def test_native_same_trace_identity_is_tenant_bound(native_store):
    native_store.insert()
    native_store.insert(_version=2, tags="[]")
    native_store.insert(project_id=OTHER_PROJECT)
    assert native_traffic(native_store, leaf(), project_id=PROJECT) == 0
    assert native_traffic(native_store, leaf(), project_id=OTHER_PROJECT) == 1


def test_native_actual_direct_non_cost_caller(native_store, settings):
    settings.DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER = ""
    settings.DASHBOARD_TRACE_REPLICA_SHARD_COUNT = 1
    native_store.insert()
    native_store.insert(_version=2, tags="[]")
    response = graph_dispatch._fetch_direct_raw_system_metric_graph(
        analytics=SimpleNamespace(execute_ch_query=native_store.execute),
        project_id=PROJECT,
        filters=window(leaf()),
        interval="hour",
        metric_id="traffic",
        observe_type="trace",
        timeout_ms=10000,
        seed=(None, 0),
    )
    assert sum(point["value"] or 0 for point in response["data"]) == 0
    assert response["query_exact"] is False
    assert response["query_provenance"] == "bounded_candidates"
