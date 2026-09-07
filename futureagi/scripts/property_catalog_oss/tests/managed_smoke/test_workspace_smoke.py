"""Offline fixture/admission guard tests; actual tenant isolation needs live APIs."""

from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from workspace_smoke import active_result, create, fixture_scopes, request


def test_foreign_header_reaches_request_and_original_credentials_are_restored():
    from django.http import JsonResponse
    from django.test import override_settings
    from django.urls import path
    from rest_framework.test import APIClient

    def echo(actual):
        return JsonResponse(
            {
                "workspace": actual.META.get("HTTP_X_WORKSPACE_ID"),
                "key": actual.META.get("HTTP_X_API_KEY"),
            }
        )

    urls = ModuleType("owned_header_echo")
    urls.urlpatterns = [path("tracer/dashboard/filter_values/", echo)]
    client = APIClient()
    client.credentials(HTTP_X_WORKSPACE_ID="original", HTTP_X_API_KEY="fixture-only")
    with override_settings(ROOT_URLCONF=urls, MIDDLEWARE=[]):
        status, body = request(
            client, "filter_values", {}, HTTP_X_WORKSPACE_ID="foreign"
        )
        assert status == 200
        assert body == {"workspace": "foreign", "key": "fixture-only"}
        assert request(client, "filter_values", {})[1]["workspace"] == "original"


def test_workspace_scopes_are_repeatable_disjoint_and_share_only_intended_org():
    run = SimpleNamespace(manifest={"run_id": "0123456789abcdef"})
    original = {"organization_id": "00000000-0000-4000-8000-000000000001"}
    scopes = fixture_scopes(run, original)
    assert scopes == fixture_scopes(run, original)
    assert scopes[0]["organization_id"] == original["organization_id"]
    assert scopes[1]["organization_id"] != original["organization_id"]
    assert (
        len({s["workspace_id"] for s in scopes} | {s["project_id"] for s in scopes})
        == 4
    )


def test_unowned_fixture_refused_before_django_writes():
    with pytest.raises(RuntimeError, match="owned disposable"):
        create(
            SimpleNamespace(
                manifest={"application": True}, owned=Mock(return_value=[])
            ),
            {},
        )


def test_request_failure_restores_original_credentials():
    client = SimpleNamespace(_credentials={"HTTP_X_WORKSPACE_ID": "original"})
    client.credentials = lambda **kwargs: setattr(client, "_credentials", kwargs)
    client.get = Mock(side_effect=RuntimeError("primary request failure"))
    with pytest.raises(RuntimeError, match="primary request failure"):
        request(client, "filter_values", {}, HTTP_X_WORKSPACE_ID="foreign")
    assert client._credentials == {"HTTP_X_WORKSPACE_ID": "original"}


def test_only_typed_pending_is_allowed_for_new_workspace():
    result = {
        "query_provenance": "property_catalog_bootstrap",
        "query_status": "pending",
        "query_complete": False,
        "query_exact": False,
        "metrics": [],
    }
    assert active_result(200, {"result": result}, allow_pending=True) is None
    with pytest.raises(RuntimeError, match="pretended"):
        active_result(
            200, {"result": {**result, "query_complete": True}}, allow_pending=True
        )
    with pytest.raises(RuntimeError, match="did not use selected"):
        active_result(200, {"result": result})


def test_http_failure_is_never_retried_as_pending():
    with pytest.raises(RuntimeError, match="503"):
        active_result(503, {"error": "unavailable"}, allow_pending=True)
