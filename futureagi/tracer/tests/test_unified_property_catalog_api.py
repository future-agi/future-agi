from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tracer.serializers.dashboard import (
    DashboardFilterValuesQuerySerializer,
    DashboardMetricsCatalogQuerySerializer,
)
from tracer.services.clickhouse.v2.property_catalog.cursor import (
    PropertyCatalogCursorError,
)
from tracer.services.clickhouse.v2.property_catalog.reader import (
    PropertyCatalogUnavailable,
)
from tracer.views.dashboard import DashboardViewSet

WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"


def _enable_catalog_reads(settings):
    settings.PROPERTY_CATALOG_READ_MODE = "read"
    settings.PROPERTY_CATALOG_READ_DEPLOYMENT = "dev"
    settings.PROPERTY_CATALOG_DATABASE = "property_catalog_dev_clean"
    settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST = (WORKSPACE_ID,)
    settings.PROPERTY_CATALOG_PROD_WORKSPACE_ALLOWLIST = ()
    settings.PROPERTY_CATALOG_PROD_WORKSPACE_SCOPE_MODE = "allowlist"


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


def test_metrics_cursor_normalizes_exact_custom_attribute_sources():
    for source in ("traces", "spans", "voice_calls", "prompts"):
        serializer = DashboardMetricsCatalogQuerySerializer(
            data={
                "cursor_mode": True,
                "page_size": 50,
                "category": "custom_attribute",
                "source": source,
            }
        )
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["source"] == "traces"


@pytest.mark.parametrize(
    "source", ["sessions", "users", "datasets", "simulation", "all", "both"]
)
def test_metrics_cursor_rejects_unsupported_custom_attribute_sources(source):
    serializer = DashboardMetricsCatalogQuerySerializer(
        data={
            "cursor_mode": True,
            "page_size": 50,
            "category": "custom_attribute",
            "source": source,
        }
    )

    assert not serializer.is_valid()
    assert "source" in serializer.errors


def test_filter_values_normalizes_logical_definition_sources_to_native_transport():
    cases = (
        ("system_attribute:spans:latency", "spans", "traces"),
        ("system_attribute:users:user", "users", "sessions"),
        ("system_attribute:voice_calls:latency", "voice_calls", "traces"),
        ("system_attribute:prompts:avg_latency", "prompts", "traces"),
        ("custom_attribute:customer.plan", "spans", "traces"),
        ("custom_attribute:customer.plan", "voice_calls", "traces"),
        ("custom_attribute:customer.plan", "prompts", "traces"),
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


@pytest.mark.parametrize(
    ("property_id", "source"),
    [
        ("annotation:11111111-1111-4111-8111-111111111111", "both"),
        ("annotation:11111111-1111-4111-8111-111111111111", "all"),
        ("eval_config:22222222-2222-4222-8222-222222222222", "both"),
        ("eval_template:33333333-3333-4333-8333-333333333333", "all"),
    ],
)
def test_filter_values_accepts_shared_definition_sources(property_id, source):
    serializer = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": property_id,
            "source": source,
            "page_size": 25,
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["source"] == source


def test_filter_values_rejects_unsupported_custom_attribute_source():
    serializer = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": "custom_attribute:customer.plan",
            "source": "sessions",
            "page_size": 25,
        }
    )

    assert not serializer.is_valid()
    assert "property_id" in serializer.errors

    legacy_serializer = DashboardFilterValuesQuerySerializer(
        data={
            "metric_name": "customer.plan",
            "metric_type": "custom_attribute",
            "source": "sessions",
            "page_size": 25,
        }
    )
    assert not legacy_serializer.is_valid()
    assert "source" in legacy_serializer.errors


@pytest.mark.parametrize(
    "project_ids",
    [
        "not-a-uuid",
        ",".join(f"00000000-0000-4000-8000-{index:012d}" for index in range(1, 66)),
    ],
)
def test_filter_values_rejects_malformed_or_oversized_project_scope(project_ids):
    serializer = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": "custom_attribute:customer.plan",
            "source": "traces",
            "page_size": 25,
            "project_ids": project_ids,
        }
    )

    assert not serializer.is_valid()
    assert "project_ids" in serializer.errors


@pytest.mark.parametrize(
    ("catalog_max", "dashboard_max"),
    [(7, 11), (11, 7)],
)
def test_filter_values_page_size_honors_both_configured_maxima(
    settings, catalog_max, dashboard_max
):
    settings.PROPERTY_CATALOG_MAX_PAGE_SIZE = catalog_max
    settings.DASHBOARD_FILTER_VALUE_MAX_PAGE_SIZE = dashboard_max
    admitted_max = min(catalog_max, dashboard_max)

    accepted = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": "custom_attribute:customer.plan",
            "source": "traces",
            "page_size": admitted_max,
        }
    )
    rejected = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": "custom_attribute:customer.plan",
            "source": "traces",
            "page_size": admitted_max + 1,
        }
    )

    assert accepted.is_valid(), accepted.errors
    assert not rejected.is_valid()
    assert "page_size" in rejected.errors


