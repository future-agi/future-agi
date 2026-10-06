"""Real-ClickHouse parity for the unfiltered aggregate-state route's exactness.

The unfiltered Observe system-metric graph and the unfiltered dashboard span
widgets read ``spans``'s hourly aggregate projections. Those projections are
built per physical part: they carry no latest-version reduction and no
``is_deleted`` predicate. On ``ReplacingMergeTree(_version, is_deleted)`` that
means

* before a merge, every version of a row is counted;
* after ``OPTIMIZE ... FINAL``, the surviving version of a deleted row is the
  tombstone itself, and ordinary merges keep it, so it is still counted.

The latest-live answer (``FINAL`` with ``is_deleted = 0``) differs from the
route's numbers in both cases. The route may publish numbers that differ from
latest-live, but only if it does not also say ``query_exact``. That
implication is what this module checks, on the statements the product entry
points actually emit, with the aggregate projection forced so a silent base
scan cannot stand in for it.

The concrete numbers are pinned too, so the documented approximation is a
measured fact rather than a comment.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from clickhouse_driver import Client

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services.clickhouse import graph_dispatch
from tracer.views import dashboard as dashboard_view
from tracer.views.dashboard import _read_dashboard_rollup_fast_path

pytestmark = pytest.mark.integration

PROJECT_ID = "00000000-0000-4000-8000-000000000001"

# The production replacement key and engine, and the production aggregate
# projection over (project_id, hour, observation_type, status, model,
# provider), including its doubled -State.
_SPANS_DDL = """
CREATE TABLE spans (
    project_id UUID,
    observation_type String DEFAULT 'span',
    service_name String DEFAULT '',
    start_time DateTime64(6, 'UTC'),
    trace_id String,
    id String,
    status String DEFAULT 'OK',
    model String DEFAULT '',
    provider String DEFAULT '',
    cost Float64,
    total_tokens Int32 DEFAULT 0,
    prompt_tokens Int32 DEFAULT 0,
    completion_tokens Int32 DEFAULT 0,
    latency_ms Int32 DEFAULT 0,
    is_deleted UInt8 DEFAULT 0,
    _version UInt64,
    PROJECTION proj_metrics_hourly (
        SELECT
            project_id,
            toStartOfHour(start_time) AS hour,
            observation_type,
            status,
            model,
            provider,
            countState(),
            sumState(cost),
            sumState(total_tokens),
            sumState(prompt_tokens),
            sumState(completion_tokens),
            quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)
        GROUP BY project_id, hour, observation_type, status, model, provider
    )
) ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toDate(start_time)
ORDER BY (project_id, observation_type, service_name,
          toStartOfHour(start_time), trace_id, id)
