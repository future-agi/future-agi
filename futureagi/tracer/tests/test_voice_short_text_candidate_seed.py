"""Voice pages reach the short exact-string seed lane and its width budget."""

from datetime import datetime, timedelta

import pytest

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


class RecordingTransport:
    """Return nothing for every statement and record the slices requested."""

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


def walk(builder, *, max_seed_attempts=24, page_size=25):
    transport = RecordingTransport()
    page = read_bounded_filter_page(
        builder=builder,
        analytics=transport,
        filters=builder.filters,
        key_field="trace_id",
        page_number=0,
        page_size=page_size,
        deadline_ms=30_000,
        max_query_count=128,
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
    transport, _ = walk(voice_builder())
    widths = transport.slice_widths
    assert widths, "the seed lane must issue at least one statement"
    assert widths[0] == timedelta(hours=4)
    # The measured defect: all twenty-four slices were exactly 365 h wide.
    assert timedelta(hours=365) not in widths


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
