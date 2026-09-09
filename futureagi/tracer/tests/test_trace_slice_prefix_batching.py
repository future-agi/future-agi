"""Ordered-slice batching must change acquisition cost, never public membership."""

from datetime import timedelta

import pytest

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.tests.test_bounded_trace_filter_reads import (
    END,
    _CursorPageFillIdentityHydrationFakeBuilder,
    _FakeBuilder,
    _IdentityHydrationFakeExecutor,
    _time_filter,
)


class SliceBuilder(_CursorPageFillIdentityHydrationFakeBuilder):
    build_filter_ordered_seed_page = _FakeBuilder.build_filter_seed_page

    @staticmethod
    def recommended_filter_cursor_seed_batch_size():
        return 200

    @staticmethod
    def recommended_filter_initial_slice_width():
        return timedelta(hours=1)

    recommended_filter_max_slice_width = recommended_filter_initial_slice_width


class TraceTransport(_IdentityHydrationFakeExecutor):
    def execute_ch_query(self, *args, **kwargs):
        result = super().execute_ch_query(*args, **kwargs)
        for row in result.data:
            row["trace_id"] = row["id"]
        return result


def fixture(days, rejected):
    rows = [
        {
            "id": f"trace-{index:04d}",
            "trace_id": f"trace-{index:04d}",
            "root_span_id": f"root-{index:04d}",
            "start_time": END
            - timedelta(minutes=20 if index < rejected else 80, microseconds=index),
        }
        for index in range(rejected + 200)
    ]
    return SliceBuilder(
        rows=rows,
        match_rows=rows[rejected:],
        start=END - timedelta(days=days),
        end=END,
        key_field="trace_id",
        recommended_batch_size=200,
    )


def page(builder, transport, **kwargs):
    return read_bounded_filter_page(
        builder=builder,
        analytics=transport,
        filters=[_time_filter(builder.start, builder.end)],
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=30_000,
        bounded_continuation=True,
        include_incomplete_rows=True,
        **kwargs,
    )


def classifier_sizes(transport):
    return [
        len(params["candidate_ids"])
        for query, params in transport.calls
        if query == "match_identity"
    ]


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("rejected", [0, 50, 238, 438])
def test_new_dense_slice_closes_with50_after_sparse_tail(days, rejected):
    builder = fixture(days, rejected)
    transport = TraceTransport(builder)
    result = page(builder, transport)
    assert result.complete and result.has_more
    assert result.rows == builder.match_rows[:25]
    assert classifier_sizes(transport)[-1] == 50
    if rejected >= 200:
        # Split only the first full batch of an interval; repeated sparse
        # batches in that same interval retain the original 200-ID sizing.
        assert classifier_sizes(transport)[:2] == [50, 150]
    if rejected == 438:
        assert classifier_sizes(transport) == [50, 150, 200, 38, 50]


def test_slice_prefix_pages_do_not_skip_unclassified_suffix():
    builder = fixture(7, 438)
    # This fake deliberately has no empty-time discovery; keep its final
    # exhaustion proof finite, separate from the long-window prefix cases.
    builder.start = END - timedelta(hours=2)
    seen, cursor = [], {}
    for _ in range(8):
        result = page(builder, TraceTransport(builder), **cursor)
        assert result.complete
        seen.extend(result.rows)
        cursor = {
            "cursor_start_time": result.rows[-1]["start_time"],
            "cursor_order_token": result.rows[-1]["trace_id"],
        }
    assert seen == builder.match_rows
    assert len({row["trace_id"] for row in seen}) == 200


def test_slice_prefix_corrected_order_requires_remaining_classification():
    builder = fixture(7, 0)
    builder.match_rows = [dict(row) for row in builder.match_rows]
    for row in builder.match_rows[:50]:
        row["start_time"] -= timedelta(minutes=30)
    transport = TraceTransport(builder)
    result = page(builder, transport)
    expected = sorted(
        builder.match_rows,
        key=lambda row: (row["start_time"], row["trace_id"]),
        reverse=True,
    )
    assert result.complete and result.has_more and result.rows == expected[:25]
    assert classifier_sizes(transport) == [50, 150]


@pytest.mark.parametrize("corrected", [False, True])
def test_failed_slice_remainder_replays_unpublished_matches(corrected):
    builder = fixture(7, 0)
    builder.match_rows = [dict(row) for row in builder.match_rows]
    if corrected:
        for row in builder.match_rows[:50]:
            row["start_time"] -= timedelta(minutes=30)
    else:
        builder.match_rows = builder.match_rows[40:]

    class FailRemainder(TraceTransport):
        def execute_ch_query(self, query, params, **kwargs):
            if query == "match_identity" and classifier_sizes(self):
                raise ReadDeadlineExceeded("local fixture remainder failure")
            return super().execute_ch_query(query, params, **kwargs)

    failed = page(builder, FailRemainder(builder))
    assert not failed.complete and not failed.rows
    assert failed.error_code == "read_budget_exceeded"
    resumed = page(
        builder,
        TraceTransport(builder),
        continuation_slice_start=failed.continuation_slice_start,
        continuation_slice_end=failed.continuation_slice_end,
        continuation_before_start_time=failed.continuation_before_start_time,
        continuation_before_id=failed.continuation_before_id,
    )
    fresh = page(builder, TraceTransport(builder))
    assert resumed.complete and resumed.rows == fresh.rows and resumed.has_more


