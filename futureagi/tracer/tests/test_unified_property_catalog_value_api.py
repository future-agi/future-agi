from __future__ import annotations

import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from rest_framework.response import Response

from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    system_property_value_adapter,
)
from tracer.services.clickhouse.v2.property_catalog.value_cursor import (
    PropertyCatalogValueCursorError,
)
from tracer.services.clickhouse.v2.property_catalog.value_reader import (
    PropertyCatalogValue,
    PropertyCatalogValueNotReady,
    PropertyCatalogValueUnavailable,
)
from tracer.views.dashboard import (
    DashboardViewSet,
    _read_property_catalog_value_page,
)

ORG_ID = "11111111-1111-1111-1111-111111111111"
WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"
WINDOW_START = datetime(2026, 8, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 14, tzinfo=UTC)


def _request(**validated_overrides):
    validated = {
        "property_id": "custom_attribute:customer.plan",
        "_property_kind": "custom_attribute",
        "metric_name": "customer.plan",
        "metric_type": "custom_attribute",
        "source": "traces",
        "project_ids": [PROJECT_ID],
        "search": "pro",
        "page_size": 10,
    }
    validated.update(validated_overrides)
    organization = SimpleNamespace(id=ORG_ID)
    user = SimpleNamespace(id="user-1", organization=organization)
    return SimpleNamespace(
        workspace=SimpleNamespace(id=WORKSPACE_ID, organization=organization),
        organization=organization,
        user=user,
        auth=SimpleNamespace(id="token-1"),
        query_params={},
        validated_query_data=validated,
    )


def _page():
    return SimpleNamespace(
        values=(
            PropertyCatalogValue(
                value="pro",
                attribute_type="string",
                scalar_kind="string",
                value_fingerprint="b" * 64,
                value_search_text="pro",
                first_seen=WINDOW_START,
                last_seen=WINDOW_END,
            ),
        ),
        has_more=True,
        next_cursor="signed-next",
        catalog_epoch=3,
        catalog_revision=17,
        activation_fingerprint="a" * 64,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        attribute_types=("string", "number"),
        query_count=4,
    )


def _enable(settings):
    settings.PROPERTY_CATALOG_READ_MODE = "read"
    settings.PROPERTY_CATALOG_READ_DEPLOYMENT = "dev"
    settings.PROPERTY_CATALOG_DATABASE = "property_catalog_dev_clean"
    settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST = (WORKSPACE_ID,)
    settings.PROPERTY_CATALOG_PROD_WORKSPACE_ALLOWLIST = ()


def test_filter_values_uses_authorized_activated_native_catalog_page(settings):
    _enable(settings)
    # This setting protects legacy span-fact scans. It must not truncate the
    # compact activated catalog's retained-history inventory.
    settings.FILTER_VALUES_DEFAULT_LOOKBACK_DAYS = 1
    reader = Mock()
    reader.read_page.return_value = _page()
    activation_selector = Mock(name="activation_selector")

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ) as authorize,
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor") as executor,
        patch(
            "tracer.views.dashboard.activation_control_selector_for_deployment",
            return_value=activation_selector,
        ) as activation_gate,
        patch(
            "tracer.views.dashboard.PropertyCatalogValueReader", return_value=reader
        ) as reader_factory,
        patch("tracer.views.dashboard.AttributeReadSelector") as legacy,
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(
            DashboardViewSet(), _request()
        )

    assert response.status_code == 200
    result = response.data["result"]
    assert result["values"] == [{"value": "pro", "type": "string", "label": "pro"}]
    assert result["has_more"] is True
    assert result["next_cursor"] == "signed-next"
    assert result["catalog_epoch"] == 3
    assert result["catalog_revision"] == 17
    assert result["activation_fingerprint"] == "a" * 64
    assert result["attribute_types"] == ["string", "number"]
    assert result["attribute_types_exact"] is True
    assert result["query_provenance"] == "activated_property_catalog"
    assert "query_exact" not in result
    authorize.assert_called_once()
    assert authorize.call_args.args[0].id == WORKSPACE_ID
    assert authorize.call_args.args[1] == [PROJECT_ID]
    read_kwargs = reader.read_page.call_args.kwargs
    assert read_kwargs["scope"]["project_ids"] == [PROJECT_ID]
    assert read_kwargs["query"] == {
        "property_id": "custom_attribute:customer.plan",
        "source": "traces",
        "attribute_type": "",
        "search": "pro",
    }
    assert read_kwargs["page_size"] == 10
    assert read_kwargs["window_start"] == datetime(1970, 1, 1, tzinfo=UTC)
    assert read_kwargs["window_start"] < read_kwargs["window_end"]
    assert executor.call_args.kwargs["max_wall_ms"] > 0
    activation_gate.assert_called_once_with(
        executor.return_value,
        database="property_catalog_dev_clean",
        deployment="dev",
    )
    assert reader_factory.call_args.kwargs["activation_selector"] is activation_selector
    legacy.assert_not_called()


