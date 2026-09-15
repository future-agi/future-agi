"""The span list's two row budgets, and the shape that makes them cheap.

Both of this lane's wide statements - the candidate seed and the absence proof
that tells it where to look - scan the raw span population of an interval, so
both are budgeted in ROWS and both are costed by the same ``EXPLAIN ESTIMATE``.
The tests here pin the two properties that make that safe:

* the probe and the proof carry the filter's conjunction as GRANULE-level
  index hints only, so neither reads a Map value, and
* neither decides membership: the seed re-applies every leaf and the exact
  latest-state classifier alone publishes a row.
"""

import re
from datetime import timedelta

import pytest
from django.test import override_settings

from tracer.selectors.filter_seed_width import (
    EMPTY_DENSITY_ESTIMATE,
    FilterSeedWidthPolicy,
    reduce_density_estimate,
)
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    PROJECT,
    START,
    time_filter,
)

WINDOW = (START - timedelta(days=30), START + timedelta(days=1))

# Every way a statement can read a typed Map at ROW level. ``indexHint`` may
# name any of them, because its argument is used for index analysis and is
# true for every row of a surviving granule; outside one, each of these
# decompresses a column this lane must not pay for in a probe.
_ROW_LEVEL_MAP_READS = (
    "attrs_string[",
    "attrs_number[",
    "attrs_bool[",
    "attrs_string.keys",
    "attrs_number.keys",
    "attrs_bool.keys",
    "mapContains(",
    "mapValues(",
    "mapKeys(",
    "lowerUTF8(",
)


def _outside_index_hints(sql):
    """``sql`` with every ``indexHint(...)`` argument removed."""

    out, i = [], 0
    while True:
        at = sql.find("indexHint(", i)
        if at < 0:
            out.append(sql[i:])
            return "".join(out)
        out.append(sql[i:at])
        depth, j = 0, sql.index("(", at)
        while True:
            if sql[j] == "(":
                depth += 1
            elif sql[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        i = j + 1


def _subject(*, leaves=1, op="equals", value="a"):
    filters = [time_filter(start=WINDOW[0], end=WINDOW[1])]
    filters.append(
        {
            "column_id": "attr_0",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": op,
                "filter_value": value,
            },
        }
    )
    for index in range(1, leaves):
        filters.append(
            {
                "column_id": f"attr_{index}",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "is_not_null",
                },
            }
        )
    return SpanListQueryBuilderV2(
        project_id=PROJECT, filters=filters, bounded_internal_scan=True
    )


def _hint_free(sql):
    return _outside_index_hints(sql)


def test_index_hint_stripper_is_itself_exercised():
    """A scan that cannot fire proves nothing, so seed it with a known hit."""

    assert "mapValues(" in _hint_free("x AND mapValues(attrs_string) AND y")
    assert "mapValues(" not in _hint_free("x AND indexHint(has(mapValues(a), b)) AND y")


@pytest.mark.parametrize("leaves", [1, 2, 5, 10])
def test_density_probe_compares_no_value_outside_an_index_hint(leaves):
    subject = _subject(leaves=leaves)
    sql, params = subject.build_filter_seed_density_probe_query(
        slice_start=WINDOW[1] - timedelta(hours=8), slice_end=WINDOW[1]
    )
    assert sql.lstrip().startswith("EXPLAIN ESTIMATE")
    outside = _hint_free(sql)
    for forbidden in _ROW_LEVEL_MAP_READS:
        assert forbidden not in outside, forbidden
    # The conjunction IS carried - costing against the plain time range would
    # answer for a population this lane never reads.
    assert sql.count("indexHint(") >= leaves
    assert "FINAL" not in sql and "SAMPLE" not in sql and "LIMIT" not in sql
    assert params["seed_density_start_us"] < params["seed_density_end_us"]


@pytest.mark.parametrize("leaves", [1, 2, 5, 10])
def test_population_proof_compares_no_value_outside_an_index_hint(leaves):
    subject = _subject(leaves=leaves)
    sql, _ = subject.build_filter_population_time_discovery_query(
        slice_start=WINDOW[0], slice_end=WINDOW[1]
    )
    outside = _hint_free(sql)
    for forbidden in _ROW_LEVEL_MAP_READS:
        assert forbidden not in outside, forbidden
    assert "maxOrNull" in sql
    assert "FINAL" not in sql and "SAMPLE" not in sql and "LIMIT" not in sql


def test_probe_time_predicate_stays_on_the_raw_column():
    """Spelled as the hour the optimizer could answer from a PROJECTION, whose
    aggregate rows are orders of magnitude below the interval's - an estimate
    far inside the budget, and the widest possible interval approved."""

    sql, _ = _subject(leaves=2).build_filter_seed_density_probe_query(
        slice_start=WINDOW[1] - timedelta(hours=8), slice_end=WINDOW[1]
    )
    assert "toStartOfHour" not in sql
    assert re.search(r"\bstart_time >= fromUnixTimestamp64Micro", sql)


def test_probe_interval_rounds_out_to_whole_hours():
    """Rounding OUT keeps the estimate an upper bound on the interval it
    approves, so it can only make the issued interval narrower."""

    end = WINDOW[1]
    sql, params = _subject().build_filter_seed_density_probe_query(
        slice_start=end - timedelta(minutes=90), slice_end=end - timedelta(minutes=5)
    )
    start_us, end_us = params["seed_density_start_us"], params["seed_density_end_us"]
    assert start_us % 3_600_000_000 == 0
    assert end_us % 3_600_000_000 == 0
    assert start_us <= _micros(end - timedelta(minutes=90))
    assert end_us >= _micros(end - timedelta(minutes=5))