SETTINGS deduplicate_merge_projection_mode = 'rebuild'
"""

# One insert per tuple list, so each lands in its own part. Row shape:
# (trace/span id, cost, is_deleted, _version).
_SCENARIOS = {
    # A is corrected from 10 to 20 and never merged.
    "versions_before_merge": {
        "inserts": [[("a", 10.0, 0, 1)], [("a", 20.0, 0, 2)]],
        "optimize_final": False,
        "product": (2, 30.0),
        "latest_live": (1, 20.0),
    },
    # Control: once merged, the superseded version is gone and the route
    # agrees with latest-live.
    "versions_after_optimize_final": {
        "inserts": [[("a", 10.0, 0, 1)], [("a", 20.0, 0, 2)]],
        "optimize_final": True,
        "product": (1, 20.0),
        "latest_live": (1, 20.0),
    },
    # B is inserted live at 99 and then deleted. Merging keeps the tombstone.
    "retained_delete_after_optimize_final": {
        "inserts": [
            [("a", 20.0, 0, 1), ("b", 99.0, 0, 1)],
            [("b", 99.0, 1, 2)],
        ],
        "optimize_final": True,
        "product": (2, 119.0),
        "latest_live": (1, 20.0),
    },
    # Both at once, unmerged: the review's counterexample.
    "versions_and_delete_before_merge": {
        "inserts": [
            [("a", 10.0, 0, 1), ("b", 99.0, 0, 1)],
            [("a", 20.0, 0, 2), ("b", 99.0, 1, 2)],
        ],
        "optimize_final": False,
        "product": (4, 228.0),
        "latest_live": (1, 20.0),
    },
}


# conftest resolves the live target and, on an opted-in port, proves it is the
# test sidecar before any DDL.
@pytest.fixture(scope="module")
def ch_database():
    with _ch_test_owned_database("test_agg_exact_") as database:
        yield database


@pytest.fixture()
def ch_client(ch_database):
    """A client on the test database with a fresh ``spans``, dropped on exit.

    The product statements name ``spans`` unqualified, so the table has to be
    called exactly that inside the test-owned database.
    """

    with _ch_test_native_client(database=ch_database) as client:
        client.execute(_SPANS_DDL)
        try:
            yield client
        finally:
            client.execute("DROP TABLE IF EXISTS spans SYNC")


class _LiveAnalytics:
    """Run the product's statement on the test ClickHouse, projection forced.

    ``force_optimize_projection`` makes a plan that stopped using a projection
    raise instead of quietly returning the base table's numbers.
    """

    supports_per_query_read_settings = True

    def __init__(self, client: Client):
        self._client = client
        self.queries: list[str] = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        del timeout_ms
        self.queries.append(query)
        bound = {
            key: tuple(value) if isinstance(value, list) else value
            for key, value in (params or {}).items()
        }
        rows, columns = self._client.execute(
            query,
            bound,
            with_column_types=True,
            settings={
                **(settings or {}),
                "optimize_use_projections": 1,
                "force_optimize_projection": 1,
            },
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows],
            columns=names,
        )


def _load(client: Client, scenario: dict, hour: datetime) -> None:
    client.execute("SYSTEM STOP MERGES spans")
    for batch in scenario["inserts"]:
        client.execute(
            "INSERT INTO spans"
            " (project_id, start_time, trace_id, id, cost, is_deleted, _version)"
            " VALUES",
            [
                (uuid.UUID(PROJECT_ID), hour, row_id, row_id, cost, deleted, version)
                for row_id, cost, deleted, version in batch
            ],
        )
    if scenario["optimize_final"]:
        client.execute("SYSTEM START MERGES spans")
        client.execute("OPTIMIZE TABLE spans FINAL")


def _latest_live(client: Client) -> tuple[int, float]:
    ((count, cost),) = client.execute(
        "SELECT count(), sum(cost) FROM spans FINAL WHERE is_deleted = 0"
    )
    return int(count), float(cost)


def _fixture_hour() -> datetime:
    # Inside the dashboard's relative 30D preset and away from its edges.
    return (datetime.now(UTC) - timedelta(days=1)).replace(
        minute=0, second=0, microsecond=0
    )


def _date_filter(hour: datetime) -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [
                (hour - timedelta(days=1)).isoformat(),
                (hour + timedelta(days=1)).isoformat(),
            ],
        },
    }


def _graph_total(response: dict) -> float:
    return sum(float(point["value"] or 0) for point in response["data"])


@pytest.mark.parametrize("scenario_name", sorted(_SCENARIOS))
def test_unfiltered_graph_claims_exact_only_on_latest_live_numbers(
    ch_client, scenario_name
):
    scenario = _SCENARIOS[scenario_name]
    hour = _fixture_hour()
    _load(ch_client, scenario, hour)
    oracle = _latest_live(ch_client)
    assert oracle == scenario["latest_live"]

    analytics = _LiveAnalytics(ch_client)
    responses = {
        metric_id: graph_dispatch.fetch_system_metric_graph_ch(
            analytics=analytics,
            project_id=PROJECT_ID,
            filters=[_date_filter(hour)],
            interval="day",
            metric_id=metric_id,
            observe_type="span",
        )
        for metric_id in ("traffic", "cost")
    }
    for response in responses.values():
        assert response["query_status"] == "complete", response
        assert "FINAL" not in analytics.queries[-1].upper()

    # The route's ``cost`` series is the per-span average.
    count = int(_graph_total(responses["traffic"]))
    average_cost = _graph_total(responses["cost"])
    expected_count, expected_cost = scenario["product"]
    assert count == expected_count
    assert average_cost == pytest.approx(expected_cost / expected_count)

    oracle_count, oracle_cost = oracle
    for metric_id, (product_value, oracle_value) in {
        "traffic": (count, oracle_count),
        "cost": (average_cost, oracle_cost / oracle_count),
    }.items():
        if responses[metric_id]["query_exact"]:
            assert product_value == pytest.approx(oracle_value), (
                f"{metric_id} published query_exact=true for {product_value},"
                f" but the latest-live answer is {oracle_value}"
            )


def _widget_config() -> dict:
    return {
        "project_ids": [PROJECT_ID],
        "time_range": {"preset": "30D"},
        "granularity": "day",
        "metrics": [
            {
                "id": metric_id,
                "name": metric_id,
                "type": "system_metric",
                "source": "traces",
                "aggregation": aggregation,
                "filters": [],
            }
            for metric_id, aggregation in (("span_count", "count"), ("cost", "sum"))
        ],
        "filters": [],
        "breakdowns": [],
    }


def _widget_total(metric: dict) -> float:
    total = 0.0
    for series in metric.get("series", []):
        for point in series.get("data", []):
            total += float(point.get("value") or 0)
    return total


@pytest.mark.parametrize("scenario_name", sorted(_SCENARIOS))
def test_unfiltered_dashboard_claims_exact_only_on_latest_live_numbers(
    ch_client, monkeypatch, scenario_name
):
    scenario = _SCENARIOS[scenario_name]
    hour = _fixture_hour()
    _load(ch_client, scenario, hour)
    oracle = _latest_live(ch_client)
    assert oracle == scenario["latest_live"]

    analytics = _LiveAnalytics(ch_client)
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: analytics)
    result = _read_dashboard_rollup_fast_path(_widget_config())

    assert result["query_status"] == "complete", result
    assert len(analytics.queries) == 1
    span_count, cost = result["metrics"]
    product = (int(_widget_total(span_count)), _widget_total(cost))
    expected_count, expected_cost = scenario["product"]
    assert product[0] == expected_count
    assert product[1] == pytest.approx(expected_cost)

    for metric, product_value, oracle_value in (
        (span_count, product[0], oracle[0]),
        (cost, product[1], oracle[1]),
    ):
        if metric["query_exact"]:
            assert product_value == pytest.approx(oracle_value), (
                f"{metric['name']} published query_exact=true for"
                f" {product_value}, but the latest-live answer is {oracle_value}"
            )
    if result["query_exact"]:
        assert product == pytest.approx(oracle), (
            f"the widget payload published query_exact=true for {product},"
            f" but the latest-live answer is {oracle}"
        )
