"""Deployment rollout coverage without depending on a populated metric store."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest
from django.test import override_settings
from rest_framework.response import Response
from rest_framework.routers import SimpleRouter
from rest_framework.test import APIRequestFactory, force_authenticate

from tfc.capabilities.contracts import CapabilitiesResponseSerializer
from tfc.capabilities.views import CapabilitiesView
from tfc.settings.runtime_setting_specs import (
    RUNTIME_NUMERIC_SETTING_SPECS,
    load_numeric_settings,
)
from tracer.services.derived_metrics.feature_flag import metrics_enabled
from tracer.views.derived_metric import DerivedMetricRunViewSet, DerivedMetricViewSet


@pytest.mark.parametrize("raw, expected", [(None, 0), ("", 0), ("0", 0), ("1", 1)])
def test_runtime_setting_defaults_off_and_parses_operator_switch(raw, expected):
    values = load_numeric_settings(
        RUNTIME_NUMERIC_SETTING_SPECS, source={"METRICS_ENABLED": raw}
    )
    assert values["METRICS_ENABLED"] == expected


@pytest.mark.parametrize("raw", ["true", "false", "invalid", "-1", "2", 0.5])
def test_runtime_setting_rejects_invalid_switch(raw):
    with pytest.raises(ValueError, match="METRICS_ENABLED"):
        load_numeric_settings(
            RUNTIME_NUMERIC_SETTING_SPECS, source={"METRICS_ENABLED": raw}
        )


@pytest.mark.parametrize("enabled", [0, 1])
def test_one_deployment_switch_for_every_scope(enabled):
    with override_settings(METRICS_ENABLED=enabled):
        org = SimpleNamespace(id=UUID(int=1))
        project = SimpleNamespace(id=UUID(int=2), organization=org)
        assert metrics_enabled() is bool(enabled)
        assert metrics_enabled(org=org) is bool(enabled)
        assert metrics_enabled(project=project) is bool(enabled)
        assert metrics_enabled(org=org, project=project) is bool(enabled)


def test_missing_setting_fails_closed():
    with patch(
        "tracer.services.derived_metrics.feature_flag.settings", SimpleNamespace()
    ):
        assert metrics_enabled() is False


def test_runtime_override_is_not_cached():
    with override_settings(METRICS_ENABLED=0):
        assert not metrics_enabled()
        with override_settings(METRICS_ENABLED=1):
            assert metrics_enabled()
        assert not metrics_enabled()


@pytest.mark.parametrize("enabled", [0, 1])
def test_existing_capabilities_endpoint_exposes_rollout(enabled):
    request = APIRequestFactory().get("/api/capabilities/")
    request.organization = SimpleNamespace(id=UUID(int=1))
    force_authenticate(
        request, user=SimpleNamespace(is_authenticated=True, is_staff=False)
    )
    with (
        override_settings(METRICS_ENABLED=enabled),
        patch.object(CapabilitiesView, "_is_admin", return_value=False),
    ):
        response = CapabilitiesView.as_view()(request)
    assert response.status_code == 200
    assert response.data["metrics_enabled"] is bool(enabled)
    assert "features" in response.data
    assert CapabilitiesResponseSerializer(data=response.data).is_valid()


def _routes():
    # Discover every action (including secondary @action.mapping methods),
    # so newly added endpoints cannot silently escape this shared boundary.
    router = SimpleRouter()
    router.register("derived-metrics", DerivedMetricViewSet, basename="derived-metric")
    router.register(
        "derived-metric-runs", DerivedMetricRunViewSet, basename="derived-metric-run"
    )
    for url in router.urls:
        for method, action_name in url.callback.actions.items():
            yield pytest.param(
                url.callback, method, action_name, id=f"{url.name}-{method}"
            )
        yield pytest.param(url.callback, "options", "options", id=f"{url.name}-options")
        if "get" in url.callback.actions:
            yield pytest.param(
                url.callback, "head", url.callback.actions["get"], id=f"{url.name}-head"
            )


@pytest.mark.parametrize("callback, method, action_name", list(_routes()))
@pytest.mark.parametrize("enabled", [0, 1])
def test_every_metric_route_is_hidden_off_and_dispatches_on(
    callback, method, action_name, enabled
):
    request = APIRequestFactory().generic(method.upper(), "/tracer/derived-metrics/")
    request.organization = SimpleNamespace(id=UUID(int=1))
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True))
    # Isolate business logic, not dispatch: the real router callback and shared
    # initial() must gate every method before permissions or action side effects.
    with (
        override_settings(METRICS_ENABLED=enabled),
        patch.object(callback.cls, "check_permissions") as permissions,
        patch.object(
            callback.cls, action_name, return_value=Response({"reached": True})
        ) as handler,
    ):
        response = callback(request, pk=str(UUID(int=2)))
    assert response.status_code == (200 if enabled else 404)
    assert handler.call_count == enabled
    assert permissions.call_count == enabled


@pytest.mark.parametrize("view", [DerivedMetricViewSet, DerivedMetricRunViewSet])
def test_enabled_does_not_bypass_authentication(view):
    request = APIRequestFactory().get("/tracer/derived-metrics/")
    with override_settings(METRICS_ENABLED=1):
        response = view.as_view({"get": "retrieve"})(request, pk=str(UUID(int=2)))
    assert response.status_code in (401, 403)


@pytest.mark.parametrize("view", [DerivedMetricViewSet, DerivedMetricRunViewSet])
def test_disabled_anonymous_request_is_not_found(view):
    request = APIRequestFactory().get("/tracer/derived-metrics/")
    with override_settings(METRICS_ENABLED=0):
        response = view.as_view({"get": "retrieve"})(request, pk=str(UUID(int=2)))
    assert response.status_code == 404
