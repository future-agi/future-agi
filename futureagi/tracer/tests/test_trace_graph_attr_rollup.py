import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.serializers.filters import ObserveGraphDataResultSerializer
from tracer.services.clickhouse.graph_dispatch import (
    GRAPH_READ_SETTINGS,
    SEGMENTED_GRAPH_QUERY_TIMEOUT_MS,
    SEGMENTED_GRAPH_READ_SETTINGS,
    SYSTEM_GRAPH_READ_SETTINGS,
    SYSTEM_GRAPH_READ_TIMEOUT_MS,
    TRACE_GRAPH_CANDIDATE_LIMIT,
    _segmented_graph_windows,
    degraded_graph_response,
    fetch_system_metric_graph_ch,
    format_system_metric_graph,
)
from tracer.services.clickhouse.query_builders.time_series import (
    TimeSeriesQueryBuilder,
)

PROJECT_ID = "11111111-2222-4333-8444-555555555555"
COVERED_SINCE = datetime(2020, 1, 1, tzinfo=UTC)
_UNSET = object()


def _date_filter(start="2026-07-23T00:00:00Z", end="2026-07-30T00:00:00Z"):
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start, end],
        },
    }


def _attr_filter(
    key="final_status",
    *,
    op="in",
    value=_UNSET,
):
    if value is _UNSET:
        value = ["completed"]
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": op,
            "filter_value": value,
        },
    }


def _builder(filters, *, observe_type="trace", metric_id="latency", interval="day"):
    return TimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        interval=interval,
        observe_type=observe_type,
        metric_id=metric_id,
    )