def test_filter_values_workspace_scope_binds_full_authorized_project_set(settings):
    _enable(settings)
    reader = Mock()
    reader.read_page.return_value = _page()

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ) as authorize,
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch("tracer.views.dashboard.PropertyCatalogValueReader", return_value=reader),
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(
            DashboardViewSet(), _request(project_ids=[])
        )

    assert response.status_code == 200
    assert authorize.call_args.kwargs["include_workspace_projects"] is True
    scope = reader.read_page.call_args.kwargs["scope"]
    assert scope["project_ids"] == [PROJECT_ID]
    assert scope["workspace_scope"] is True


def test_filter_values_cursor_continuation_uses_signed_window_not_new_now(settings):
    _enable(settings)
    reader = Mock()
    reader.read_page.return_value = _page()

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch("tracer.views.dashboard.PropertyCatalogValueReader", return_value=reader),
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(
            DashboardViewSet(), _request(cursor="signed-old")
        )

    assert response.status_code == 200
    read_kwargs = reader.read_page.call_args.kwargs
    assert read_kwargs["cursor_token"] == "signed-old"
    assert "window_start" not in read_kwargs
    assert "window_end" not in read_kwargs


def test_filter_values_foreign_project_is_rejected_before_catalog_clickhouse(settings):
    _enable(settings)

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            side_effect=ValueError("Some project_ids are invalid"),
        ),
        patch("tracer.views.dashboard.PropertyCatalogValueReader") as reader,
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(
            DashboardViewSet(), _request()
        )

    assert response.status_code == 400
    reader.assert_not_called()


def test_filter_values_catalog_failure_is_503_without_legacy_span_read(settings):
    _enable(settings)
    reader = Mock()
    reader.read_page.side_effect = PropertyCatalogValueUnavailable("value_conflict")

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch("tracer.views.dashboard.PropertyCatalogValueReader", return_value=reader),
        patch("tracer.views.dashboard.AttributeReadSelector") as legacy,
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(
            DashboardViewSet(), _request()
        )

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    legacy.assert_not_called()


@pytest.mark.parametrize("cursor_code", ["cursor_mismatch", "invalid_cursor"])
def test_filter_values_catalog_cursor_error_is_sanitized_400_without_legacy_fallback(
    settings, cursor_code
):
    _enable(settings)
    reader = Mock()
    reader.read_page.side_effect = PropertyCatalogValueCursorError(
        cursor_code,
        "The property-value continuation cursor does not match this request.",
    )

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch("tracer.views.dashboard.PropertyCatalogValueReader", return_value=reader),
        patch("tracer.views.dashboard.AttributeReadSelector") as legacy,
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(
            DashboardViewSet(), _request(cursor="signed-old")
        )

    assert response.status_code == 400
    assert response.data["code"] == cursor_code
    legacy.assert_not_called()