def _micros(moment):
    from tracer.services.clickhouse.query_builders.span_list import (
        _unix_microseconds,
    )

    return _unix_microseconds(moment)


def test_probe_refuses_an_interval_outside_the_request():
    subject = _subject()
    with pytest.raises(ValueError):
        subject.build_filter_seed_density_probe_query(
            slice_start=WINDOW[0] - timedelta(hours=1), slice_end=WINDOW[1]
        )


def test_a_time_only_list_declares_no_budget_and_no_probe():
    """Nothing to be blind about: with no attribute predicate the seed reads
    the population it was asked for, and today's schedule already bounds it."""

    subject = SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[time_filter(start=WINDOW[0], end=WINDOW[1])],
        bounded_internal_scan=True,
    )
    assert subject.filter_seed_width_policy() is None
    assert subject.filter_population_discovery_width_policy() is None
    assert subject.supports_filter_seed_density_probe() is False
    with pytest.raises(ValueError):
        subject.build_filter_seed_density_probe_query(
            slice_start=WINDOW[1] - timedelta(hours=1), slice_end=WINDOW[1]
        )


def test_a_native_column_list_declares_no_budget_either():
    """The budget is declared for exactly the shapes it was measured on.

    A native-column predicate compiles to no typed-Map population witness, so
    neither the seed's ``attrs_string`` replay cost model nor the rebuilt
    absence proof describes it. Nothing here was measured on that shape, so it
    keeps the schedule it ships with.
    """

    subject = SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[
            time_filter(start=WINDOW[0], end=WINDOW[1]),
            {
                "column_id": "model",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "a-model",
                },
            },
        ],
        bounded_internal_scan=True,
    )
    assert subject._filter_population_plans() == []
    assert subject.filter_seed_width_policy() is None
    assert subject.filter_population_discovery_width_policy() is None
    assert subject.supports_filter_seed_density_probe() is False


def test_both_budgets_open_and_floor_at_one_hour():
    """The seed's key predicate is hour-aligned, so a slice below an hour
    reads exactly the granules the whole hour reads for a fraction of the
    coverage. An hour is the narrowest width that buys anything."""

    subject = _subject(leaves=2)
    seed = subject.filter_seed_width_policy()
    discovery = subject.filter_population_discovery_width_policy()
    for policy in (seed, discovery):
        assert isinstance(policy, FilterSeedWidthPolicy)
        assert policy.min_width == timedelta(hours=1)
        assert policy.initial_width == timedelta(hours=1)
    assert subject.recommended_filter_initial_slice_width() == timedelta(hours=1)
    # Two statements, two read rates, two budgets - one mechanism.
    assert discovery.target_read_rows > seed.target_read_rows


@override_settings(
    FILTER_SELECTOR_SPAN_SEED_TARGET_READ_ROWS=123_456,
    FILTER_SELECTOR_SPAN_POPULATION_DISCOVERY_TARGET_READ_ROWS=7_000_000,
)
def test_both_budgets_are_runtime_settings():
    subject = _subject(leaves=2)
    assert subject.filter_seed_width_policy().target_read_rows == 123_456
    assert (
        subject.filter_population_discovery_width_policy().target_read_rows == 7_000_000
    )


def test_one_proposal_replaces_the_fixed_day_ladder():
    subject = _subject(leaves=2)
    start, end = subject._bounded_request_window
    assert subject.recommended_filter_population_time_discovery_windows() == (
        end - start,
    )
    assert subject.recommended_filter_population_time_discovery_window() == end - start


def test_estimate_reducer_sums_parts_and_refuses_to_read_an_empty_table():
    columns = ("database", "table", "parts", "rows", "marks")
    rows = [
        {"database": "default", "table": "spans", "parts": 2, "rows": 10, "marks": 3},
        {"database": "default", "table": "spans", "parts": 1, "rows": 5, "marks": 1},
    ]
    assert reduce_density_estimate(rows, columns, table="spans") == 15
    # An estimate naming no part is AMBIGUOUS - an empty interval and an
    # unreadable plan give the identical answer - so the reducer refuses.
    assert reduce_density_estimate([], columns, table="spans") is EMPTY_DENSITY_ESTIMATE
    assert reduce_density_estimate(rows, columns, table="other") is None
    assert reduce_density_estimate(rows, ("rows",), table="spans") is None
    assert reduce_density_estimate([{"table": "spans"}], columns, table="spans") is None


def test_span_and_trace_lanes_read_one_estimate_the_same_way():
    """The two lanes emit the same statement; the reading of an empty estimate
    is the part that must not drift between them."""

    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )

    columns = ("database", "table", "parts", "rows", "marks")
    rows = [
        {"database": "default", "table": "spans", "parts": 1, "rows": 9, "marks": 1}
    ]
    span = _subject(leaves=2)
    trace = TraceListQueryBuilderV2(
        project_id=PROJECT,
        filters=[time_filter(start=WINDOW[0], end=WINDOW[1])],
        bounded_internal_scan=True,
    )
    assert span.TABLE == trace.TABLE
    for subject in (span, trace):
        assert subject.filter_seed_density_probe_estimate(rows, columns) == 9
        assert (
            subject.filter_seed_density_probe_estimate([], columns)
            is EMPTY_DENSITY_ESTIMATE
        )
