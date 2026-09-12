"""Voice pages reach the short exact-string seed lane and its width budget."""

from datetime import datetime, timedelta

import pytest
from clickhouse_driver.errors import Error as ClickHouseError
from clickhouse_driver.errors import ErrorCodes

from tracer.selectors.filter_seed_width import FilterSeedWidthPolicy
from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
ACCOUNT_KEY = "call.account_id"
END = datetime(2026, 8, 1)
SHORT_VALUES = ["acct-1", "acct-2"]
LONG_VALUES = [
    f"https://recordings.example.invalid/{'a' * 90}/{index}" for index in range(2)
]


def attribute_filter(operation="in", values=None, key=ACCOUNT_KEY, filter_type="text"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": operation,
            "filter_value": SHORT_VALUES if values is None else values,
        },
    }


def window_filter(days):
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [END - timedelta(days=days), END],
        },
    }


def voice_builder(*, days=365, leaves=None, page_size=25, **kwargs):
    return VoiceCallListQueryBuilderV2(
        project_id=PROJECT,
        page_size=page_size,
        filters=[
            window_filter(days),
            *(leaves if leaves is not None else [attribute_filter()]),
        ],
        **kwargs,
    )


ESTIMATE_COLUMNS = ("database", "table", "parts", "rows", "marks")


class RecordingTransport:
    """An empty history that reports NO native progress and no estimate table.

    This is the conservative end of the contract and it is deliberately kept
    as its own transport: with no ``read_rows`` the selector may not widen a
    slice past the lane's unprobed cap, and with no estimate columns the lane's
    own reducer reads every probe as "unknown". Tests about widening must use
    ``ProgressTransport``; this one pins the floor.
    """

    supports_bounded_speculative_reads = False

    def __init__(self):
        self.seeds: list[tuple[str, dict]] = []
        self.probes: list[str] = []

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if query.lstrip().upper().startswith("EXPLAIN"):
            self.probes.append(query)
        elif "filter_slice_start_us" in params:
            self.seeds.append((query, dict(params)))
        return QueryResult(
            data=[], row_count=0, backend_used="clickhouse", query_time_ms=1
        )

    @property
    def slice_widths(self) -> list[timedelta]:
        return [
            params["filter_slice_end"] - params["filter_slice_start"]
            for _, params in self.seeds
        ]


class ProgressTransport(RecordingTransport):
    """The same empty history as reported by the production transport.

    ``execute_ch_query`` returns the server's own rows-read counter and the
    ``EXPLAIN ESTIMATE`` column set, so the two signals the width budget reads
    are both present: a completed statement that read zero rows, and an
    estimate table that named no part. Only together do they let an empty
    estimate be believed as a zero and a widening be approved.
    """

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        if query.lstrip().upper().startswith("EXPLAIN"):
            self.probes.append(query)
            return QueryResult(
                data=[],
                row_count=0,
                backend_used="clickhouse",
                query_time_ms=1,
                columns=list(ESTIMATE_COLUMNS),
                read_rows=0,
            )
        if "filter_slice_start_us" in params:
            self.seeds.append((query, dict(params)))
        return QueryResult(
            data=[],
            row_count=0,
            backend_used="clickhouse",
            query_time_ms=1,
            read_rows=0,
        )


def walk(builder, *, transport=None, max_seed_attempts=24, page_size=25):
    """Read one page with the PRODUCT's own budget defaults.

    ``max_query_count`` is deliberately not passed: the view does not pass it
    either, so the builder's own ``recommended_filter_max_query_count`` is what
    decides the acquisition budget and therefore the probe allowance the
    selector funds from the headroom the budget leaves. Passing 128 here - an
    earlier revision of this helper did - zeroes that allowance and hides
    whether the lane can widen at all.
    """

    transport = RecordingTransport() if transport is None else transport
    page = read_bounded_filter_page(
        builder=builder,
        analytics=transport,
        filters=builder.filters,
        key_field="trace_id",
        page_number=0,
        page_size=page_size,
        deadline_ms=30_000,
        max_seed_attempts=max_seed_attempts,
    )
    return transport, page