def test_empty_slice_prefix_still_classifies_every_candidate():
    builder = fixture(7, 438)
    builder.start = END - timedelta(hours=2)
    builder.match_rows = []
    transport = TraceTransport(builder)
    result = page(builder, transport)
    classified = [
        candidate
        for query, params in transport.calls
        if query == "match_identity"
        for candidate in params["candidate_ids"]
    ]
    assert result.complete and not result.rows and not result.has_more
    assert classified == [row["id"] for row in builder.rows]


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("application_policy", [True, False])
@pytest.mark.parametrize("dense_minutes", [140, 260])
@pytest.mark.parametrize(
    "operation,value",
    [
        ("in", ["company"]),
        ("not_in", ["absent"]),
        ("contains", "comp"),
        ("not_contains", "absent"),
    ],
)
def test_native_slice_prefix_rejects_tombstones_then_replays_dense_prefix(
    days, application_policy, dense_minutes, operation, value
):
    from tracer.services.clickhouse.v2.query_builders.trace_list import (
        TraceListQueryBuilderV2,
    )
    from tracer.tests.test_trace_root_physical_replay import (
        NOW,
        PROJECT,
        InlineAnalytics,
        execute,
        make_builder,
        physical_row,
    )

    engine = pytest.importorskip("chdb")
    filters = make_builder(days=days).filters + [
        {
            "column_id": "company_id",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": operation,
                "filter_value": value,
                **(
                    {"attribute_value_types": ["string"]}
                    if isinstance(value, list)
                    else {}
                ),
            },
        }
    ]
    builder = TraceListQueryBuilderV2(project_id=PROJECT, filters=filters, page_size=25)
    assert builder.recommended_filter_classify_batch_size() == 200
    assert builder.recommended_filter_cursor_seed_batch_size() == 200
    rows = []
    for index in range(488):
        row = physical_row(
            trace_id=f"trace-{index:04d}",
            start_time=NOW
            - timedelta(
                minutes=20 if index < 50 else 80 if index < 288 else dense_minutes,
                microseconds=index,
            ),
        )
        rows.append(row)
        if index < 288:
            rows.append({**row, "_version": 3, "is_deleted": 1})

    class NativeTransport(InlineAnalytics):
        supports_bounded_speculative_reads = not application_policy

    # Generate the same 776 physical versions without repeating a large VALUES
    # literal in every expanded query CTE. This changes only fixture encoding.
    row_source = f"""(
        SELECT toUUID('{PROJECT}') AS project_id, 'SPAN' AS observation_type,
            'svc' AS service_name,
            concat('trace-', leftPad(toString(number), 4, '0')) AS trace_id,
            'root' AS id,
            toDateTime64('{NOW.replace(tzinfo=None).isoformat(sep=" ")}', 6, 'UTC')
                - toIntervalMinute(multiIf(number < 50, 20, number < 288, 80, {dense_minutes}))
                - toIntervalMicrosecond(number) AS start_time,
            version AS _version, toUInt8(version = 3) AS is_deleted,
            '' AS parent_span_id, CAST('new-input', 'Nullable(String)') AS input,
            CAST(NULL, 'Nullable(String)') AS output,
            CAST(NULL, 'Nullable(UUID)') AS project_version_id, 'new-name' AS name
        FROM numbers(488)
        ARRAY JOIN if(number < 288, [toUInt64(2), toUInt64(3)], [toUInt64(2)]) AS version
    )"""
    physical = execute(
        engine,
        builder,
        rows,
        ("SELECT " + ", ".join(rows[0]) + " FROM spans", {}),
        row_source=row_source,
    )
    for row in physical:
        row["_version"] = int(row["_version"])
    def identity(row):
        return row["trace_id"], row["_version"]

    assert sorted(physical, key=identity) == sorted(rows, key=identity)
    transport = NativeTransport(engine, builder, rows, row_source=row_source)
    result = read_bounded_filter_page(
        builder=builder,
        analytics=transport,
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=30_000,
        bounded_continuation=True,
        include_incomplete_rows=True,
        root_time_discovery=True,
        carry_continuation_slice_width=True,
        # Bind the initial one-hour acquisition interval explicitly so these
        # cases test the same slice boundary across positive/negative operators.
        # The request and exact child-history replay still cover all `days`.
        continuation_slice_start=(NOW - timedelta(hours=1)).replace(tzinfo=None),
        continuation_slice_end=NOW.replace(tzinfo=None),
    )
    assert result.complete and result.has_more
    assert [row["trace_id"] for row in result.rows] == [
        f"trace-{index:04d}" for index in range(288, 313)
    ]
    for row in result.rows:
        assert str(row["project_id"]) == PROJECT
        assert row["root_span_id"] == "root"
        assert row["_root_version"] == 2 and row["_root_service_name"] == "svc"
    batches = [
        len(params["candidate_trace_ids"])
        for _, params, *_ in transport.calls
        if "candidate_trace_ids" in params
    ]
    # Only a new slice resets the density check: at 140 minutes the dense
    # population shares the second, two-hour slice with the rejected roots.
    # Speculative discovery may choose different boundaries in bounded mode;
    # its result proof and batch ceiling are still checked. The real uncapped
    # application path additionally proves the expected sizing transition.
    assert all(0 < width <= 200 for width in batches)
    if application_policy:
        assert batches[-1] == (50 if dense_minutes == 260 else 200)