@pytest.fixture(scope="module")
def latest_state_graph_ch():
    """Isolated loopback ReplacingMergeTree with unmerged row versions."""
    if os.environ.get("FUTUREAGI_TEST_ALLOW_LOCAL_CH_DDL") != "1":
        pytest.skip("local ClickHouse DDL test requires explicit opt-in")
    host = os.environ.get("CH25_HOST") or os.environ.get("CH_HOST") or "localhost"
    if host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("local ClickHouse latest-state test refuses a non-loopback host")

    clickhouse_driver = pytest.importorskip("clickhouse_driver")
    database = f"test_graph_latest_{uuid.uuid4().hex[:8]}"
    kwargs = {
        "host": host,
        "port": int(
            os.environ.get("CH25_TCP_PORT") or os.environ.get("CH_PORT") or 19002
        ),
        "user": os.environ.get("CH25_USER") or os.environ.get("CH_USER") or "default",
        "password": os.environ.get("CH_PASSWORD", ""),
    }
    admin = clickhouse_driver.Client(**kwargs)
    try:
        admin.execute("SELECT 1")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"local ClickHouse is unavailable: {exc!r}")
    admin.execute(f"CREATE DATABASE {database}")
    client = clickhouse_driver.Client(database=database, **kwargs)
    client.execute(
        """
        CREATE TABLE spans (
            project_id UUID,
            observation_type LowCardinality(String),
            service_name LowCardinality(String),
            start_time DateTime64(6, 'UTC'),
            trace_id String,
            id String,
            parent_span_id String DEFAULT '',
            latency_ms Int32 DEFAULT 0,
            total_tokens Int32 DEFAULT 0,
            cost Float64 DEFAULT 0,
            prompt_tokens Int32 DEFAULT 0,
            completion_tokens Int32 DEFAULT 0,
            status LowCardinality(String) DEFAULT '',
            attrs_string Map(String, String),
            is_deleted UInt8 DEFAULT 0,
            _version UInt64,
            INDEX idx_attrs_str_keys mapKeys(attrs_string)
                TYPE bloom_filter(0.01) GRANULARITY 1
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
        PARTITION BY toDate(start_time)
        ORDER BY (
            project_id, observation_type, service_name,
            toStartOfHour(start_time), trace_id, id
        )
        SETTINGS index_granularity = 1
        """
    )
    client.execute("SYSTEM STOP MERGES spans")
    columns = (
        "project_id",
        "observation_type",
        "service_name",
        "start_time",
        "trace_id",
        "id",
        "parent_span_id",
        "latency_ms",
        "status",
        "attrs_string",
        "is_deleted",
        "_version",
    )
    at = datetime(2026, 7, 24, 1)
    versions = [
        # Metric/status update: newest matching version must win.
        (
            "trace-a",
            "span-a",
            "",
            100,
            "OK",
            {"final_status": "Rechazado", "team": "Support"},
            0,
            1,
        ),
        (
            "trace-a",
            "span-a",
            "",
            300,
            "ERROR",
            {"final_status": "Rechazado", "team": "Support"},
            0,
            2,
        ),
        # Key clear: the old match must not be resurrected.
        (
            "trace-b",
            "span-b",
            "",
            100,
            "OK",
            {"final_status": "Rechazado", "team": "Support"},
            0,
            1,
        ),
        ("trace-b", "span-b", "", 999, "ERROR", {}, 0, 2),
        # Value changed away from the predicate.
        (
            "trace-c",
            "span-c",
            "",
            100,
            "OK",
            {"final_status": "Rechazado", "team": "Support"},
            0,
            1,
        ),
        (
            "trace-c",
            "span-c",
            "",
            999,
            "ERROR",
            {"final_status": "Aceptado", "team": "Internal"},
            0,
            2,
        ),
        # Newest tombstone must suppress its older matching row.
        (
            "trace-d",
            "span-d",
            "",
            500,
            "ERROR",
            {"final_status": "Rechazado", "team": "Support"},
            0,
            1,
        ),
        (
            "trace-d",
            "span-d",
            "",
            500,
            "ERROR",
            {"final_status": "Rechazado", "team": "Support"},
            1,
            2,
        ),
        # Value changed into the predicate.
        (
            "trace-e",
            "span-e",
            "",
            100,
            "OK",
            {"final_status": "Aceptado", "team": "Internal"},
            0,
            1,
        ),
        (
            "trace-e",
            "span-e",
            "",
            600,
            "OK",
            {"final_status": "Rechazado", "team": "Support"},
            0,
            2,
        ),
        # A child proves span mode is not silently forced to root semantics.
        (
            "trace-f",
            "span-f",
            "span-parent",
            900,
            "ERROR",
            {"final_status": "Rechazado", "team": "Support"},
            0,
            1,
        ),
        # An attribute found only on a child exercises trace candidate hydration.
        (
            "trace-g",
            "span-g-root",
            "",
            700,
            "OK",
            {"final_status": "Aceptado"},
            0,
            1,
        ),
        (
            "trace-g",
            "span-g-child",
            "span-g-root",
            50,
            "OK",
            {"team": "FieldOnly"},
            0,
            1,
        ),
    ]
    for (
        trace_id,
        span_id,
        parent_id,
        latency,
        status,
        attrs,
        deleted,
        version,
    ) in versions:
        client.execute(
            f"INSERT INTO spans ({', '.join(columns)}) VALUES",
            [
                (
                    PROJECT_ID,
                    "span",
                    "svc",
                    at,
                    trace_id,
                    span_id,
                    parent_id,
                    latency,
                    status,
                    attrs,
                    deleted,
                    version,
                )
            ],
        )
    try:
        yield client
    finally:
        try:
            client.execute("SYSTEM START MERGES spans")
        finally:
            client.disconnect()
            admin.execute(f"DROP DATABASE IF EXISTS {database}")
            admin.disconnect()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("observe_type", "expected_traffic", "expected_latency", "expected_error_rate"),
    [
        ("trace", 2, 450.0, 50.0),
        ("span", 3, 600.0, 200.0 / 3.0),
    ],
)
def test_real_clickhouse_latest_state_update_clear_nonmatch_and_tombstone_parity(
    latest_state_graph_ch,
    observe_type,
    expected_traffic,
    expected_latency,
    expected_error_rate,
):
    calls = []

    class Analytics:
        def execute_ch_query(self, query, params, timeout_ms, settings):
            calls.append((query, dict(params), timeout_ms, dict(settings)))
            rows, columns = latest_state_graph_ch.execute(
                query,
                params,
                with_column_types=True,
                settings=settings,
            )
            names = [column[0] for column in columns]
            return SimpleNamespace(
                data=[dict(zip(names, row, strict=False)) for row in rows],
                columns=names,
            )

    actual = {}
    for metric_id in ("latency", "traffic", "error_rate"):
        response = fetch_system_metric_graph_ch(
            analytics=Analytics(),
            project_id=PROJECT_ID,
            filters=[
                _date_filter(
                    "2026-07-24T00:00:00Z",
                    "2026-07-24T11:59:59Z",
                ),
                _attr_filter(value=["Rechazado"]),
            ],
            interval="day",
            metric_id=metric_id,
            observe_type=observe_type,
        )
        assert len(response["data"]) == 1
        actual[metric_id] = response["data"][0]["value"]
        assert response["data"][0]["primary_traffic"] == expected_traffic

    assert actual["latency"] == pytest.approx(expected_latency)
    assert actual["traffic"] == expected_traffic
    assert actual["error_rate"] == pytest.approx(expected_error_rate)
    assert len(calls) == 3
    assert all("FROM spans FINAL" in query for query, *_ in calls)
    assert all("is_deleted = 0" not in query for query, *_ in calls)
    assert all(settings["use_skip_indexes_if_final"] == 0 for *_, settings in calls)

    # Prove why the pairing above matters. Under FINAL, enabling a mutable Map
    # skip index prunes span-b's newest key-clear version and resurrects v1.
    probe = """
        SELECT id
        FROM spans FINAL
        PREWHERE project_id = %(project_id)s
          AND start_time >= %(start)s AND start_time < %(end)s
        WHERE mapContains(attrs_string, 'final_status')
          AND attrs_string['final_status'] = 'Rechazado'
    """
    probe_params = {
        "project_id": PROJECT_ID,
        "start": datetime(2026, 7, 24),
        "end": datetime(2026, 7, 25),
    }
    safe_ids = {
        row[0]
        for row in latest_state_graph_ch.execute(
            probe,
            probe_params,
            settings={"use_skip_indexes_if_final": 0},
        )
    }
    unsafe_ids = {
        row[0]
        for row in latest_state_graph_ch.execute(
            probe,
            probe_params,
            settings={"use_skip_indexes_if_final": 1},
        )
    }
    assert "span-b" not in safe_ids
    assert "span-b" in unsafe_ids


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "value", "trace_traffic", "span_traffic"),
    [
        ("equals", "Rechazado", 2, 3),
        ("not_equals", "Rechazado", 2, 2),
        ("in", ["Rechazado"], 2, 3),
        ("not_in", ["Rechazado"], 2, 2),
        ("contains", "CHAZ", 2, 3),
        ("not_contains", "CHAZ", 2, 2),
        ("starts_with", "rech", 2, 3),
        ("ends_with", "ADO", 4, 5),
        ("is_null", None, 1, 2),
        ("is_not_null", None, 4, 5),
    ],
)
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_real_clickhouse_final_status_operator_matrix_uses_latest_live_rows(
    latest_state_graph_ch,
    op,
    value,
    trace_traffic,
    span_traffic,
    observe_type,
):
    calls = []

    class Analytics:
        def execute_ch_query(self, query, params, timeout_ms, settings):
            calls.append((query, dict(params), timeout_ms, dict(settings)))
            rows, columns = latest_state_graph_ch.execute(
                query,
                params,
                with_column_types=True,
                settings=settings,
            )
            names = [column[0] for column in columns]
            return SimpleNamespace(
                data=[dict(zip(names, row, strict=False)) for row in rows],
                columns=names,
            )

    response = fetch_system_metric_graph_ch(
        analytics=Analytics(),
        project_id=PROJECT_ID,
        filters=[
            _date_filter("2026-07-24T00:00:00Z", "2026-07-24T11:59:59Z"),
            _attr_filter(op=op, value=value),
        ],
        interval="day",
        metric_id="traffic",
        observe_type=observe_type,
    )

    expected = trace_traffic if observe_type == "trace" else span_traffic
    assert response["data"][0]["value"] == expected
    assert response["data"][0]["primary_traffic"] == expected
    assert len(calls) == 1
    query, _, _, settings = calls[0]
    assert "FROM spans FINAL" in query
    assert "is_deleted = 0" not in query
    assert settings["use_skip_indexes_if_final"] == 0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("op", "value", "expected_traffic"),
    [
        ("equals", "support", 3),
        ("not_equals", "support", 2),
        ("in", ["support"], 3),
        ("not_in", ["support"], 2),
        ("contains", "PP", 3),
        ("not_contains", "PP", 2),
        ("starts_with", "SUP", 3),
        ("ends_with", "NAL", 1),
        ("is_null", None, 2),
        ("is_not_null", None, 5),
    ],
)
def test_real_clickhouse_arbitrary_span_attribute_operator_matrix(
    latest_state_graph_ch,
    op,
    value,
    expected_traffic,
):
    calls = []

    class Analytics:
        def execute_ch_query(self, query, params, timeout_ms, settings):
            calls.append((query, dict(params), timeout_ms, dict(settings)))
            rows, columns = latest_state_graph_ch.execute(
                query,
                params,
                with_column_types=True,
                settings=settings,
            )
            names = [column[0] for column in columns]
            return SimpleNamespace(
                data=[dict(zip(names, row, strict=False)) for row in rows],
                columns=names,
            )

    response = fetch_system_metric_graph_ch(
        analytics=Analytics(),
        project_id=PROJECT_ID,
        filters=[
            _date_filter("2026-07-24T00:00:00Z", "2026-07-24T11:59:59Z"),
            _attr_filter("team", op=op, value=value),
        ],
        interval="day",
        metric_id="traffic",
        observe_type="span",
    )

    assert response["data"][0]["value"] == expected_traffic
    assert response["data"][0]["primary_traffic"] == expected_traffic
    assert len(calls) == 1
    query, _, _, settings = calls[0]
    assert "FROM spans FINAL" in query
    assert "is_deleted = 0" not in query
    assert settings["use_skip_indexes_if_final"] == 0