def test_filter_values_maps_reader_value_error_to_400(settings):
    _enable_catalog_reads(settings)
    reader = Mock()
    reader.read_page.side_effect = ValueError("page_size must be between 1 and 25")
    request = _request(
        metric_name="customer.plan",
        metric_type="custom_attribute",
        property_id="custom_attribute:customer.plan",
        _property_kind="custom_attribute",
        source="traces",
        search="",
        page_size=26,
    )

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch(
            "tracer.views.dashboard.PropertyCatalogValueReader",
            return_value=reader,
        ),
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(
            DashboardViewSet(), request
        )

    assert response.status_code == 400
    reader.read_page.assert_called_once()


def test_metrics_cursor_mode_uses_one_activated_definition_reader(settings):
    _enable_catalog_reads(settings)
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
            "tracer.views.dashboard.PropertyCatalogReader", return_value=reader
        ) as reader_factory,
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
    activation_gate.assert_called_once_with(
        executor.return_value,
        database="property_catalog_dev_clean",
        deployment="dev",
    )
    assert reader_factory.call_args.kwargs["activation_selector"] is activation_selector
    legacy.assert_not_called()


def test_metrics_workspace_scope_binds_full_authorized_project_set(settings):
    _enable_catalog_reads(settings)
    reader = Mock()
    reader.read_page.return_value = SimpleNamespace(
        metrics=(),
        has_more=False,
        next_cursor=None,
        catalog_epoch=3,
        catalog_revision=17,
        activation_fingerprint="a" * 64,
        category_counts={
            "all": 0,
            "system_metric": 0,
            "eval_metric": 0,
            "annotation_metric": 0,
            "custom_attribute": 0,
            "custom_column": 0,
        },
        category_counts_exact=True,
    )

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ) as authorize,
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch("tracer.views.dashboard.PropertyCatalogReader", return_value=reader),
    ):
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request(project_ids=[])
        )

    assert response.status_code == 200
    assert authorize.call_args.kwargs["include_workspace_projects"] is True
    scope = reader.read_page.call_args.kwargs["scope"]
    assert scope["project_ids"] == [PROJECT_ID]
    assert scope["workspace_scope"] is True


def test_metrics_cursor_mode_fails_closed_before_reader_when_not_allowlisted(settings):
    _enable_catalog_reads(settings)
    settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST = ()

    with patch("tracer.views.dashboard.PropertyCatalogReader") as reader:
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request()
        )

    assert response.status_code == 503
    assert response.data["code"] == "property_catalog_not_ready"
    reader.assert_not_called()


def test_metrics_cursor_mode_admits_authenticated_workspace_in_global_prod_scope(
    settings,
):
    _enable_catalog_reads(settings)
    settings.PROPERTY_CATALOG_READ_DEPLOYMENT = "prod"
    settings.PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST = ()
    settings.PROPERTY_CATALOG_PROD_WORKSPACE_SCOPE_MODE = "all"
    reader = Mock()
    reader.read_page.return_value = SimpleNamespace(
        metrics=(),
        has_more=False,
        next_cursor=None,
        catalog_epoch=1,
        catalog_revision=3,
        activation_fingerprint="a" * 64,
        category_counts={
            "all": 0,
            "system_metric": 0,
            "eval_metric": 0,
            "annotation_metric": 0,
            "custom_attribute": 0,
            "custom_column": 0,
        },
        category_counts_exact=True,
    )

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch(
            "tracer.views.dashboard.resolve_property_catalog_agent_scope",
            return_value=None,
        ),
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch("tracer.views.dashboard.PropertyCatalogReader", return_value=reader),
        patch("tracer.views.dashboard.activation_control_selector_for_deployment"),
    ):
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request()
        )

    assert response.status_code == 200
    reader.read_page.assert_called_once()


def test_metrics_cursor_error_is_sanitized_400(settings):
    _enable_catalog_reads(settings)
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


@pytest.mark.parametrize(
    "reason",
    [
        "activation_missing",
        "activation_not_active",
        "projection_incompatible",
        "activation_scope_incomplete",
    ],
)
def test_metrics_cursor_maps_only_genuine_activation_readiness_to_typed_503(
    settings, reason
):
    _enable_catalog_reads(settings)
    reader = Mock()
    reader.read_page.side_effect = PropertyCatalogUnavailable(reason)

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch("tracer.views.dashboard.PropertyCatalogReader", return_value=reader),
    ):
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request()
        )

    assert response.status_code == 503
    assert response.data["code"] == "property_catalog_not_ready"


@pytest.mark.parametrize(
    "reason",
    [
        "activation_mismatch",
        "activation_conflict",
        "activation_scope_conflict",
        "activation_scope_invalid",
        "definition_conflict",
        "deadline_exceeded",
        "query_failed",
    ],
)
def test_metrics_cursor_keeps_conflicts_and_query_defects_generic(settings, reason):
    _enable_catalog_reads(settings)
    reader = Mock()
    reader.read_page.side_effect = PropertyCatalogUnavailable(reason)

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch("tracer.views.dashboard.PropertyCatalogReadExecutor"),
        patch("tracer.views.dashboard.PropertyCatalogReader", return_value=reader),
    ):
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request()
        )

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"


def test_metrics_cursor_rejects_foreign_project_before_clickhouse(settings):
    _enable_catalog_reads(settings)

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
    _enable_catalog_reads(settings)
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