# ---------------------------------------------------------------------------
# Capability matrix
# ---------------------------------------------------------------------------


def test_short_text_voice_filter_reaches_the_trace_lane_hooks():
    builder = voice_builder()
    reference = TraceListQueryBuilderV2(
        project_id=PROJECT, page_size=25, filters=builder.filters
    )
    assert builder.supports_filter_candidate_seed_page()
    assert builder.supports_filter_seed_density_probe()
    assert not builder.supports_filter_anchor_probe()
    policy = builder.filter_seed_width_policy()
    assert isinstance(policy, FilterSeedWidthPolicy)
    # Voice defaults to an unbounded witness, so it opens at the unbounded
    # lane's four-hour floor rather than the trace list's bounded one hour.
    assert policy.initial_width == policy.min_width == timedelta(hours=4)
    assert builder.recommended_filter_initial_slice_width() == timedelta(hours=4)
    assert (
        builder.recommended_filter_classify_batch_size()
        == reference.recommended_filter_classify_batch_size()
    )
    assert builder.recommended_filter_max_slice_width() == timedelta(days=365)
    # The budget hook the width policy is useless without: a statement budget
    # that leaves the density probe its allowance.
    assert builder.recommended_filter_max_query_count() is None
    assert (
        builder.recommended_filter_max_query_count()
        == reference.recommended_filter_max_query_count()
    )
    # The delegate's per-statement timeout is the one answer this lane does
    # NOT take. Voice keeps the whole request wall; see the dedicated test.
    assert reference.recommended_filter_query_timeout_ms() == 9_500
    assert builder.recommended_filter_query_timeout_ms() == 30_000


@pytest.mark.parametrize(
    "leaves,expected",
    [
        ([attribute_filter()], True),
        ([attribute_filter(operation="equals", values=SHORT_VALUES[0])], True),
        # Long values keep the existing index-anchored long-text lane.
        ([attribute_filter(values=LONG_VALUES)], False),
        ([attribute_filter(operation="not_in")], False),
        (
            [
                attribute_filter(
                    operation="in", values=[SHORT_VALUES[0], LONG_VALUES[0]]
                )
            ],
            False,
        ),
        (
            [
                attribute_filter(
                    operation="greater_than", values=3, filter_type="number"
                )
            ],
            False,
        ),
        ([], False),
    ],
)
def test_only_the_short_exact_string_shape_declares_the_seed_width_budget(
    leaves, expected
):
    builder = voice_builder(leaves=leaves)
    assert (builder.filter_seed_width_policy() is not None) is expected
    assert builder.supports_filter_seed_density_probe() is expected
    assert (builder.filter_seed_witness_slack_hours() is not None) is expected


@pytest.mark.parametrize(
    "mode",
    [
        {"bounded_internal_scan": True},
        {"bounded_identity_only": True},
        {"bounded_sampling_rate": 10, "bounded_sampling_salt": "fixture"},
    ],
)
def test_internal_and_sampled_voice_consumers_keep_their_existing_plan(mode):
    builder = voice_builder(**mode)
    assert builder.filter_seed_width_policy() is None
    assert builder.filter_seed_witness_slack_hours() is None
    assert not builder.supports_filter_candidate_seed_page()


# ---------------------------------------------------------------------------
# Seed statement shape
# ---------------------------------------------------------------------------


