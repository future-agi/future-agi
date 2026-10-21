"""A ClickHouse output bucket must land in the zero-filled point it names.

Over the CH25 ``DateTime64(6, 'UTC')`` / ``DateTime64(3, 'UTC')`` columns,
``toStartOfMinute`` / ``toStartOfHour`` / ``toStartOfDay`` return
``DateTime('UTC')``, which the native driver decodes as a tz-aware UTC
``datetime``; ``toMonday`` / ``toStartOfMonth`` / ``toStartOfYear`` return
``Date``, decoded as a ``date``. ``BaseQueryBuilder._generate_timestamp_range``
yields naive UTC ``datetime`` values. A formatter that keys result rows by one
representation and looks them up by the other publishes zero for a populated
bucket while the read still reports complete.

Every bucket below is decoded through the driver's own column factory, so the
formatters see exactly what a native read hands them.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from clickhouse_driver.columns.service import get_column_by_spec
from clickhouse_driver.context import Context

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
EVAL_CONFIG_ID = "22222222-2222-4222-8222-222222222222"


def _driver_value(spec: str, value: datetime | date) -> datetime | date:
    """Decode one naive-UTC value as clickhouse_driver decodes column *spec*."""

    context = Context()
    context.settings = {}
    context.client_settings = {
        "input_format_null_as_default": False,
        "use_numpy": False,
    }
    context.server_info = SimpleNamespace(get_timezone=lambda: "UTC")
    column = get_column_by_spec(spec, {"context": context})
    if spec == "Date":
        raw = (value - date(1970, 1, 1)).days
    else:
        seconds = (
            value.replace(tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)
        ).total_seconds()
        raw = round(seconds * 10 ** getattr(column, "scale", 0))
    (decoded,) = column.after_read_items((raw,))
    return decoded


def _window_filters(start: datetime, end: datetime) -> list[dict]:
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
                "col_type": "SYSTEM_METRIC",
            },
        }
    ]


def _populated(points: list[dict]) -> list[dict]:
    return [point for point in points if point["value"] or point["primary_traffic"]]


def test_the_driver_decodes_buckets_in_three_representations():
    aware = _driver_value("DateTime('UTC')", datetime(2026, 9, 24))
    naive = _driver_value("DateTime", datetime(2026, 9, 24))
    calendar = _driver_value("Date", date(2026, 9, 21))

    assert aware.tzinfo is not None and aware.utcoffset() == timedelta(0)
    assert aware.isoformat() == "2026-09-24T00:00:00+00:00"
    assert naive.tzinfo is None and naive.isoformat() == "2026-09-24T00:00:00"
    assert type(calendar) is date and calendar.isoformat() == "2026-09-21"


_SESSION_CASES = [
    pytest.param(
        "DateTime",
        datetime(2026, 9, 24),
        "day",
        timedelta(days=2),
        "2026-09-24T00:00:00",
        id="naive-day-control",
    ),
    pytest.param(
        "DateTime('UTC')",
        datetime(2026, 9, 24),
        "day",
        timedelta(days=2),
        "2026-09-24T00:00:00",
        id="utc-aware-day",
    ),
    pytest.param(
        "DateTime('UTC')",
        datetime(2026, 9, 24, 13),
        "hour",
        timedelta(days=2),
        "2026-09-24T13:00:00",
        id="utc-aware-hour",
    ),
    # Past DASHBOARD_WEEKLY_AGGREGATION_AFTER_DAYS the graph buckets on toMonday.
    pytest.param(
        "Date",
        date(2026, 9, 21),
        "day",
        timedelta(days=365),
        "2026-09-21T00:00:00",
        id="date-week-long-window",
    ),
]


@pytest.mark.parametrize("spec,bucket,interval,span,expected", _SESSION_CASES)
def test_session_graph_publishes_the_bucket_its_statement_returned(
    monkeypatch, spec, bucket, interval, span, expected
):
    from tracer.services.clickhouse import exact_graph_reads as reads

    end = datetime(2026, 9, 25, tzinfo=UTC)
    start = end - span
    row = {
        "time_bucket": _driver_value(spec, bucket),
        "value": 2,
        "primary_traffic": 2,
    }
    monkeypatch.setattr(
        reads, "_session_aggregate_source_sql", lambda **_: ("SELECT 1", {})
    )
    monkeypatch.setattr(
        reads,
        "_execute_direct_exact_graph_query",
        lambda **_: SimpleNamespace(
            data=[row], columns=["time_bucket", "value", "primary_traffic"]
        ),
    )

    result = reads.read_exact_session_system_graph(
        analytics=object(),
        project_id=PROJECT_ID,
        filters=_window_filters(start, end),
        interval=interval,
        metric_id="session_count",
    )

    assert _populated(result["data"]) == [
        {"timestamp": expected, "value": 2.0, "primary_traffic": 2}
    ]
    # The published timestamp contract is unchanged: naive UTC isoformat.
    assert all(
        datetime.fromisoformat(point["timestamp"]).tzinfo is None
        for point in result["data"]
    )


_EVAL_CASES = [
    pytest.param(
        "DateTime",
        datetime(2026, 9, 24),
        "day",
        "2026-09-24T00:00:00",
        id="naive-day-control",
    ),
    pytest.param(
        "DateTime('UTC')",
        datetime(2026, 9, 24),
        "day",
        "2026-09-24T00:00:00",
        id="utc-aware-day",
    ),
    pytest.param(
        "Date",
        date(2026, 9, 21),
        "week",
        "2026-09-21T00:00:00",
        id="date-week",
    ),
]


@pytest.mark.parametrize("spec,bucket,interval,expected", _EVAL_CASES)
def test_eval_graph_primary_traffic_follows_the_bucket_its_statement_returned(
    spec, bucket, interval, expected
):
    from tracer.services.clickhouse.exact_graph_reads import _add_primary_traffic
    from tracer.services.clickhouse.v2.query_builders.eval_metrics import (
        EvalMetricsQueryBuilderV2,
    )

    builder = EvalMetricsQueryBuilderV2(
        custom_eval_config_id=EVAL_CONFIG_ID,
        project_id=PROJECT_ID,
        start_date=datetime(2026, 9, 1),
        end_date=datetime(2026, 9, 25),
        interval=interval,
        eval_output_type="SCORE",
        eval_name="score",
    )
    rows = [
        {
            "time_bucket": _driver_value(spec, bucket),
            "value": 50.0,
            "primary_traffic": 3,
        }
    ]
    columns = ["time_bucket", "value", "primary_traffic"]

    series = _add_primary_traffic(builder.format_result(rows, columns), rows, columns)

    assert _populated(series["data"]) == [
        {"timestamp": expected, "value": 50.0, "primary_traffic": 3}
    ]


def test_bucket_key_converts_an_offset_to_utc_before_dropping_it():
    from tracer.services.clickhouse.exact_graph_reads import _bucket_key

    midnight = datetime(2026, 9, 24)
    plus_0530 = timezone(timedelta(hours=5, minutes=30))

    assert _bucket_key(datetime(2026, 9, 24, 5, 30, tzinfo=plus_0530)) == midnight
    assert _bucket_key(_driver_value("DateTime('UTC')", midnight)) == midnight
    assert _bucket_key(_driver_value("DateTime", midnight)) == midnight
    assert _bucket_key(date(2026, 9, 24)) == midnight
    assert _bucket_key("2026-09-24T00:00:00") == midnight
    assert _bucket_key("2026-09-24 00:00:00+00:00") == midnight
    assert _bucket_key("not a bucket") is None
    assert _bucket_key(None) is None


# ---------------------------------------------------------------------------
# Siblings that zero-fill from the same range: each already reduces the
# driver's value to the range's representation before the lookup.
# ---------------------------------------------------------------------------


def _sample(rows, *, start: datetime, end: datetime):
    from tracer.services.clickhouse.bounded_graph_reads import GraphCandidateSample

    return GraphCandidateSample(
        rows=tuple(rows),
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        window_start=start,
        window_end=end,
        elapsed_ms=0.0,
        query_count=1,
        rows_returned=len(rows),
        result_payload_bytes=0,
        total_rows_lower_bound=len(rows),
    )


def test_bounded_trace_graph_buckets_a_utc_aware_start_time():
    from tracer.services.clickhouse.bounded_graph_reads import (
        aggregate_system_candidate_graph,
    )

    start_time = _driver_value("DateTime64(6, 'UTC')", datetime(2026, 9, 24, 13, 45))
    sample = _sample(
        [{"start_time": start_time, "latency_ms": 10.0, "trace_id": "t"}],
        start=datetime(2026, 9, 23),
        end=datetime(2026, 9, 25),
    )

    result = aggregate_system_candidate_graph(
        sample, metric_id="traffic", interval="day"
    )

    assert [
        (point["timestamp"], point["primary_traffic"])
        for point in result["data"]
        if point["primary_traffic"]
    ] == [("2026-09-24T00:00:00", 1)]


def test_bounded_session_graph_buckets_a_utc_aware_start_time():
    from tracer.services.clickhouse.session_graph import _aggregate_session_candidates

    start_time = _driver_value("DateTime64(6, 'UTC')", datetime(2026, 9, 24, 13, 45))
    rows = (
        {
            "trace_session_id": "s",
            "trace_id": "t",
            "start_time": start_time,
            "end_time": start_time,
        },
    )
    sample = _sample(rows, start=datetime(2026, 9, 23), end=datetime(2026, 9, 25))

    result = _aggregate_session_candidates(
        sample=sample,
        rows=rows,
        session_id_map={},
        interval="day",
        metric_id="session_count",
        started=0.0,
        extra_query_count=0,
        extra_rows_returned=0,
        extra_result_payload_bytes=0,
    )

    assert _populated(result["data"]) == [
        {"timestamp": "2026-09-24T00:00:00", "value": 1.0, "primary_traffic": 1}
    ]


def test_bounded_eval_graph_buckets_a_utc_aware_created_at():
    from tracer.services.clickhouse.graph_dispatch import (
        _eval_bucket_values,
        _zero_filled_points,
    )

    created_at = _driver_value("DateTime64(3, 'UTC')", datetime(2026, 9, 24, 13, 45))
    values = _eval_bucket_values(
        [{"created_at": created_at, "output_float": 0.5}],
        interval="day",
        output_type="SCORE",
    )

    points = _zero_filled_points(
        sample=_sample([], start=datetime(2026, 9, 23), end=datetime(2026, 9, 25)),
        interval="day",
        values=values,
    )

    assert _populated(points) == [
        {"timestamp": "2026-09-24T00:00:00", "value": 50.0, "primary_traffic": 1}
    ]


@pytest.mark.parametrize(
    "spec,bucket,granularity,expected",
    [
        pytest.param(
            "DateTime('UTC')",
            datetime(2026, 9, 24),
            "day",
            "2026-09-24T00:00:00+00:00",
            id="utc-aware-day",
        ),
        pytest.param(
            "Date",
            date(2026, 9, 21),
            "week",
            "2026-09-21T00:00:00+00:00",
            id="date-week",
        ),
    ],
)
def test_dashboard_series_keeps_a_driver_bucket(spec, bucket, granularity, expected):
    from tracer.services.clickhouse.query_builders.dashboard import (
        DashboardQueryBuilder,
    )

    builder = DashboardQueryBuilder(
        {
            "project_ids": [PROJECT_ID],
            "granularity": granularity,
            "time_range": {
                "custom_start": "2026-09-01T00:00:00",
                "custom_end": "2026-09-25T00:00:00",
            },
            "metrics": [{"id": "latency", "name": "latency", "type": "system_metric"}],
        }
    )

    result = builder.format_results(
        [
            (
                {"id": "latency", "name": "latency", "aggregation": "avg"},
                [{"time_bucket": _driver_value(spec, bucket), "value": 7.0}],
            )
        ]
    )

    (series,) = result["metrics"][0]["series"]
    assert [
        (point["timestamp"], point["value"])
        for point in series["data"]
        if point["value"] is not None
    ] == [(expected, 7.0)]


@pytest.mark.parametrize(
    "bucket_minutes,interval_start",
    [
        (10, datetime(2026, 9, 24, 14, 0)),
        (360, datetime(2026, 9, 24, 12, 0)),
        (1440, datetime(2026, 9, 24)),
    ],
)
def test_eval_usage_chart_keys_match_the_utc_interval_bucket(
    bucket_minutes, interval_start
):
    from model_hub.views.separate_evals import _round_to_usage_bucket

    # ``read_eval_usage`` buckets with toStartOfInterval(created_at, ..., 'UTC'),
    # a DateTime('UTC') column; the view's zero-fill starts from an aware UTC
    # ``start_date`` (timezone.now() or a DRF DateTimeField under TIME_ZONE=UTC).
    start_date = datetime(2026, 9, 24, 14, 7, 31, tzinfo=UTC)
    driver_bucket = _driver_value("DateTime('UTC')", interval_start)

    assert (
        _round_to_usage_bucket(start_date, bucket_minutes).isoformat()
        == driver_bucket.isoformat()
    )
