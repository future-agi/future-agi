"""DEV-only attribute-catalog read routing and public fallback contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from tracer.services.clickhouse.attribute_reads import (
    AttributeKeyCursorPageRead,
    AttributeKeyRow,
    AttributeReadMetadata,
    AttributeValueCursorPageRead,
    AttributeValueRow,
)
from tracer.services.clickhouse.v2 import attribute_catalog_cutover as cutover
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    CatalogKeyCandidate,
    CatalogKeyCheckpoint,
    CatalogKeyPage,
    CatalogQualification,
    CatalogValueCandidate,
    CatalogValueCheckpoint,
    CatalogValuePage,
)

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
WINDOW_START = NOW - timedelta(days=365)
PROJECT_ID = "c4de3065-12b5-488c-a814-aa1c8e3f856f"


def _qualification() -> CatalogQualification:
    return CatalogQualification(
        source="span_attribute_catalog.qualification.v1",
        catalog_epoch=7,
        project_scope_fingerprint="a" * 64,
        window_start=WINDOW_START,
        window_end=NOW,
        qualification_fingerprint="b" * 64,
        source_fence_fingerprint="c" * 64,
    )


def _key_page(
    *, key: str = "catalog.key", has_more: bool = False, total_count: int = 37
) -> CatalogKeyPage:
    checkpoint = None
    if has_more:
        checkpoint = CatalogKeyCheckpoint(
            source="span_attribute_catalog.keys.v1",
            catalog_epoch=7,
            project_scope_fingerprint="a" * 64,
            window_start=WINDOW_START,
            window_end=NOW,
            attribute_types=(
                "string",
                "number",
                "boolean",
                "array",
                "map",
                "json",
            ),
            normalized_search="",
            query_fingerprint="d" * 64,
            qualification_fingerprint="b" * 64,
            key_folded=key,
            attribute_key=key,
            attribute_type_rank=1,
        )
    return CatalogKeyPage(
        candidates=(
            CatalogKeyCandidate(
                key,
                "string",
                WINDOW_START,
                NOW - timedelta(microseconds=1),
            ),
        ),
        has_more=has_more,
        next_checkpoint=checkpoint,
        qualification=_qualification(),
        total_count=total_count,
    )


def _value_page(*, value: str = "Straße", has_more: bool = False) -> CatalogValuePage:
    checkpoint = None
    if has_more:
        checkpoint = CatalogValueCheckpoint(
            source="span_attribute_catalog.values.v1",
            catalog_epoch=7,
            project_scope_fingerprint="a" * 64,
            window_start=WINDOW_START,
            window_end=NOW,
            attribute_key="catalog.key",
            attribute_types=(
                "string",
                "number",
                "boolean",
                "array",
                "map",
                "json",
            ),
            normalized_search="",
            query_fingerprint="d" * 64,
            qualification_fingerprint="b" * 64,
            value_fingerprint="e" * 64,
            attribute_type_rank=1,
        )
    return CatalogValuePage(
        candidates=(
            CatalogValueCandidate(
                attribute_key="catalog.key",
                attribute_type="string",
                scalar_kind="string",
                value=value,
                value_json=f'"{value}"',
                value_search_text=value,
                value_fingerprint="e" * 64,
                first_seen=WINDOW_START,
                last_seen=NOW - timedelta(microseconds=1),
            ),
        ),
        has_more=has_more,
        next_checkpoint=checkpoint,
        qualification=_qualification(),
    )


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_public_cutover_does_not_construct_catalog_reader_outside_read_mode(
    monkeypatch, settings, mode
):
    settings.SPAN_ATTRIBUTE_CATALOG_READ_MODE = mode
    monkeypatch.setattr(
        cutover,
        "_new_executor",
        lambda: pytest.fail("off/shadow mode must not construct a catalog client"),
    )

    attempt = cutover.try_catalog_key_page(
        project_ids=(PROJECT_ID,),
        window_start=WINDOW_START,
        window_end=NOW,
        page_size=10,
        search=None,
        after=None,
        request_deadline=None,
    )

    assert attempt.attempted is False
    assert attempt.page is None
    assert attempt.fallback_reason is None


@pytest.mark.unit
@override_settings(
    ENV_TYPE="prod",
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="read",
    SPAN_ATTRIBUTE_CATALOG_DEV_READ_ACK=cutover.CATALOG_DEV_READ_ACK,
)
def test_public_cutover_read_mode_is_impossible_outside_dev(monkeypatch):
    monkeypatch.setattr(
        cutover,
        "_new_executor",
        lambda: pytest.fail("production guard must close before client construction"),
    )

    attempt = cutover.try_catalog_key_page(
        project_ids=(PROJECT_ID,),
        window_start=WINDOW_START,
        window_end=NOW,
        page_size=10,
        search=None,
        after=None,
        request_deadline=None,
    )

    assert attempt.attempted is True
    assert attempt.page is None
    assert attempt.fallback_reason == "dev_read_guard_closed"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("environment", "cloud_deployment"),
    (("staging", ""), ("staging", "US"), ("prod", "DEV")),
)
def test_public_cutover_rejects_non_dev_deployment_combinations(
    monkeypatch,
    settings,
    environment,
    cloud_deployment,
):
    settings.ENV_TYPE = environment
    settings.CLOUD_DEPLOYMENT = cloud_deployment
    settings.SPAN_ATTRIBUTE_CATALOG_READ_MODE = "read"
    settings.SPAN_ATTRIBUTE_CATALOG_DEV_READ_ACK = cutover.CATALOG_DEV_READ_ACK
    settings.SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED = True
    monkeypatch.setattr(
        cutover,
        "_new_executor",
        lambda: pytest.fail("non-DEV deployment must not construct a client"),
    )

    attempt = cutover.try_catalog_key_page(
        project_ids=(PROJECT_ID,),
        window_start=WINDOW_START,
        window_end=NOW,
        page_size=10,
        search=None,
        after=None,
        request_deadline=None,
    )

    assert attempt.fallback_reason == "dev_read_guard_closed"


@pytest.mark.unit
@override_settings(
    ENV_TYPE="staging",
    CLOUD_DEPLOYMENT="DEV",
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="read",
    SPAN_ATTRIBUTE_CATALOG_DEV_READ_ACK=cutover.CATALOG_DEV_READ_ACK,
    SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=True,
    SPAN_ATTRIBUTE_CATALOG_HANDOFF_START=WINDOW_START,
    SPAN_ATTRIBUTE_CATALOG_HANDOFF_END=NOW,
    SPAN_ATTRIBUTE_CATALOG_EPOCH=7,
    SPAN_ATTRIBUTE_CATALOG_DATABASE="property_catalog_dev",
    SPAN_ATTRIBUTE_CATALOG_CH_DATABASE="property_catalog_dev",
)
def test_staging_dev_cloud_deployment_passes_catalog_specific_guard(monkeypatch):
    class Reader:
        def read_key_candidates(self, **_kwargs):
            return _key_page()

    monkeypatch.setattr(cutover, "_new_executor", object)
    monkeypatch.setattr(cutover, "_new_reader", lambda *_args, **_kwargs: Reader())

    attempt = cutover.try_catalog_key_page(
        project_ids=(PROJECT_ID,),
        window_start=WINDOW_START,
        window_end=NOW,
        page_size=10,
        search=None,
        after=None,
        request_deadline=None,
    )

    assert attempt.used_catalog is True


@pytest.mark.unit
@override_settings(
    ENV_TYPE="dev",
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="read",
    SPAN_ATTRIBUTE_CATALOG_DEV_READ_ACK=cutover.CATALOG_DEV_READ_ACK,
    SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=True,
    SPAN_ATTRIBUTE_CATALOG_HANDOFF_START=WINDOW_START,
    SPAN_ATTRIBUTE_CATALOG_HANDOFF_END=NOW,
    SPAN_ATTRIBUTE_CATALOG_EPOCH=7,
    SPAN_ATTRIBUTE_CATALOG_DATABASE="property_catalog_dev",
    SPAN_ATTRIBUTE_CATALOG_CH_DATABASE="property_catalog_dev",
)
def test_dev_read_routes_only_the_authorized_frozen_scope(monkeypatch):
    captured: dict[str, Any] = {}

    class Reader:
        def read_key_candidates(self, **kwargs):
            captured["read"] = kwargs
            return _key_page()

    monkeypatch.setattr(cutover, "_new_executor", object)

    def construct(_executor, **kwargs):
        captured["reader"] = kwargs
        return Reader()

    monkeypatch.setattr(cutover, "_new_reader", construct)

    attempt = cutover.try_catalog_key_page(
        project_ids=(PROJECT_ID,),
        window_start=WINDOW_START,
        window_end=NOW,
        page_size=10,
        search="ss",
        after=None,
        request_deadline=None,
    )

    assert attempt.attempted is True
    assert attempt.used_catalog is True
    assert attempt.page == _key_page()
    assert captured["reader"] == {
        "project_ids": (PROJECT_ID,),
        "catalog_epoch": 7,
        "window_start": WINDOW_START,
        "window_end": NOW,
        "catalog_database": "property_catalog_dev",
    }
    assert captured["read"]["search"] == "ss"


@pytest.mark.unit
@override_settings(
    ENV_TYPE="dev",
    SPAN_ATTRIBUTE_CATALOG_READ_MODE="read",
    SPAN_ATTRIBUTE_CATALOG_DEV_READ_ACK=cutover.CATALOG_DEV_READ_ACK,
    SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=False,
)
def test_dev_read_requires_runtime_snapshot_guard_before_client_construction(
    monkeypatch,
):
    monkeypatch.setattr(
        cutover,
        "_new_executor",
        lambda: pytest.fail("closed snapshot guard must not construct a client"),
    )

    attempt = cutover.try_catalog_key_page(
        project_ids=(PROJECT_ID,),
        window_start=WINDOW_START,
        window_end=NOW,
        page_size=10,
        search=None,
        after=None,
        request_deadline=None,
    )

    assert attempt.attempted is True
    assert attempt.page is None
    assert attempt.fallback_reason == "snapshot_guard_closed"


@pytest.mark.unit
def test_catalog_checkpoint_state_round_trips_without_weakening_cursor_identity():
    checkpoint = _key_page(has_more=True).next_checkpoint
    assert checkpoint is not None

    encoded = cutover.key_checkpoint_state(checkpoint)

    assert cutover.key_checkpoint_from_state(encoded) == checkpoint
    with pytest.raises(ValueError, match="invalid catalog key checkpoint"):
        cutover.key_checkpoint_from_state(encoded[:-1])


def _authenticated_get(path: str, data: dict[str, Any]):
    request = APIRequestFactory().get(path, data)
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    return request


def _metadata(window_start, window_end) -> AttributeReadMetadata:
    return AttributeReadMetadata(
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        query_window_start=window_start,
        query_window_end=window_end,
        query_count=1,
    )


@pytest.mark.unit
def test_span_key_view_publishes_catalog_rows_with_unchanged_payload_shape(
    monkeypatch,
):
    from tracer.views import span_attributes

    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    monkeypatch.setattr(
        span_attributes,
        "try_catalog_key_page",
        lambda **_kwargs: cutover.CatalogReadAttempt(True, _key_page()),
    )
    monkeypatch.setattr(
        span_attributes,
        "AttributeReadSelector",
        lambda **_kwargs: pytest.fail("catalog success must not query spans"),
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_ID, "page_size": 10},
    )

    response = span_attributes.SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert response["X-FutureAGI-Attribute-Catalog"] == "catalog"
    assert response.data["result"] == [
        {
            "key": "catalog.key",
            "type": "string",
            "count": 1,
            "types": ("string",),
            "count_exact": False,
        }
    ]
    assert response.data["has_more"] is False
    assert response.data["next_cursor"] is None
    assert response.data["query_complete"] is True
    assert response.data["total_count"] == 37


@pytest.mark.unit
def test_span_key_workspace_catalog_page_does_not_publish_batch_local_total(
    monkeypatch,
):
    from tracer.views import span_attributes

    monkeypatch.setattr(
        span_attributes,
        "_workspace_project_batch",
        lambda _request: ((PROJECT_ID,), False),
    )
    monkeypatch.setattr(
        span_attributes,
        "_run_span_attribute_pg_read",
        lambda _deadline, operation: operation(),
    )
    monkeypatch.setattr(
        span_attributes,
        "try_catalog_key_page",
        lambda **_kwargs: cutover.CatalogReadAttempt(True, _key_page(total_count=37)),
    )
    monkeypatch.setattr(
        span_attributes,
        "AttributeReadSelector",
        lambda **_kwargs: pytest.fail("catalog success must not query spans"),
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"workspace_scope": True, "page_size": 10},
    )

    response = span_attributes.SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert response["X-FutureAGI-Attribute-Catalog"] == "catalog"
    assert "total_count" not in response.data


@pytest.mark.unit
def test_span_key_signed_cursor_restores_frozen_catalog_checkpoint(monkeypatch):
    from tracer.views import span_attributes

    first_page = _key_page(has_more=True)
    captured_after = []

    def read_catalog(**kwargs):
        captured_after.append(kwargs["after"])
        if len(captured_after) == 1:
            return cutover.CatalogReadAttempt(True, first_page)
        return cutover.CatalogReadAttempt(True, _key_page(key="next.key"))

    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    monkeypatch.setattr(span_attributes, "try_catalog_key_page", read_catalog)
    monkeypatch.setattr(
        span_attributes,
        "AttributeReadSelector",
        lambda **_kwargs: pytest.fail("catalog continuation must not query spans"),
    )

    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_ID, "page_size": 1},
    )
    first_response = span_attributes.SpanAttributeKeysView.as_view()(first_request)
    assert first_response.status_code == 200
    assert first_response.data["next_cursor"]

    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "project_id": PROJECT_ID,
            "page_size": 1,
            "cursor": first_response.data["next_cursor"],
        },
    )
    second_response = span_attributes.SpanAttributeKeysView.as_view()(second_request)

    assert second_response.status_code == 200
    assert captured_after == [None, first_page.next_checkpoint]
    assert [row["key"] for row in second_response.data["result"]] == ["next.key"]
    assert second_response.data["has_more"] is False


@pytest.mark.unit
def test_catalog_cursor_failure_restarts_exact_authoritative_window_with_seen_state(
    monkeypatch,
):
    from tracer.views import span_attributes

    calls = 0

    def read_catalog(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return cutover.CatalogReadAttempt(True, _key_page(has_more=True))
        return cutover.CatalogReadAttempt(True, None, "source_stream_open")

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def read_key_cursor_page(self, _project_ids, **kwargs):
            assert kwargs["segment_end"] == kwargs["window_end"]
            assert kwargs["segment_start"] is None
            assert kwargs["before_identity"] is None
            assert kwargs["resume_identity"] is None
            assert kwargs["seen_key_count"] == 1
            return AttributeKeyCursorPageRead(
                rows=(AttributeKeyRow("authoritative.next", "string", 2),),
                metadata=_metadata(kwargs["window_start"], kwargs["window_end"]),
                has_more=False,
                browse_status="exhausted",
                next_segment_end=kwargs["window_start"],
                next_before_identity=None,
                next_resume_identity=None,
                next_resume_key_offset=0,
                seen_key_digests=(),
            )

    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    monkeypatch.setattr(span_attributes, "try_catalog_key_page", read_catalog)
    monkeypatch.setattr(span_attributes, "AttributeReadSelector", Selector)

    first_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_ID, "page_size": 1},
    )
    first_response = span_attributes.SpanAttributeKeysView.as_view()(first_request)
    second_request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "project_id": PROJECT_ID,
            "page_size": 1,
            "cursor": first_response.data["next_cursor"],
        },
    )

    second_response = span_attributes.SpanAttributeKeysView.as_view()(second_request)

    assert second_response.status_code == 200
    assert second_response["X-FutureAGI-Attribute-Catalog"] == "fallback"
    assert [row["key"] for row in second_response.data["result"]] == [
        "authoritative.next"
    ]


@pytest.mark.unit
def test_span_key_compatibility_view_uses_complete_catalog_inventory(monkeypatch):
    from tracer.views import span_attributes

    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    monkeypatch.setattr(
        span_attributes,
        "try_catalog_key_page",
        lambda **_kwargs: cutover.CatalogReadAttempt(True, _key_page()),
    )
    monkeypatch.setattr(
        span_attributes,
        "AttributeReadSelector",
        lambda **_kwargs: pytest.fail("complete catalog must bypass spans"),
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_ID},
    )

    response = span_attributes.SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert response["X-FutureAGI-Attribute-Catalog"] == "catalog"
    assert response.data["result"][0]["key"] == "catalog.key"
    assert "has_more" not in response.data


@pytest.mark.unit
def test_span_key_catalog_failure_uses_authoritative_selector_and_marks_reason(
    monkeypatch,
):
    from tracer.views import span_attributes

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def read_key_cursor_page(self, _project_ids, **kwargs):
            return AttributeKeyCursorPageRead(
                rows=(AttributeKeyRow("authoritative.key", "string", 3),),
                metadata=_metadata(kwargs["window_start"], kwargs["window_end"]),
                has_more=False,
                browse_status="exhausted",
                next_segment_end=kwargs["window_start"],
                next_before_identity=None,
                next_resume_identity=None,
                next_resume_key_offset=0,
                seen_key_digests=(),
            )

    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    monkeypatch.setattr(
        span_attributes,
        "try_catalog_key_page",
        lambda **_kwargs: cutover.CatalogReadAttempt(True, None, "source_stream_open"),
    )
    monkeypatch.setattr(span_attributes, "AttributeReadSelector", Selector)
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_ID, "page_size": 10},
    )

    response = span_attributes.SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert response["X-FutureAGI-Attribute-Catalog"] == "fallback"
    assert response["X-FutureAGI-Attribute-Catalog-Fallback"] == "source_stream_open"
    assert response.data["result"] == [
        {
            "key": "authoritative.key",
            "type": "string",
            "count": 3,
            "count_exact": False,
        }
    ]


class _ProjectQuery:
    def filter(self, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def values_list(self, *_args, **_kwargs):
        return [PROJECT_ID]


@pytest.mark.unit
def test_dashboard_custom_value_view_publishes_catalog_typed_values(monkeypatch):
    from tracer.views import dashboard

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def retained_window_start(self, _project_ids, *, window_end):
            return window_end - timedelta(days=365)

        def read_value_cursor_page(self, *_args, **_kwargs):
            pytest.fail("catalog success must not query span values")

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
        "try_catalog_value_page",
        lambda **_kwargs: cutover.CatalogReadAttempt(True, _value_page()),
    )
    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "catalog.key",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_ID,
            "source": "traces",
            "page_size": 10,
        },
    )

    response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 200
    assert response["X-FutureAGI-Attribute-Catalog"] == "catalog"
    assert response.data["result"]["values"] == [
        {"value": "Straße", "type": "string", "label": "Straße"}
    ]
    assert response.data["result"]["has_more"] is False
    assert response.data["result"]["next_cursor"] is None


@pytest.mark.unit
def test_dashboard_signed_cursor_restores_catalog_value_checkpoint(monkeypatch):
    from tracer.views import dashboard

    first_page = _value_page(has_more=True)
    captured_after = []

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def retained_window_start(self, _project_ids, *, window_end):
            return window_end - timedelta(days=365)

        def read_value_cursor_page(self, *_args, **_kwargs):
            pytest.fail("catalog continuation must not query span values")

    def read_catalog(**kwargs):
        captured_after.append(kwargs["after"])
        if len(captured_after) == 1:
            return cutover.CatalogReadAttempt(True, first_page)
        return cutover.CatalogReadAttempt(True, _value_page(value="Next"))

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
    monkeypatch.setattr(dashboard, "try_catalog_value_page", read_catalog)

    first_request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "catalog.key",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_ID,
            "source": "traces",
            "page_size": 1,
        },
    )
    first_response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(
        first_request
    )
    assert first_response.status_code == 200
    assert first_response.data["result"]["next_cursor"]

    second_request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "catalog.key",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_ID,
            "source": "traces",
            "page_size": 1,
            "cursor": first_response.data["result"]["next_cursor"],
        },
    )
    second_response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(
        second_request
    )

    assert second_response.status_code == 200
    assert captured_after == [None, first_page.next_checkpoint]
    assert second_response.data["result"]["values"] == [
        {"value": "Next", "type": "string", "label": "Next"}
    ]
    assert second_response.data["result"]["has_more"] is False


@pytest.mark.unit
def test_dashboard_custom_value_compatibility_view_uses_complete_catalog(
    monkeypatch,
):
    from tracer.views import dashboard

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
    monkeypatch.setattr(
        dashboard,
        "try_catalog_value_page",
        lambda **_kwargs: cutover.CatalogReadAttempt(True, _value_page()),
    )
    monkeypatch.setattr(
        dashboard,
        "AttributeReadSelector",
        lambda **_kwargs: pytest.fail("complete catalog must bypass span values"),
    )
    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "catalog.key",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_ID,
            "source": "traces",
        },
    )

    response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 200
    assert response["X-FutureAGI-Attribute-Catalog"] == "catalog"
    assert response.data["result"]["values"] == [
        {"value": "Straße", "type": "string", "label": "Straße"}
    ]
    assert "has_more" not in response.data["result"]


@pytest.mark.unit
def test_dashboard_catalog_failure_preserves_authoritative_typed_value_response(
    monkeypatch,
):
    from tracer.views import dashboard

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def retained_window_start(self, _project_ids, *, window_end):
            return window_end - timedelta(days=365)

        def read_value_cursor_page(self, _project_ids, _key, **kwargs):
            return AttributeValueCursorPageRead(
                rows=(AttributeValueRow("authoritative", "string", 2),),
                metadata=_metadata(kwargs["window_start"], kwargs["window_end"]),
                has_more=False,
                next_segment_end=kwargs["window_start"],
                next_before_identity=None,
                next_resume_identity=None,
                next_resume_member_offset=0,
                seen_value_digests=(),
            )

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
        "try_catalog_value_page",
        lambda **_kwargs: cutover.CatalogReadAttempt(
            True, None, "checkpoint_declared_gap"
        ),
    )
    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "catalog.key",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_ID,
            "source": "traces",
            "page_size": 10,
        },
    )

    response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 200
    assert response["X-FutureAGI-Attribute-Catalog"] == "fallback"
    assert (
        response["X-FutureAGI-Attribute-Catalog-Fallback"] == "checkpoint_declared_gap"
    )
    assert response.data["result"]["values"] == [
        {"value": "authoritative", "type": "string", "label": "authoritative"}
    ]
