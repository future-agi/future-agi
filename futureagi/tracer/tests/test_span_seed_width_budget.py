"""The span list's two row budgets, and the shape that makes them cheap.

Both of this lane's wide statements - the candidate seed and the absence proof
that tells it where to look - scan the raw span population of an interval, so
both are budgeted in ROWS and both are costed by the same ``EXPLAIN ESTIMATE``.
The tests here pin the properties that make that safe:

* neither the probe nor the proof reads a Map VALUE: the filter's value
  comparison reaches them only inside ``indexHint``, where it prunes granules
  and decompresses nothing, and what remains at row level is the thin ``.keys``
  stream the population witness names (``test_span_probe_index_witness`` owns
  that witness definition; these tests only require that this statement carry
  it rather than something wider);
* the probe is costed against THIS lane's population, never the plain time
  range, for every leaf shape - including one whose type carries no value
  index at all; and
* neither decides membership: the seed re-applies every leaf and the exact
  latest-state classifier alone publishes a row.

The absence proof's wall-clock ladder is NOT replaced by the row budget. The
ladder remains the outer contract each proof validates itself against and the
budget only ever narrows a rung, so the two bounds compose: the ladder bounds
the interval, the budget bounds the rows inside it.
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
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_v1_sql_to_v2
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.tests.test_span_physical_identity_latest import (
    PROJECT,
    START,
    time_filter,
)
from tracer.tests.test_span_population_time_discovery import (
    PopulationBuilder,
    PopulationExecutor,
)
from tracer.tests.test_span_population_time_discovery import run as _run_population
from tracer.tests.test_span_probe_index_witness import without_index_hints
from tracer.tests.test_trace_root_time_discovery import END as PROBE_END

WINDOW = (START - timedelta(days=30), START + timedelta(days=1))

# Every way a statement can decompress a typed Map VALUE stream. ``indexHint``
# may name any of them, because its argument is used for index analysis and is
# true for every row of a surviving granule; outside one, each of these reads
# the column this lane must not pay for in a probe or a proof.
#
# ``<map>.keys`` is deliberately NOT in this list. It is the thin key stream
# the population witness evaluates at row level, which is what makes the proof
# a tighter necessary condition than a granule hint alone, and it is the
# contract ``test_span_probe_index_witness`` pins.
_ROW_LEVEL_MAP_VALUE_READS = (
    "attrs_string[",
    "attrs_number[",
    "attrs_bool[",
    "mapContains(",
    "mapValues(",
    "mapKeys(",
    "lowerUTF8(",
)


def _subject(*, leaves=1, op="equals", value="a", filter_type="text"):
    filters = [time_filter(start=WINDOW[0], end=WINDOW[1])]
    filters.append(
        {
            "column_id": "attr_0",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": filter_type,
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


def _micros(moment):
    from tracer.services.clickhouse.query_builders.span_list import (
        _unix_microseconds,
    )

    return _unix_microseconds(moment)


@pytest.mark.unit
def test_index_hint_stripper_is_itself_exercised():
    """A scan that cannot fire proves nothing, so seed it with a known hit."""

    assert "mapValues(" in without_index_hints("x AND mapValues(attrs_string) AND y")
    assert "mapValues(" not in without_index_hints(
        "x AND indexHint(has(mapValues(a), b)) AND y"
    )


@pytest.mark.unit
@pytest.mark.parametrize("leaves", [1, 2, 5, 10])
def test_density_probe_compares_no_map_value_outside_an_index_hint(leaves):
    subject = _subject(leaves=leaves)
    sql, params = subject.build_filter_seed_density_probe_query(
        slice_start=WINDOW[1] - timedelta(hours=8), slice_end=WINDOW[1]
    )
    assert sql.lstrip().startswith("EXPLAIN ESTIMATE")
    outside = without_index_hints(sql)
    for forbidden in _ROW_LEVEL_MAP_VALUE_READS:
        assert forbidden not in outside, forbidden
    # The conjunction IS carried - costing against the plain time range would
    # answer for a population this lane never reads - and it is carried as the
    # population witness itself, key presence included, not a second spelling.
    assert sql.count("indexHint(") >= leaves
    assert outside.count("has(attrs_string.keys, ") == leaves
    for plan in subject._filter_population_plans():
        # The compiler emits legacy column tokens that only reach CH25 names at
        # the rewrite boundary, so compare the witness through that same
        # rewrite rather than against the raw compiled text. A presence leaf
        # has no value index and therefore no index companion; its KEY witness
        # is what the probe must carry.
        witness = plan.raw_index_witness_predicate or plan.raw_key_witness_predicate
        assert rewrite_v1_sql_to_v2(witness) in sql
    assert "FINAL" not in sql and "SAMPLE" not in sql and "LIMIT" not in sql
    assert params["seed_density_start_us"] < params["seed_density_end_us"]


@pytest.mark.unit
@pytest.mark.parametrize("leaves", [1, 2, 5, 10])
def test_population_proof_compares_no_map_value_outside_an_index_hint(leaves):
    subject = _subject(leaves=leaves)
    sql, _ = subject.build_filter_population_time_discovery_query(
        slice_start=WINDOW[1] - timedelta(days=1), slice_end=WINDOW[1]
    )
    outside = without_index_hints(sql)
    for forbidden in _ROW_LEVEL_MAP_VALUE_READS:
        assert forbidden not in outside, forbidden
    assert outside.count("has(attrs_string.keys, ") == leaves
    assert "maxOrNull" in sql
    assert "FINAL" not in sql and "SAMPLE" not in sql and "LIMIT" not in sql


@pytest.mark.unit
def test_a_leaf_without_a_value_index_is_still_costed_against_its_population():
    """A Boolean Map value carries no value index, so its plan publishes no
    index companion. The probe must fall back to that plan's KEY witness
    rather than dropping the conjunct: costing against the plain time range
    would answer for a population this lane never reads and would shrink every
    interval by the ratio of the two."""

    subject = _subject(filter_type="boolean", value=True)
    (plan,) = subject._filter_population_plans()
    assert plan.raw_index_witness_predicate is None
    sql, _ = subject.build_filter_seed_density_probe_query(
        slice_start=WINDOW[1] - timedelta(hours=8), slice_end=WINDOW[1]
    )
    assert rewrite_v1_sql_to_v2(plan.raw_key_witness_predicate) in sql
    outside = without_index_hints(sql)
    for forbidden in _ROW_LEVEL_MAP_VALUE_READS:
        assert forbidden not in outside, forbidden
    assert "has(attrs_bool.keys, " in outside


@pytest.mark.unit
def test_probe_time_predicate_stays_on_the_raw_column():
    """Spelled as the hour the optimizer could answer from a PROJECTION, whose
    aggregate rows are orders of magnitude below the interval's - an estimate
    far inside the budget, and the widest possible interval approved."""

    sql, _ = _subject(leaves=2).build_filter_seed_density_probe_query(
        slice_start=WINDOW[1] - timedelta(hours=8), slice_end=WINDOW[1]
    )
    assert "toStartOfHour" not in sql
    assert re.search(r"\bstart_time >= fromUnixTimestamp64Micro", sql)


@pytest.mark.unit
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


@pytest.mark.unit
def test_probe_refuses_an_interval_outside_the_request():
    subject = _subject()
    with pytest.raises(ValueError):
        subject.build_filter_seed_density_probe_query(
            slice_start=WINDOW[0] - timedelta(hours=1), slice_end=WINDOW[1]
        )


@pytest.mark.unit
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


@pytest.mark.unit
def test_a_native_column_list_declares_no_budget_either():
    """The budget is declared for exactly the shapes it was measured on.

    A native-column predicate compiles to no typed-Map population witness, so
    neither the seed's ``attrs_string`` replay cost model nor the absence
    proof's witness describes it. Nothing here was measured on that shape, so
    it keeps the schedule it ships with.
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


