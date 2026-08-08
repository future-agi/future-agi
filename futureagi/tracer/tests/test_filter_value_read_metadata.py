"""Focused metadata contract for finite filter-value picker reads."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.filter_value_reads import (
    FilterValueRead,
    read_span_system_filter_values,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
PROJECT_ID = "00000000-0000-4000-8000-000000000001"


def _read(
    values: tuple[str, ...],
    *,
    complete: bool,
    error_code: str | None,
) -> FilterValueRead:
    return FilterValueRead(
        values,
        complete,
        error_code,
        NOW - timedelta(days=7),
        NOW,
    )


def test_filter_value_metadata_labels_only_usable_finite_caps_as_sampled():
    complete = _read(("one",), complete=True, error_code=None)
    sampled = _read(("one",), complete=False, error_code="sample_limit")
    empty_cap = _read((), complete=False, error_code="sample_limit")
    resource_failure = _read(
        ("one",),
        complete=False,
        error_code="read_budget_exceeded",
    )

    assert complete.metadata()["query_status"] == "complete"
    assert sampled.metadata()["query_status"] == "sampled"
    assert empty_cap.metadata()["query_status"] == "degraded"
    assert resource_failure.metadata()["query_status"] == "degraded"


def test_system_filter_value_cap_produces_a_labelled_sample():
    class Analytics:
        def execute_ch_query(self, *_args, **_kwargs):
            return SimpleNamespace(data=[{"val": "one"}, {"val": "two"}])

    read = read_span_system_filter_values(
        Analytics(),
        project_ids=[PROJECT_ID],
        metric_name="model",
        limit=1,
        now=NOW,
    )

    assert read.values == ("one",)
    assert read.has_more is True
    assert read.query_complete is False
    assert read.query_error_code == "sample_limit"
    assert read.metadata()["query_status"] == "sampled"


@pytest.mark.parametrize(
    ("metric_name", "expected_value", "sql_markers"),
    [
        (
            "call_status",
            "completed",
            (
                "multiIf(",
                "('ended', 'done', 'complete', 'completed'",
                "attrs_string",
            ),
        ),
        (
            "cost_cents",
            "12.2",
            ("'call_cost', 'combined_cost'", "'cost_breakdown.total'", "* 100"),
        ),
    ],
)
def test_voice_system_suggestions_use_normalized_list_expressions(
    metric_name,
    expected_value,
    sql_markers,
):
    class Analytics:
        call = None

        def execute_ch_query(self, query, params, **kwargs):
            self.call = (query, params, kwargs)
            return SimpleNamespace(data=[{"val": expected_value}])

    analytics = Analytics()
    read = read_span_system_filter_values(
        analytics,
        project_ids=[PROJECT_ID],
        metric_name=metric_name,
        now=NOW,
    )

    assert read.values == (expected_value,)
    query, _, _ = analytics.call
    assert "latest_observation_type = 'conversation'" in query
    assert "latest_parent_span_id IS NULL" in query
    assert "attributes_extra" in query
    for marker in sql_markers:
        assert marker in query
