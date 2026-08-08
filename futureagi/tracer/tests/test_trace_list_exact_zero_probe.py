"""Focused exact-zero coverage for the generic trace-list endpoint."""

from datetime import UTC, datetime, timedelta

import pytest

from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.query_service import QueryResult
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000527"


def _filters(end: datetime, *, window: timedelta = timedelta(days=365)) -> list[dict]:
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [end - window, end],
            },
        },
        {
            "column_id": "call.total_turns",
            "filter_config": {
                "filter_type": "number",
                "filter_op": "equals",
                "filter_value": 2,
                "col_type": "SPAN_ATTRIBUTE",
            },
        },
        {
            "column_id": "conversation.transcript.16.message.role",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ["assistant"],
                "col_type": "SPAN_ATTRIBUTE",
            },
        },
    ]


def _result(rows: list[dict]) -> QueryResult:
    return QueryResult(
        data=rows,
        row_count=len(rows),
        backend_used="clickhouse",
        query_time_ms=1,
    )


@pytest.mark.unit
def test_generic_trace_exact_zero_probe_intersects_independent_span_witnesses():
    end = datetime(2026, 8, 8, tzinfo=UTC)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_size=25,
        filters=_filters(end, window=timedelta(minutes=5)),
    )

    sql, params = builder.build_filter_exact_zero_probe()
    first_leaf_branch, second_leaf_branch = sql.split("UNION ALL")

    assert builder.supports_filter_exact_zero_probe() is True
    assert "%(latest_filter_key_0)s" in first_leaf_branch
    assert "%(latest_filter_key_1)s" not in first_leaf_branch
    assert "%(latest_filter_key_1)s" in second_leaf_branch
    assert "%(latest_filter_key_0)s" not in second_leaf_branch
    assert "countIf(witness_kind = 0) > 0" in sql
    assert "countIf(witness_kind = 1) > 0" in sql
    assert params["latest_filter_key_0"] == "call.total_turns"
    assert params["latest_filter_param_0"] == 2
    assert params["latest_filter_key_1"] == "conversation.transcript.16.message.role"
    assert params["latest_filter_param_1"] == ("assistant",)
    assert "attrs_number" in sql
    assert "attrs_string" in sql
    assert "span_attr_num" not in sql
    assert "span_attr_str" not in sql
    assert sql.count("SETTINGS ") == 1


@pytest.mark.unit
def test_generic_trace_exact_zero_probe_terminal_empty_skips_bounded_scan():
    end = datetime(2026, 8, 8, tzinfo=UTC)

    class Executor:
        supports_per_query_read_settings = True

        def __init__(self):
            self.calls: list[tuple[str, dict, dict]] = []

        def execute_ch_query(self, query, params, **kwargs):
            self.calls.append((query, params, kwargs))
            return _result([])

    executor = Executor()
    filters = _filters(end, window=timedelta(minutes=5))
    page = read_bounded_filter_page(
        builder=TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_size=25,
            filters=filters,
        ),
        analytics=executor,
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert page.rows == []
    assert page.error_code is None
    assert [attempt.kind for attempt in page.attempts] == ["zero_probe"]
    assert len(executor.calls) == 1


@pytest.mark.unit
def test_generic_trace_exact_zero_probe_skips_broad_long_window_union():
    end = datetime(2026, 8, 8, tzinfo=UTC)
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_size=25,
        filters=_filters(end),
    )

    assert builder.supports_filter_exact_zero_probe() is False
    assert builder.prefer_filter_candidate_witness_probe_first() is True
    assert builder.recommended_filter_candidate_witness_probe_strata() == 1
    with pytest.raises(ValueError, match="exact-zero probe is unavailable"):
        builder.build_filter_exact_zero_probe()


@pytest.mark.unit
def test_generic_trace_positive_zero_probe_falls_back_to_exact_bounded_scan():
    end = datetime(2026, 8, 8, tzinfo=UTC)
    filters = _filters(end, window=timedelta(minutes=5))
    candidates = [
        {
            "project_id": PROJECT_ID,
            "trace_id": f"trace-{index:02d}",
            "root_span_id": f"root-{index:02d}",
            "start_time": end - timedelta(seconds=index + 1),
        }
        for index in range(26)
    ]
    hydrated = [
        {**row, "trace_name": f"trace-name-{index:02d}"}
        for index, row in enumerate(candidates[:25])
    ]

    class Executor:
        supports_per_query_read_settings = True

        def __init__(self):
            self.calls: list[tuple[str, dict, dict]] = []
            self.results = [
                [{"trace_id": "raw-witness-is-not-public"}],
                candidates,
                candidates[:10],
                candidates[10:20],
                candidates[20:],
                hydrated,
            ]

        def execute_ch_query(self, query, params, **kwargs):
            self.calls.append((query, params, kwargs))
            return _result(self.results.pop(0))

    executor = Executor()
    page = read_bounded_filter_page(
        builder=TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_size=25,
            filters=filters,
        ),
        analytics=executor,
        filters=filters,
        key_field="trace_id",
        page_number=0,
        page_size=25,
        deadline_ms=5_000,
    )

    assert page.complete is True
    assert [row["trace_id"] for row in page.rows] == [
        row["trace_id"] for row in hydrated
    ]
    assert "raw-witness-is-not-public" not in {row["trace_id"] for row in page.rows}
    assert [attempt.kind for attempt in page.attempts] == [
        "zero_probe",
        "anchor",
        "classify",
        "classify",
        "classify",
        "hydrate",
    ]
    assert len(executor.calls) == 6


@pytest.mark.unit
def test_generic_trace_exact_zero_probe_rejects_unsupported_shapes():
    end = datetime(2026, 8, 8, tzinfo=UTC)
    single_leaf = _filters(end)[:2]
    negative_leaf = _filters(end)
    negative_leaf[1]["filter_config"]["filter_op"] = "not_equals"

    for filters in (single_leaf, negative_leaf):
        builder = TraceListQueryBuilderV2(
            project_id=PROJECT_ID,
            page_size=25,
            filters=filters,
        )
        assert builder.supports_filter_exact_zero_probe() is False
        with pytest.raises(ValueError, match="exact-zero probe is unavailable"):
            builder.build_filter_exact_zero_probe()
