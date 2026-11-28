"""The raw trace lane's witness band is bound at microsecond precision.

A filtered trace graph on the raw lane (``resolve_span_versions=False``)
decides trace membership from the rows in ``[start - 1 day, end + 1 day)``.
clickhouse-driver renders a ``datetime`` at whole-second precision, so a
fractional request bound used to truncate that band: a witness in the
sub-second tail at either end was admitted or dropped. A sub-second band bound
is now bound as epoch microseconds, as the output window is. A whole-second
band keeps the historical ``%(name)s`` text and params. The resolved lane
rounds the band to whole hours and is unchanged.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse.query_builders.base import _unix_microseconds
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
WITNESS_START = "%(graph_witness_start_date)s"
WITNESS_END = "%(graph_witness_end_date)s"
EXACT_WITNESS_START = (
    "fromUnixTimestamp64Micro(%(graph_witness_start_date_exact_us)s, 'UTC')"
)
EXACT_WITNESS_END = (
    "fromUnixTimestamp64Micro(%(graph_witness_end_date_exact_us)s, 'UTC')"
)
_PLACEHOLDER = re.compile(r"%\(([A-Za-z0-9_]+)\)s")


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


def _no_end_user_filter() -> dict:
    # A negative any-sibling requirement: no row in the band has an end user.
    return {
        "column_id": "user",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": "is_null",
            "filter_value": None,
        },
    }


def _trace_builder(
    start: datetime,
    end: datetime,
    *,
    attribute_filter: dict | None = None,
    resolve_span_versions: bool = False,
    **kwargs,
) -> TimeSeriesQueryBuilder:
    return TimeSeriesQueryBuilder(
        project_id=PROJECT_ID,
        filters=[_time_filter(start, end), attribute_filter or _stage_filter()],
        interval="hour",
        exact_snapshot=True,
        observe_type="trace",
        start_date=start,
        end_date=end,
        resolve_span_versions=resolve_span_versions,
        **kwargs,
    )


def _assert_params_match_placeholders(query: str, params: dict) -> None:
    referenced = set(_PLACEHOLDER.findall(query))
    assert referenced <= set(params)
    assert {name for name in params if name.endswith("_exact_us")} <= referenced


@pytest.mark.parametrize(
    "attribute_filter", [None, _no_end_user_filter()], ids=["stage-in", "user-null"]
)
def test_whole_second_band_keeps_the_historical_text_and_params(attribute_filter):
    start = datetime(2026, 10, 25, 8, 56, 54)
    end = datetime(2026, 10, 25, 9, 56, 54)

    query, params = _trace_builder(
        start, end, attribute_filter=attribute_filter
    ).build()

    assert f"AND start_time >= {WITNESS_START}" in query
    assert f"AND start_time < {WITNESS_END}" in query
    assert "_exact_us" not in query
    assert not [name for name in params if name.endswith("_exact_us")]
    assert params["graph_witness_start_date"] == start - timedelta(days=1)
    assert params["graph_witness_end_date"] == end + timedelta(days=1)
    _assert_params_match_placeholders(query, params)


@pytest.mark.parametrize(
    "attribute_filter", [None, _no_end_user_filter()], ids=["stage-in", "user-null"]
)
def test_fractional_band_is_bound_as_epoch_microseconds(attribute_filter):
    start = datetime(2026, 10, 25, 8, 56, 54, 500000)
    end = datetime(2026, 10, 25, 8, 56, 55, 500000)

    query, params = _trace_builder(
        start, end, attribute_filter=attribute_filter
    ).build()

    assert f"AND start_time >= {EXACT_WITNESS_START}" in query
    assert f"AND start_time < {EXACT_WITNESS_END}" in query
    assert WITNESS_START not in query
    assert WITNESS_END not in query
    assert params["graph_witness_start_date_exact_us"] == _unix_microseconds(
        start - timedelta(days=1)
    )
    assert params["graph_witness_end_date_exact_us"] == _unix_microseconds(
        end + timedelta(days=1)
    )
    _assert_params_match_placeholders(query, params)


@pytest.mark.parametrize(
    ("start", "end", "exact_start", "exact_end"),
    [
        (
            datetime(2026, 10, 25, 8, 56, 54),
            datetime(2026, 10, 25, 8, 56, 55, 1),
            False,
            True,
        ),
        (
            datetime(2026, 10, 25, 8, 56, 54, 999999),
            datetime(2026, 10, 25, 8, 56, 56),
            True,
            False,
        ),
    ],
    ids=["fractional-end", "fractional-start"],
)
def test_each_band_bound_is_exact_only_when_fractional(
    start, end, exact_start, exact_end
):
    query, params = _trace_builder(start, end).build()

    assert (EXACT_WITNESS_START in query) is exact_start
    assert (WITNESS_START in query) is not exact_start
    assert ("graph_witness_start_date_exact_us" in params) is exact_start
    assert (EXACT_WITNESS_END in query) is exact_end
    assert (WITNESS_END in query) is not exact_end
    assert ("graph_witness_end_date_exact_us" in params) is exact_end
    _assert_params_match_placeholders(query, params)


@pytest.mark.parametrize("cluster", ["", "graph_shards"], ids=["single", "cluster"])
def test_candidate_seed_and_cluster_read_use_the_same_exact_band(cluster):
    start = datetime(2026, 10, 25, 8, 56, 54, 250000)
    end = datetime(2026, 10, 25, 8, 56, 55, 750000)

    query, params = _trace_builder(
        start,
        end,
        raw_replica_shard_cluster=cluster,
        raw_replica_shard_count=2 if cluster else 1,
        raw_trace_candidate_predicate="attrs_string['stage'] = %(seed_value)s",
        raw_trace_candidate_params={"seed_value": "prod"},
    ).build()

    seed = query.split("AS graph_seed_spans", 1)[1].split("GROUP BY trace_id", 1)[0]
    outer = query.split("AS graph_seed_spans", 1)[0]
    for block in (seed, outer):
        assert f"start_time >= {EXACT_WITNESS_START}" in block
        assert f"start_time < {EXACT_WITNESS_END}" in block
        assert WITNESS_START not in block
        assert WITNESS_END not in block
    assert ("GLOBAL IN" in query) is bool(cluster)
    _assert_params_match_placeholders(query, params)


def test_resolved_lane_keeps_the_whole_hour_band():
    start = datetime(2026, 10, 25, 8, 56, 54, 250000)
    end = datetime(2026, 10, 25, 8, 56, 55, 750000)

    query, params = _trace_builder(start, end, resolve_span_versions=True).build()

    # Version resolution replays whole replacement-key hours of the band.
    assert "%(graph_scalar_scan_start)s" in query
    assert "%(graph_scalar_scan_end)s" in query
    assert params["graph_scalar_scan_start"] == datetime(2026, 10, 24, 8, 0)
    assert params["graph_scalar_scan_end"] == datetime(2026, 10, 26, 9, 0)
    assert "graph_witness_start_date_exact_us" not in params
    assert "graph_witness_end_date_exact_us" not in params
    assert EXACT_WITNESS_START not in query
    assert EXACT_WITNESS_END not in query
    _assert_params_match_placeholders(query, params)