@pytest.mark.unit
def test_a_score_relation_lane_keeps_its_full_window_slice():
    """A candidate-seed (Score relation) lane declares no row budget even when
    it also carries an attribute leaf.

    That lane does not issue the raw seed this budget models; it acquires
    through a live Score relation and asks for the WHOLE request window as one
    slice on purpose. An hour-floored budget would replace that one statement
    with a slice per hour.
    """

    filters = [
        time_filter(start=WINDOW[0], end=WINDOW[1]),
        {
            "column_id": "quality",
            "filter_config": {
                "col_type": "ANNOTATION",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "good",
            },
        },
        {
            "column_id": "attr_0",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "a",
            },
        },
    ]
    subject = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=filters, bounded_internal_scan=True
    )
    start, end = subject._bounded_request_window
    assert subject.supports_filter_candidate_seed_page() is True
    assert subject._filter_population_plans()
    assert subject.filter_seed_width_policy() is None
    assert subject.filter_population_discovery_width_policy() is None
    assert subject.supports_filter_seed_density_probe() is False
    assert subject.recommended_filter_initial_slice_width() == end - start
    assert subject.recommended_filter_max_slice_width() == end - start


@pytest.mark.unit
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


@pytest.mark.unit
def test_a_request_narrower_than_the_selector_minimum_names_no_width():
    """The selector clips its OWN five-minute default to a shorter request but
    rejects an explicit recommendation below it, so an hour-floored policy on
    a three-minute window must name nothing rather than name a width the
    bounded contract refuses."""

    end = WINDOW[1]
    subject = SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[
            time_filter(start=end - timedelta(minutes=3), end=end),
            {
                "column_id": "attr_0",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "a",
                },
            },
        ],
        bounded_internal_scan=True,
    )
    assert subject.filter_seed_width_policy() is not None
    assert subject.recommended_filter_initial_slice_width() is None


