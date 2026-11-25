"""Exact system graphs keep sub-second request bounds.

clickhouse-driver renders a bound ``datetime`` at whole-second precision. The
exact graph builders compare ``start_time`` (``DateTime64(6, 'UTC')``) with
the request window, so a fractional bound must be sent as epoch microseconds.
Whole-second bounds keep the historical ``%(name)s`` text and params.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.exact_graph_reads import read_exact_system_graph
from tracer.services.clickhouse.query_builders import base as base_query_module
from tracer.services.clickhouse.query_builders.base import (
    BaseQueryBuilder,
    _unix_microseconds,
)
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.exact_aggregation_cache import normalize_exact_observe_identity

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
DEV_OUTPUT_WINDOW = "start_time >= %(start_date)s AND start_time < %(end_date)s"
DEV_CONTRIBUTION_WINDOW = (
    "start_time >= %(graph_contribution_start)s "
    "AND start_time < %(graph_contribution_end)s"
)
EXACT_START = "fromUnixTimestamp64Micro(%(start_date_exact_us)s, 'UTC')"
EXACT_END = "fromUnixTimestamp64Micro(%(end_date_exact_us)s, 'UTC')"
WHOLE_START = datetime(2026, 10, 25, 8, 0)
WHOLE_END = datetime(2026, 10, 25, 9, 0)


def _time_filter(start, end, op: str = "between") -> dict:
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": op,
            "filter_value": [start, end] if op == "between" else start,
        },
    }


def _attribute_filter() -> dict:
    return {
        "column_id": "stage",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["prod"],
        },
    }


def _exact_builder(
    start: datetime,
    end: datetime,
    *,
    observe_type: str = "span",
    filtered: bool = False,
    resolve_span_versions: bool = True,
) -> TimeSeriesQueryBuilder:
    filters = [_time_filter(start, end)]
    if filtered:
        filters.append(_attribute_filter())
    return TimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        interval="hour",
        exact_snapshot=True,
        observe_type=observe_type,
        start_date=start,
        end_date=end,
        resolve_span_versions=resolve_span_versions,
    )


def _prewhere_blocks(query: str) -> list[str]:
    return [
        block.split("WHERE", 1)[0] for block in query.split("PREWHERE")[1:] if block
    ]


def exact_datetime_bound_sql(params: dict, name: str) -> str:
    # Imported per call so the module still collects on a tree without the
    # helper: the VM probe runs the parity cases of this file on dev, where
    # only the four helper tests below fail.
    from tracer.services.clickhouse.query_builders.base import (
        exact_datetime_bound_sql as bound_sql,
    )

    return bound_sql(params, name)


class _CapturingAnalytics:
    def __init__(self):
        self.calls = []

    def execute_ch_query(self, query, params, **_kwargs):
        self.calls.append((query, dict(params)))
        return SimpleNamespace(data=[], columns=["time_bucket"], query_time_ms=100.0)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_whole_second_bound_keeps_historical_placeholder_and_params():
    params = {"start_date": datetime(2026, 10, 25, 8, 56, 54)}

    assert exact_datetime_bound_sql(params, "start_date") == "%(start_date)s"
    assert params == {"start_date": datetime(2026, 10, 25, 8, 56, 54)}


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 10, 25, 8, 56, 54, 1),
        datetime(2026, 10, 25, 8, 56, 54, 1, tzinfo=UTC),
    ],
    ids=["naive-utc", "aware-utc"],
)
def test_sub_second_bound_is_bound_as_epoch_microseconds(value):
    params = {"start_date": value}

    rendered = exact_datetime_bound_sql(params, "start_date")

    assert rendered == EXACT_START
    assert params["start_date"] is value
    assert params["start_date_exact_us"] == _unix_microseconds(value)
    assert params["start_date_exact_us"] == 1_792_918_614_000_001


@pytest.mark.unit
@pytest.mark.parametrize(
    "value", [date(2026, 10, 25), time(8, 56, 54, 1)], ids=["date", "time"]
)
def test_non_datetime_bounds_keep_historical_placeholder(value):
    params = {"start_date": value}

    assert exact_datetime_bound_sql(params, "start_date") == "%(start_date)s"
    assert set(params) == {"start_date"}


@pytest.mark.unit
def test_bound_check_survives_the_frozen_datetime_test_patch(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 8, 9, 12, 30, 45, 123456)

    monkeypatch.setattr(base_query_module, "datetime", FrozenDateTime)
    plain = base_query_module.datetime.utcnow()
    assert type(plain) is datetime
    params = {"end_date": plain}

    assert (
        exact_datetime_bound_sql(params, "end_date")
        == "fromUnixTimestamp64Micro(%(end_date_exact_us)s, 'UTC')"
    )
    assert params["end_date_exact_us"] == 1_786_278_645_123_456


# ---------------------------------------------------------------------------
# Whole-second windows keep the dev statement text
# ---------------------------------------------------------------------------


WHOLE_SECOND_CASES = [
    *(
        f"build-{observe_type}-{'filtered' if filtered else 'unfiltered'}-"
        f"{'resolved' if resolve else 'raw'}"
        for observe_type in ("span", "trace")
        for filtered in (False, True)
        for resolve in (True, False)
    ),
    "trace-contribution-batch",
    "span-partition",
]


def _whole_second_statement(case: str) -> tuple[str, dict]:
    if case == "trace-contribution-batch":
        builder = _exact_builder(
            WHOLE_START, WHOLE_END, observe_type="trace", filtered=True
        )
        return builder.build_exact_trace_contribution_batch(["trace-1"])
    if case == "span-partition":
        builder = _exact_builder(WHOLE_START, WHOLE_END, filtered=True)
        return builder.build_exact_span_partition(
            partition_start=WHOLE_START, partition_end=WHOLE_END
        )
    _, observe_type, filtered, resolve = case.split("-")
    return _exact_builder(
        WHOLE_START,
        WHOLE_END,
        observe_type=observe_type,
        filtered=filtered == "filtered",
        resolve_span_versions=resolve == "resolved",
    ).build()


@pytest.mark.unit
@pytest.mark.parametrize("case", WHOLE_SECOND_CASES)
def test_whole_second_windows_render_the_dev_statement(case):
    query, params = _whole_second_statement(case)

    assert "_exact_us" not in query
    assert not [name for name in params if name.endswith("_exact_us")]
    if case == "span-partition":
        assert DEV_CONTRIBUTION_WINDOW in query
    assert DEV_OUTPUT_WINDOW in query


@pytest.mark.unit
def test_whole_second_raw_span_prewhere_keeps_request_placeholders():
    query, _ = _exact_builder(
        WHOLE_START, WHOLE_END, filtered=True, resolve_span_versions=False
    ).build()

    assert "AND start_time >= %(start_date)s" in query
    assert "AND start_time < %(end_date)s" in query


# ---------------------------------------------------------------------------
# Sub-second windows are exact
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_trace_contribution_batch_uses_exact_fractional_upper_bound():
    start = datetime(2026, 10, 25, 8, 56, 54)
    end = start + timedelta(microseconds=1)
    builder = _exact_builder(start, end, observe_type="trace", filtered=True)

    query, params = builder.build_exact_trace_contribution_batch(["trace-1"])

    assert f"start_time >= %(start_date)s AND start_time < {EXACT_END}" in query
    assert "%(end_date)s" not in query
    assert params["end_date_exact_us"] == _unix_microseconds(end)
    assert "start_date_exact_us" not in params
    # Hour-rounded replacement-key scan bounds keep their whole-hour params.
    assert "start_time >= %(graph_trace_scan_start)s" in query
    assert "start_time < %(graph_trace_scan_end)s" in query
    assert params["graph_trace_scan_start"] == datetime(2026, 10, 25, 8, 0)
    assert params["graph_trace_scan_end"] == datetime(2026, 10, 25, 9, 0)


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["span", "trace"])
@pytest.mark.parametrize("filtered", [False, True])
def test_resolved_exact_build_uses_exact_window_and_usage_fence(observe_type, filtered):
    start = datetime(2026, 10, 25, 8, 56, 54, 500000)
    end = datetime(2026, 10, 25, 8, 56, 55, 500000)

    query, params = _exact_builder(
        start, end, observe_type=observe_type, filtered=filtered
    ).build()

    window = f"start_time >= {EXACT_START} AND start_time < {EXACT_END}"
    # Output window and the repeated-summary usage fence both use it.
    assert query.count(window) >= 2
    assert DEV_OUTPUT_WINDOW not in query
    assert params["start_date_exact_us"] == _unix_microseconds(start)
    assert params["end_date_exact_us"] == _unix_microseconds(end)
    # Version-resolving scans stay on whole-hour params. A filtered trace
    # graph scans the +/-1 day witness band, rounded to whole hours.
    scan = (
        (datetime(2026, 10, 24, 8, 0), datetime(2026, 10, 26, 9, 0))
        if observe_type == "trace" and filtered
        else (datetime(2026, 10, 25, 8, 0), datetime(2026, 10, 25, 9, 0))
    )
    assert "%(graph_scalar_scan_start)s" in query
    assert "%(graph_scalar_scan_end)s" in query
    assert (params["graph_scalar_scan_start"], params["graph_scalar_scan_end"]) == scan


@pytest.mark.unit
def test_raw_span_lane_prewhere_keeps_the_fractional_upper_bound():
    start = datetime(2026, 10, 25, 8, 56, 54)
    end = datetime(2026, 10, 25, 8, 56, 55, 500000)

    query, params = _exact_builder(
        start, end, filtered=True, resolve_span_versions=False
    ).build()

    prewhere = _prewhere_blocks(query)
    assert any(
        "AND start_time >= %(start_date)s" in block
        and f"AND start_time < {EXACT_END}" in block
        for block in prewhere
    )
    assert f"start_time >= %(start_date)s AND start_time < {EXACT_END}" in query
    assert params["end_date_exact_us"] == _unix_microseconds(end)


@pytest.mark.unit
def test_raw_trace_lane_keeps_witness_band_and_exact_output_window():
    start = datetime(2026, 10, 25, 8, 56, 54, 250000)
    end = datetime(2026, 10, 25, 8, 56, 55, 750000)

    query, params = _exact_builder(
        start,
        end,
        observe_type="trace",
        filtered=True,
        resolve_span_versions=False,
    ).build()

    assert "AND start_time >= %(graph_witness_start_date)s" in query
    assert "AND start_time < %(graph_witness_end_date)s" in query
    assert params["graph_witness_start_date"] == start - timedelta(days=1)
    assert params["graph_witness_end_date"] == end + timedelta(days=1)
    assert f"start_time >= {EXACT_START} AND start_time < {EXACT_END}" in query
    assert DEV_OUTPUT_WINDOW not in query


@pytest.mark.unit
def test_raw_trace_lane_candidate_seed_keeps_witness_placeholders():
    start = datetime(2026, 10, 25, 8, 56, 54, 250000)
    end = datetime(2026, 10, 25, 8, 56, 55, 750000)
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(start, end), _attribute_filter()],
        interval="hour",
        exact_snapshot=True,
        observe_type="trace",
        start_date=start,
        end_date=end,
        resolve_span_versions=False,
        raw_trace_candidate_predicate="attrs_string['stage'] = %(seed_value)s",
        raw_trace_candidate_params={"seed_value": "prod"},
    )

    query, _ = builder.build()

    seed = query.split("AS graph_seed_spans", 1)[1].split("GROUP BY trace_id", 1)[0]
    assert "start_time >= %(graph_witness_start_date)s" in seed
    assert "start_time < %(graph_witness_end_date)s" in seed
    assert "_exact_us" not in seed


@pytest.mark.unit
def test_span_partitions_are_exact_at_fractional_edges_only(monkeypatch):
    monkeypatch.setattr(
        TimeSeriesQueryBuilder, "_exact_span_candidate_plan", lambda _: None
    )
    start = datetime(2026, 8, 1, 0, 2, 0, 500000)
    end = datetime(2026, 8, 1, 3, 14, 0, 250000)
    analytics = _CapturingAnalytics()

    result = read_exact_system_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_time_filter(start, end), _attribute_filter()],
        interval="minute",
        metric_id="traffic",
        observe_type="span",
    )

    assert result["query_complete"] is True
    assert len(analytics.calls) >= 2
    contributions = []
    for query, params in analytics.calls:
        lower = params["graph_contribution_start"]
        upper = params["graph_contribution_end"]
        contributions.append((lower, upper))
        if lower.microsecond:
            assert (
                "start_time >= fromUnixTimestamp64Micro("
                "%(graph_contribution_start_exact_us)s, 'UTC')"
            ) in query
            assert params["graph_contribution_start_exact_us"] == (
                _unix_microseconds(lower)
            )
        else:
            assert "start_time >= %(graph_contribution_start)s" in query
        if upper.microsecond:
            assert (
                "start_time < fromUnixTimestamp64Micro("
                "%(graph_contribution_end_exact_us)s, 'UTC')"
            ) in query
            assert params["graph_contribution_end_exact_us"] == (
                _unix_microseconds(upper)
            )
        else:
            assert "start_time < %(graph_contribution_end)s" in query
        # Contribution bounds never enter the whole-hour physical scan.
        for block in _prewhere_blocks(query):
            assert "graph_contribution" not in block
    assert contributions[0][0] == start
    assert contributions[-1][1] == end
    assert all(
        left[1] == right[0]
        for left, right in zip(contributions, contributions[1:], strict=False)
    )
    first_query = analytics.calls[0][0]
    last_query = analytics.calls[-1][0]
    assert "%(graph_contribution_start_exact_us)s" in first_query
    assert "%(graph_contribution_end_exact_us)s" in last_query


@pytest.mark.unit
def test_non_exact_dashboard_builders_keep_dev_text_for_fractional_windows():
    start = datetime(2026, 10, 25, 8, 56, 54, 500000)
    end = datetime(2026, 10, 25, 8, 56, 55, 500000)
    for filters in ([_time_filter(start, end), _attribute_filter()], []):
        query, params = TimeSeriesQueryBuilder(
            project_id=PROJECT_ID,
            filters=filters,
            interval="hour",
            start_date=start,
            end_date=end,
        ).build()

        assert "_exact_us" not in query
        assert not [name for name in params if name.endswith("_exact_us")]
        assert "%(start_date)s" in query
        assert "%(end_date)s" in query


# ---------------------------------------------------------------------------
# Datetime operators that add one microsecond
# ---------------------------------------------------------------------------


def _operator_filter(op: str, value: datetime) -> dict:
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": op,
            "filter_value": value,
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "op,companion,exact_start,exact_end",
    [
        ("equals", None, False, True),
        ("greater_than", ("less_than", timedelta(hours=1)), True, False),
        (
            "less_than_or_equal",
            ("greater_than_or_equal", -timedelta(hours=1)),
            False,
            True,
        ),
    ],
)
def test_microsecond_operators_use_the_exact_form_for_that_bound(
    op, companion, exact_start, exact_end
):
    value = datetime(2026, 10, 25, 8, 56, 54)
    filters = [_operator_filter(op, value), _attribute_filter()]
    if companion is not None:
        # Give the other side an explicit whole-second bound; the rolling
        # default would almost always be sub-second.
        filters.append(_operator_filter(companion[0], value + companion[1]))
    start, end = BaseQueryBuilder.parse_time_range(filters, strict=True)
    assert bool(start.microsecond) is exact_start
    assert bool(end.microsecond) is exact_end
    builder = TimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=filters,
        interval="hour",
        exact_snapshot=True,
        observe_type="span",
        start_date=start,
        end_date=end,
    )

    query, params = builder.build()

    assert (EXACT_START in query) is exact_start
    assert ("start_date_exact_us" in params) is exact_start
    assert (EXACT_END in query) is exact_end
    assert ("end_date_exact_us" in params) is exact_end


# ---------------------------------------------------------------------------
# Frozen default window (normalize_exact_observe_identity keeps microseconds)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("observe_type", ["span", "trace"])
def test_frozen_default_window_is_read_at_microsecond_precision(
    monkeypatch, observe_type
):
    class FrozenDateTime(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 8, 9, 12, 30, 45, 123456)

    monkeypatch.setattr(base_query_module, "datetime", FrozenDateTime)
    frozen = normalize_exact_observe_identity(
        {"project_id": PROJECT_ID, "filters": [], "metric_id": "latency"}
    )
    assert frozen["filters"][0]["filter_config"]["filter_value"] == [
        "2026-07-10T12:30:45.123456Z",
        "2026-08-09T12:30:45.123456Z",
    ]
    analytics = _CapturingAnalytics()

    # The patch stays active: the bound check must not depend on the
    # module-level ``datetime`` name.
    read_exact_system_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=frozen["filters"],
        interval="day",
        metric_id="latency",
        observe_type=observe_type,
    )

    assert len(analytics.calls) == 1
    query, params = analytics.calls[0]
    assert f"start_time >= {EXACT_START} AND start_time < {EXACT_END}" in query
    assert DEV_OUTPUT_WINDOW not in query
    assert params["start_date_exact_us"] == 1_783_686_645_123_456
    assert params["end_date_exact_us"] == 1_786_278_645_123_456
