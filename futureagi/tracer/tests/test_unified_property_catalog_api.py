from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tracer.serializers.dashboard import (
    DashboardFilterValuesQuerySerializer,
    DashboardMetricsCatalogQuerySerializer,
)
from tracer.services.clickhouse.v2.property_catalog.cursor import (
    PropertyCatalogCursorError,
)
from tracer.views.dashboard import DashboardViewSet

WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"


def _request(**validated_overrides):
    validated = {
        "project_ids": [PROJECT_ID],
        "category": "custom_attribute",
        "source": "traces",
        "search": "customer",
        "page_size": 50,
        "cursor_mode": True,
        "per_eval_config": False,
        "exclude_custom_attributes": False,
    }
    validated.update(validated_overrides)
    organization = SimpleNamespace(id="11111111-1111-1111-1111-111111111111")
    user = SimpleNamespace(id="user-1", organization=organization)
    return SimpleNamespace(
        workspace=SimpleNamespace(id=WORKSPACE_ID, organization=organization),
        organization=organization,
        user=user,
        auth=SimpleNamespace(id="token-1"),
        query_params={
            "cursor_mode": "true",
            "page_size": "50",
            "category": "custom_attribute",
        },
        validated_query_data=validated,
    )


def test_metrics_cursor_contract_requires_explicit_bounded_mode():
    assert not DashboardMetricsCatalogQuerySerializer(
        data={"cursor": "signed", "page_size": 50}
    ).is_valid()
    assert not DashboardMetricsCatalogQuerySerializer(
        data={"cursor_mode": True}
    ).is_valid()
    assert not DashboardMetricsCatalogQuerySerializer(
        data={"cursor_mode": True, "page": 1, "page_size": 50}
    ).is_valid()
    assert not DashboardMetricsCatalogQuerySerializer(
        data={
            "cursor_mode": True,
            "page_size": 50,
            "exclude_custom_attributes": True,
        }
    ).is_valid()
    valid = DashboardMetricsCatalogQuerySerializer(
        data={
            "cursor_mode": True,
            "page_size": 50,
            "search": "customer",
            "role": "metric",
        }
    )
    assert valid.is_valid(), valid.errors
    assert valid.validated_data["role"] == "metric"
    assert not DashboardMetricsCatalogQuerySerializer(
        data={"cursor_mode": True, "page_size": 50, "role": "aggregate"}
    ).is_valid()
    assert not DashboardMetricsCatalogQuerySerializer(
        data={"page": 1, "page_size": 50, "role": "metric"}
    ).is_valid()
    cursor_page_too_large = DashboardMetricsCatalogQuerySerializer(
        data={"cursor_mode": True, "page_size": 51}
    )
    assert not cursor_page_too_large.is_valid()
    legacy_page_200 = DashboardMetricsCatalogQuerySerializer(
        data={"page": 1, "page_size": 200}
    )
    assert legacy_page_200.is_valid(), legacy_page_200.errors

    for logical_source in (
        "spans",
        "sessions",
        "users",
        "voice_calls",
        "prompts",
    ):
        logical = DashboardMetricsCatalogQuerySerializer(
            data={
                "cursor_mode": True,
                "page_size": 50,
                "source": logical_source,
            }
        )
        assert logical.is_valid(), logical.errors

        legacy = DashboardMetricsCatalogQuerySerializer(
            data={"page": 1, "page_size": 50, "source": logical_source}
        )
        assert not legacy.is_valid()

    too_many_projects = DashboardMetricsCatalogQuerySerializer(
        data={
            "cursor_mode": True,
            "page_size": 50,
            "project_ids": ",".join(
                f"00000000-0000-0000-0000-{index:012d}" for index in range(1, 66)
            ),
        }
    )
    assert not too_many_projects.is_valid()

    multibyte_search = DashboardMetricsCatalogQuerySerializer(
        data={"cursor_mode": True, "page_size": 50, "search": "💡" * 129}
    )
    assert not multibyte_search.is_valid()


