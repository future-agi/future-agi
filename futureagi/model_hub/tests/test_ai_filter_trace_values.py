"""Direct-write CH25 value discovery used by the smart trace filter."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from model_hub.views.ai_filter import (
    SmartFilterGroundingError,
    _authorize_smart_property_schema,
    _fetch_trace_field_values,
    _run_smart_agent,
)
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
        [PROJECT_ID],
        "final_status",
        "custom_attribute",
        search_query="rech",
    ) == ["Rechazado", "Aprobado"]
    assert capture["selector_kwargs"]["typed_only"] is True
    assert capture["selector_kwargs"]["json_attribute_mode"] == "arrays"
    assert 0 < capture["selector_kwargs"]["wall_timeout_ms"] <= 4_000
    assert capture["project_ids"] == [PROJECT_ID]
    assert capture["key"] == "final_status"
    assert capture["read_kwargs"] == {
        "search": "rech",
        "max_values": 100,
        "horizon_days": 365,
    }


def test_custom_attribute_sample_is_typed_too_broad_refusal(monkeypatch):
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

    with pytest.raises(SmartFilterGroundingError) as error:
        _fetch_trace_field_values(
            [PROJECT_ID],
            "structured_result",
            "custom_attribute",
            search_query="one",
        )
    assert error.value.status_code == 422
    assert error.value.code == "ai_filter_grounding_too_broad"


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

    with pytest.raises(SmartFilterGroundingError) as error:
        _fetch_trace_field_values(
            [PROJECT_ID],
            "final_status",
            "custom_attribute",
            search_query="reject",
        )
    assert error.value.status_code == 503
    assert error.value.code == "ai_filter_grounding_unavailable"


def test_system_metric_uses_v2_service_and_exact_bounded_reader(monkeypatch):
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
            query_complete=True,
            query_error_code=None,
            query_window_start=NOW,
            query_window_end=NOW,
            has_more=True,
        )

    monkeypatch.setattr(
        "tracer.services.clickhouse.filter_value_reads.read_span_system_filter_values",
        _read,
    )

    assert _fetch_trace_field_values(
        [PROJECT_ID], "model", "system_metric", search_query="gpt-4"
    ) == [
        "gpt-4o",
        "gpt-4o-mini",
    ]
    assert capture == {
        "service": analytics,
        "project_ids": [PROJECT_ID],
        "metric_name": "model",
        "search": "gpt-4",
        "limit": 100,
        "lookback_days": 365,
        "deadline": capture["deadline"],
    }


def test_system_metric_sample_is_typed_too_broad_refusal(monkeypatch):
    monkeypatch.setattr(
        "tracer.services.clickhouse.filter_value_reads.read_span_system_filter_values",
        lambda *args, **kwargs: FilterValueRead(
            values=("gpt-4o",),
            query_complete=False,
            query_error_code="sample_limit",
            query_window_start=NOW,
            query_window_end=NOW,
            has_more=True,
        ),
    )

    with pytest.raises(SmartFilterGroundingError) as error:
        _fetch_trace_field_values(
            [PROJECT_ID], "model", "system_metric", search_query="gpt"
        )
    assert error.value.status_code == 422


def test_unknown_system_metric_does_not_construct_service(monkeypatch):
    construct = SimpleNamespace(called=False)

    def _service():
        construct.called = True
        return object()

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        _service,
    )

    with pytest.raises(SmartFilterGroundingError) as error:
        _fetch_trace_field_values(
            [PROJECT_ID],
            "untrusted-column-name",
            "system_metric",
            search_query="secret",
        )
    assert error.value.status_code == 422
    assert construct.called is False


def test_reader_exception_returns_sanitized_typed_unavailable(monkeypatch):
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

    with pytest.raises(SmartFilterGroundingError) as error:
        _fetch_trace_field_values(
            [PROJECT_ID],
            "final_status",
            "custom_attribute",
            search_query="reject",
        )
    assert error.value.status_code == 503
    assert secret not in error.value.public_message


def test_smart_schema_keeps_logical_source_separate_from_native_transport():
    schema = [
        {
            "field": "model",
            "property_id": "system_attribute:voice_calls:model",
            "label": "Model",
            "category": "system",
            "type": "string",
        }
    ]

    assert (
        _authorize_smart_property_schema(
            schema,
            source="traces",
            workspace=SimpleNamespace(),
            project_ids=(PROJECT_ID,),
        )
        == schema
    )


def test_smart_schema_accepts_session_native_alias_but_rejects_wrong_source():
    schema = [
        {
            "field": "session_id",
            "property_id": "system_attribute:sessions:session",
            "label": "Session ID",
            "category": "system",
            "type": "string",
        }
    ]

    assert (
        _authorize_smart_property_schema(
            schema,
            source="sessions",
            workspace=SimpleNamespace(),
            project_ids=(PROJECT_ID,),
        )
        == schema
    )
    with pytest.raises(SmartFilterGroundingError):
        _authorize_smart_property_schema(
            schema,
            source="traces",
            workspace=SimpleNamespace(),
            project_ids=(PROJECT_ID,),
        )


def test_smart_agent_does_not_alias_same_native_field_across_properties(monkeypatch):
    schema = [
        {
            "field": "status",
            "property_id": "system_attribute:traces:status",
            "label": "Status",
            "category": "system",
            "type": "string",
            "choices": ["OK"],
        },
        {
            "field": "status",
            "property_id": "annotation:status",
            "label": "Review status",
            "category": "annotation",
            "type": "string",
            "choices": ["Approved"],
        },
    ]
    submit = SimpleNamespace(
        id="submit",
        function=SimpleNamespace(
            name="submit_filter",
            arguments=(
                '{"filters":[{"field":"annotation:status",'
                '"operator":"is","value":"Approved"}]}'
            ),
        ),
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[submit]))
        ]
    )

    class _LLM:
        def __init__(self, **kwargs):
            pass

        def _get_completion_with_tools(self, *args, **kwargs):
            return response

    monkeypatch.setattr("agentic_eval.core.llm.llm.LLM", _LLM)

    assert _run_smart_agent("approved reviews", schema, lambda *a, **k: []) == [
        {
            "field": "status",
            "property_id": "annotation:status",
            "operator": "is",
            "value": "Approved",
        }
    ]
