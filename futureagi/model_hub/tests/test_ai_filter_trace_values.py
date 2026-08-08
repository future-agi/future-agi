"""Direct-write CH25 value discovery used by the smart trace filter."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from model_hub.views.ai_filter import _fetch_trace_field_values
from tracer.services.clickhouse.attribute_reads import (
    AttributeReadMetadata,
    AttributeValueRead,
    AttributeValueRow,
)
from tracer.services.clickhouse.filter_value_reads import FilterValueRead

pytestmark = pytest.mark.unit

PROJECT_ID = "00000000-0000-4000-8000-000000000001"
NOW = datetime(2026, 8, 3, tzinfo=UTC)


def _attribute_metadata(*, complete: bool, error_code: str | None = None):
    return AttributeReadMetadata(
        query_complete=complete,
        query_status=(
            "complete"
            if complete
            else "sampled"
            if error_code == "sample_limit"
            else "degraded"
        ),
        query_error_code=error_code,
        query_window_start=NOW,
        query_window_end=NOW,
        query_count=2,
    )


def test_final_status_uses_bounded_typed_attribute_reader(monkeypatch):
    capture = {}

    class _Selector:
        def __init__(self, **kwargs):
            capture["selector_kwargs"] = kwargs

        def read_values(self, project_ids, key, **kwargs):
            capture.update(
                project_ids=project_ids,
                key=key,
                read_kwargs=kwargs,
            )
            return AttributeValueRead(
                rows=(
                    AttributeValueRow("Rechazado", "string", 12),
                    AttributeValueRow("Aprobado", "string", 7),
                    AttributeValueRow("Rechazado", "string", 1),
                ),
                metadata=_attribute_metadata(complete=True),
            )

    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads.AttributeReadSelector",
        _Selector,
    )

    assert _fetch_trace_field_values(
        [PROJECT_ID], "final_status", "custom_attribute"
    ) == ["Rechazado", "Aprobado"]
    assert capture == {
        "selector_kwargs": {
            "typed_only": True,
            "json_attribute_mode": "arrays",
        },
        "project_ids": [PROJECT_ID],
        "key": "final_status",
        "read_kwargs": {"max_values": 100, "horizon_days": 365},
    }


def test_custom_attribute_sample_is_usable_and_flattens_arrays(monkeypatch):
    class _Selector:
        def __init__(self, **kwargs):
            pass

        def read_values(self, *args, **kwargs):
            return AttributeValueRead(
                rows=(AttributeValueRow(("one", "two", True, False), "array", 3),),
                metadata=_attribute_metadata(
                    complete=False,
                    error_code="sample_limit",
                ),
            )

    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads.AttributeReadSelector",
        _Selector,
    )

    assert _fetch_trace_field_values(
        [PROJECT_ID], "structured_result", "custom_attribute"
    ) == ["one", "two", "true", "false"]


def test_custom_attribute_degraded_read_fails_closed(monkeypatch):
    class _Selector:
        def __init__(self, **kwargs):
            pass

        def read_values(self, *args, **kwargs):
            return AttributeValueRead(
                rows=(AttributeValueRow("must-not-escape", "string", 1),),
                metadata=_attribute_metadata(
                    complete=False,
                    error_code="read_budget_exceeded",
                ),
            )

    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads.AttributeReadSelector",
        _Selector,
    )

    assert (
        _fetch_trace_field_values([PROJECT_ID], "final_status", "custom_attribute")
        == []
    )


def test_system_metric_uses_v2_service_and_bounded_reader(monkeypatch):
    capture = {}
    analytics = object()

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        lambda: analytics,
    )

    def _read(service, **kwargs):
        capture.update(service=service, **kwargs)
        return FilterValueRead(
            values=("gpt-4o", "gpt-4o-mini"),
            query_complete=False,
            query_error_code="sample_limit",
            query_window_start=NOW,
            query_window_end=NOW,
            has_more=True,
        )

    monkeypatch.setattr(
        "tracer.services.clickhouse.filter_value_reads.read_span_system_filter_values",
        _read,
    )

    assert _fetch_trace_field_values([PROJECT_ID], "model", "system_metric") == [
        "gpt-4o",
        "gpt-4o-mini",
    ]
    assert capture == {
        "service": analytics,
        "project_ids": [PROJECT_ID],
        "metric_name": "model",
        "limit": 100,
        "lookback_days": 365,
    }


def test_unknown_system_metric_does_not_construct_service(monkeypatch):
    construct = SimpleNamespace(called=False)

    def _service():
        construct.called = True
        return object()

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        _service,
    )

    assert (
        _fetch_trace_field_values(
            [PROJECT_ID], "untrusted-column-name", "system_metric"
        )
        == []
    )
    assert construct.called is False


def test_reader_exception_returns_sanitized_empty_list(monkeypatch):
    secret = "SELECT secret FROM private_table"

    class _Selector:
        def __init__(self, **kwargs):
            pass

        def read_values(self, *args, **kwargs):
            raise RuntimeError(secret)

    monkeypatch.setattr(
        "tracer.services.clickhouse.attribute_reads.AttributeReadSelector",
        _Selector,
    )

    assert (
        _fetch_trace_field_values([PROJECT_ID], "final_status", "custom_attribute")
        == []
    )
