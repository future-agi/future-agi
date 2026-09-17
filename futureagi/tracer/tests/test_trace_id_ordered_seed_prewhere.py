from datetime import datetime, timedelta
from typing import Any

import pytest

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
WINDOW_END = datetime(2026, 8, 3, 0, 20)
WINDOW_START = WINDOW_END - timedelta(days=14)
TRACE_IDS = [
    "003B76F1-2B4A-4AF5-B0DC-224D687374D4",
    "103b76f1-2b4a-4af5-b0dc-224d687374d4",
]


def _time_filter() -> dict[str, Any]:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [WINDOW_START.isoformat(), WINDOW_END.isoformat()],
        },
    }


def _trace_id_filter(values: list[str], *, operation: str = "in") -> dict[str, Any]:
    return {
        "column_id": "trace_id",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "text",
            "filter_op": operation,
            "filter_value": values,
        },
    }


def _attribute_filter() -> dict[str, Any]:
    return {
        "column_id": "final_status",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "Rejected",
        },
    }


@pytest.mark.parametrize(
    "builder_cls", [TraceListQueryBuilder, TraceListQueryBuilderV2]
)
def test_sparse_trace_id_in_is_canonicalized_and_pushed_into_prewhere(
    builder_cls: type[TraceListQueryBuilder],
) -> None:
    builder = builder_cls(
        project_id=PROJECT_ID,
        page_number=0,
        page_size=25,
        filters=[_time_filter(), _attribute_filter(), _trace_id_filter(TRACE_IDS)],
    )

    sql, params = builder.build_filter_ordered_seed_page(
        slice_start=WINDOW_START,
        slice_end=WINDOW_END,
        limit=26,
    )
    prewhere_sql, separator, where_sql = sql.partition("\n        WHERE 1 = 1")

    assert separator
    assert "trace_id IN %(latest_filter_param_1)s" in prewhere_sql
    assert "trace_id IN %(latest_filter_param_1)s" not in where_sql
    assert "lowerUTF8(toString(trace_id))" not in sql
    assert params["latest_filter_param_1"] == tuple(
        trace_id.lower() for trace_id in TRACE_IDS
    )


@pytest.mark.parametrize(
    ("values", "operation", "predicate"),
    [
        (["trace-a", "trace-b"], "in", "lowerUTF8(toString(trace_id)) IN"),
        (TRACE_IDS, "not_in", "lowerUTF8(toString(trace_id)) NOT IN"),
    ],
)
def test_noncanonical_or_negative_trace_id_filters_stay_in_where(
    values: list[str],
    operation: str,
    predicate: str,
) -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        page_number=0,
        page_size=25,
        filters=[_time_filter(), _trace_id_filter(values, operation=operation)],
    )

    sql, _params = builder.build_filter_ordered_seed_page(
        slice_start=WINDOW_START,
        slice_end=WINDOW_END,
        limit=26,
    )
    prewhere_sql, separator, where_sql = sql.partition("\n        WHERE 1 = 1")

    assert separator
    assert predicate not in prewhere_sql
    assert predicate in where_sql
