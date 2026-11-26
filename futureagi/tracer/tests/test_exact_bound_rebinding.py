"""A whole-second bound never keeps an earlier sub-second binding.

``exact_datetime_bound_sql`` binds a sub-second bound as ``<name>_exact_us``.
The exact span reader renders successive storage partitions with one builder
and one params dict: the first partition's contribution window starts at the
request's fractional start, the middle ones on whole hours, the last one ends
at the fractional end. Each statement's params must carry exactly the
``_exact_us`` bindings its SQL references, so a whole-second partition does not
inherit the first partition's binding.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.exact_graph_reads import read_exact_system_graph
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
_PLACEHOLDER = re.compile(r"%\(([A-Za-z0-9_]+)\)s")


def exact_datetime_bound_sql(params: dict, name: str) -> str:
    # Imported per call, like test_exact_graph_subsecond_bounds, so the module
    # collects on a tree without the helper.
    from tracer.services.clickhouse.query_builders.base import (
        exact_datetime_bound_sql as bound_sql,
    )

    return bound_sql(params, name)


def _time_filter(start: datetime, end: datetime) -> dict:
    return {
        "column_id": "start_time",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start, end],
        },
    }


def _stage_filter() -> dict:
    return {
        "column_id": "stage",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in",
            "filter_value": ["prod"],
        },
    }


def _exact_keys(params: dict) -> set[str]:
    return {name for name in params if name.endswith("_exact_us")}


def _assert_bindings_match_sql(query: str, params: dict) -> None:
    referenced = set(_PLACEHOLDER.findall(query))
    assert referenced <= set(params)
    assert _exact_keys(params) == {
        name for name in referenced if name.endswith("_exact_us")
    }


class _CapturingAnalytics:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute_ch_query(self, query, params, **_kwargs):
        self.calls.append((query, dict(params)))
        return SimpleNamespace(data=[], columns=["time_bucket"], query_time_ms=100.0)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "later",
    [datetime(2026, 10, 25, 9, 0), date(2026, 10, 25)],
    ids=["whole-second", "date"],
)
def test_later_whole_second_value_drops_the_earlier_binding(later):
    params = {"graph_contribution_start": datetime(2026, 10, 25, 8, 56, 54, 500000)}
    exact_datetime_bound_sql(params, "graph_contribution_start")
    assert "graph_contribution_start_exact_us" in params

    params["graph_contribution_start"] = later
    rendered = exact_datetime_bound_sql(params, "graph_contribution_start")

    assert rendered == "%(graph_contribution_start)s"
    assert params == {"graph_contribution_start": later}


def test_later_sub_second_value_replaces_the_binding():
    params = {"end_date": datetime(2026, 10, 25, 9, 0)}
    assert exact_datetime_bound_sql(params, "end_date") == "%(end_date)s"

    params["end_date"] = datetime(2026, 10, 25, 9, 0, 0, 1)
    exact_datetime_bound_sql(params, "end_date")
    first = params["end_date_exact_us"]
    params["end_date"] = datetime(2026, 10, 25, 9, 0, 0, 2)
    exact_datetime_bound_sql(params, "end_date")

    assert params["end_date_exact_us"] == first + 1


def test_rebinding_leaves_other_names_alone():
    params = {
        "start_date": datetime(2026, 10, 25, 8, 0, 0, 1),
        "end_date": datetime(2026, 10, 25, 9, 0, 0, 1),
    }
    exact_datetime_bound_sql(params, "start_date")
    exact_datetime_bound_sql(params, "end_date")

    params["start_date"] = datetime(2026, 10, 25, 8, 0)
    exact_datetime_bound_sql(params, "start_date")

    assert _exact_keys(params) == {"end_date_exact_us"}


# ---------------------------------------------------------------------------
# Span partitions from one builder
# ---------------------------------------------------------------------------


def _partition_builder(start: datetime, end: datetime) -> TimeSeriesQueryBuilder:
    return TimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(start, end), _stage_filter()],
        interval="hour",
        exact_snapshot=True,
        observe_type="span",
        start_date=start,
        end_date=end,
    )


def test_each_partition_carries_exactly_its_own_contribution_bindings(monkeypatch):
    monkeypatch.setattr(
        TimeSeriesQueryBuilder, "_exact_span_candidate_plan", lambda _: None
    )
    start = datetime(2026, 8, 1, 0, 2, 0, 500000)
    end = datetime(2026, 8, 1, 3, 14, 0, 250000)
    builder = _partition_builder(start, end)
    hour = timedelta(hours=1)
    cut = start.replace(minute=0, second=0, microsecond=0)
    statements = []
    while cut < end:
        statements.append(
            builder.build_exact_span_partition(
                partition_start=cut, partition_end=cut + hour
            )
        )
        cut += hour

    assert len(statements) == 4
    contribution_keys = [
        _exact_keys(params)
        & {"graph_contribution_start_exact_us", "graph_contribution_end_exact_us"}
        for _, params in statements
    ]
    assert contribution_keys == [
        {"graph_contribution_start_exact_us"},
        set(),
        set(),
        {"graph_contribution_end_exact_us"},
    ]
    for query, params in statements:
        _assert_bindings_match_sql(query, params)
    # The output window and usage fence stay exact in every partition.
    for _, params in statements:
        assert {"start_date_exact_us", "end_date_exact_us"} <= _exact_keys(params)


def test_reader_partitions_carry_only_referenced_bindings(monkeypatch):
    monkeypatch.setattr(
        TimeSeriesQueryBuilder, "_exact_span_candidate_plan", lambda _: None
    )
    start = datetime(2026, 8, 1, 0, 2, 0, 500000)
    end = datetime(2026, 8, 1, 3, 14, 0, 250000)
    analytics = _CapturingAnalytics()

    result = read_exact_system_graph(
        analytics=analytics,
        project_id=PROJECT_ID,
        filters=[_time_filter(start, end), _stage_filter()],
        interval="minute",
        metric_id="traffic",
        observe_type="span",
    )

    assert result["query_complete"] is True
    assert len(analytics.calls) >= 2
    for query, params in analytics.calls:
        _assert_bindings_match_sql(query, params)
    middle = [
        params
        for _, params in analytics.calls
        if not params["graph_contribution_start"].microsecond
        and not params["graph_contribution_end"].microsecond
    ]
    for params in middle:
        assert "graph_contribution_start_exact_us" not in params
        assert "graph_contribution_end_exact_us" not in params


def test_whole_second_window_after_a_fractional_one_keeps_no_binding():
    builder = _partition_builder(
        datetime(2026, 10, 25, 8, 56, 54, 500000),
        datetime(2026, 10, 25, 8, 56, 55, 500000),
    )
    query, params = builder.build()
    assert {"start_date_exact_us", "end_date_exact_us"} <= _exact_keys(params)

    builder.start_date = datetime(2026, 10, 25, 8, 0)
    builder.end_date = datetime(2026, 10, 25, 9, 0)
    query, params = builder.build()

    assert "_exact_us" not in query
    assert _exact_keys(params) == set()
    _assert_bindings_match_sql(query, params)