@pytest.mark.unit
def test_real_clickhouse_selective_child_attribute_hydrates_canonical_root(
    latest_state_graph_ch,
):
    calls = []

    class Analytics:
        def execute_ch_query(self, query, params, timeout_ms, settings):
            calls.append((query, dict(params), timeout_ms, dict(settings)))
            rows, columns = latest_state_graph_ch.execute(
                query,
                params,
                with_column_types=True,
                settings=settings,
            )
            names = [column[0] for column in columns]
            return SimpleNamespace(
                data=[dict(zip(names, row, strict=False)) for row in rows],
                columns=names,
            )

    response = fetch_system_metric_graph_ch(
        analytics=Analytics(),
        project_id=PROJECT_ID,
        filters=[
            _date_filter("2026-07-24T00:00:00Z", "2026-07-24T11:59:59Z"),
            _attr_filter("team", op="equals", value="FieldOnly"),
        ],
        interval="day",
        metric_id="latency",
        observe_type="trace",
    )

    assert response["data"][0]["value"] == 700
    assert response["data"][0]["primary_traffic"] == 1
    assert len(calls) == 3
    assert all("FROM spans FINAL" in query for query, *_ in calls)
    assert all(
        settings["use_skip_indexes_if_final"] == 0 for _, _, _, settings in calls
    )