@override_settings(
    FILTER_SELECTOR_SPAN_SEED_TARGET_READ_ROWS=123_456,
    FILTER_SELECTOR_SPAN_POPULATION_DISCOVERY_TARGET_READ_ROWS=7_000_000,
)
@pytest.mark.unit
def test_both_budgets_are_runtime_settings():
    subject = _subject(leaves=2)
    assert subject.filter_seed_width_policy().target_read_rows == 123_456
    assert (
        subject.filter_population_discovery_width_policy().target_read_rows == 7_000_000
    )


@pytest.mark.unit
def test_the_row_budget_narrows_the_proof_ladder_it_does_not_replace_it():
    """Two bounds compose on the absence proof, and the row budget is the
    inner one.

    The ladder still decides how wide a proof may be proposed - a text lane at
    the daily rung, other witness lanes widening 7d -> 28d -> request - and
    the row budget refuses the rung whose interval the primary index costs
    above the budget. A proof this lane cannot cost falls back to a DAY - the
    narrowest rung this lane ships, rather than the policy's four-hour default.
    That is a fallback, not a promise that nothing narrows: a non-text lane
    whose proof cannot be costed does get a day where the ladder proposed 7 or
    28, which costs statements and never history.
    """

    subject = _subject(leaves=2)
    start, end = subject._bounded_request_window
    assert subject.recommended_filter_population_time_discovery_windows() == (
        timedelta(days=1),
    )
    without_text = SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[
            time_filter(start=WINDOW[0], end=WINDOW[1]),
            {
                "column_id": "attr_0",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "number",
                    "filter_op": "equals",
                    "filter_value": 7,
                },
            },
        ],
        bounded_internal_scan=True,
    )
    assert without_text.recommended_filter_population_time_discovery_windows() == (
        timedelta(days=7),
        timedelta(days=28),
        end - start,
    )
    policy = without_text.filter_population_discovery_width_policy()
    assert policy.unprobed_cap == timedelta(days=1)
    assert policy.requires_density_probe(timedelta(days=28))
    # A 28-day rung costed at ten times the budget is issued at a fitted
    # width, not at the rung, and never below the floor.
    fitted = policy.probed_width(timedelta(days=28), policy.target_read_rows * 10)
    assert policy.min_width <= fitted < timedelta(days=28)
    # Inside the budget the rung stands: the ladder, not the budget, is what
    # bounds a proof whose interval is affordable.
    assert policy.probed_width(
        timedelta(days=28), policy.target_read_rows
    ) == timedelta(days=28)


