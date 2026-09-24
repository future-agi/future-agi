"""Fail-open contracts for the inactive span-attribute catalog shadow."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.services.clickhouse.attribute_reads import (
    AttributeKeyRead,
    AttributeKeyRow,
    AttributeReadMetadata,
    AttributeValueRead,
    AttributeValueRow,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2 import attribute_catalog_shadow as shadow
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    CatalogKeyCandidate,
    CatalogKeyPage,
    CatalogQualification,
    CatalogUnavailable,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
PROJECT_ID = "c4de3065-12b5-488c-a814-aa1c8e3f856f"
OTHER_PROJECT_ID = "790063cd-bc6a-4ad0-866b-35f11b5bc29b"


def _metadata() -> AttributeReadMetadata:
    return AttributeReadMetadata(
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        query_window_start=NOW - timedelta(days=365),
        query_window_end=NOW,
        query_count=1,
    )


def _qualification() -> CatalogQualification:
    return CatalogQualification(
        source="span_attribute_catalog.qualification.v1",
        catalog_epoch=7,
        project_scope_fingerprint="a" * 64,
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        qualification_fingerprint="b" * 64,
    )


class _Reader:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result
        self.error = error

    def read_key_candidates(self, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.result


class _RecordingLogger:
    def __init__(self):
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs) -> None:
        self.events.append((event, kwargs))

    def warning(self, event: str, **kwargs) -> None:
        self.events.append((event, kwargs))


@pytest.fixture(autouse=True)
def _no_metric_side_effect(monkeypatch):
    monkeypatch.setattr(shadow, "_record_metrics", lambda _observation: None)


@pytest.mark.unit
@override_settings(
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="off",
    SPAN_ATTRIBUTE_CATALOG_EPOCH=7,
)
def test_shadow_is_off_by_default_and_does_not_construct_a_reader(monkeypatch):
    monkeypatch.setattr(
        shadow,
        "_new_reader",
        lambda *_args, **_kwargs: pytest.fail("off mode must not construct a reader"),
    )

    observed = shadow.run_catalog_key_shadow(
        project_ids=(PROJECT_ID,),
        authoritative_rows=(AttributeKeyRow("private.key", "string", 1),),
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert observed is None


@pytest.mark.unit
@override_settings(
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="shadow",
    SPAN_ATTRIBUTE_CATALOG_EPOCH=7,
)
def test_shadow_qualification_fails_closed_when_activation_is_missing(monkeypatch):
    calls = 0

    def execute(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(data=[])

    monkeypatch.setattr(
        shadow._LazyV2CatalogExecutor,
        "execute",
        execute,
    )

    observed = shadow.run_catalog_key_shadow(
        project_ids=(PROJECT_ID,),
        authoritative_rows=(AttributeKeyRow("private.key", "string", 1),),
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert observed is not None
    assert observed.outcome == "unavailable"
    assert observed.reason == "activation_missing"
    assert calls == 1


@pytest.mark.unit
@override_settings(
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="shadow",
    SPAN_ATTRIBUTE_CATALOG_EPOCH=7,
    SPAN_ATTRIBUTE_CATALOG_DATABASE="property_catalog_dev_normal_0813b",
)
def test_shadow_routes_reader_to_isolated_catalog_database(monkeypatch):
    captured: dict[str, Any] = {}

    def construct(_executor, **kwargs):
        captured.update(kwargs)
        return _Reader(CatalogUnavailable("activation_writer_lag"))

    monkeypatch.setattr(shadow, "_new_reader", construct)

    observed = shadow.run_catalog_key_shadow(
        project_ids=(PROJECT_ID,),
        authoritative_rows=(),
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert observed is not None
    assert captured["catalog_database"] == "property_catalog_dev_normal_0813b"


@pytest.mark.unit
@override_settings(
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="shadow",
    SPAN_ATTRIBUTE_CATALOG_EPOCH=7,
    SPAN_ATTRIBUTE_CATALOG_DATABASE="",
)
def test_shadow_unset_catalog_database_preserves_reader_default(monkeypatch):
    captured: dict[str, Any] = {}

    def construct(_executor, **kwargs):
        captured.update(kwargs)
        return _Reader(CatalogUnavailable("activation_writer_lag"))

    monkeypatch.setattr(shadow, "_new_reader", construct)

    observed = shadow.run_catalog_key_shadow(
        project_ids=(PROJECT_ID,),
        authoritative_rows=(),
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert observed is not None
    assert captured["catalog_database"] is None


@pytest.mark.unit
@override_settings(
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="shadow",
    SPAN_ATTRIBUTE_CATALOG_EPOCH=7,
)
def test_writer_lag_is_unavailable_and_logs_only_identity_hashes(monkeypatch):
    recorder = _RecordingLogger()
    monkeypatch.setattr(shadow, "logger", recorder)
    monkeypatch.setattr(
        shadow,
        "_new_reader",
        lambda *_args, **_kwargs: _Reader(CatalogUnavailable("activation_writer_lag")),
    )

    observed = shadow.run_catalog_key_shadow(
        project_ids=(PROJECT_ID,),
        authoritative_rows=(AttributeKeyRow("secret.customer.key", "string", 1),),
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert observed is not None
    assert observed.outcome == "unavailable"
    assert observed.reason == "activation_writer_lag"
    serialized = json.dumps(recorder.events)
    assert PROJECT_ID not in serialized
    assert "secret.customer.key" not in serialized
    assert len(observed.project_scope_hash) == 64
    assert len(observed.query_identity_hash) == 64


@pytest.mark.unit
@override_settings(
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="shadow",
    SPAN_ATTRIBUTE_CATALOG_EPOCH=7,
)
def test_mismatch_reports_hashes_without_exposing_tenant_keys(monkeypatch):
    recorder = _RecordingLogger()
    monkeypatch.setattr(shadow, "logger", recorder)
    catalog_page = CatalogKeyPage(
        candidates=(
            CatalogKeyCandidate(
                "different.private.key",
                "string",
                NOW - timedelta(days=2),
                NOW - timedelta(days=1),
            ),
        ),
        has_more=False,
        next_checkpoint=None,
        qualification=_qualification(),
    )
    monkeypatch.setattr(
        shadow,
        "_new_reader",
        lambda *_args, **_kwargs: _Reader(catalog_page),
    )

    observed = shadow.run_catalog_key_shadow(
        project_ids=(PROJECT_ID,),
        authoritative_rows=(AttributeKeyRow("authoritative.private.key", "string", 1),),
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert observed is not None
    assert observed.outcome == "mismatch"
    assert observed.mismatch_count == 2
    assert len(observed.mismatch_identity_hashes) == 2
    assert all(len(identity) == 64 for identity in observed.mismatch_identity_hashes)
    serialized = json.dumps(recorder.events)
    assert PROJECT_ID not in serialized
    assert "authoritative.private.key" not in serialized
    assert "different.private.key" not in serialized


@pytest.mark.unit
@override_settings(
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="shadow",
    SPAN_ATTRIBUTE_CATALOG_EPOCH=7,
)
def test_shadow_error_is_sanitized_and_never_raises(monkeypatch):
    recorder = _RecordingLogger()
    monkeypatch.setattr(shadow, "logger", recorder)
    monkeypatch.setattr(
        shadow,
        "_new_reader",
        lambda *_args, **_kwargs: _Reader(
            error=RuntimeError("secret SQL and private.customer.value")
        ),
    )

    observed = shadow.run_catalog_key_shadow(
        project_ids=(PROJECT_ID,),
        authoritative_rows=(AttributeKeyRow("private.customer.value", "string", 1),),
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert observed is not None
    assert observed.outcome == "error"
    assert observed.reason == "shadow_exception"
    serialized = json.dumps(recorder.events)
    assert "secret SQL" not in serialized
    assert "private.customer.value" not in serialized


@pytest.mark.unit
@override_settings(
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="shadow",
    SPAN_ATTRIBUTE_CATALOG_EPOCH=7,
)
def test_shadow_rejects_more_than_64_projects_before_reader_construction(
    monkeypatch,
):
    projects = tuple(f"00000000-0000-4000-8000-{index:012d}" for index in range(65))
    monkeypatch.setattr(
        shadow,
        "_new_reader",
        lambda *_args, **_kwargs: pytest.fail("oversized scope must not be queried"),
    )

    observed = shadow.run_catalog_key_shadow(
        project_ids=projects,
        authoritative_rows=(),
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
    )

    assert observed is not None
    assert observed.outcome == "skipped"
    assert observed.reason == "project_scope_too_large"


@pytest.mark.unit
@override_settings(
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="shadow",
    SPAN_ATTRIBUTE_CATALOG_EPOCH=7,
)
def test_shadow_executor_uses_at_most_request_remaining_wall(monkeypatch):
    calls: list[tuple[int, dict[str, Any]]] = []

    class Executor:
        def execute(self, _query, _params, *, timeout_ms, settings):
            calls.append((timeout_ms, settings))
            return SimpleNamespace(data=[])

    class Reader:
        def __init__(self, executor):
            self.executor = executor

        def read_key_candidates(self, **_kwargs):
            self.executor.execute(
                "SELECT 1",
                {},
                timeout_ms=10_000,
                settings={},
            )
            return CatalogUnavailable("activation_writer_lag")

    monkeypatch.setattr(shadow, "_LazyV2CatalogExecutor", Executor)
    monkeypatch.setattr(
        shadow,
        "_new_reader",
        lambda executor, **_kwargs: Reader(executor),
    )
    request_deadline = ReadDeadline.start(750)

    observed = shadow.run_catalog_key_shadow(
        project_ids=(PROJECT_ID,),
        authoritative_rows=(),
        window_start=NOW - timedelta(days=365),
        window_end=NOW,
        request_deadline=request_deadline,
    )

    assert observed is not None
    assert observed.outcome == "unavailable"
    assert len(calls) == 1
    timeout_ms, query_settings = calls[0]
    assert 1 <= timeout_ms <= 750
    assert query_settings["max_execution_time"] == timeout_ms / 1_000


def _authenticated_get(path: str, data: dict[str, Any]):
    request = APIRequestFactory().get(path, data)
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    return request


class _ProjectQuery:
    def filter(self, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def values_list(self, *_args, **_kwargs):
        return [PROJECT_ID]


@pytest.mark.unit
def test_span_key_view_keeps_authoritative_response_when_shadow_raises(monkeypatch):
    from tracer.views import span_attributes

    read = AttributeKeyRead(
        (AttributeKeyRow("authoritative.key", "string", 3),),
        _metadata(),
    )

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def discover_keys(self, *_args, **_kwargs):
            return read

    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    monkeypatch.setattr(span_attributes, "AttributeReadSelector", Selector)
    monkeypatch.setattr(
        span_attributes,
        "run_catalog_key_shadow",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("private shadow defect")),
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_ID},
    )

    response = span_attributes.SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert response.data["result"] == [
        {
            "key": "authoritative.key",
            "type": "string",
            "count": 3,
            "count_exact": False,
        }
    ]


@pytest.mark.unit
def test_dashboard_value_view_keeps_authoritative_response_when_shadow_raises(
    monkeypatch,
):
    from tracer.views import dashboard

    read = AttributeValueRead(
        (AttributeValueRow("authoritative-value", "string", 2),),
        _metadata(),
    )

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def read_values(self, *_args, **_kwargs):
            return read

    monkeypatch.setattr(
        dashboard,
        "project_queryset_for_request",
        lambda _request: _ProjectQuery(),
    )
    monkeypatch.setattr(
        dashboard,
        "_run_filter_value_pg_read",
        lambda _deadline, read_fn: read_fn(),
    )
    monkeypatch.setattr(dashboard, "AttributeReadSelector", Selector)
    monkeypatch.setattr(
        dashboard,
        "run_catalog_value_shadow",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("private shadow defect")),
    )
    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "private.attribute.key",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_ID,
            "source": "traces",
        },
    )

    response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 200
    assert response.data["result"]["values"] == [
        {
            "value": "authoritative-value",
            "type": "string",
            "label": "authoritative-value",
        }
    ]