@pytest.mark.unit
class TestTraceGraphAttributeRollup:
    def _enable(self, settings):
        settings.TRACE_GRAPH_ATTR_ROLLUP_ENABLED = True
        settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = COVERED_SINCE

    @pytest.mark.parametrize("metric_id", ["latency", "traffic", "error_rate"])
    @pytest.mark.parametrize("observe_type", ["trace", "span"])
    def test_final_status_routes_to_exact_latest_state_raw_query(
        self, settings, observe_type, metric_id
    ):
        self._enable(settings)
        builder = _builder(
            [_date_filter(), _attr_filter(value=["completed", "failed"])],
            observe_type=observe_type,
            metric_id=metric_id,
        )

        query, params = builder.build()

        assert "FROM spans FINAL" in query
        assert "dashboard_attr_rollup" not in query
        assert "is_deleted = 0" not in query
        assert "mapContains(attrs_string, %(latest_attr_key)s)" in query
        assert "IN %(latest_attr_values)s" in query
        assert params["latest_attr_key"] == "final_status"
        assert params["latest_attr_values"] == ("completed", "failed")
        assert params["project_id"] == PROJECT_ID
        assert builder.query_source == "raw_latest_state"
        assert builder.attribute_filtered is True
        assert builder.raw_segmentation_safe is True
        root_predicate = "(parent_span_id IS NULL OR parent_span_id = '')"
        assert (root_predicate in query) is (observe_type == "trace")

    @pytest.mark.parametrize("observe_type", ["trace", "span"])
    @pytest.mark.parametrize(
        ("op", "value", "expected_sql", "expected_param"),
        [
            ("equals", "Rechazado", " = %(latest_attr_value)s", "rechazado"),
            ("not_equals", "Rechazado", " != %(latest_attr_value)s", "rechazado"),
            ("in", ["Rechazado"], " IN %(latest_attr_values)s", ("rechazado",)),
            (
                "not_in",
                ["Rechazado"],
                " NOT IN %(latest_attr_values)s",
                ("rechazado",),
            ),
            ("contains", "CHAZ", "positionUTF8(", "CHAZ"),
            ("not_contains", "CHAZ", ") = 0", "CHAZ"),
            ("starts_with", "rech", "startsWith(", "rech"),
            ("ends_with", "ADO", "endsWith(", "ADO"),
            ("is_null", None, "NOT mapContains(", None),
            ("is_not_null", None, "WHERE mapContains(", None),
        ],
    )
    def test_final_status_complete_text_operator_matrix(
        self,
        settings,
        observe_type,
        op,
        value,
        expected_sql,
        expected_param,
    ):
        self._enable(settings)

        builder = _builder(
            [_date_filter(), _attr_filter(op=op, value=value)],
            observe_type=observe_type,
        )
        query, params = builder.build()

        assert "FROM spans FINAL" in query
        assert expected_sql in query
        assert "is_deleted = 0" not in query
        assert params["latest_attr_key"] == "final_status"
        if op in {"in", "not_in"}:
            assert params["latest_attr_values"] == expected_param
        elif op not in {"is_null", "is_not_null"}:
            assert params["latest_attr_value"] == expected_param
        else:
            assert "latest_attr_value" not in params
            assert "latest_attr_values" not in params
        if op in {"not_equals", "not_in", "not_contains"}:
            assert "mapContains(attrs_string, %(latest_attr_key)s) AND" in query
        assert builder.query_source == "raw_latest_state"
        assert builder.raw_segmentation_safe is True

    def test_final_status_literal_text_does_not_treat_wildcards_as_patterns(
        self, settings
    ):
        self._enable(settings)

        query, params = _builder(
            [_date_filter(), _attr_filter(op="contains", value=r"%_\\")]
        ).build()

        assert "positionUTF8(" in query
        assert "LIKE" not in query.upper()
        assert params["latest_attr_value"] == r"%_\\"

    def test_rollup_flags_never_restore_append_only_attribute_success(self, settings):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = True
        settings.TRACE_GRAPH_ATTR_ROLLUP_ENABLED = True
        settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = COVERED_SINCE

        query, _ = _builder([_date_filter(), _attr_filter()]).build()

        assert "FROM spans FINAL" in query
        assert "dashboard_attr_rollup" not in query

    def test_trace_any_span_attribute_builds_capped_candidate_plan(self, settings):
        self._enable(settings)

        builder = _builder(
            [_date_filter(), _attr_filter("country", op="equals", value="US")]
        )
        query, params = builder.build()

        assert builder.query_source == "trace_candidate_plan"
        assert builder.attribute_filtered is True
        assert builder.raw_segmentation_safe is False
        assert "FROM spans FINAL" in query
        assert "GROUP BY trace_id" in query
        assert "mapContains(attrs_string, 'country')" in query
        assert params["graph_trace_candidate_limit"] == 1

    def test_incident_14d_fetch_executes_only_exact_adjacent_final_windows(
        self, settings
    ):
        self._enable(settings)
        calls = []
        columns = [
            "time_bucket",
            "avg_latency",
            "total_tokens",
            "avg_cost",
            "traffic_count",
            "prompt_tokens",
            "completion_tokens",
            "error_rate",
        ]

        class Result:
            def __init__(self, data):
                self.data = data
                self.columns = columns

        class Analytics:
            def execute_ch_query(self, query, params, timeout_ms, settings):
                calls.append((query, dict(params), timeout_ms, settings))
                if params["start_date"] == datetime(2026, 7, 17, 8, 30):
                    return Result(
                        [
                            {
                                "time_bucket": datetime(2026, 7, 24),
                                "avg_latency": 0,
                                "total_tokens": 0,
                                "avg_cost": 0,
                                "traffic_count": 3,
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "error_rate": 0,
                            }
                        ]
                    )
                return Result([])

        response = fetch_system_metric_graph_ch(
            analytics=Analytics(),
            project_id=PROJECT_ID,
            filters=[
                _date_filter(
                    "2026-07-17T08:30:00Z",
                    "2026-07-31T08:30:00Z",
                ),
                _attr_filter(value=["completed"]),
            ],
            interval="day",
            metric_id="traffic",
            observe_type="trace",
        )

        expected_windows = _segmented_graph_windows(
            datetime(2026, 7, 17, 8, 30),
            datetime(2026, 7, 31, 8, 30),
        )
        assert len(calls) == len(expected_windows)
        assert all("FROM spans FINAL" in query for query, *_ in calls)
        assert all("dashboard_attr_rollup" not in query for query, *_ in calls)
        assert all("is_deleted = 0" not in query for query, *_ in calls)
        assert all(settings["use_skip_indexes_if_final"] == 0 for *_, settings in calls)
        observed_windows = sorted(
            (params["start_date"], params["end_date"])
            for _query, params, _timeout, _settings in calls
        )
        assert observed_windows == expected_windows
        assert all(
            left[1] == right[0]
            for left, right in zip(observed_windows, observed_windows[1:], strict=False)
        )
        assert response["data"][0]["timestamp"] == "2026-07-17T00:00:00"
        assert response["data"][-1]["timestamp"] == "2026-07-31T00:00:00"
        assert sum(point["primary_traffic"] for point in response["data"]) == 3
        assert "query_status" not in response
        assert "query_window_adjusted" not in response

    @pytest.mark.parametrize(
        ("metric_id", "expected_value"),
        [
            ("latency", 200.0),
            ("traffic", 6.0),
            ("error_rate", 50.0),
        ],
    )
    def test_latest_state_segments_merge_numeric_aggregate_states_exactly(
        self, settings, metric_id, expected_value
    ):
        self._enable(settings)
        calls = []
        columns = [
            "time_bucket",
            "avg_latency",
            "total_tokens",
            "avg_cost",
            "traffic_count",
            "prompt_tokens",
            "completion_tokens",
            "error_rate",
        ]

        class Result:
            def __init__(self, row):
                self.data = [row]
                self.columns = columns

        def aggregate_row(*, latency, traffic, error_rate):
            return {
                "time_bucket": datetime(2026, 7, 24),
                "avg_latency": latency,
                "total_tokens": 0,
                "avg_cost": 0,
                "traffic_count": traffic,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "error_rate": error_rate,
            }

        class Analytics:
            def execute_ch_query(self, query, params, timeout_ms, settings):
                calls.append((query, dict(params), timeout_ms, settings))
                if params["start_date"] == datetime(2026, 7, 24, 11, 30):
                    return Result(aggregate_row(latency=100, traffic=2, error_rate=50))
                return Result(aggregate_row(latency=250, traffic=4, error_rate=50))

        response = fetch_system_metric_graph_ch(
            analytics=Analytics(),
            project_id=PROJECT_ID,
            filters=[
                _date_filter(
                    "2026-07-24T11:30:00Z",
                    "2026-07-24T13:30:00Z",
                ),
                _attr_filter(value=["completed"]),
            ],
            interval="day",
            metric_id=metric_id,
            observe_type="trace",
        )

        assert len(calls) == 2
        observed = sorted(
            (params["start_date"], params["end_date"])
            for _query, params, _timeout, _settings in calls
        )
        assert observed == [
            (datetime(2026, 7, 24, 11, 30), datetime(2026, 7, 24, 12)),
            (datetime(2026, 7, 24, 12), datetime(2026, 7, 24, 13, 30)),
        ]
        assert all("FROM spans FINAL" in query for query, *_ in calls)

        assert len(response["data"]) == 1
        assert response["data"][0]["timestamp"] == "2026-07-24T00:00:00"
        assert response["data"][0]["primary_traffic"] == 6
        assert response["data"][0]["value"] == pytest.approx(expected_value)
        assert "query_status" not in response
        assert "query_window_adjusted" not in response

    def test_latest_state_zero_fill_uses_original_unaligned_hour_range(self, settings):
        self._enable(settings)
        columns = [
            "time_bucket",
            "avg_latency",
            "total_tokens",
            "avg_cost",
            "traffic_count",
            "prompt_tokens",
            "completion_tokens",
            "error_rate",
        ]

        class Result:
            def __init__(self, data):
                self.data = data
                self.columns = columns

        class Analytics:
            def execute_ch_query(self, query, params, timeout_ms, settings):
                if params["start_date"] != datetime(2026, 7, 24, 12):
                    return Result([])
                return Result(
                    [
                        {
                            "time_bucket": datetime(2026, 7, 24, 12),
                            "avg_latency": 200,
                            "total_tokens": 0,
                            "avg_cost": 0,
                            "traffic_count": 3,
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "error_rate": 0,
                        }
                    ]
                )

        response = fetch_system_metric_graph_ch(
            analytics=Analytics(),
            project_id=PROJECT_ID,
            filters=[
                _date_filter(
                    "2026-07-24T11:30:00Z",
                    "2026-07-24T13:30:00Z",
                ),
                _attr_filter(value=["completed"]),
            ],
            interval="hour",
            metric_id="latency",
            observe_type="trace",
        )

        assert [point["timestamp"] for point in response["data"]] == [
            "2026-07-24T11:00:00",
            "2026-07-24T12:00:00",
            "2026-07-24T13:00:00",
        ]
        assert [point["primary_traffic"] for point in response["data"]] == [0, 3, 0]
        assert "query_status" not in response

    def test_latest_state_component_failure_never_returns_partial(self, settings):
        self._enable(settings)

        class Result:
            data = []
            columns = [
                "time_bucket",
                "avg_latency",
                "total_tokens",
                "avg_cost",
                "traffic_count",
                "prompt_tokens",
                "completion_tokens",
                "error_rate",
            ]

        class Analytics:
            def execute_ch_query(self, query, params, timeout_ms, settings):
                if params["start_date"] == datetime(2026, 7, 24, 11, 30):
                    raise TimeoutError("raw leading boundary exceeded its budget")
                return Result()

        with pytest.raises(TimeoutError):
            fetch_system_metric_graph_ch(
                analytics=Analytics(),
                project_id=PROJECT_ID,
                filters=[
                    _date_filter(
                        "2026-07-24T11:30:00Z",
                        "2026-07-24T13:30:00Z",
                    ),
                    _attr_filter(value=["completed"]),
                ],
                interval="hour",
                metric_id="latency",
                observe_type="trace",
            )

    def test_sub_hour_span_window_keeps_latest_span_row_semantics(self, settings):
        self._enable(settings)

        query, _ = _builder(
            [
                _date_filter(
                    "2026-07-30T12:15:00Z",
                    "2026-07-30T12:45:00Z",
                ),
                _attr_filter(),
            ],
            observe_type="span",
        ).build()

        assert "FROM spans FINAL" in query
        assert "dashboard_attr_rollup" not in query
        assert "attrs_string[%(latest_attr_key)s]" in query
        assert "trace_id IN (SELECT trace_id" not in query
        assert "(parent_span_id IS NULL OR parent_span_id = '')" not in query

    def test_offset_window_is_normalized_on_latest_state_path(self, settings):
        self._enable(settings)
        builder = _builder(
            [
                _date_filter(
                    "2026-07-23T12:34:56-07:00",
                    "2026-07-30T18:12:34-07:00",
                ),
                _attr_filter(),
            ]
        )

        query, params = builder.build()

        assert "FROM spans FINAL" in query
        assert params["start_date"] == datetime(2026, 7, 23, 19, 34, 56)
        assert params["end_date"] == datetime(2026, 7, 31, 1, 12, 34)

    def test_hour_zero_fill_covers_original_inclusive_bucket_boundaries(self, settings):
        self._enable(settings)
        builder = _builder(
            [
                _date_filter(
                    "2026-07-23T12:34:56Z",
                    "2026-07-23T15:12:34Z",
                ),
                _attr_filter(),
            ],
            interval="hour",
        )
        builder.build()

        formatted = builder.format_result([], [])
        timestamps = [point["timestamp"] for point in formatted["latency"]]

        assert timestamps == [
            "2026-07-23T12:00:00",
            "2026-07-23T13:00:00",
            "2026-07-23T14:00:00",
            "2026-07-23T15:00:00",
        ]

    def test_full_window_membership_filter_fails_explicitly(self, settings):
        self._enable(settings)
        status_filter = {
            "column_id": "status",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "OK",
            },
        }

        with pytest.raises(
            ValueError,
            match="unsupported full-window membership",
        ):
            _builder([_date_filter(), _attr_filter(), status_filter]).build()

    @pytest.mark.parametrize("observe_type", ["trace", "span"])
    def test_trace_and_span_final_status_preserve_their_entity_scope(
        self, settings, observe_type
    ):
        self._enable(settings)
        filters = [_date_filter(), _attr_filter(value=["completed", "failed"])]

        query, params = _builder(filters, observe_type=observe_type).build()
        assert "FROM spans FINAL" in query
        assert params["latest_attr_values"] == ("completed", "failed")
        root_predicate = "(parent_span_id IS NULL OR parent_span_id = '')"
        assert (root_predicate in query) is (observe_type == "trace")

    @pytest.mark.parametrize(
        ("observe_type", "metric_id"),
        [
            ("trace", "tokens"),
            ("span", "tokens"),
        ],
    )
    def test_other_metrics_reuse_exact_latest_state_path(
        self, settings, observe_type, metric_id
    ):
        self._enable(settings)

        query, _ = _builder(
            [_date_filter(), _attr_filter()],
            observe_type=observe_type,
            metric_id=metric_id,
        ).build()

        assert "dashboard_attr_rollup" not in query
        assert "FROM spans FINAL" in query

    def test_span_graph_attribute_filter_targets_each_span_row(self, settings):
        self._enable(settings)

        query, _ = _builder(
            [_date_filter(), _attr_filter("prompt_slug", op="equals", value="agent_2")],
            observe_type="span",
        ).build()

        assert "dashboard_attr_rollup" not in query
        assert "mapContains(attrs_string, 'prompt_slug')" in query
        assert "INNER JOIN (" not in query
        assert "trace_id IN (SELECT trace_id" not in query
        assert "(parent_span_id IS NULL OR parent_span_id = '')" not in query
        assert "PREWHERE project_id = %(project_id)s" in query

    @pytest.mark.parametrize(
        ("op", "value"),
        [
            ("equals", "support"),
            ("in", ["support", "success"]),
            ("not_equals", "internal"),
            ("not_in", ["internal", "sandbox"]),
            ("contains", "support"),
            ("not_contains", "sandbox"),
            ("starts_with", "supp"),
            ("ends_with", "port"),
            ("is_null", None),
            ("is_not_null", None),
        ],
    )
    def test_arbitrary_span_attribute_complete_text_operator_matrix(
        self,
        settings,
        op,
        value,
    ):
        self._enable(settings)

        builder = _builder(
            [_date_filter(), _attr_filter("team", op=op, value=value)],
            observe_type="span",
        )
        query, _ = builder.build()

        assert "FROM spans FINAL" in query
        assert "INNER JOIN (" not in query
        assert "trace_id IN (SELECT trace_id" not in query
        assert "is_deleted = 0" not in query
        assert builder.query_source == "raw_latest_state"
        assert builder.raw_segmentation_safe is True
        if op == "is_null":
            assert "NOT mapContains(attrs_string, 'team')" in query
        else:
            assert "mapContains(attrs_string, 'team')" in query
        if op in {"not_equals", "not_in", "not_contains"}:
            assert "mapContains(attrs_string, 'team') AND" in query

    @pytest.mark.parametrize(
        ("op", "value"),
        [
            ("equals", "support"),
            ("in", ["support", "success"]),
            ("not_equals", "internal"),
            ("not_in", ["internal", "sandbox"]),
            ("contains", "support"),
            ("not_contains", "sandbox"),
            ("starts_with", "supp"),
            ("ends_with", "port"),
            ("is_null", None),
            ("is_not_null", None),
        ],
    )
    def test_general_trace_text_operators_use_capped_candidate_plan(
        self,
        settings,
        op,
        value,
    ):
        self._enable(settings)

        builder = _builder([_date_filter(), _attr_filter("team", op=op, value=value)])
        query, _ = builder.build()

        assert builder.query_source == "trace_candidate_plan"
        assert "FROM spans FINAL" in query
        assert "GROUP BY trace_id" in query
        if op == "is_null":
            assert "NOT mapContains(attrs_string, 'team')" in query
        else:
            assert "mapContains(attrs_string, 'team')" in query

    def test_multiple_trace_any_span_attributes_fail_explicitly(self, settings):
        self._enable(settings)

        with pytest.raises(
            ValueError,
            match="unsupported full-window membership",
        ):
            _builder(
                [
                    _date_filter(),
                    _attr_filter("team", op="equals", value="support"),
                    _attr_filter("region", op="contains", value="latam"),
                ]
            ).build()

    def test_multiple_span_attributes_stay_on_the_same_span_row(self, settings):
        self._enable(settings)

        query, params = _builder(
            [
                _date_filter(),
                _attr_filter("team", op="not_equals", value="internal"),
                _attr_filter("region", op="is_not_null", value=None),
            ],
            observe_type="span",
        ).build()

        assert "AS graph_attr_candidates USING (trace_id)" not in query
        assert "HAVING countIf" not in query
        assert "lower(attrs_string['team']) !=" in query
        assert "mapContains(attrs_string, 'region')" in query
        assert params["graph_span_attr_attr_1"] == "internal"

    def test_root_plus_any_span_attribute_fails_explicitly(self, settings):
        self._enable(settings)

        with pytest.raises(
            ValueError,
            match="unsupported full-window membership",
        ):
            _builder(
                [
                    _date_filter(),
                    _attr_filter("final_status", op="contains", value="reject"),
                    _attr_filter("region", op="equals", value="latam"),
                ]
            ).build()

    def test_rollup_flag_and_coverage_are_ignored_for_latest_state(self, settings):
        settings.DASHBOARD_ATTR_ROLLUP_ENABLED = True
        settings.TRACE_GRAPH_ATTR_ROLLUP_ENABLED = False
        settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = COVERED_SINCE
        query, _ = _builder([_date_filter(), _attr_filter()]).build()
        assert "dashboard_attr_rollup" not in query
        assert "FROM spans FINAL" in query

    def test_builder_exposes_the_selected_query_source(self, settings):
        self._enable(settings)
        latest = _builder([_date_filter(), _attr_filter()])
        latest.build()
        assert latest.query_source == "raw_latest_state"
        assert latest.attribute_filtered is True
        assert latest.raw_segmentation_safe is True

        trace_candidates = _builder(
            [_date_filter(), _attr_filter("country", value=["CO"])],
        )
        trace_candidates.build()
        assert trace_candidates.query_source == "trace_candidate_plan"
        assert trace_candidates.attribute_filtered is True
        assert trace_candidates.raw_segmentation_safe is False

        span_raw = _builder(
            [_date_filter(), _attr_filter("country", value=["CO"])],
            observe_type="span",
        )
        span_raw.build()
        assert span_raw.query_source == "raw_latest_state"
        assert span_raw.attribute_filtered is True
        assert span_raw.raw_segmentation_safe is True

        forced_raw = TimeSeriesQueryBuilder(
            project_id=PROJECT_ID,
            filters=[_date_filter(), _attr_filter()],
            interval="day",
            metric_id="latency",
            allow_attr_rollup=False,
        )
        query, _ = forced_raw.build()
        assert forced_raw.query_source == "raw_latest_state"
        assert forced_raw.attribute_filtered is True
        assert forced_raw.raw_segmentation_safe is True
        assert "dashboard_attr_rollup" not in query

    def test_sub_hour_final_status_is_labeled_latest_state(self, settings):
        self._enable(settings)
        builder = _builder(
            [
                _date_filter(
                    "2026-07-30T12:15:00Z",
                    "2026-07-30T12:45:00Z",
                ),
                _attr_filter(),
            ],
            interval="hour",
        )

        query, _ = builder.build()

        assert "FROM spans FINAL" in query
        assert builder.query_source == "raw_latest_state"
        assert builder.attribute_filtered is True

        settings.TRACE_GRAPH_ATTR_ROLLUP_ENABLED = True
        settings.DASHBOARD_ATTR_ROLLUP_COVERED_SINCE = datetime(2026, 7, 24, tzinfo=UTC)
        builder = _builder([_date_filter(), _attr_filter()])
        query, params = builder.build()
        assert "dashboard_attr_rollup" not in query
        assert "FROM spans FINAL" in query
        assert params["start_date"] == datetime(2026, 7, 23)


