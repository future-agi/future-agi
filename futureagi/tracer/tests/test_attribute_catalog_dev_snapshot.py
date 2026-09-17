"""Honesty and fallback contracts for the explicit DEV catalog snapshot."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
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
    attribute_key_cursor_digest,
    attribute_value_cursor_digest,
)
from tracer.services.clickhouse.filter_value_reads import (
    EndUserFilterValueCursorPageRead,
    FilterValueCursorPageRead,
)
from tracer.services.clickhouse.list_cursor import ListCursorError, encode_list_cursor
from tracer.services.clickhouse.v2 import attribute_catalog_cutover as cutover
from tracer.services.clickhouse.v2 import attribute_catalog_snapshot as snapshot
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    CatalogKeyCandidate,
    CatalogKeyCheckpoint,
    CatalogKeyPage,
    CatalogQualification,
    CatalogValueCandidate,
    CatalogValuePage,
)

WINDOW_START = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
PROJECT_ID = "c4de3065-12b5-488c-a814-aa1c8e3f856f"

SNAPSHOT_SETTINGS = {
    "ENV_TYPE": "dev",
    "SPAN_ATTRIBUTE_CATALOG_READ_MODE": "read",
    "SPAN_ATTRIBUTE_CATALOG_DEV_READ_ACK": cutover.CATALOG_DEV_READ_ACK,
    "SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED": True,
    "SPAN_ATTRIBUTE_CATALOG_HANDOFF_START": WINDOW_START,
    "SPAN_ATTRIBUTE_CATALOG_HANDOFF_END": WINDOW_END,
}


def _qualification() -> CatalogQualification:
    return CatalogQualification(
        source="span_attribute_catalog.qualification.v1",
        catalog_epoch=7,
        project_scope_fingerprint="a" * 64,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        qualification_fingerprint="b" * 64,
        source_fence_fingerprint="c" * 64,
    )


def _key_page(*, has_more: bool = False) -> CatalogKeyPage:
    checkpoint = None
    if has_more:
        checkpoint = CatalogKeyCheckpoint(
            source="span_attribute_catalog.keys.v1",
            catalog_epoch=7,
            project_scope_fingerprint="a" * 64,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            attribute_types=(
                "string",
                "number",
                "boolean",
                "array",
                "map",
                "json",
            ),
            normalized_search="",
            query_fingerprint="e" * 64,
            qualification_fingerprint="b" * 64,
            key_folded="snapshot.key",
            attribute_key="snapshot.key",
            attribute_type_rank=1,
        )
    return CatalogKeyPage(
        candidates=(
            CatalogKeyCandidate(
                "snapshot.key",
                "string",
                WINDOW_START,
                WINDOW_END - timedelta(microseconds=1),
            ),
        ),
        has_more=has_more,
        next_checkpoint=checkpoint,
        qualification=_qualification(),
        query_count=4,
    )


def _value_page() -> CatalogValuePage:
    return CatalogValuePage(
        candidates=(
            CatalogValueCandidate(
                attribute_key="snapshot.key",
                attribute_type="string",
                scalar_kind="string",
                value="snapshot.value",
                value_json='"snapshot.value"',
                value_search_text="snapshot.value",
                value_fingerprint="d" * 64,
                first_seen=WINDOW_START,
                last_seen=WINDOW_END - timedelta(microseconds=1),
            ),
        ),
        has_more=False,
        next_checkpoint=None,
        qualification=_qualification(),
        query_count=4,
    )


def _metadata(window_start: datetime, window_end: datetime) -> AttributeReadMetadata:
    return AttributeReadMetadata(
        query_complete=True,
        query_status="complete",
        query_error_code=None,
        query_window_start=window_start,
        query_window_end=window_end,
        query_count=1,
    )


def _authenticated_get(path: str, data: dict[str, Any]):
    request = APIRequestFactory().get(path, data)
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    return request


@pytest.mark.unit
def test_snapshot_cursor_decoder_accepts_only_baseline_or_frozen_query_mode():
    scope = {"principal_id": "test"}
    baseline_query = {"project_id": PROJECT_ID, "mode": "recent_attribute_keys"}
    token = encode_list_cursor(
        resource="span_attribute_keys",
        scope=scope,
        query={**baseline_query, "query_window_mode": "unsupported_mode"},
        page_size=10,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        order=(WINDOW_END, (), (), 0, ()),
        seen_rows=0,
    )

    with pytest.raises(ListCursorError) as exc_info:
        snapshot.decode_catalog_snapshot_list_cursor(
            token,
            resource="span_attribute_keys",
            scope=scope,
            query=baseline_query,
            page_size=10,
        )

    assert exc_info.value.code == "cursor_mismatch"


@pytest.mark.unit
@override_settings(**SNAPSHOT_SETTINGS)
@pytest.mark.parametrize(
    ("start", "end"),
    [
        (WINDOW_START + timedelta(minutes=1), WINDOW_END),
        (
            WINDOW_START.replace(tzinfo=timezone(timedelta(hours=1))),
            WINDOW_END,
        ),
        (WINDOW_END, WINDOW_START),
    ],
)
def test_runtime_snapshot_bounds_fail_closed_before_public_routing(
    monkeypatch,
    start,
    end,
):
    monkeypatch.setattr(
        cutover,
        "_new_executor",
        lambda: pytest.fail("invalid snapshot bounds must fail before ClickHouse"),
    )

    with override_settings(
        SPAN_ATTRIBUTE_CATALOG_HANDOFF_START=start,
        SPAN_ATTRIBUTE_CATALOG_HANDOFF_END=end,
    ):
        assert snapshot.catalog_dev_snapshot_window() is None
        attempt = cutover.try_catalog_key_page(
            project_ids=(PROJECT_ID,),
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            page_size=10,
            search=None,
            after=None,
            request_deadline=None,
        )
    assert attempt.fallback_reason == "snapshot_config_invalid"


@pytest.mark.unit
@override_settings(**SNAPSHOT_SETTINGS)
def test_span_key_fresh_snapshot_pins_public_window_and_marks_response(monkeypatch):
    from tracer.views import span_attributes

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )

    def read_catalog(**kwargs):
        captured.update(kwargs)
        return cutover.CatalogReadAttempt(True, _key_page())

    monkeypatch.setattr(span_attributes, "try_catalog_key_page", read_catalog)
    monkeypatch.setattr(
        span_attributes,
        "AttributeReadSelector",
        lambda **_kwargs: pytest.fail("qualified snapshot must bypass spans"),
    )
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_ID, "page_size": 10},
    )

    response = span_attributes.SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert (captured["window_start"], captured["window_end"]) == (
        WINDOW_START,
        WINDOW_END,
    )
    assert captured["attribute_types"] == (
        "string",
        "number",
        "boolean",
        "array",
        "map",
    )
    assert response.data["query_window_start"] == "2026-08-01T00:00:00Z"
    assert response.data["query_window_end"] == "2026-08-02T00:00:00Z"
    assert response.data["query_window_mode"] == "frozen_snapshot"
    assert response.data["query_count"] == 4
    assert response["X-FutureAGI-Attribute-Catalog-Window"] == "frozen-snapshot"


@pytest.mark.unit
@override_settings(**SNAPSHOT_SETTINGS)
def test_span_key_eval_mapping_catalog_includes_json_type(monkeypatch):
    from tracer.views import span_attributes

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )

    def read_catalog(**kwargs):
        captured.update(kwargs)
        return cutover.CatalogReadAttempt(True, _key_page())

    monkeypatch.setattr(span_attributes, "try_catalog_key_page", read_catalog)
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {
            "project_id": PROJECT_ID,
            "page_size": 10,
            "discovery_mode": "eval_mapping",
        },
    )

    response = span_attributes.SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert captured["attribute_types"] == (
        "string",
        "number",
        "boolean",
        "array",
        "map",
        "json",
    )


@pytest.mark.unit
@override_settings(**SNAPSHOT_SETTINGS)
def test_span_key_continuation_keeps_signed_snapshot_bounds(monkeypatch):
    from tracer.views import span_attributes

    captured_windows = []
    direct_windows = []
    calls = 0

    def read_catalog(**kwargs):
        nonlocal calls
        calls += 1
        captured_windows.append((kwargs["window_start"], kwargs["window_end"]))
        if calls == 1:
            return cutover.CatalogReadAttempt(True, _key_page(has_more=True))
        return cutover.CatalogReadAttempt(True, None, "snapshot_window_mismatch")

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def read_key_cursor_page(self, _project_ids, **kwargs):
            direct_windows.append((kwargs["window_start"], kwargs["window_end"]))
            return AttributeKeyCursorPageRead(
                rows=(AttributeKeyRow("direct.continuation", "string", 1),),
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
    assert first_response.data["next_cursor"]

    # A settings change can govern only fresh walks.  The continuation remains
    # bound to the authenticated A/B embedded in its signed cursor.
    with override_settings(SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=False):
        second_request = _authenticated_get(
            "/api/traces/span-attribute-keys/",
            {
                "project_id": PROJECT_ID,
                "page_size": 1,
                "cursor": first_response.data["next_cursor"],
            },
        )
        second_response = span_attributes.SpanAttributeKeysView.as_view()(
            second_request
        )

    assert second_response.status_code == 200
    assert captured_windows == [
        (WINDOW_START, WINDOW_END),
        (WINDOW_START, WINDOW_END),
    ]
    assert direct_windows == [(WINDOW_START, WINDOW_END)]
    assert second_response.data["query_window_mode"] == "frozen_snapshot"
    assert second_response["X-FutureAGI-Attribute-Catalog-Window"] == "frozen-snapshot"


@pytest.mark.unit
@override_settings(**SNAPSHOT_SETTINGS)
def test_span_key_baseline_continuation_ignores_later_snapshot_enable(monkeypatch):
    from tracer.views import span_attributes

    captured_windows = []
    calls = 0

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def read_key_cursor_page(self, _project_ids, **kwargs):
            nonlocal calls
            calls += 1
            key = f"baseline.key.{calls}"
            digest = attribute_key_cursor_digest(key)
            captured_windows.append((kwargs["window_start"], kwargs["window_end"]))
            return AttributeKeyCursorPageRead(
                rows=(AttributeKeyRow(key, "string", 1),),
                metadata=_metadata(kwargs["window_start"], kwargs["window_end"]),
                has_more=calls == 1,
                browse_status="continuation" if calls == 1 else "exhausted",
                next_segment_end=kwargs["window_end"],
                next_before_identity=None,
                next_resume_identity=None,
                next_resume_key_offset=0,
                seen_key_digests=(*kwargs["seen_key_digests"], digest),
                appended_key_digests=(digest,),
                seen_key_count=kwargs["seen_key_count"] + 1,
            )

    monkeypatch.setattr(
        span_attributes,
        "_project_is_in_request_scope",
        lambda _request, _project_id: True,
    )
    monkeypatch.setattr(
        span_attributes,
        "try_catalog_key_page",
        lambda **_kwargs: cutover.CatalogReadAttempt(
            False,
            None,
            "runtime_disabled",
        ),
    )
    monkeypatch.setattr(span_attributes, "AttributeReadSelector", Selector)
    params = {"project_id": PROJECT_ID, "page_size": 1}

    with override_settings(SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=False):
        first_response = span_attributes.SpanAttributeKeysView.as_view()(
            _authenticated_get("/api/traces/span-attribute-keys/", params)
        )
    assert first_response.status_code == 200
    assert first_response.data["next_cursor"]

    with override_settings(SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=True):
        second_response = span_attributes.SpanAttributeKeysView.as_view()(
            _authenticated_get(
                "/api/traces/span-attribute-keys/",
                {**params, "cursor": first_response.data["next_cursor"]},
            )
        )

    assert second_response.status_code == 200
    assert captured_windows[1] == captured_windows[0]
    assert "query_window_mode" not in first_response.data
    assert "query_window_mode" not in second_response.data
    assert "X-FutureAGI-Attribute-Catalog-Window" not in second_response


@pytest.mark.unit
@override_settings(**SNAPSHOT_SETTINGS)
def test_unactivated_span_key_snapshot_falls_back_over_the_same_window(monkeypatch):
    from tracer.views import span_attributes

    captured: dict[str, Any] = {}

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def read_key_cursor_page(self, _project_ids, **kwargs):
            captured.update(kwargs)
            return AttributeKeyCursorPageRead(
                rows=(AttributeKeyRow("direct.key", "string", 1),),
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
        lambda **_kwargs: cutover.CatalogReadAttempt(
            True,
            None,
            "activation_missing",
        ),
    )
    monkeypatch.setattr(span_attributes, "AttributeReadSelector", Selector)
    request = _authenticated_get(
        "/api/traces/span-attribute-keys/",
        {"project_id": PROJECT_ID, "page_size": 10},
    )

    response = span_attributes.SpanAttributeKeysView.as_view()(request)

    assert response.status_code == 200
    assert (captured["window_start"], captured["window_end"]) == (
        WINDOW_START,
        WINDOW_END,
    )
    assert response["X-FutureAGI-Attribute-Catalog"] == "fallback"
    assert response.data["query_window_mode"] == "frozen_snapshot"
    assert response["X-FutureAGI-Attribute-Catalog-Window"] == "frozen-snapshot"


class _ProjectQuery:
    def filter(self, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def values_list(self, *_args, **_kwargs):
        return [PROJECT_ID]


def _patch_dashboard_project_scope(monkeypatch, dashboard) -> None:
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


@pytest.mark.unit
@override_settings(**SNAPSHOT_SETTINGS)
def test_dashboard_custom_value_fresh_snapshot_skips_retention_probe(monkeypatch):
    from tracer.views import dashboard

    captured: dict[str, Any] = {}
    _patch_dashboard_project_scope(monkeypatch, dashboard)

    def read_catalog(**kwargs):
        captured.update(kwargs)
        return cutover.CatalogReadAttempt(True, _value_page())

    monkeypatch.setattr(dashboard, "try_catalog_value_page", read_catalog)
    monkeypatch.setattr(
        dashboard,
        "AttributeReadSelector",
        lambda **_kwargs: pytest.fail(
            "qualified snapshot must not probe retention or spans"
        ),
    )
    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "snapshot.key",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_ID,
            "source": "traces",
            "page_size": 10,
        },
    )

    response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 200
    assert (captured["window_start"], captured["window_end"]) == (
        WINDOW_START,
        WINDOW_END,
    )
    result = response.data["result"]
    assert result["query_window_start"] == WINDOW_START.isoformat()
    assert result["query_window_end"] == WINDOW_END.isoformat()
    assert result["query_window_mode"] == "frozen_snapshot"
    assert result["query_count"] == 4
    assert response["X-FutureAGI-Attribute-Catalog-Window"] == "frozen-snapshot"


@pytest.mark.unit
@override_settings(**SNAPSHOT_SETTINGS)
def test_unactivated_dashboard_snapshot_falls_back_over_the_same_window(monkeypatch):
    from tracer.views import dashboard

    captured: dict[str, Any] = {}

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def retained_window_start(self, *_args, **_kwargs):
            pytest.fail("snapshot fallback must not probe a different window")

        def read_value_cursor_page(self, _project_ids, _key, **kwargs):
            captured.update(kwargs)
            return AttributeValueCursorPageRead(
                rows=(AttributeValueRow("direct.value", "string", 1),),
                metadata=_metadata(kwargs["window_start"], kwargs["window_end"]),
                has_more=False,
                next_segment_end=kwargs["window_start"],
                next_before_identity=None,
                next_resume_identity=None,
                next_resume_member_offset=0,
                seen_value_digests=(),
            )

    _patch_dashboard_project_scope(monkeypatch, dashboard)
    monkeypatch.setattr(dashboard, "AttributeReadSelector", Selector)
    monkeypatch.setattr(
        dashboard,
        "try_catalog_value_page",
        lambda **_kwargs: cutover.CatalogReadAttempt(
            True,
            None,
            "activation_missing",
        ),
    )
    request = _authenticated_get(
        "/tracer/dashboard/filter_values/",
        {
            "metric_name": "snapshot.key",
            "metric_type": "custom_attribute",
            "project_ids": PROJECT_ID,
            "source": "traces",
            "page_size": 10,
        },
    )

    response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(request)

    assert response.status_code == 200
    assert (captured["window_start"], captured["window_end"]) == (
        WINDOW_START,
        WINDOW_END,
    )
    assert response["X-FutureAGI-Attribute-Catalog"] == "fallback"
    assert response.data["result"]["query_window_mode"] == "frozen_snapshot"
    assert response["X-FutureAGI-Attribute-Catalog-Window"] == "frozen-snapshot"


@pytest.mark.unit
@override_settings(**SNAPSHOT_SETTINGS)
@pytest.mark.parametrize(
    "first_snapshot_enabled",
    [
        pytest.param(True, id="snapshot-to-baseline-runtime"),
        pytest.param(False, id="baseline-to-snapshot-runtime"),
    ],
)
def test_dashboard_custom_value_continuation_authenticates_original_window_mode(
    monkeypatch,
    first_snapshot_enabled,
):
    from tracer.views import dashboard

    captured_windows = []
    calls = 0

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def retained_window_start(self, *_args, **_kwargs):
            return WINDOW_START - timedelta(days=30)

        def read_value_cursor_page(self, _project_ids, _key, **kwargs):
            nonlocal calls
            calls += 1
            value = f"value-{calls}"
            digest = attribute_value_cursor_digest("string", value)
            captured_windows.append((kwargs["window_start"], kwargs["window_end"]))
            return AttributeValueCursorPageRead(
                rows=(AttributeValueRow(value, "string", 1),),
                metadata=_metadata(kwargs["window_start"], kwargs["window_end"]),
                has_more=calls == 1,
                next_segment_end=kwargs["window_end"],
                next_before_identity=None,
                next_resume_identity=None,
                next_resume_member_offset=0,
                seen_value_digests=(*kwargs["seen_value_digests"], digest),
                browse_status="continuation" if calls == 1 else "exhausted",
                appended_value_digests=(digest,),
                seen_value_count=kwargs["seen_value_count"] + 1,
            )

    _patch_dashboard_project_scope(monkeypatch, dashboard)
    monkeypatch.setattr(dashboard, "AttributeReadSelector", Selector)
    monkeypatch.setattr(
        dashboard,
        "try_catalog_value_page",
        lambda **_kwargs: cutover.CatalogReadAttempt(
            False,
            None,
            "runtime_disabled",
        ),
    )
    monkeypatch.setattr(
        dashboard,
        "_run_catalog_value_shadow_fail_open",
        lambda **_kwargs: None,
    )
    params = {
        "metric_name": "snapshot.key",
        "metric_type": "custom_attribute",
        "project_ids": PROJECT_ID,
        "source": "traces",
        "page_size": 1,
    }

    with override_settings(
        SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=first_snapshot_enabled
    ):
        first_response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(
            _authenticated_get("/tracer/dashboard/filter_values/", params)
        )
    first_result = first_response.data["result"]
    assert first_response.status_code == 200
    assert first_result["next_cursor"]

    with override_settings(
        SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=not first_snapshot_enabled
    ):
        second_response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(
            _authenticated_get(
                "/tracer/dashboard/filter_values/",
                {**params, "cursor": first_result["next_cursor"]},
            )
        )
    second_result = second_response.data["result"]

    assert second_response.status_code == 200
    assert captured_windows[1] == captured_windows[0]
    if first_snapshot_enabled:
        assert first_result["query_window_mode"] == "frozen_snapshot"
        assert second_result["query_window_mode"] == "frozen_snapshot"
        assert (
            second_response["X-FutureAGI-Attribute-Catalog-Window"] == "frozen-snapshot"
        )
    else:
        assert "query_window_mode" not in first_result
        assert "query_window_mode" not in second_result
        assert "X-FutureAGI-Attribute-Catalog-Window" not in second_response


@pytest.mark.unit
@override_settings(**SNAPSHOT_SETTINGS)
@pytest.mark.parametrize(
    "first_snapshot_enabled",
    [
        pytest.param(True, id="enabled-to-disabled"),
        pytest.param(False, id="disabled-to-enabled"),
    ],
)
def test_dashboard_system_value_continuation_is_stable_across_snapshot_flag_toggle(
    monkeypatch,
    first_snapshot_enabled,
):
    from tracer.views import dashboard

    calls = 0

    def read_page(_analytics, **_kwargs):
        nonlocal calls
        calls += 1
        return EndUserFilterValueCursorPageRead(
            values=(f"user-{calls}",),
            has_more=calls == 1,
            next_value_after=f"user-{calls}" if calls == 1 else None,
            browse_status="continuation" if calls == 1 else "exhausted",
        )

    _patch_dashboard_project_scope(monkeypatch, dashboard)
    monkeypatch.setattr(dashboard, "V2AnalyticsQueryService", object)
    monkeypatch.setattr(
        dashboard,
        "read_end_user_filter_value_cursor_page",
        read_page,
    )
    params = {
        "metric_name": "user_id",
        "metric_type": "system_metric",
        "project_ids": PROJECT_ID,
        "source": "traces",
        "page_size": 1,
    }

    with override_settings(
        SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=first_snapshot_enabled
    ):
        first_response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(
            _authenticated_get("/tracer/dashboard/filter_values/", params)
        )
    first_result = first_response.data["result"]
    assert first_result["next_cursor"]

    with override_settings(
        SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=not first_snapshot_enabled
    ):
        second_response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(
            _authenticated_get(
                "/tracer/dashboard/filter_values/",
                {**params, "cursor": first_result["next_cursor"]},
            )
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.data["result"]["values"] == [
        {"value": "user-2", "label": "user-2"}
    ]


@pytest.mark.unit
@override_settings(**SNAPSHOT_SETTINGS)
@pytest.mark.parametrize(
    "first_snapshot_enabled",
    [
        pytest.param(True, id="enabled-to-disabled"),
        pytest.param(False, id="disabled-to-enabled"),
    ],
)
def test_dashboard_span_system_value_continuation_is_stable_across_flag_toggle(
    monkeypatch,
    first_snapshot_enabled,
):
    from tracer.views import dashboard

    calls = 0

    class Selector:
        def __init__(self, **_kwargs):
            pass

        def retained_window_start(self, *_args, **_kwargs):
            return WINDOW_START - timedelta(days=30)

    def read_page(_analytics, **kwargs):
        nonlocal calls
        calls += 1
        value = f"status-{calls}"
        digest = dashboard._filter_value_digest(value)
        return FilterValueCursorPageRead(
            values=(value,),
            query_window_start=kwargs["window_start"],
            query_window_end=kwargs["window_end"],
            has_more=calls == 1,
            next_segment_end=kwargs["window_end"],
            next_segment_start=(kwargs["window_start"] if calls == 1 else None),
            next_value_after=(value if calls == 1 else None),
            seen_value_digests=(*kwargs["seen_value_digests"], digest),
            browse_status="continuation" if calls == 1 else "exhausted",
            appended_value_digests=(digest,),
            seen_value_count=kwargs["seen_value_count"] + 1,
        )

    _patch_dashboard_project_scope(monkeypatch, dashboard)
    monkeypatch.setattr(dashboard, "AttributeReadSelector", Selector)
    monkeypatch.setattr(dashboard, "V2AnalyticsQueryService", object)
    monkeypatch.setattr(
        dashboard,
        "read_span_system_filter_value_cursor_page",
        read_page,
    )
    params = {
        "metric_name": "status",
        "metric_type": "system_metric",
        "project_ids": PROJECT_ID,
        "source": "traces",
        "page_size": 1,
    }

    with override_settings(
        SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=first_snapshot_enabled
    ):
        first_response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(
            _authenticated_get("/tracer/dashboard/filter_values/", params)
        )
    first_result = first_response.data["result"]
    assert first_result["next_cursor"]

    with override_settings(
        SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED=not first_snapshot_enabled
    ):
        second_response = dashboard.DashboardViewSet.as_view({"get": "filter_values"})(
            _authenticated_get(
                "/tracer/dashboard/filter_values/",
                {**params, "cursor": first_result["next_cursor"]},
            )
        )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.data["result"]["values"] == [
        {"value": "status-2", "label": "status-2"}
    ]
