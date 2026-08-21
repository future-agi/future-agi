from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
END = datetime(2026, 8, 11, 12)
START = END - timedelta(days=365)


def _time_filter() -> dict:
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [START.isoformat(), END.isoformat()],
        },
    }


@pytest.mark.unit
def test_system_ended_reason_is_classifier_only_during_root_discovery() -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            {
                "column_id": "ended_reason",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "assistant-ended-call",
                },
            },
        ],
    )

    seed_sql, _ = builder.build_filter_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=26,
    )
    match_sql, _ = builder.build_filter_match_query(["trace-a"])

    assert "raw_log" not in seed_sql
    assert "endedReason" not in seed_sql
    assert "disconnection_reason" not in seed_sql
    assert "'raw_log', 'endedReason'" in match_sql
    assert "'raw_log', 'disconnection_reason'" in match_sql
    assert "attrs_string['ended_reason']" in match_sql


@pytest.mark.unit
@pytest.mark.parametrize("values", [[PROJECT_ID], (PROJECT_ID,)])
def test_saved_annotation_alias_accepts_list_and_tuple_values_in_classifier(
    values,
) -> None:
    builder = TraceListQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=[
            _time_filter(),
            {
                "column_id": "annotator",
                "filter_config": {
                    "col_type": "ANNOTATION",
                    "filter_type": "annotator",
                    "filter_op": "equals",
                    "filter_value": values,
                },
            },
        ],
    )

    assert builder.supports_filter_candidate_seed_page() is True
    seed_sql, seed_params = builder.build_filter_candidate_seed_page(
        slice_start=END - timedelta(minutes=5),
        slice_end=END,
        limit=26,
    )
    match_sql, match_params = builder.build_filter_match_query(["trace-a"])

    for sql, params in (
        (seed_sql, seed_params),
        (match_sql, match_params),
    ):
        assert "FROM model_hub_score AS s FINAL" in sql
        assert "s.annotator_id IN" in sql
        assert PROJECT_ID in params.values()
