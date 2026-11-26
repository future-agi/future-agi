"""Actual reader shapes: DateTime64 UTC values can be naive datetimes."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tracer.services.feed_escalation_inputs import source_occurrence_time

pytestmark = pytest.mark.unit


def test_naive_clickhouse_root_completion_is_interpreted_in_schema_utc(monkeypatch):
    completed = datetime(2026, 10, 14, 12)
    reader = SimpleNamespace(
        list_root_spans_by_trace_ids=lambda *args, **kwargs: {
            "trace": {"trace_id": "trace", "end_time": completed},
        }
    )
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    at, provenance = source_occurrence_time(project_id="project", trace_id="trace")
    assert at == datetime(2026, 10, 14, 12, tzinfo=UTC)
    assert provenance == "trace_end"


def test_span_completion_normalizes_an_existing_offset_to_utc(monkeypatch):
    completed = datetime(
        2026, 10, 14, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )
    reader = SimpleNamespace(
        get=lambda *args, **kwargs: SimpleNamespace(end_time=completed)
    )
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    at, provenance = source_occurrence_time(project_id="project", span_id="span")
    assert at.isoformat() == "2026-10-14T12:00:00+00:00"
    assert provenance == "span_end"


def test_missing_completion_never_becomes_processing_time(monkeypatch):
    reader = SimpleNamespace(list_root_spans_by_trace_ids=lambda *args, **kwargs: {})
    monkeypatch.setattr("tracer.services.clickhouse.v2.get_reader", lambda: reader)
    assert source_occurrence_time(project_id="project", trace_id="trace") == (None, "")
