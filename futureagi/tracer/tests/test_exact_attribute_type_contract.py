"""Regression coverage for the typed exact trace-attribute contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.services.clickhouse.attribute_reads import (
    AttributeKeyRead,
    AttributeKeyRow,
    AttributeReadMetadata,
    AttributeReadSelector,
)
from tracer.views.span_attributes import SpanAttributeKeysView

PROJECT_ID = "c4de3065-12b5-488c-a814-aa1c8e3f856f"
NOW = datetime(2026, 8, 2, 12, tzinfo=UTC)


def _metadata() -> AttributeReadMetadata:
    return AttributeReadMetadata(
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        query_window_start=NOW - timedelta(days=365),
        query_window_end=NOW,
        query_count=2,
    )


@pytest.mark.parametrize(
    ("key", "attribute_type"),
    [
        ("retry_count", "number"),
        ("was_escalated", "boolean"),
        ("customer_context", "map"),
    ],
)
def test_exact_attribute_key_response_preserves_numeric_and_boolean_types(
    monkeypatch,
    key,
    attribute_type,
):
    captured = {}

    def discover_keys(self, project_ids, exact_key=None):
        captured.update(project_ids=project_ids, exact_key=exact_key)
        return AttributeKeyRead(
            (AttributeKeyRow(key, attribute_type, 1),),
            _metadata(),
        )

    monkeypatch.setattr(AttributeReadSelector, "discover_keys", discover_keys)
    monkeypatch.setattr(
        "tracer.views.span_attributes._project_is_in_request_scope",
        lambda _request, _project_id: True,
    )

    request = APIRequestFactory().get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_ID, "q": key},
    )
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    response = SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert response.data["result"] == [
        {"key": key, "type": attribute_type, "count": 1, "count_exact": False}
    ]
    assert response.data["query_complete"] is True
    assert response.data["query_status"] == "complete"
    assert captured == {"project_ids": [PROJECT_ID], "exact_key": key}
