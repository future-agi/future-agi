"""Historical budget escalation must keep the original selection window."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from tracer.models.eval_task import RowType
from tracer.selectors.eval_tasks import row_resolver
from tracer.selectors.trace_filter_reads import BoundedFilterPage
from tracer.services.clickhouse.query_builders import base

pytestmark = pytest.mark.unit


def _datetime_filter(operator, value):
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": operator,
            "filter_value": value,
        },
    }


@pytest.mark.parametrize(
    "row_type",
    [RowType.SPANS, RowType.TRACES, RowType.SESSIONS, RowType.VOICE_CALLS],
)
@pytest.mark.parametrize(
    "task_filters",
    [
        pytest.param(None, id="null-default-window"),
        pytest.param({}, id="default-window"),
        pytest.param(
            {"date_range": ["2026-08-16T12:00:00", "2026-09-15T12:00:00"]},
            id="explicit-window",
        ),
        pytest.param(
            {"filters": [_datetime_filter("greater_than", "2026-08-20T12:00:00")]},
            id="default-upper-bound",
        ),
        pytest.param(
            {
                "filters": [
                    _datetime_filter("less_than_or_equal", "2026-09-14T12:00:00")
                ]
            },
            id="default-lower-bound",
        ),
        pytest.param(
            {
                "filters": [
                    _datetime_filter(
                        "not_between", ["2026-08-25T12:00:00", "2026-08-26T12:00:00"]
                    )
                ]
            },
            id="excluded-interval",
        ),
        pytest.param(
            {"created_at": "2026-08-20T12:00:00"},
            id="legacy-lower-bound",
        ),
    ],
)
def test_escalation_keeps_original_window_and_exclusions(
    monkeypatch, row_type, task_filters
):
    class Clock(datetime):
        now_value = datetime(2026, 9, 15, 12)

        @classmethod
        def utcnow(cls):
            return cls.fromisoformat(cls.now_value.isoformat())

    monkeypatch.setattr(base, "datetime", Clock)
    original_filters = deepcopy(task_filters)
    expected = base.BaseQueryBuilder.analyze_bounded_datetime_filters(
        row_resolver._task_ui_filters(task_filters, row_type=row_type)
    )
    calls = []

    def read(**kwargs):
        builder = kwargs["builder"]
        before = builder.analyze_bounded_datetime_filters(kwargs["filters"])
        # Model time spent on interactive selection, then workflow selection.
        # Repeated builder calls inside either pass must use the same bounds.
        Clock.now_value += timedelta(seconds=106)
        after = builder.analyze_bounded_datetime_filters(builder.filters)
        calls.append((kwargs["workflow_exact"], before, after))
        complete = kwargs["workflow_exact"]
        return BoundedFilterPage(
            rows=[],
            has_more=False,
            complete=complete,
            status="complete" if complete else "degraded",
            error_code=None if complete else "query_budget_exceeded",
            total_rows_lower_bound=0,
            elapsed_ms=106_000,
            query_count=112,
            rows_returned=0,
            result_payload_bytes=0,
            attempts=(),
        )

    monkeypatch.setattr(
        "tracer.selectors.trace_filter_reads.read_bounded_filter_page", read
    )

    result = row_resolver._resolve_bounded_historical_span_ids(
        object(),
        sql=None,
        params=None,
        project_id="00000000-0000-4000-8000-000000000001",
        salt="task-salt",
        sampling_rate=100,
        filters=task_filters,
        limit=10,
        batch_size=10,
        row_type=row_type,
    )

    assert result == []
    assert [mode for mode, _, _ in calls] == [False, True]
    assert all(before == after == expected for _, before, after in calls), calls
    assert task_filters == original_filters
