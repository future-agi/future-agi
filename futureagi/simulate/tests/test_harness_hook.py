"""Tests for the public harness-hook surface (``simulate/views/harness_hook.py``):
a hosted agent's tool calls and room-config lookups. No platform credential
exists for these requests — the capability token in the URL is the credential
— so these views must be reachable with no auth and no CSRF token.
"""

import uuid
from unittest.mock import patch

import httpx
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from simulate.models import RLContract, RLEnvironment, RLScenario, RLWorld, RLWorldCopy

HOOK_BASE = "/simulate/harness-hook"


@pytest.fixture(autouse=True)
def _internal_secret(settings):
    settings.INTERNAL_API_SECRET = "harness-hook-secret"


def _room(run_id="11111111-1111-4111-8111-111111111111", index=0, case="case_1"):
    return f"hosted-{run_id}-i{index}-{case}"


def _make_copy(organization, workspace, status=RLWorldCopy.Status.READY):
    environment = RLEnvironment.objects.create(
        organization=organization, workspace=workspace, title="Hook Env"
    )
    contract = RLContract.objects.create(
        organization=organization,
        environment=environment,
        version=1,
        status=RLContract.Status.ACTIVE,
        data={},
    )
    world = RLWorld.objects.create(
        organization=organization, environment=environment, contract=contract, version=1
    )
    scenario = RLScenario.objects.create(
        organization=organization, environment=environment, world=world, name="Hook Scenario"
    )
    return RLWorldCopy.objects.create(
        organization=organization,
        environment=environment,
        world=world,
        scenario=scenario,
        purpose=RLWorldCopy.Purpose.GATE,
        status=status,
    )


def _json_response(body, status_code=200):
    return httpx.Response(
        status_code, json=body, headers={"content-type": "application/json"}
    )


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_room_config_malformed_400(api_client):
    response = api_client.get(f"{HOOK_BASE}/room-config/garbage")
    assert response.status_code == 400
    assert response.json()["room"] == "garbage"


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_room_config_wellformed_unknown_404(api_client):
    room = _room()
    response = api_client.get(f"{HOOK_BASE}/room-config/{room}")
    assert response.status_code == 404
    assert response.json()["room"] == room


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_room_config_get_only(api_client):
    response = api_client.post(f"{HOOK_BASE}/room-config/{_room()}", {}, format="json")
    assert response.status_code == 405


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_hook_unknown_token_404(api_client):
    with patch("simulate.views.harness_hook.httpx.request") as mock_request:
        response = api_client.post(
            f"{HOOK_BASE}/{uuid.uuid4()}/some_tool", {}, format="json"
        )
    assert response.status_code == 404
    assert response.json()["error"] == "unknown token"
    mock_request.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_hook_wrong_state_names_state(api_client, organization, workspace):
    copy = _make_copy(organization, workspace, status=RLWorldCopy.Status.DROPPED)
    with patch("simulate.views.harness_hook.httpx.request") as mock_request:
        response = api_client.post(f"{HOOK_BASE}/{copy.token}/some_tool", {}, format="json")
    assert response.status_code == 404
    assert "dropped" in response.json()["error"]
    mock_request.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_hook_forwards_and_relays(api_client, organization, workspace):
    copy = _make_copy(organization, workspace, status=RLWorldCopy.Status.READY)
    with patch(
        "simulate.views.harness_hook.httpx.request",
        return_value=_json_response({"result": 1}),
    ) as mock_request:
        response = api_client.post(
            f"{HOOK_BASE}/{copy.token}/lookup_order", {"order_id": "A1"}, format="json"
        )
    assert response.status_code == 200, response.content
    assert response.json() == {"result": 1}

    args, kwargs = mock_request.call_args
    assert f"/internal/hook/{copy.token}/lookup_order" in args[1]
    assert kwargs["headers"] == {"Authorization": "Bearer harness-hook-secret"}


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_hook_upstream_down_502(api_client, organization, workspace):
    copy = _make_copy(organization, workspace, status=RLWorldCopy.Status.IN_CALL)
    with patch(
        "simulate.views.harness_hook.httpx.request",
        side_effect=httpx.ConnectError("boom"),
    ):
        response = api_client.post(f"{HOOK_BASE}/{copy.token}/some_tool", {}, format="json")
    assert response.status_code == 502


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_hook_needs_no_auth_or_csrf(organization, workspace):
    # enforce_csrf_checks=True is what actually exercises the CSRF-exemption
    # half of this claim — the default test client never enforces CSRF at all.
    csrf_client = APIClient(enforce_csrf_checks=True)
    copy = _make_copy(organization, workspace, status=RLWorldCopy.Status.READY)
    with patch(
        "simulate.views.harness_hook.httpx.request",
        return_value=_json_response({"ok": True}),
    ):
        response = csrf_client.post(
            f"{HOOK_BASE}/{copy.token}/some_tool", {}, format="json"
        )
    assert response.status_code not in (401, 403)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
def test_hook_throttled(api_client, organization, workspace, monkeypatch):
    # ScopedRateThrottle's request history is keyed by (scope, client ip) in the
    # shared test cache, so an earlier test's calls to this same scope would
    # otherwise count against this test's budget.
    cache.clear()
    copy = _make_copy(organization, workspace, status=RLWorldCopy.Status.READY)
    # settings.REST_FRAMEWORK is a no-op here: SimpleRateThrottle.THROTTLE_RATES
    # binds the original dict at import time, so the override has to land on
    # that dict directly for get_rate() to see it.
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "harness_hook", "2/min")

    with patch(
        "simulate.views.harness_hook.httpx.request",
        return_value=_json_response({"ok": True}),
    ):
        first = api_client.post(f"{HOOK_BASE}/{copy.token}/some_tool", {}, format="json")
        second = api_client.post(f"{HOOK_BASE}/{copy.token}/some_tool", {}, format="json")
        third = api_client.post(f"{HOOK_BASE}/{copy.token}/some_tool", {}, format="json")

    assert first.status_code == 200, first.content
    assert second.status_code == 200, second.content
    assert third.status_code == 429
