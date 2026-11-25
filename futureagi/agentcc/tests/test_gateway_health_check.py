"""Live gateway probes report their completion time without persisted state."""

import datetime
import json
from functools import partial
from unittest.mock import MagicMock

import httpx
import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.user import User
from agentcc.services import config_push
from agentcc.services.gateway_client import GatewayClient
from agentcc.views import gateway


@pytest.mark.unit
@pytest.mark.parametrize("action", ["list", "retrieve", "health_check"])
@pytest.mark.parametrize("outcome", ["healthy", "connection_error", "timeout"])
def test_gateway_reports_probe_completion_time(monkeypatch, action, outcome):
    started = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
    completed = started + datetime.timedelta(seconds=10)
    clock = {"now": started}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    probes = []

    def transport(request):
        assert request.method == "GET"
        assert request.url == "http://gateway.test/healthz"
        probes.append(request)
        clock["now"] = completed
        if outcome == "connection_error":
            raise httpx.ConnectError("Gateway offline", request=request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("Gateway timed out", request=request)
        return httpx.Response(200, json={"status": "ok"})

    # Keep the real GatewayClient and its HTTP error handling; replace transport only.
    monkeypatch.setattr(
        httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(transport))
    )
    monkeypatch.setattr(
        gateway, "get_gateway_client", lambda: GatewayClient("http://gateway.test")
    )

    def provider_rows(**kwargs):
        # Provider enrichment and config sync must not change the probe's timestamp.
        clock["now"] = completed + datetime.timedelta(seconds=20)
        providers = MagicMock()
        providers.count.return_value = 0
        providers.__iter__.return_value = iter([])
        return providers

    monkeypatch.setattr(
        gateway.AgentccProviderCredential.no_workspace_objects, "filter", provider_rows
    )
    monkeypatch.setattr(gateway.cache, "get", lambda key: True)

    factory = APIRequestFactory()
    method = "post" if action == "health_check" else "get"
    request = getattr(factory, method)("/agentcc/gateways/default/", {}, format="json")
    request.organization = Organization(name="Probe timestamp test")
    force_authenticate(request, user=User(email="health-check@example.test"))
    view = gateway.AgentccGatewayViewSet.as_view({method: action})
    response = view(request, **({"pk": "default"} if action != "list" else {}))
    response.render()
    payload = json.loads(response.content)

    assert len(probes) == 1
    unreachable = outcome != "healthy"
    assert response.status_code == (
        400 if unreachable and action == "health_check" else 200
    )
    result = payload["result"][0] if action == "list" else payload["result"]
    assert result["status"] == ("unreachable" if unreachable else "healthy")
    assert datetime.datetime.fromisoformat(result["last_health_check"]) == completed

    schema = getattr(gateway.AgentccGatewayViewSet, action)._swagger_auto_schema
    schema = schema.get(method, schema)
    serializer = schema["responses"][response.status_code](data=payload)
    assert serializer.is_valid(), serializer.errors
    validated_result = serializer.validated_data["result"]
    if action == "list":
        validated_result = validated_result[0]
    assert "last_health_check" in validated_result


@pytest.mark.unit
@pytest.mark.parametrize("authenticated", [True, False])
def test_rejected_health_check_does_not_claim_a_completed_probe(
    monkeypatch, authenticated
):
    client = MagicMock()
    monkeypatch.setattr(gateway, "get_gateway_client", client)
    request = APIRequestFactory().post(
        "/agentcc/gateways/default/health_check/", {"unexpected": True}, format="json"
    )
    if authenticated:
        force_authenticate(request, user=User(email="health-check@example.test"))
    response = gateway.AgentccGatewayViewSet.as_view({"post": "health_check"})(
        request, pk="default"
    )
    response.render()

    assert response.status_code == (400 if authenticated else 401)
    assert "last_health_check" not in response.content.decode()
    client.assert_not_called()
    if authenticated:
        schema = getattr(
            gateway.AgentccGatewayViewSet.health_check, "_swagger_auto_schema"
        )["post"]
        serializer = schema["responses"][400](data=json.loads(response.content))
        assert serializer.is_valid(), serializer.errors


def _stub_providers(monkeypatch):
    providers = MagicMock()
    providers.count.return_value = 0
    providers.__iter__.return_value = iter([])
    monkeypatch.setattr(
        gateway.AgentccProviderCredential.no_workspace_objects,
        "filter",
        lambda **kwargs: providers,
    )


def _use_gateway_transport(monkeypatch, transport):
    monkeypatch.setattr(
        httpx, "Client", partial(httpx.Client, transport=httpx.MockTransport(transport))
    )
    monkeypatch.setattr(
        gateway, "get_gateway_client", lambda: GatewayClient("http://gateway.test")
    )


def _post_health_check():
    request = APIRequestFactory().post(
        "/agentcc/gateways/default/health_check/", {}, format="json"
    )
    request.organization = Organization(name="Health check test")
    force_authenticate(request, user=User(email="health-check@example.test"))
    response = gateway.AgentccGatewayViewSet.as_view({"post": "health_check"})(
        request, pk="default"
    )
    response.render()
    return response, json.loads(response.content)