def test_voice_short_text_seed_carries_both_the_value_witness_and_the_root():
    builder = voice_builder()
    start, end = builder.parse_time_range(builder.filters)
    query, params = builder.build_filter_candidate_seed_page(
        slice_start=end - timedelta(hours=4), slice_end=end, limit=200
    )
    candidates, roots = query.split("SELECT trace_id, id AS root_span_id", 1)
    assert "matching_scalar_trace_identities" in candidates
    # The typed value companion is what makes this seed selective at all.
    assert "mapValues(attrs_string)" in candidates
    assert "LIMIT" not in candidates
    # The conversation-root invariant is re-applied by the seed itself.
    assert "parent_span_id IS NULL OR parent_span_id = ''" in roots
    assert "conversation" in str(params.values())
    assert "ORDER BY start_time DESC, trace_id DESC" in roots
    # Slack zero emits no witness envelope at all.
    assert "filter_witness_start_us" not in params
    assert params["filter_slice_end"] == end
    assert params["filter_seed_limit"] == 200


def test_voice_seed_density_probe_estimates_from_the_index_alone():
    builder = voice_builder()
    _, end = builder.parse_time_range(builder.filters)
    query, _ = builder.build_filter_seed_density_probe_query(
        slice_start=end - timedelta(days=2), slice_end=end
    )
    assert query.lstrip().upper().startswith("EXPLAIN ESTIMATE")
    estimate_columns = ("database", "table", "parts", "rows", "marks")
    assert (
        builder.filter_seed_density_probe_estimate(
            [{"table": builder.TABLE, "rows": 41}, {"table": builder.TABLE, "rows": 1}],
            estimate_columns,
        )
        == 42
    )
    assert (
        voice_builder(
            leaves=[attribute_filter(operation="not_in")]
        ).filter_seed_density_probe_estimate([], estimate_columns)
        is None
    )


# ---------------------------------------------------------------------------
# Witness slack: voice defaults to today's unbounded contract
# ---------------------------------------------------------------------------


def test_voice_witness_slack_defaults_to_zero_and_mints_it_on_the_cursor():
    builder = voice_builder()
    assert builder.filter_seed_witness_slack_hours() == 0


def test_a_pinned_voice_witness_slack_bounds_the_seed_and_survives_a_fresh_delegate():
    builder = voice_builder()
    builder.pin_filter_seed_witness_slack_hours(3)
    assert builder.filter_seed_witness_slack_hours() == 3
    assert builder.filter_seed_width_policy().min_width == timedelta(hours=1)
    _, end = builder.parse_time_range(builder.filters)
    _, params = builder.build_filter_candidate_seed_page(
        slice_start=end - timedelta(hours=1), slice_end=end, limit=200
    )
    assert params["filter_witness_end"] == end + timedelta(hours=3)
    assert params["filter_witness_start"] == end - timedelta(hours=4)


def test_a_pinned_zero_is_a_pin_and_a_cleared_pin_returns_to_the_voice_setting(
    settings,
):
    settings.VOICE_FILTER_TEXT_SEED_WITNESS_SLACK_HOURS = 5
    builder = voice_builder()
    assert builder.filter_seed_witness_slack_hours() == 5
    builder.pin_filter_seed_witness_slack_hours(0)
    assert builder.filter_seed_witness_slack_hours() == 0
    builder.pin_filter_seed_witness_slack_hours(None)
    assert builder.filter_seed_witness_slack_hours() == 5


@pytest.mark.parametrize("hours", [-1, 169, True, 1.5, "1"])
def test_an_unusable_voice_slack_pin_is_refused_at_the_pin(hours):
    with pytest.raises(ValueError):
        voice_builder().pin_filter_seed_witness_slack_hours(hours)


# ---------------------------------------------------------------------------
# Slice widths on a twelve-month numbered page
# ---------------------------------------------------------------------------


