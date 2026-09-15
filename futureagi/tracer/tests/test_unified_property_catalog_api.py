from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tracer.serializers.dashboard import (
    DashboardFilterValuesQuerySerializer,
    DashboardMetricsCatalogQuerySerializer,
)
from tracer.views.dashboard import DashboardViewSet

WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
PROJECT_ID = "33333333-3333-3333-3333-333333333333"


def _enable_catalog_reads(settings):
    settings.PROPERTY_CATALOG_DATABASE = "property_catalog_dev_clean"


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
        ",".join(
            [f"00000000-0000-4000-8000-{index:012x}" for index in range(1024)]
            + ["not-a-uuid"]
        ),
    ],
    ids=("malformed", "malformed_after_large_valid_scope"),
)
def test_filter_values_rejects_malformed_project_scope(project_ids):
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


@pytest.mark.parametrize("project_count", (65, 178, 257, 1024))
@pytest.mark.parametrize("endpoint", ("properties", "values"))
def test_catalog_serializers_accept_full_project_inventory(project_count, endpoint):
    projects = [
        f"00000000-0000-4000-8000-{index:012x}" for index in range(project_count)
    ]
    data = {"page_size": 25, "project_ids": ",".join(projects)}
    if endpoint == "properties":
        serializer = DashboardMetricsCatalogQuerySerializer(
            data={**data, "cursor_mode": True}
        )
    else:
        serializer = DashboardFilterValuesQuerySerializer(
            data={
                **data,
                "property_id": "custom_attribute:customer.plan",
                "source": "traces",
            }
        )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["project_ids"] == projects


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


def test_current_metrics_envelope_has_no_activation_requirement(settings):
    settings.PROPERTY_CATALOG_DATABASE = "test_index"
    reader = Mock()
    reader.read_page.return_value = SimpleNamespace(
        metrics=(), has_more=False, next_cursor=None
    )
    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ),
        patch(
            "tracer.views.dashboard.resolve_property_catalog_agent_scope",
            return_value="",
        ),
        patch("tracer.views.dashboard.PropertyCatalogReader", return_value=reader),
    ):
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request()
        )
    assert response.status_code == 200
    result = response.data["result"]
    assert result["query_provenance"] == "current_property_catalog"
    assert result["query_exact"] is False and result["total"] is None
    assert not (
        {
            "catalog_epoch",
            "catalog_revision",
            "activation_fingerprint",
            "category_counts",
        }
        & result.keys()
    )


@pytest.mark.parametrize("action", ["metrics", "filter_values"])
def test_catalog_read_post_validates_large_body_and_rejects_mixed_parameters(action):
    from django.http import QueryDict
    from rest_framework.response import Response

    method = getattr(DashboardViewSet, action)
    assert method.mapping == {"get": action, "post": action}
    assert method._read_query_post is True
    data = (
        {"cursor_mode": True, "cursor": "x" * 24000, "page_size": 1}
        if action == "metrics"
        else {
            "property_id": "custom_attribute:key",
            "source": "traces",
            "cursor": "x" * 24000,
            "page_size": 1,
        }
    )
    request = _request()
    request.method, request.data, request.query_params = "POST", data, QueryDict()
    # Execute the real decorator and stop at scope resolution; a long body must
    # reach the same authorization path, not fall back to a different reader.
    with patch(
        "tracer.views.dashboard.resolve_property_catalog_project_scope",
        side_effect=ValueError("fixture scope rejection"),
    ) as scope:
        response = method(DashboardViewSet(), request)
    assert isinstance(response, Response) and response.status_code == 400
    scope.assert_called_once()
    assert request.validated_query_data["cursor"] == data["cursor"]
    request.query_params = QueryDict("page_size=2")
    with patch(
        "tracer.views.dashboard.resolve_property_catalog_project_scope"
    ) as scope:
        assert method(DashboardViewSet(), request).status_code == 400
    scope.assert_not_called()


@pytest.mark.parametrize("permission", ["project", "agent"])
def test_current_metrics_rechecks_scope_before_reader(permission):
    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            return_value=[PROJECT_ID],
        ) as project,
        patch(
            "tracer.views.dashboard.resolve_property_catalog_agent_scope",
            return_value="",
        ) as agent,
        patch("tracer.views.dashboard.PropertyCatalogReader") as reader,
    ):
        (project if permission == "project" else agent).side_effect = ValueError(
            "Scope is invalid"
        )
        response = inspect.unwrap(DashboardViewSet.metrics)(
            DashboardViewSet(), _request()
        )
    assert response.status_code == 400
    reader.assert_not_called()