class _BudgetedProofBuilder(PopulationBuilder):
    """A proof lane carrying BOTH bounds: the ladder and the row budget."""

    TABLE = "spans"
    rows_per_hour = 1
    discovery_target_read_rows = 10_000_000

    def recommended_filter_population_time_discovery_window(self):
        return self.end - self.start

    def recommended_filter_population_time_discovery_windows(self):
        width = self.end - self.start
        return (min(width, timedelta(days=7)), min(width, timedelta(days=28)), width)

    def filter_seed_width_policy(self):
        return FilterSeedWidthPolicy(
            initial_width=timedelta(hours=1),
            min_width=timedelta(hours=1),
            target_read_rows=500_000,
        )

    def filter_population_discovery_width_policy(self):
        return FilterSeedWidthPolicy(
            initial_width=timedelta(hours=1),
            min_width=timedelta(hours=1),
            target_read_rows=self.discovery_target_read_rows,
            unsignalled_cap=timedelta(days=1),
        )

    def supports_filter_seed_density_probe(self):
        return True

    def build_filter_seed_density_probe_query(self, *, slice_start, slice_end):
        return "density_probe", {"slice_start": slice_start, "slice_end": slice_end}

    def filter_seed_density_probe_estimate(self, rows, columns=None):
        return reduce_density_estimate(rows, columns, table=self.TABLE)


class _BudgetedProofExecutor(PopulationExecutor):
    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if query != "density_probe":
            return super().execute_ch_query(
                query, params, timeout_ms=timeout_ms, settings=settings
            )
        self.calls.append((query, params))
        hours = (params["slice_end"] - params["slice_start"]) // timedelta(hours=1)
        return QueryResult(
            [
                {
                    "database": "default",
                    "table": "spans",
                    "parts": 1,
                    "rows": hours * self.builder.rows_per_hour,
                    "marks": 1,
                }
            ],
            1,
            "clickhouse",
            0.1,
            columns=["database", "table", "parts", "rows", "marks"],
            read_rows=1,
        )


def _issued_proof_widths(*, discovery_target_read_rows):
    subject = _BudgetedProofBuilder(
        [{"id": "span", "start_time": PROBE_END - timedelta(days=2)}],
        start=PROBE_END - timedelta(days=30),
        end=PROBE_END,
        recommended_seed_batch_size=4,
    )
    subject.rows_per_hour = 100
    subject.discovery_target_read_rows = discovery_target_read_rows
    executor = _BudgetedProofExecutor(subject)
    result = _run_population(subject, executor)
    assert [row["id"] for row in result.rows] == ["span"]
    proofs = [params for query, params in executor.calls if query == "population_probe"]
    assert proofs
    return result, [params["slice_end"] - params["slice_start"] for params in proofs]


@pytest.mark.unit
def test_a_proof_inside_the_budget_is_issued_at_the_ladder_rung():
    """The budget is the inner bound: when the interval is affordable the
    ladder decides, and the proof still widens past a day the way it does
    today."""

    result, issued = _issued_proof_widths(discovery_target_read_rows=10_000_000)
    assert max(issued) > timedelta(days=1)
    assert max(issued) <= timedelta(days=30)
    # Every width that needed a costing question got one, and the estimate it
    # was chosen from is recorded on the statement that bought it.
    probes = [a for a in result.attempts if a.kind == "seed_density_probe"]
    assert probes and all(a.probe_rows is not None for a in probes)


@pytest.mark.unit
def test_a_proof_over_the_budget_is_narrowed_below_the_rung_it_proposed():
    """The same ladder, the same statements, an interval the index costs above
    the budget: the proof is issued at a fitted width instead of the rung.
    Intervals stay contiguous, so the remainder is the next proof's work and
    no history is skipped - the published page is identical."""

    wide, generous = _issued_proof_widths(discovery_target_read_rows=10_000_000)
    narrow, tight = _issued_proof_widths(discovery_target_read_rows=2_400)
    assert max(tight) < max(generous)
    assert max(tight) <= timedelta(days=1)
    # THE PAGE IS UNCHANGED. Only the width of an acquisition-sizing proof
    # moved; the same rows are published in the same order.
    assert narrow.rows == wide.rows


@pytest.mark.unit
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


@pytest.mark.unit
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