def test_a_twelve_month_voice_page_no_longer_scans_a_fortnight_per_statement():
    """The floor holds even for a transport that reports no progress at all.

    ``RecordingTransport`` signals neither read rows nor an estimate table, so
    this is the one case where the budget may not widen: the lane opens at its
    floor and stays there. The claim is only that the fortnight-per-statement
    inflation is gone; completeness on such a transport is asserted separately.
    """

    transport, page = walk(voice_builder())
    widths = transport.slice_widths
    assert widths, "the seed lane must issue at least one statement"
    assert widths[0] == timedelta(hours=4)
    # The measured defect: all twenty-four slices were exactly 365 h wide.
    assert timedelta(hours=365) not in widths
    # A progress-less transport cannot justify a wider slice, so twenty-four
    # floor slices do not reach the start of a twelve-month window. This is the
    # shared selector's unprobed cap, and the merged trace list answers the
    # same way on the same arguments.
    assert set(widths) == {timedelta(hours=4)}
    assert page.complete is False
    assert page.error_code == "scan_budget_exceeded"


@pytest.mark.parametrize("days", [365, 30, 7])
def test_an_empty_long_window_voice_page_is_complete_and_covers_the_window(days):
    """A filter that matches nothing returns an empty COMPLETE page.

    The width budget is only sound if it reaches the start of the request
    window, and it reaches it by widening: the density probe approves each
    doubling, so an empty window is crossed logarithmically instead of in
    equal floor-width steps. Before the lane had probe headroom this page
    returned ``scan_budget_exceeded`` after twenty-four four-hour slices -
    four days of the twelve months asked for.
    """

    builder = voice_builder(days=days)
    transport, page = walk(builder, transport=ProgressTransport())
    request_start, request_end = builder.parse_time_range(builder.filters)
    assert page.complete is True
    assert page.error_code is None
    assert page.rows == []
    assert sum(transport.slice_widths, timedelta()) == request_end - request_start
    assert max(transport.slice_widths) > timedelta(hours=4)
    # Crossed by doubling, not in equal floor-width steps: the window is
    # covered inside the twenty-four-attempt cap with budget left over.
    assert len(transport.slice_widths) < 24


def test_the_voice_lane_widens_only_on_an_approving_probe():
    """Every slice wider than the unprobed cap is bought by a probe.

    The cap is the widest slice the lane may issue on the previous statement's
    read rows alone; past it the selector must first ask the index what the
    candidate slice holds. So a widening is not merely correlated with a probe,
    it is caused by one, and the probe's own recorded answer is the number the
    width was chosen from.
    """

    transport, page = walk(voice_builder(), transport=ProgressTransport())
    widths = transport.slice_widths
    assert transport.probes, "the lane must be able to issue a density probe"
    assert [round(w / timedelta(hours=1)) for w in widths[:5]] == [4, 8, 16, 32, 64]
    probes = [a for a in page.attempts if a.kind == "seed_density_probe"]
    assert len(probes) == len(transport.probes)
    # An empty estimate corroborated by a completed zero-row statement reads as
    # a zero, which is inside the row budget and approves the proposal.
    assert {attempt.probe_rows for attempt in probes} == {0}
    # One probe buys one widening: as many probes as widened slices.
    assert len(probes) == sum(1 for w in widths if w > timedelta(hours=4))


class EnforcingProgressTransport(ProgressTransport):
    """A transport that honours ``timeout_ms`` as a statement deadline.

    The production voice transport does not - ``AnalyticsQueryService`` calls
    the client with ``timeout_ms=None`` and ``application_read_settings``
    zeroes ``max_execution_time`` - so this is the pessimistic deployment: the
    designated seed statement needs twelve seconds and is aborted with
    ClickHouse ``TIMEOUT_EXCEEDED`` when its statement timeout is smaller.
    """

    def __init__(self, *, slow_seed_index=1, slow_ms=12_000):
        super().__init__()
        self.slow_seed_index = slow_seed_index
        self.slow_ms = slow_ms

    def execute_ch_query(self, query, params, *, timeout_ms, settings):
        is_seed = not query.lstrip().upper().startswith("EXPLAIN") and (
            "filter_slice_start_us" in params
        )
        if (
            is_seed
            and len(self.seeds) == self.slow_seed_index
            and timeout_ms is not None
            and timeout_ms < self.slow_ms
        ):
            self.seeds.append((query, dict(params)))
            error = ClickHouseError("statement timeout")
            error.code = ErrorCodes.TIMEOUT_EXCEEDED
            raise error
        return super().execute_ch_query(
            query, params, timeout_ms=timeout_ms, settings=settings
        )


