"""Voice long-text reads must use exact acquisition without speculative caps."""

from datetime import datetime, timedelta

import pytest

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders._rewrite import _rewrite_sql_in
from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)
from tracer.tests.test_trace_root_physical_replay import (
    complete_root_row,
)

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
END = datetime(2026, 8, 1)
VALUES = [
    f"https://recordings.example.invalid/{'a' * 90}/{index}" for index in range(3)
]


def voice_builder(*, operation="in", values=None, days=365, **kwargs):
    return VoiceCallListQueryBuilderV2(
        project_id=PROJECT,
        page_size=25,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [END - timedelta(days=days), END],
                },
            },
            {
                "column_id": "call.recording.url",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": operation,
                    "filter_value": VALUES if values is None else values,
                },
            },
        ],
        **kwargs,
    )


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("has_match", [False, True])
def test_voice_long_text_acquisition_runs_with_uncapped_application_reads(
    days, has_match
):
    builder = voice_builder(days=days)
    assert builder.supports_filter_candidate_seed_page()
    start, end = builder.parse_time_range(builder.filters)
    row = complete_root_row(
        {
            "trace_id": "matching-call",
            "root_span_id": "root",
            "start_time": start + timedelta(days=1),
            "_root_observation_type": "conversation",
        },
        project_id=PROJECT,
    )
    calls = []

    class ApplicationTransport:
        supports_bounded_speculative_reads = False

        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            calls.append(query)
            if len(calls) == 1:
                assert "matching_scalar_trace_identities" in query
                assert "indexHint(arrayStringConcat" in query
                assert params["filter_slice_start"] == start
                assert params["filter_slice_end"] == end
            elif len(calls) == 2:
                # Acquisition changes; the existing global voice classifier
                # remains the authority for canonical-root and value state.
                expected, expected_params = (
                    builder._bounded_delegate().build_filter_identity_match_query_from_seed_rows(
                        [row]
                    )
                )
                assert (query, params) == _rewrite_sql_in((expected, expected_params))
                assert params["candidate_trace_ids"] == ("matching-call",)
                assert "conversation" in str(params.values())
            else:
                assert len(calls) == 3
                assert "page_hydration_root_identities" in params
            assert query.count("SETTINGS ") == 1
            assert "span_attr_str" not in query
            rows = [row] if has_match else []
            return QueryResult(
                data=rows,
                row_count=len(rows),
                backend_used="clickhouse",
                query_time_ms=1,
            )

    page = read_bounded_filter_page(
        builder=builder,
        analytics=ApplicationTransport(),
        filters=builder.filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=30_000,
        max_query_count=8,
        max_seed_attempts=1,
        include_incomplete_rows=True,
        bounded_continuation=True,
    )
    assert page.complete and not page.has_more
    assert page.rows == ([row] if has_match else [])
    assert len(calls) == (3 if has_match else 1)


@pytest.mark.parametrize(
    "operation", ["equals", "in", "contains", "starts_with", "ends_with"]
)
def test_voice_candidate_keeps_complete_child_history_and_outer_keyset(operation):
    builder = voice_builder(
        operation=operation, values=VALUES if operation == "in" else VALUES[0]
    )
    start, end = builder.parse_time_range(builder.filters)
    query, params = builder.build_filter_candidate_seed_page(
        slice_start=start,
        slice_end=end,
        limit=25,
        before_start_time=end - timedelta(days=1),
        before_id="previous-call",
    )
    candidates, roots = query.split("SELECT trace_id, id AS root_span_id", 1)
    assert "matching_scalar_trace_identities" in candidates
    assert "indexHint(arrayStringConcat" in candidates
    assert "attrs_string[" in candidates
    assert "LIMIT" not in candidates
    assert "is_deleted" not in candidates
    assert "filter_before" not in candidates
    assert candidates.count("start_time >=") == 1  # Raw root interval only.
    assert candidates.count("start_time <") == 1
    assert "parent_span_id IS NULL OR parent_span_id = ''" in candidates
    assert "filter_before" in roots
    assert "ORDER BY start_time DESC, trace_id DESC" in roots
    assert "LIMIT %(filter_seed_limit)s" in roots
    assert "conversation" in str(params.values())
    assert params["project_id"] == PROJECT
    assert params["filter_seed_limit"] == 25
    assert all(value not in query for value in VALUES)


@pytest.mark.parametrize(
    "operation,values",
    [
        ("in", [VALUES[0], "short"]),
        ("in", [VALUES[0], "K" * 100]),
        ("not_in", VALUES),
        ("not_equals", VALUES[0]),
        ("not_contains", VALUES[0]),
    ],
)
def test_voice_filters_without_exhaustive_long_text_witness_keep_existing_plan(
    operation, values
):
    builder = voice_builder(operation=operation, values=values)
    assert not builder.supports_filter_candidate_seed_page()


@pytest.mark.parametrize(
    "mode",
    [
        {"bounded_internal_scan": True},
        {"bounded_identity_only": True},
        {"bounded_sampling_rate": 10, "bounded_sampling_salt": "fixture"},
    ],
)
def test_voice_internal_and_sampled_consumers_keep_existing_plan(mode):
    assert not voice_builder(**mode).supports_filter_candidate_seed_page()