def test_native_value_cursor_bypasses_additive_catalog_decoder(settings):
    _enable(settings)
    request = _request(
        property_id="system_attribute:traces:provider",
        _property_kind="system_attribute",
        metric_name="provider",
        metric_type="system_metric",
        search="",
        cursor="native-signed-cursor",
    )
    deadline = Mock()
    deadline.remaining_ms.return_value = 1_000

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ) as authorize,
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch("tracer.views.dashboard.PropertyCatalogValueReader") as reader,
        pytest.raises(PropertyCatalogValueNotReady) as raised,
    ):
        _read_property_catalog_value_page(
            request,
            request.validated_query_data,
            deadline=deadline,
        )
    assert raised.value.reason == "native_value_adapter"
    authorize.assert_called_once()
    reader.assert_not_called()


def test_native_system_value_preflight_skips_catalog_queries(settings):
    _enable(settings)
    request = _request(
        property_id="system_attribute:voice_calls:ended_reason",
        _property_kind="system_attribute",
        metric_name="ended_reason",
        metric_type="system_metric",
        source="traces",
        search="",
    )
    deadline = Mock()

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ) as authorize,
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor") as executor,
        patch("tracer.views.dashboard.PropertyCatalogValueReader") as reader,
        pytest.raises(PropertyCatalogValueNotReady) as raised,
    ):
        _read_property_catalog_value_page(
            request,
            request.validated_query_data,
            deadline=deadline,
        )

    assert raised.value.reason == "native_value_adapter"
    authorize.assert_called_once()
    executor.assert_not_called()
    reader.assert_not_called()
    deadline.remaining_ms.assert_not_called()


def test_oversized_native_scope_is_400_before_legacy_fallback(settings):
    _enable(settings)
    request = _request(
        property_id="system_attribute:traces:provider",
        _property_kind="system_attribute",
        metric_name="provider",
        metric_type="system_metric",
        search="",
        project_ids=[PROJECT_ID] * (RUNTIME_LIMITS.max_projects + 1),
    )

    with (
        patch("tracer.views.dashboard.PropertyCatalogValueReader") as reader,
        patch("tracer.views.dashboard.AttributeReadSelector") as legacy,
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(
            DashboardViewSet(), request
        )

    assert response.status_code == 400
    reader.assert_not_called()
    legacy.assert_not_called()


def test_catalog_definition_native_adapter_mismatch_is_503_without_legacy(settings):
    _enable(settings)
    reader = Mock()
    reader.read_page.side_effect = PropertyCatalogValueNotReady("native_value_adapter")

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch("tracer.views.dashboard.PropertyCatalogValueReader", return_value=reader),
        patch("tracer.views.dashboard.AttributeReadSelector") as legacy,
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(
            DashboardViewSet(), _request()
        )

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    legacy.assert_not_called()


def test_system_value_adapter_preflight_matches_manifest_contract():
    assert system_property_value_adapter("traces", "model") == ("span_attribute_value")
    assert system_property_value_adapter("voice_calls", "ended_reason") == (
        "system_traces"
    )
    assert system_property_value_adapter("spans", "provider") == "system_traces"
    assert system_property_value_adapter("missing", "property") is None


def test_only_typed_not_ready_signal_enters_existing_native_adapter(settings):
    _enable(settings)
    sentinel = Response({"status": True, "result": {"values": ["legacy"]}})
    view = DashboardViewSet()
    view._filter_values_dataset = Mock(return_value=sentinel)
    request = _request(
        property_id="dataset_column:44444444-4444-4444-4444-444444444444",
        _property_kind="dataset_column",
        metric_name="dataset",
        metric_type="system_metric",
        source="datasets",
    )

    with patch(
        "tracer.views.dashboard._read_property_catalog_value_page",
        side_effect=PropertyCatalogValueNotReady("native_value_adapter"),
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(view, request)

    assert response is sentinel
    view._filter_values_dataset.assert_called_once()
