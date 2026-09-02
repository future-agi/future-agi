from __future__ import annotations

from datetime import datetime, timedelta
from unittest import mock

import pytest

from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.span_list import (
    SpanListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
START = datetime(2026, 7, 1)
END = START + timedelta(days=30)
CURSOR = END - timedelta(days=1, microseconds=123)


def _filters() -> list[dict[str, object]]:
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [START.isoformat(), END.isoformat()],
            },
        },
        {
            "column_id": "final_status",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "equals",
                "filter_value": "Rejected",
            },
        },
    ]


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["trace", "span"])
def test_navigation_newer_seed_flips_sql_order_and_strict_tuple(kind: str) -> None:
    builder = (
        TraceListQueryBuilder(project_id=PROJECT_ID, filters=_filters())
        if kind == "trace"
        else SpanListQueryBuilder(project_id=PROJECT_ID, filters=_filters())
    )
    token = (
        "trace-current"
        if kind == "trace"
        else (
            "span-current",
            "trace-current",
            PROJECT_ID,
        )
    )

    sql, params = builder.build_filter_navigation_seed_page(
        direction="newer",
        slice_start=CURSOR,
        slice_end=END,
        limit=100,
        cursor_start_time=CURSOR,
        cursor_order_token=token,
    )

    assert sql.index("ORDER BY start_time ASC") < sql.index("LIMIT 1 BY")
    assert "toUnixTimestamp64Micro(start_time) > %(filter_after_start_us)s" in sql
    assert params["filter_after_start_us"] == 1_785_369_599_999_877
    assert "filter_before_start_us" not in params
    if kind == "trace":
        assert "trace_id > %(filter_after_id)s" in sql
        assert params["filter_after_id"] == "trace-current"
        assert "LIMIT 1 BY trace_id" in sql
    else:
        assert "id > %(filter_after_id)s" in sql
        assert "trace_id > %(filter_after_trace_id)s" in sql
        assert "toString(project_id) > %(filter_after_project_id)s" in sql
        assert params["filter_after_id"] == "span-current"
        assert params["filter_after_trace_id"] == "trace-current"
        assert params["filter_after_project_id"] == PROJECT_ID
        assert "LIMIT 1 BY project_id, trace_id, id, start_time" in sql


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["trace", "span"])
def test_navigation_older_seed_preserves_existing_builder_bytes(kind: str) -> None:
    builder = (
        TraceListQueryBuilder(project_id=PROJECT_ID, filters=_filters())
        if kind == "trace"
        else SpanListQueryBuilder(project_id=PROJECT_ID, filters=_filters())
    )
    token = (
        "trace-current"
        if kind == "trace"
        else (
            "span-current",
            "trace-current",
            PROJECT_ID,
        )
    )
    method = (
        builder.build_filter_ordered_seed_page
        if kind == "trace"
        else builder.build_filter_seed_page
    )
    kwargs = {
        "slice_start": START,
        "slice_end": END,
        "limit": 100,
        "before_start_time": CURSOR,
        "before_id": token,
    }

    default_sql, default_params = method(**kwargs)
    older_sql, older_params = method(**kwargs, direction="older")

    assert default_sql == older_sql
    assert default_params == older_params
    assert "ORDER BY start_time DESC" in default_sql
    assert "filter_after_start_us" not in default_params


@pytest.mark.unit
def test_span_navigation_target_keeps_two_identity_sentinel() -> None:
    builder = SpanListQueryBuilder(
        project_id=PROJECT_ID,
        filters=_filters(),
        bounded_identity_only=True,
    )

    sql, params = builder.build_filter_navigation_target_query(
        target_id="shared-span",
        result_limit=2,
    )

    assert params["candidate_span_ids"] == ("shared-span",)
    assert "GROUP BY project_id, trace_id, id, start_time" in sql
    assert "candidate_span_identities" not in params
    assert "LIMIT 2" in sql
    assert "grouped_project_id AS project_id" in sql
    assert "latest_trace_id AS trace_id" in sql


@pytest.mark.unit
def test_trace_navigation_target_is_membership_only() -> None:
    builder = TraceListQueryBuilder(
        project_id=PROJECT_ID,
        filters=_filters(),
        bounded_identity_only=True,
        bounded_bulk_scan=True,
    )

    sql, params = builder.build_filter_navigation_target_query(
        target_id="trace-current",
        result_limit=2,
    )

    assert params["candidate_trace_ids"] == ("trace-current",)
    assert "LIMIT 2" in sql
    assert "filter_witness_" not in sql
    assert "canonical_root_identity.1 AS root_span_id" in sql


@pytest.mark.unit
@pytest.mark.parametrize(
    ("builder_cls", "token"),
    [
        (TraceListQueryBuilder, "trace-current"),
        (
            SpanListQueryBuilder,
            ("span-current", "trace-current", PROJECT_ID),
        ),
    ],
)
def test_navigation_seed_rejects_invalid_direction_and_partial_cursor(
    builder_cls, token
) -> None:
    builder = builder_cls(project_id=PROJECT_ID, filters=_filters())
    with pytest.raises(ValueError, match="direction"):
        builder.build_filter_navigation_seed_page(
            direction="sideways",
            slice_start=START,
            slice_end=END,
            limit=1,
        )
    with pytest.raises(ValueError, match="provided together"):
        builder.build_filter_navigation_seed_page(
            direction="older",
            slice_start=START,
            slice_end=END,
            limit=1,
            cursor_start_time=CURSOR,
        )
    with pytest.raises(ValueError, match="provided together"):
        builder.build_filter_navigation_seed_page(
            direction="newer",
            slice_start=START,
            slice_end=END,
            limit=1,
            cursor_order_token=token,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "builder_cls", [TraceListQueryBuilderV2, SpanListQueryBuilderV2]
)
def test_v2_navigation_target_crosses_one_statement_rewrite_boundary(
    builder_cls,
) -> None:
    from tracer.services.clickhouse.v2.query_builders import _rewrite

    builder = builder_cls(
        project_id=PROJECT_ID,
        filters=_filters(),
        bounded_identity_only=True,
    )
    real_rewrite = _rewrite.rewrite_v1_sql_to_v2
    with mock.patch.object(
        _rewrite,
        "rewrite_v1_sql_to_v2",
        wraps=real_rewrite,
    ) as rewrite:
        sql, _ = builder.build_filter_navigation_target_query(
            target_id="target-id",
            result_limit=2,
        )

    statement_calls = [
        call
        for call in rewrite.call_args_list
        if str(call.args[0]).lstrip().upper().startswith(("SELECT", "WITH"))
    ]
    assert len(statement_calls) == 1
    assert sql.upper().count("\nSETTINGS ") == 1