def test_the_voice_seed_lane_keeps_the_whole_request_wall_per_statement():
    """The lane forwards the delegate's budget but NOT its 9.5 s timeout.

    An earlier revision of this lane forwarded
    ``recommended_filter_query_timeout_ms`` too, on the rationale that a
    per-statement share makes the selector's halve-and-retry recovery
    reachable. It does not: that recovery is gated on
    ``retry_wide_read_budget``, which the voice list view never passes. On
    this endpoint's transport the number is discarded outright, so it buys
    nothing; where a transport does enforce it, a seed statement that would
    have finished inside the request wall is aborted instead and the view
    answers 503. This test pins the cheaper answer by exercising the
    expensive transport: twelve seconds on one seed must still produce the
    complete page.
    """

    builder = voice_builder()
    # Not a share of the wall - the wall, which is what every other voice
    # filtered read already answers.
    assert builder.recommended_filter_query_timeout_ms() == 30_000
    assert (
        builder.recommended_filter_query_timeout_ms()
        == voice_builder(
            leaves=[attribute_filter(operation="not_in")]
        ).recommended_filter_query_timeout_ms()
    )

    transport, page = walk(builder, transport=EnforcingProgressTransport())
    request_start, request_end = builder.parse_time_range(builder.filters)
    assert page.complete is True
    assert page.error_code is None
    assert [a.error_code for a in page.attempts] == [None] * len(page.attempts)
    assert sum(transport.slice_widths, timedelta()) == request_end - request_start


@pytest.mark.parametrize(
    ("leaves", "inherited_max_query_count"),
    [
        ([attribute_filter(values=LONG_VALUES)], 128),
        ([attribute_filter(operation="not_in")], None),
        (
            [
                attribute_filter(
                    operation="greater_than", values=3, filter_type="number"
                )
            ],
            128,
        ),
        ([], None),
    ],
)
def test_voice_shapes_off_the_lane_keep_their_statement_budget_and_wall(
    leaves, inherited_max_query_count
):
    """Only the lane shape takes the trace list's statement budget.

    That hook is a reservation every voice filtered read depends on, so the
    change is scoped to the shape that declared the width policy: off the lane
    the inherited answer is returned unchanged, whether that is the
    candidate-witness delegate's whole-contract reservation or no answer at
    all. The per-statement wall is unchanged everywhere, on the lane included,
    which is why every row here expects the same 30 000 ms.
    """

    builder = voice_builder(leaves=leaves)
    assert builder.filter_seed_width_policy() is None
    assert builder.recommended_filter_max_query_count() == inherited_max_query_count
    assert builder.recommended_filter_query_timeout_ms() == 30_000


def test_a_voice_shape_off_the_seed_lane_keeps_its_numbered_inflation_exactly():
    """Shapes without the lane are untouched - deliberately, not by omission.

    The numbered branch has no cursor to resume from, so it still inflates each
    slice until the whole remaining window is scheduled inside the attempt
    budget: a fortnight per statement on a twelve-month request. Capping that
    is a coverage trade, not a free win - twenty-four two-day slices cover 48
    of 365 days, which turns an empty twelve-month voice page from a complete
    empty result into ``scan_budget_exceeded`` - so it is left for the owner.
    """

    builder = voice_builder(leaves=[attribute_filter(operation="not_in")])
    assert builder.filter_seed_width_policy() is None
    transport, _ = walk(builder)
    assert set(transport.slice_widths) == {timedelta(hours=365)}