def test_filter_values_normalizes_logical_definition_sources_to_native_transport():
    cases = (
        ("system_attribute:spans:latency", "spans", "traces"),
        ("system_attribute:users:user", "users", "sessions"),
        ("system_attribute:voice_calls:latency", "voice_calls", "traces"),
        ("system_attribute:prompts:avg_latency", "prompts", "traces"),
    )

    for property_id, source, expected_transport in cases:
        serializer = DashboardFilterValuesQuerySerializer(
            data={
                "property_id": property_id,
                "source": source,
                "page_size": 25,
            }
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["source"] == expected_transport


def test_metrics_cursor_mode_uses_one_activated_definition_reader(settings):
    settings.PROPERTY_CATALOG_READ_MODE = "read"
    settings.PROPERTY_CATALOG_DATABASE = "th7247_catalog_dev_clean"
    settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST = (WORKSPACE_ID,)
    page = SimpleNamespace(
        metrics=(
            {
                "name": "customer.plan",
                "property_id": "custom_attribute:customer.plan",
                "property_kind": "custom_attribute",
                "category": "custom_attribute",
            },
        ),
        has_more=True,
        next_cursor="signed-next",
        catalog_epoch=3,
        catalog_revision=17,
        activation_fingerprint="a" * 64,
        category_counts={
            "all": 1,
            "system_metric": 0,
            "eval_metric": 0,
            "annotation_metric": 0,
            "custom_attribute": 1,
            "custom_column": 0,
        },
        category_counts_exact=True,
    )
    reader = Mock()
    reader.read_page.return_value = page

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ) as authorize,
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor") as executor,
        patch("tracer.views.dashboard.PropertyCatalogReader", return_value=reader),
        patch("tracer.views.dashboard.build_metrics_catalog_page") as legacy,
    ):
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request(role="metric")
        )

    assert response.status_code == 200
    result = response.data["result"]
    assert result["metrics"][0]["property_id"] == ("custom_attribute:customer.plan")
    assert result["total"] is None
    assert result["total_is_exact"] is False
    assert result["category_counts"] == {
        "all": 1,
        "system_metric": 0,
        "eval_metric": 0,
        "annotation_metric": 0,
        "custom_attribute": 1,
        "custom_column": 0,
    }
    assert result["category_counts_exact"] is True
    assert result["has_more"] is True
    assert result["next_cursor"] == "signed-next"
    assert result["catalog_revision"] == 17
    assert result["query_complete"] is True
    assert result["query_exact"] is True
    assert result["query_provenance"] == "activated_property_catalog"
    authorize.assert_called_once()
    reader.read_page.assert_called_once()
    assert reader.read_page.call_args.kwargs["scope"]["project_ids"] == [PROJECT_ID]
    assert reader.read_page.call_args.kwargs["query"]["role"] == "metric"
    assert executor.call_args.kwargs["max_wall_ms"] > 0
    legacy.assert_not_called()


def test_metrics_cursor_mode_fails_closed_before_reader_when_not_allowlisted(settings):
    settings.PROPERTY_CATALOG_READ_MODE = "read"
    settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST = ()

    with patch("tracer.views.dashboard.PropertyCatalogReader") as reader:
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request()
        )

    assert response.status_code == 503
    assert response.data["code"] == "property_catalog_not_ready"
    reader.assert_not_called()


def test_metrics_cursor_error_is_sanitized_400(settings):
    settings.PROPERTY_CATALOG_READ_MODE = "read"
    settings.PROPERTY_CATALOG_DATABASE = "th7247_catalog_dev_clean"
    settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST = (WORKSPACE_ID,)
    reader = Mock()
    reader.read_page.side_effect = PropertyCatalogCursorError(
        "cursor_mismatch", "The property continuation cursor does not match."
    )

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch("tracer.views.dashboard.PropertyCatalogReader", return_value=reader),
    ):
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request(cursor="signed-old")
        )

    assert response.status_code == 400
    assert response.data["code"] == "cursor_mismatch"


def test_metrics_cursor_rejects_foreign_project_before_clickhouse(settings):
    settings.PROPERTY_CATALOG_READ_MODE = "read"
    settings.PROPERTY_CATALOG_DATABASE = "th7247_catalog_dev_clean"
    settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST = (WORKSPACE_ID,)

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            side_effect=ValueError("Some project_ids are invalid"),
        ),
        patch("tracer.views.dashboard.PropertyCatalogReader") as reader,
    ):
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request()
        )

    assert response.status_code == 400
    reader.assert_not_called()


def test_metrics_cursor_rejects_foreign_agent_before_clickhouse(settings):
    settings.PROPERTY_CATALOG_READ_MODE = "read"
    settings.PROPERTY_CATALOG_DATABASE = "th7247_catalog_dev_clean"
    settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST = (WORKSPACE_ID,)
    agent_id = "44444444-4444-4444-4444-444444444444"

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch(
            "tracer.views.dashboard.resolve_property_catalog_agent_scope",
            side_effect=ValueError("agent_definition_id is invalid"),
        ),
        patch("tracer.views.dashboard.PropertyCatalogReader") as reader,
    ):
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request(agent_definition_id=agent_id)
        )

    assert response.status_code == 400
    reader.assert_not_called()