@pytest.mark.unit
class TestGraphReadFailureContract:
    @pytest.mark.parametrize(
        ("metric_id", "series_key", "point_field", "expected"),
        [
            ("latency", "latency", "latency", 42.5),
            ("tokens", "tokens", "tokens", 12),
            ("total_tokens", "total_tokens", "tokens", 13),
            ("cost", "cost", "cost", 0.125),
            ("traffic", "traffic", "traffic", 9),
            ("prompt_tokens", "prompt_tokens", "prompt_tokens", 7),
            ("input_tokens", "input_tokens", "prompt_tokens", 8),
            (
                "completion_tokens",
                "completion_tokens",
                "completion_tokens",
                5,
            ),
            ("output_tokens", "output_tokens", "completion_tokens", 6),
            ("error_rate", "error_rate", "error_rate", 2.5),
        ],
    )
    def test_system_graph_uses_the_supported_metric_value_alias(
        self, metric_id, series_key, point_field, expected
    ):
        timestamp = "2026-07-30T00:00:00"
        result = format_system_metric_graph(
            {
                series_key: [
                    {
                        "timestamp": timestamp,
                        point_field: expected,
                    }
                ],
                "traffic": [{"timestamp": timestamp, "traffic": 9}],
            },
            metric_id,
        )

        assert result["data"] == [
            {
                "timestamp": timestamp,
                "value": expected,
                "primary_traffic": 9,
            }
        ]

    def test_graph_limits_throw_instead_of_returning_partial_results(self):
        assert GRAPH_READ_SETTINGS["read_overflow_mode"] == "throw"
        assert GRAPH_READ_SETTINGS["result_overflow_mode"] == "throw"
        assert GRAPH_READ_SETTINGS["timeout_overflow_mode"] == "throw"

    def test_system_graph_reads_only_requested_raw_metric_with_bounded_headroom(self):
        calls = []

        class Result:
            data = []
            columns = []

        class Analytics:
            def execute_ch_query(self, query, params, timeout_ms, settings):
                calls.append((query, params, timeout_ms, settings))
                return Result()

        fetch_system_metric_graph_ch(
            analytics=Analytics(),
            project_id=PROJECT_ID,
            filters=[
                _date_filter(),
                _attr_filter("prompt_slug", op="equals", value="agent_2"),
            ],
            interval="day",
            metric_id="latency",
            observe_type="span",
        )

        assert len(calls) == 14
        assert sorted(
            (params["start_date"], params["end_date"]) for _, params, _, _ in calls
        ) == [
            (
                datetime(2026, 7, day, hour, tzinfo=UTC).replace(tzinfo=None),
                (
                    datetime(2026, 7, day, hour, tzinfo=UTC) + timedelta(hours=12)
                ).replace(tzinfo=None),
            )
            for day in range(23, 30)
            for hour in (0, 12)
        ]
        for query, _, timeout_ms, settings in calls:
            assert "FROM spans" in query
            assert "avg(latency_ms) AS avg_latency" in query
            assert "count() AS traffic_count" in query
            assert "0 AS total_tokens" in query
            assert "0 AS avg_cost" in query
            assert "0 AS prompt_tokens" in query
            assert "0 AS completion_tokens" in query
            assert "0 AS error_rate" in query
            assert "sum(total_tokens)" not in query
            assert "avg(cost)" not in query
            assert "sum(prompt_tokens)" not in query
            assert "sum(completion_tokens)" not in query
            assert "countIf(status = 'ERROR')" not in query
            assert 0 < timeout_ms <= SEGMENTED_GRAPH_QUERY_TIMEOUT_MS
            assert settings == SEGMENTED_GRAPH_READ_SETTINGS
            assert settings["max_memory_usage"] == 512 * 1024 * 1024
            assert settings["max_bytes_to_read"] == 1536 * 1024 * 1024
            assert settings["max_execution_time"] == 1.5
            assert settings["use_skip_indexes_if_final"] == 0
        assert SYSTEM_GRAPH_READ_TIMEOUT_MS == 1750
        assert SYSTEM_GRAPH_READ_SETTINGS["max_memory_usage"] == 512 * 1024 * 1024
        assert SYSTEM_GRAPH_READ_SETTINGS["max_bytes_to_read"] == 2 * 1024 * 1024 * 1024

    def test_selective_trace_any_span_graph_discovers_verifies_and_hydrates(self):
        calls = []

        class Result:
            def __init__(self, data, columns):
                self.data = data
                self.columns = columns

        class Analytics:
            def execute_ch_query(self, query, params, timeout_ms, settings):
                calls.append((query, dict(params), timeout_ms, settings))
                if "time_bucket" in query:
                    return Result(
                        [
                            {
                                "time_bucket": datetime(2026, 7, 24),
                                "avg_latency": 12.0,
                                "total_tokens": 0,
                                "avg_cost": 0,
                                "traffic_count": 1,
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "error_rate": 0,
                            }
                        ],
                        [
                            "time_bucket",
                            "avg_latency",
                            "total_tokens",
                            "avg_cost",
                            "traffic_count",
                            "prompt_tokens",
                            "completion_tokens",
                            "error_rate",
                        ],
                    )
                return Result([{"trace_id": "trace-one"}], ["trace_id"])

        result = fetch_system_metric_graph_ch(
            analytics=Analytics(),
            project_id=PROJECT_ID,
            filters=[
                _date_filter(),
                _attr_filter("country", op="equals", value="CO"),
            ],
            interval="day",
            metric_id="latency",
            observe_type="trace",
        )

        assert len(calls) == 16
        discovery = [call for call in calls if "graph_trace_candidate_limit" in call[1]]
        verification = [
            call
            for call in calls
            if "graph_trace_candidate_ids" in call[1] and "time_bucket" not in call[0]
        ]
        hydration = [call for call in calls if "time_bucket" in call[0]]
        assert len(discovery) == 14
        assert len(verification) == 1
        assert len(hydration) == 1
        assert all(
            call[1]["graph_trace_candidate_limit"] == TRACE_GRAPH_CANDIDATE_LIMIT + 1
            for call in discovery
        )
        assert all("FROM spans FINAL" in query for query, *_ in calls)
        assert all(
            settings["use_skip_indexes_if_final"] == 0 for _, _, _, settings in calls
        )
        assert any(point["value"] == 12.0 for point in result["data"])

    def test_broad_trace_any_span_graph_fails_closed_at_candidate_cap(self):
        calls = []

        class Result:
            columns = ["trace_id"]

            def __init__(self):
                self.data = [
                    {"trace_id": f"trace-{index}"}
                    for index in range(TRACE_GRAPH_CANDIDATE_LIMIT + 1)
                ]

        class Analytics:
            def execute_ch_query(self, query, params, timeout_ms, settings):
                calls.append((query, dict(params), timeout_ms, settings))
                return Result()

        with pytest.raises(TimeoutError, match="narrower range"):
            fetch_system_metric_graph_ch(
                analytics=Analytics(),
                project_id=PROJECT_ID,
                filters=[
                    _date_filter(),
                    _attr_filter("country", op="is_not_null", value=None),
                ],
                interval="day",
                metric_id="latency",
                observe_type="trace",
            )

        assert len(calls) == 14
        assert all("time_bucket" not in query for query, *_ in calls)

    def test_concurrent_auto_refresh_calls_share_four_read_slots(self):
        lock = threading.Lock()
        active = 0
        peak = 0

        class Result:
            data = []
            columns = ["trace_id"]

        class Analytics:
            def execute_ch_query(self, query, params, timeout_ms, settings):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                try:
                    time.sleep(0.01)
                    return Result()
                finally:
                    with lock:
                        active -= 1

        def fetch():
            return fetch_system_metric_graph_ch(
                analytics=Analytics(),
                project_id=PROJECT_ID,
                filters=[
                    _date_filter(),
                    _attr_filter("country", op="equals", value="CO"),
                ],
                interval="day",
                metric_id="latency",
                observe_type="trace",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _index: fetch(), range(2)))

        assert peak == 4
        assert active == 0
        assert all(
            response["metric_name"] == "latency" and response["data"]
            for response in responses
        )

    def test_root_attr_plus_membership_filter_fails_before_clickhouse(self):
        filters = [
            _date_filter(),
            _attr_filter("final_status", op="equals", value="completed"),
            {
                "column_id": "model",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "gpt-4o",
                },
            },
        ]
        builder = _builder(filters)

        with pytest.raises(ValueError, match="unsupported full-window membership"):
            builder.build()

        assert builder.query_source == "raw"
        assert builder.attribute_filtered is True
        assert builder.raw_segmentation_safe is False

    def test_segmented_graph_windows_are_exact_half_open_12_hour_windows(self):
        start = datetime(2026, 7, 23, 12, 34, 56)
        end = datetime(2026, 7, 25, 3, 4, 5)

        assert _segmented_graph_windows(start, end) == [
            (start, datetime(2026, 7, 24)),
            (datetime(2026, 7, 24), datetime(2026, 7, 24, 12)),
            (datetime(2026, 7, 24, 12), datetime(2026, 7, 25)),
            (datetime(2026, 7, 25), end),
        ]

    def test_segmented_week_graph_merges_daily_aggregate_states_exactly(self):
        calls = []
        columns = [
            "time_bucket",
            "avg_latency",
            "total_tokens",
            "avg_cost",
            "traffic_count",
            "prompt_tokens",
            "completion_tokens",
            "error_rate",
        ]

        class Result:
            def __init__(self, day):
                self.data = [
                    {
                        "time_bucket": datetime(2026, 7, 20),
                        "avg_latency": float(day),
                        "total_tokens": 0,
                        "avg_cost": 0,
                        "traffic_count": 2,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "error_rate": 0,
                    }
                ]
                self.columns = columns

        class Analytics:
            def execute_ch_query(self, query, params, timeout_ms, settings):
                calls.append((query, dict(params), timeout_ms, settings))
                return Result(params["start_date"].day - 19)

        result = fetch_system_metric_graph_ch(
            analytics=Analytics(),
            project_id=PROJECT_ID,
            filters=[
                _date_filter("2026-07-20T00:00:00Z", "2026-07-27T00:00:00Z"),
                _attr_filter("country", value=["CO"]),
            ],
            interval="week",
            metric_id="latency",
            observe_type="span",
        )

        assert len(calls) == 14
        assert result["data"][0] == {
            "timestamp": "2026-07-20T00:00:00",
            "value": 4.0,
            "primary_traffic": 28,
        }

    def test_segment_failure_waits_for_running_queries_before_returning(self):
        running_started = threading.Event()
        running_finished = threading.Event()

        class Result:
            data = []
            columns = []

        class Analytics:
            def execute_ch_query(self, query, params, timeout_ms, settings):
                if params["start_date"] == datetime(2026, 7, 23):
                    running_started.wait(timeout=1)
                    raise TimeoutError("first window failed")
                running_started.set()
                time.sleep(0.05)
                running_finished.set()
                return Result()

        with pytest.raises(TimeoutError):
            fetch_system_metric_graph_ch(
                analytics=Analytics(),
                project_id=PROJECT_ID,
                filters=[
                    _date_filter(
                        "2026-07-23T00:00:00Z",
                        "2026-07-24T00:00:00Z",
                    ),
                    _attr_filter("prompt_slug", value=["agent_2"]),
                ],
                interval="day",
                metric_id="latency",
                observe_type="span",
            )

        assert running_started.is_set()
        assert running_finished.is_set()

    @pytest.mark.parametrize(
        ("exc", "expected_code"),
        [
            (TimeoutError("private timeout detail"), "read_budget_exceeded"),
            (RuntimeError("private ClickHouse stack"), "query_failed"),
        ],
    )
    def test_degraded_response_is_explicit_and_does_not_leak_error(
        self, exc, expected_code
    ):
        result = degraded_graph_response("latency", exc)

        assert result == {
            "metric_name": "latency",
            "data": [],
            "query_complete": False,
            "query_status": "degraded",
            "query_error_code": expected_code,
        }
        assert "private" not in str(result)
        ObserveGraphDataResultSerializer(data=result).is_valid(raise_exception=True)