@pytest.mark.unit
@pytest.mark.parametrize(
    "failure, expected",
    [
        ("connect", gateway.HEALTH_CHECK_UNREACHABLE),
        ("timeout", gateway.HEALTH_CHECK_TIMED_OUT),
        ("http_503", "The gateway returned HTTP 503."),
    ],
)
def test_failed_health_check_returns_a_sanitized_error(monkeypatch, failure, expected):
    """TH-3947 A-1: users see a fixed message; the raw diagnostics stay in logs."""
    upstream_body = "Traceback (most recent call last): node agentcc-7 failed"

    def transport(request):
        if failure == "connect":
            raise httpx.ConnectError("[Errno -3] name resolution", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(503, text=upstream_body)

    _use_gateway_transport(monkeypatch, transport)
    logged = []
    monkeypatch.setattr(
        gateway.logger, "warning", lambda event, **kw: logged.append((event, kw))
    )

    response, payload = _post_health_check()

    assert response.status_code == 400
    assert payload["result"]["status"] == "unreachable"
    assert payload["result"]["error"] == expected
    body = response.content.decode()
    for raw in ("gateway.test", "healthz", "Errno", "Traceback", "agentcc-7"):
        assert raw not in body
    events = [event for event, _ in logged]
    assert "gateway_health_check_failed" in events
    diagnostic = dict(logged)["gateway_health_check_failed"]["error"]
    raw_marker = {"connect": "Errno", "timeout": "timed out", "http_503": "agentcc-7"}
    assert raw_marker[failure] in diagnostic


@pytest.mark.unit
def test_unexpected_health_check_error_is_not_echoed(monkeypatch):
    def broken_client():
        raise RuntimeError("postgres://svc:hunter2@db.internal:5432 refused")

    monkeypatch.setattr(gateway, "get_gateway_client", broken_client)
    monkeypatch.setattr(gateway.logger, "exception", lambda *a, **kw: None)

    response, payload = _post_health_check()

    assert response.status_code == 400
    assert payload["result"] == gateway.HEALTH_CHECK_UNEXPECTED
    body = response.content.decode()
    assert "hunter2" not in body and "db.internal" not in body


@pytest.mark.unit
def test_cold_sync_cache_dispatches_resync_off_the_request_path(monkeypatch):
    """TH-3947 F1: the all-org config push never holds the health-check response."""
    _use_gateway_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"status": "ok"})
    )
    _stub_providers(monkeypatch)
    store = {}

    def add(key, value, timeout):
        if key in store:
            return False
        store[key] = value
        return True

    monkeypatch.setattr(gateway.cache, "get", store.get)
    monkeypatch.setattr(gateway.cache, "add", add)
    monkeypatch.setattr(gateway.cache, "delete", lambda key: store.pop(key, None))
    dispatched = []
    monkeypatch.setattr(
        gateway.push_all_org_configs_activity,
        "apply_async",
        lambda **kwargs: dispatched.append(kwargs),
    )

    def inline_sync(*args, **kwargs):
        raise AssertionError("config push must not run inside the request")

    monkeypatch.setattr(config_push, "push_all_org_configs", inline_sync)

    first, first_payload = _post_health_check()
    second, _ = _post_health_check()

    assert first.status_code == second.status_code == 200
    assert first_payload["result"]["status"] == "healthy"
    assert len(dispatched) == 1
    assert dispatched[0]["dispatch_timeout_seconds"] <= 5
    assert store == {"agentcc:gateway_config_synced": True}


@pytest.mark.unit
def test_failed_resync_dispatch_keeps_the_probe_result_and_releases_the_claim(
    monkeypatch,
):
    _use_gateway_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"status": "ok"})
    )
    _stub_providers(monkeypatch)
    store = {}
    monkeypatch.setattr(gateway.cache, "get", store.get)
    monkeypatch.setattr(
        gateway.cache, "add", lambda key, value, timeout: store.setdefault(key, value)
    )
    monkeypatch.setattr(gateway.cache, "delete", lambda key: store.pop(key, None))

    def unavailable(**kwargs):
        raise RuntimeError("temporal unavailable")

    monkeypatch.setattr(
        gateway.push_all_org_configs_activity, "apply_async", unavailable
    )
    monkeypatch.setattr(gateway.logger, "warning", lambda *a, **kw: None)

    response, payload = _post_health_check()

    assert response.status_code == 200
    assert payload["result"]["status"] == "healthy"
    assert store == {}


@pytest.mark.unit
@pytest.mark.parametrize("operation", ["get", "add", "delete"])
def test_sync_cache_failure_preserves_healthy_probe_and_timestamp(
    monkeypatch, operation
):
    """TH-3947 FX1-1: optional re-sync cache failures cannot invalidate a probe."""
    completed = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
    clock = {"now": completed}
    monkeypatch.setattr(timezone, "now", lambda: clock["now"])
    _use_gateway_transport(
        monkeypatch, lambda request: httpx.Response(200, json={"status": "ok"})
    )
    _stub_providers(monkeypatch)
    cache = MagicMock()
    cache.get.return_value = None
    cache.add.return_value = True

    def unavailable(*args, **kwargs):
        clock["now"] = completed + datetime.timedelta(seconds=20)
        raise ConnectionError(f"cache.{operation} unavailable")

    getattr(cache, operation).side_effect = unavailable
    monkeypatch.setattr(gateway, "cache", cache)
    dispatch = MagicMock()
    if operation == "delete":
        dispatch.side_effect = RuntimeError("temporal unavailable")
    monkeypatch.setattr(gateway.push_all_org_configs_activity, "apply_async", dispatch)

    response, payload = _post_health_check()

    assert response.status_code == 200
    assert payload["result"]["status"] == "healthy"
    assert (
        datetime.datetime.fromisoformat(payload["result"]["last_health_check"])
        == completed
    )
    assert f"cache.{operation}" not in response.content.decode()
    getattr(cache, operation).assert_called_once()
    if operation == "delete":
        dispatch.assert_called_once_with(dispatch_timeout_seconds=5)
        cache.delete.assert_called_once_with("agentcc:gateway_config_synced")
    else:
        dispatch.assert_not_called()
        cache.delete.assert_not_called()
