"""Deterministic CH25 gate for exact filtered trace list and graph reads.

The same broad scalar-attribute filter runs against the ordinary seeded project
and the larger mixed/noise project.  Expected membership and graph traffic come
from the fixed-seed loadgen manifests, while ``system.query_log`` supplies the
resource measurements.  No production credentials or customer data are used.
"""

from __future__ import annotations

import pytest

from tests.stress.budgets import (
    EXACT_TRACE_GRAPH_MAX_CH_QUERIES,
    EXACT_TRACE_GRAPH_MAX_MEMORY,
    EXACT_TRACE_GRAPH_MAX_QUERY_DURATION_MS,
    EXACT_TRACE_GRAPH_MAX_READ_ROWS_FACTOR,
    EXACT_TRACE_LIST_MAX_CH_QUERIES,
    EXACT_TRACE_LIST_MAX_MEMORY,
    EXACT_TRACE_LIST_MAX_QUERY_DURATION_MS,
    EXACT_TRACE_LIST_MAX_READ_ROWS_FACTOR,
    EXACT_TRACE_LIST_READ_ROWS_GRANULE_FLOOR,
)
from tests.stress.ch_asserts import BudgetResult, ch_query_budget
from tracer.selectors.trace_filter_reads import read_bounded_filter_page
from tracer.services.clickhouse.exact_graph_reads import read_exact_system_graph
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

pytestmark = pytest.mark.stress

_WINDOW = ("2025-12-30T00:00:00Z", "2026-01-02T00:00:00Z")
_PAGE_SIZE = 25


class _TaggedAnalytics(V2AnalyticsQueryService):
    """Attach a query-log tag without changing the production executor."""

    def __init__(self, tag: str) -> None:
        super().__init__()
        self._stress_tag = tag

    def execute_ch_query(
        self,
        query,
        params=None,
        timeout_ms=10_000,
        settings=None,
    ):
        return super().execute_ch_query(
            query,
            params,
            timeout_ms=timeout_ms,
            settings={**(settings or {}), "log_comment": self._stress_tag},
        )


def _filters(session_ids: list[str]) -> list[dict]:
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": list(_WINDOW),
            },
        },
        {
            "column_id": "session.id",
            "display_name": "session.id",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": list(session_ids),
                "col_type": "SPAN_ATTRIBUTE",
            },
        },
    ]


def _metrics(budget: BudgetResult) -> dict[str, float]:
    return {
        "queries": budget.count,
        "duration_ms": budget.total("query_duration_ms"),
        "max_duration_ms": budget.max("query_duration_ms"),
        "read_rows": budget.total("read_rows"),
        "read_bytes": budget.total("read_bytes"),
        "max_memory": budget.max("memory_usage"),
    }


def _assert_budget(
    budget: BudgetResult,
    *,
    span_count: int,
    max_queries: int,
    max_read_rows_factor: float,
    max_memory: int,
    max_query_duration_ms: int,
    read_rows_floor: int = 0,
) -> None:
    assert 0 < budget.count <= max_queries
    assert budget.total("read_rows") <= max(
        span_count * max_read_rows_factor,
        read_rows_floor,
    )
    assert budget.max("memory_usage") <= max_memory
    assert budget.max("query_duration_ms") <= max_query_duration_ms


def test_exact_filtered_trace_reads_are_complete_and_project_scoped(stress_dataset):
    measurements: dict[str, dict[str, dict[str, float]]] = {}

    for dataset_name, manifest in (
        ("target", stress_dataset.target),
        ("noise", stress_dataset.noise),
    ):
        filters = _filters(manifest.session_ids)
        expected_trace_ids = set(manifest.trace_ids)

        list_tag = f"stress:PROPERTY_CATALOG:exact-trace-list:{dataset_name}"
        with ch_query_budget(list_tag) as list_budget:
            analytics = _TaggedAnalytics(list_tag)
            builder = TraceListQueryBuilderV2(
                project_id=manifest.project_id,
                filters=filters,
                page_number=0,
                page_size=_PAGE_SIZE,
            )
            page = read_bounded_filter_page(
                builder=builder,
                analytics=analytics,
                filters=filters,
                key_field="trace_id",
                page_number=0,
                page_size=_PAGE_SIZE,
                deadline_ms=30_000,
            )

        assert page.complete is True
        assert page.error_code is None
        assert len(page.rows) == _PAGE_SIZE
        assert {str(row["trace_id"]) for row in page.rows} <= expected_trace_ids
        assert list_budget.count == page.query_count
        _assert_budget(
            list_budget,
            span_count=manifest.span_count,
            max_queries=EXACT_TRACE_LIST_MAX_CH_QUERIES,
            max_read_rows_factor=EXACT_TRACE_LIST_MAX_READ_ROWS_FACTOR,
            max_memory=EXACT_TRACE_LIST_MAX_MEMORY,
            max_query_duration_ms=EXACT_TRACE_LIST_MAX_QUERY_DURATION_MS,
            read_rows_floor=EXACT_TRACE_LIST_READ_ROWS_GRANULE_FLOOR,
        )

        graph_tag = f"stress:PROPERTY_CATALOG:exact-trace-graph:{dataset_name}"
        with ch_query_budget(graph_tag) as graph_budget:
            graph = read_exact_system_graph(
                analytics=_TaggedAnalytics(graph_tag),
                project_id=manifest.project_id,
                filters=filters,
                interval="hour",
                metric_id="traffic",
                observe_type="trace",
            )

        assert graph["query_complete"] is True
        assert graph["query_sampled"] is False
        assert graph["query_count"] == graph_budget.count
        assert sum(point["value"] for point in graph["data"]) == manifest.span_count
        _assert_budget(
            graph_budget,
            span_count=manifest.span_count,
            max_queries=EXACT_TRACE_GRAPH_MAX_CH_QUERIES,
            max_read_rows_factor=EXACT_TRACE_GRAPH_MAX_READ_ROWS_FACTOR,
            max_memory=EXACT_TRACE_GRAPH_MAX_MEMORY,
            max_query_duration_ms=EXACT_TRACE_GRAPH_MAX_QUERY_DURATION_MS,
        )

        measurements[dataset_name] = {
            "list": _metrics(list_budget),
            "graph": _metrics(graph_budget),
        }

    print(f"\nPROPERTY_CATALOG-EXACT-TRACE-READS :: {measurements}")
