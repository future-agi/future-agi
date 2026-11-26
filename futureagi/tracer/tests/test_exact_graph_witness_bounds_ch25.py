"""The raw trace lane's witness band, executed on the CH25 sidecar.

A filtered trace graph on the raw lane decides membership from the rows in
``[start - 1 day, end + 1 day)``. Each scenario below is one trace whose root
sits inside the request window and whose witness child sits near one end of
that band. With a fractional window, the child is in the band's sub-second
tail: a truncated band drops a witness in ``[floor(end + 1 day), end + 1 day)``
and admits one in ``[floor(start - 1 day), start - 1 day)``.

Expected counts come from an independent oracle over the inserted rows, not
from the builders: membership is decided on integer microseconds over every
live physical row, because the raw lane does not collapse versions (an unmerged
older version still witnesses where it falls). A positive filter
(``stage in [prod]``) needs a matching row in the band; a negative one
(``user is_null``) needs no row with an end user in the band.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse import graph_dispatch
from tracer.tests import test_graph_native_cost_coverage as graph_fixtures
from tracer.tests.test_exact_graph_subsecond_bounds_ch25 import (
    _builder,
    _bundle_traffic,
    _graph_traffic,
    _stage_filter,
    _window_filter,
)
from tracer.tests.test_graph_native_cost_coverage import (
    PROJECT,
    USER,
    NativeAnalytics,
    insert_span,
)

native_graph_db = graph_fixtures.native_graph_db
pytestmark = pytest.mark.integration

T = datetime(2026, 10, 20, 12, 56, 54, tzinfo=UTC)
MS = timedelta(milliseconds=1)
DAY = timedelta(days=1)
ROOT_AT = T + 1000 * MS

# Request windows. The fractional one puts each band bound 500 ms past a whole
# second, so a witness 250 ms either side of the truncated bound separates the
# exact band from the truncated one. The whole-second window is a control in
# which both bands coincide.
WINDOWS = {
    "fractional": (T + 500 * MS, T + 1500 * MS),
    "whole-second": (T, T + 2000 * MS),
}


@dataclass(frozen=True)
class Row:
    trace: str
    span: str
    start: datetime
    stage: str = "dev"
    user: str | None = USER
    parent: str = ""
    version: int = 1


def _scenario(name: str, root: dict, witnesses: list[dict]) -> list[Row]:
    rows = [Row(trace=name, span=f"{name}-root", start=ROOT_AT, **root)]
    rows.extend(
        Row(trace=name, span=f"{name}-witness", parent=f"{name}-root", **witness)
        for witness in witnesses
    )
    return rows


NO_USER: dict = {"user": None}
PROD_WITNESS = {"stage": "prod"}
USER_WITNESS = {"user": USER}
SCENARIOS = {
    # Positive filter: stage in [prod].
    "positive-control": _scenario("positive-control", {"stage": "prod"}, []),
    "positive-upper-tail": _scenario(
        "positive-upper-tail", {}, [{"start": T + DAY + 1250 * MS, **PROD_WITNESS}]
    ),
    "positive-past-upper": _scenario(
        "positive-past-upper", {}, [{"start": T + DAY + 1750 * MS, **PROD_WITNESS}]
    ),
    "positive-lower-tail": _scenario(
        "positive-lower-tail", {}, [{"start": T - DAY + 250 * MS, **PROD_WITNESS}]
    ),
    "positive-inside-lower": _scenario(
        "positive-inside-lower", {}, [{"start": T - DAY + 750 * MS, **PROD_WITNESS}]
    ),
    # Two unmerged versions of one witness in the same replacement-key hour:
    # v1 in the upper tail, v2 past the band.
    "positive-reversioned": _scenario(
        "positive-reversioned",
        {},
        [
            {"start": T + DAY + 1250 * MS, "version": 1, **PROD_WITNESS},
            {"start": T + DAY + 1750 * MS, "version": 2, **PROD_WITNESS},
        ],
    ),
    # Negative filter: user is_null (no row in the band has an end user).
    "negative-control": _scenario("negative-control", NO_USER, []),
    "negative-upper-tail": _scenario(
        "negative-upper-tail", NO_USER, [{"start": T + DAY + 1250 * MS, **USER_WITNESS}]
    ),
    "negative-lower-tail": _scenario(
        "negative-lower-tail", NO_USER, [{"start": T - DAY + 250 * MS, **USER_WITNESS}]
    ),
    "negative-reversioned": _scenario(
        "negative-reversioned",
        NO_USER,
        [
            {"start": T + DAY + 1250 * MS, "version": 1, **USER_WITNESS},
            {"start": T + DAY + 1750 * MS, "version": 2, **USER_WITNESS},
        ],
    ),
}

USER_IS_NULL = {
    "column_id": "user",
    "filter_config": {
        "col_type": "SYSTEM_METRIC",
        "filter_type": "text",
        "filter_op": "is_null",
        "filter_value": None,
    },
}
FILTERS = {"stage-in-prod": _stage_filter(), "user-is-null": USER_IS_NULL}


def _micros(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _witnesses(row: Row, filter_name: str) -> bool:
    if filter_name == "stage-in-prod":
        return row.stage == "prod"
    return row.user is not None


def oracle(rows: list[Row], lo: datetime, hi: datetime, filter_name: str) -> int:
    """Rows of member traces in ``[lo, hi)`` on the raw lane.

    Every physical row is live (nothing is deleted) and counts on its own, so
    both versions of a re-versioned witness witness where they fall.
    """

    lo_us, hi_us = _micros(lo), _micros(hi)
    band_lo, band_hi = _micros(lo - DAY), _micros(hi + DAY)
    traces = {row.trace for row in rows}
    witnessed = {
        row.trace
        for row in rows
        if band_lo <= _micros(row.start) < band_hi and _witnesses(row, filter_name)
    }
    members = witnessed if filter_name == "stage-in-prod" else traces - witnessed
    return sum(
        1
        for row in rows
        if row.trace in members and lo_us <= _micros(row.start) < hi_us
    )


def _seed(client, rows: list[Row]) -> None:
    for row in rows:
        insert_span(
            client,
            span_id=row.span,
            trace=row.trace,
            parent=row.parent,
            start=row.start,
            attrs={"stage": row.stage},
            user=row.user,
            version=row.version,
            kind="chain",
            tokens=0,
        )


def _raw_lane_traffic(analytics, filters, **kwargs) -> int:
    builder = _builder(filters, "trace", resolve_span_versions=False, **kwargs)
    query, params = builder.build()
    result = analytics.execute_ch_query(query, params)
    return _bundle_traffic(builder.format_result(result.data, result.columns))


def test_oracle_separates_the_exact_band_from_the_truncated_one():
    """The fixture is meaningful: a truncated band disagrees on every tail."""

    lo, hi = WINDOWS["fractional"]

    def truncated_band(rows: list[Row], filter_name: str) -> int:
        band_lo = (lo - DAY).replace(microsecond=0)
        band_hi = (hi + DAY).replace(microsecond=0)
        traces = {row.trace for row in rows}
        witnessed = {
            row.trace
            for row in rows
            if band_lo <= row.start < band_hi and _witnesses(row, filter_name)
        }
        members = witnessed if filter_name == "stage-in-prod" else traces - witnessed
        return sum(1 for row in rows if row.trace in members and lo <= row.start < hi)

    differs = {
        (name, filter_name)
        for name, rows in SCENARIOS.items()
        for filter_name in FILTERS
        if oracle(rows, lo, hi, filter_name) != truncated_band(rows, filter_name)
    }
    assert differs == {
        ("positive-upper-tail", "stage-in-prod"),
        ("positive-lower-tail", "stage-in-prod"),
        ("positive-reversioned", "stage-in-prod"),
        ("negative-upper-tail", "user-is-null"),
        ("negative-lower-tail", "user-is-null"),
        ("negative-reversioned", "user-is-null"),
    }


@pytest.mark.parametrize("window", sorted(WINDOWS))
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_raw_trace_lane_membership_uses_the_exact_witness_band(
    native_graph_db, monkeypatch, scenario, window
):
    client = native_graph_db
    rows = SCENARIOS[scenario]
    lo, hi = WINDOWS[window]
    client.execute("SYSTEM STOP MERGES spans")
    try:
        _seed(client, rows)
        analytics = NativeAnalytics(client)
        got: dict[tuple[str, str], int] = {}
        want: dict[tuple[str, str], int] = {}
        for filter_name, attribute_filter in FILTERS.items():
            filters = [_window_filter(lo, hi), attribute_filter]
            expected = oracle(rows, lo, hi, filter_name)
            want[(filter_name, "raw")] = expected
            got[(filter_name, "raw")] = _raw_lane_traffic(analytics, filters)
            # A compiler-proven candidate seed reads the same band. The user
            # filter is negative, so its seed is every row (a superset).
            seed_predicate, seed_params = (
                (
                    "attrs_string['stage'] = %(witness_seed_stage)s",
                    {"witness_seed_stage": "prod"},
                )
                if filter_name == "stage-in-prod"
                else ("1 = 1", {})
            )
            want[(filter_name, "seeded")] = expected
            got[(filter_name, "seeded")] = _raw_lane_traffic(
                analytics,
                filters,
                raw_trace_candidate_predicate=seed_predicate,
                raw_trace_candidate_params=seed_params,
            )

        # The interactive direct lane reads traffic on the raw lane.
        monkeypatch.setattr(
            graph_dispatch, "_read_or_refresh_exact_graph", lambda **_: None
        )
        monkeypatch.setattr(
            graph_dispatch, "_affordable_raw_graph_seed", lambda **_: (None, 0)
        )
        graph = graph_dispatch.fetch_system_metric_graph_ch(
            analytics=analytics,
            project_id=PROJECT,
            filters=[_window_filter(lo, hi), _stage_filter()],
            interval="hour",
            metric_id="traffic",
            observe_type="trace",
        )
        want[("stage-in-prod", "interactive")] = oracle(rows, lo, hi, "stage-in-prod")
        got[("stage-in-prod", "interactive")] = _graph_traffic(graph)

        assert got == want
    finally:
        client.execute("SYSTEM START MERGES spans")
