"""Keyless self-hosting must not contact licensing or managed gateways.

HTTP boundaries are recording fakes, never live transports. The stale-state
cases model removal of a previously configured license in a running process.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import socket
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from ee.licensing import activation_client, heartbeat, managed_ai, startup, state
from ee.usage.deployment import DeploymentMode
from ee.usage.services import gateway_llm_client

DETAIL = "Managed AI requires an Enterprise license"
INSTANCE_ID = "00000000-0000-0000-0000-000000000205"


@pytest.fixture(autouse=True)
def isolated_install(monkeypatch, settings):
    settings.EE_LICENSE_KEY = ""
    settings.EE_LICENSE_PUBLIC_KEY = ""
    settings.EE_LICENSE_PUBLIC_KEYS = ""
    settings.AGENTCC_INTERNAL_API_KEY = ""
    monkeypatch.delenv("EE_LICENSE_KEY", raising=False)
    monkeypatch.delenv("AGENTCC_INTERNAL_API_KEY", raising=False)
    monkeypatch.delenv("FUTURE_AGI_ENTERPRISE_HEARTBEAT_DISABLED", raising=False)
    monkeypatch.setattr(DeploymentMode, "is_cloud", lambda: False)
    monkeypatch.setattr(activation_client, "_cached_token", None)
    monkeypatch.setattr(gateway_llm_client, "_client", None)
    monkeypatch.setattr(gateway_llm_client, "_async_client", None)
    monkeypatch.setattr(state, "_current_snapshot", state.get_snapshot())
    monkeypatch.setattr(
        "tfc.deployment_telemetry.state.get_or_create_telemetry_state",
        Mock(return_value=SimpleNamespace(instance_id=INSTANCE_ID)),
    )
    monkeypatch.setattr("tfc.deployment_telemetry.config.get_version", lambda: "1.8.0")
    monkeypatch.setattr(
        "tfc.deployment_telemetry.config.detect_deployment_type", lambda: "docker"
    )
    monkeypatch.setattr(heartbeat, "collect_heartbeat_usage", lambda: {})
    monkeypatch.setattr(heartbeat, "_next_sequence", lambda: 1)


@pytest.fixture
def wire(monkeypatch):
    attempts = []
    sockets = []
    response = {"scope": "oss"}  # Actual keyless activation response: no token.

    def deny_socket(*args, **kwargs):
        sockets.append("unexpected socket/DNS attempt")
        raise AssertionError("Real networking is forbidden in licensing tests")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, deny_socket)
    monkeypatch.setattr(socket, "getaddrinfo", deny_socket)
    monkeypatch.setattr(socket, "create_connection", deny_socket)

    def post(url, **kwargs):
        attempts.append(("POST", str(url), kwargs))
        return httpx.Response(200, json=response, request=httpx.Request("POST", url))

    def send(client, request, **kwargs):
        attempts.append((request.method, str(request.url), dict(request.headers)))
        return httpx.Response(200, json=response, request=request)

    async def async_send(client, request, **kwargs):
        attempts.append((request.method, str(request.url), dict(request.headers)))
        return httpx.Response(
            200,
            content=b'data: {"choices": []}\n\ndata: [DONE]\n\n',
            request=request,
        )

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_send)
    yield SimpleNamespace(attempts=attempts, response=response)
    assert sockets == [], "Even a caught real network attempt fails this test"


@pytest.fixture(params=[None, "", " \t\n"], ids=["unset", "empty", "whitespace"])
def keyless(request, monkeypatch, settings, wire):
    value = request.param
    if value is not None:
        settings.EE_LICENSE_KEY = value
        monkeypatch.setenv("EE_LICENSE_KEY", value)
    return wire


def assert_no_license(error):
    assert error.code == "NO_ENTERPRISE_LICENSE"
    assert error.detail == DETAIL
    assert str(error) == f"NO_ENTERPRISE_LICENSE: {DETAIL}"


def enterprise_token():
    return activation_client.ServiceToken(
        access_token="fixture-service-token",
        gateway_url="https://gateway.example.invalid",
        expires_at=time.time() + 3600,
        allowed_services=["falcon", "turing"],
        allowed_models=["falcon_ai", "turing_small"],
        scope="enterprise",
    )


@pytest.mark.parametrize("entry", ["get_service_token", "_activate"])
def test_keyless_activation_is_local(keyless, entry):
    with pytest.raises(activation_client.ManagedServiceError) as error:
        getattr(activation_client, entry)()
    assert keyless.attempts == []
    assert_no_license(error.value)


def test_keyless_ignores_previously_cached_service_token(keyless, monkeypatch):
    monkeypatch.setattr(activation_client, "_cached_token", enterprise_token())
    with pytest.raises(activation_client.ManagedServiceError) as error:
        activation_client.get_service_token()
    assert keyless.attempts == []
    assert_no_license(error.value)


def test_keyless_managed_service(keyless):
    with pytest.raises(activation_client.ManagedServiceError) as error:
        activation_client.call_managed_service(json_body={"model": "falcon_ai"})
    assert keyless.attempts == []
    assert_no_license(error.value)


async def consume(stream):
    return [chunk async for chunk in stream]


def test_keyless_managed_service_stream(keyless):
    with pytest.raises(activation_client.ManagedServiceError) as error:
        asyncio.run(consume(activation_client.stream_managed_service(json_body={})))
    assert keyless.attempts == []
    assert_no_license(error.value)


@pytest.mark.parametrize("model", ["falcon_ai", "turing_small", "protect_flash"])
@pytest.mark.parametrize("stream", [False, True], ids=["sync", "stream"])
def test_keyless_managed_chat(keyless, model, stream):
    payload = {"model": model, "messages": []}
    with pytest.raises(activation_client.ManagedServiceError) as error:
        if stream:
            asyncio.run(consume(managed_ai.stream_chat_completion(payload)))
        else:
            managed_ai.chat_completion(payload)
    assert keyless.attempts == []
    assert_no_license(error.value)


def test_keyless_heartbeat_with_stale_license_snapshot(keyless, monkeypatch):
    monkeypatch.setattr(
        state, "get_snapshot", lambda: SimpleNamespace(license_id="old")
    )
    assert heartbeat.send_heartbeat() is False
    assert keyless.attempts == []


def test_keyless_startup_has_no_checkin(keyless):
    startup.validate_on_startup()
    assert keyless.attempts == []


def test_keyless_guard_precedes_instance_lookup_and_logs(keyless, monkeypatch):
    from structlog.testing import capture_logs

    lookup = Mock(
        side_effect=AssertionError("Keyless calls must not read instance state")
    )
    monkeypatch.setattr(
        "tfc.deployment_telemetry.state.get_or_create_telemetry_state", lookup
    )
    with capture_logs() as logs:
        with pytest.raises(activation_client.ManagedServiceError) as error:
            activation_client._activate()
        assert heartbeat.send_heartbeat() is False
    assert_no_license(error.value)
    assert {entry["event"] for entry in logs} >= {
        "managed_ai_skipped_no_license_key",
        "enterprise_heartbeat_skipped_no_license_key",
    }
    lookup.assert_not_called()
    assert keyless.attempts == []


def test_keyless_real_turing_client_propagates_denial(keyless):
    from ee.turing.client import TuringClient

    with pytest.raises(activation_client.ManagedServiceError) as error:
        TuringClient().chat_completion(model="turing_small", messages=[])
    assert keyless.attempts == []
    assert_no_license(error.value)


def test_keyless_falcon_http_mapping_matches_existing_no_license_branch(
    keyless, monkeypatch
):
    # OpenAPI-only response schemas pull in unrelated DB models. Stub those
    # schemas, not the real view, Falcon client, transport, or error formatter.
    from importlib import import_module

    from rest_framework import serializers

    monkeypatch.setitem(
        sys.modules,
        "ee.falcon_ai.serializers_contracts",
        SimpleNamespace(
            FalconErrorResponseSerializer=serializers.Serializer,
            QuickAnalysisResponseSerializer=serializers.Serializer,
        ),
    )
    view = import_module("ee.falcon_ai.views_quick_analysis").QuickAnalysisView()
    request = SimpleNamespace(data={"prompt": "fixture prompt"})
    oss_token = enterprise_token()
    oss_token.scope = "oss"
    oss_token.access_token = ""
    with monkeypatch.context() as baseline:
        baseline.setattr(activation_client, "get_service_token", lambda: oss_token)
        expected = view.post(request)
    actual = view.post(request)
    assert actual.status_code == expected.status_code == 502
    assert actual.data == expected.data
    assert f"NO_ENTERPRISE_LICENSE: {DETAIL}" in str(actual.data)
    assert keyless.attempts == []


@pytest.mark.parametrize("async_client", [False, True])
def test_keyless_gateway_does_not_reuse_cached_client(
    keyless, monkeypatch, async_client
):
    name = "_async_client" if async_client else "_client"
    monkeypatch.setattr(gateway_llm_client, name, object())
    get_client = (
        gateway_llm_client.get_async_gateway_client
        if async_client
        else gateway_llm_client.get_gateway_client
    )
    # OSS has no gateway client; whitespace may still resolve the EE route,
    # but it must fail with the same typed no-license error, without egress.
    try:
        assert get_client() is None
    except activation_client.ManagedServiceError as error:
        assert_no_license(error)
    assert keyless.attempts == []


def test_keyless_raw_gateway_rejects_previously_acquired_client(keyless):
    client = Mock()
    create = client.chat.completions.with_raw_response.create
    create.return_value = SimpleNamespace(headers={}, parse=lambda: {})
    with pytest.raises(activation_client.ManagedServiceError) as error:
        gateway_llm_client.call_llm_raw(client, model="falcon_ai", messages=[])
    create.assert_not_called()
    assert keyless.attempts == []
    assert_no_license(error.value)


@pytest.mark.parametrize("async_call", [False, True])
def test_keyless_gateway_managed_model_never_falls_back(
    keyless, monkeypatch, async_call
):
    fallback = Mock(
        side_effect=AssertionError("Managed models must not use BYOK fallback")
    )
    monkeypatch.setattr(gateway_llm_client, "_call_litellm", fallback)
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=fallback))
    with pytest.raises(activation_client.ManagedServiceError) as error:
        if async_call:
            asyncio.run(gateway_llm_client.acall_llm("falcon_ai", []))
        else:
            gateway_llm_client.call_llm("falcon_ai", [])
    assert keyless.attempts == []
    fallback.assert_not_called()
    assert_no_license(error.value)


@pytest.mark.parametrize("source", ["settings", "environment"])
@pytest.mark.parametrize("license_key", ["fixture-license", " fixture-license \n"])
def test_licensed_activation_payload_and_cache_unchanged(
    wire, settings, monkeypatch, source, license_key
):
    if source == "settings":
        settings.EE_LICENSE_KEY = license_key
    else:
        monkeypatch.setenv("EE_LICENSE_KEY", license_key)
    wire.response.update(
        access_token="fixture-service-token",
        scope="enterprise",
        expires_in=3600,
        gateway_url="https://gateway.example.invalid",
        allowed_services=["falcon"],
        allowed_models=["falcon_ai"],
    )
    token = activation_client.get_service_token()
    assert activation_client.get_service_token() is token
    assert token.access_token == "fixture-service-token"
    assert token.scope == "enterprise"
    assert token.gateway_url == "https://gateway.example.invalid"
    assert token.allowed_services == ["falcon"]
    assert token.allowed_models == ["falcon_ai"]
    assert token.is_expired is False
    assert wire.attempts == [
        (
            "POST",
            activation_client.get_activation_url(),
            {
                "json": {
                    "instance_id": INSTANCE_ID,
                    "version": "1.8.0",
                    "license_key_hash": hashlib.sha256(
                        license_key.encode()
                    ).hexdigest(),
                },
                "timeout": 10.0,
            },
        )
    ]


def test_licensed_heartbeat_still_sends_signed_payload(wire, settings, monkeypatch):
    settings.EE_LICENSE_KEY = "fixture-license"
    monkeypatch.setattr(
        state, "get_snapshot", lambda: SimpleNamespace(license_id="fixture-id")
    )
    assert heartbeat.send_heartbeat() is True
    method, url, kwargs = wire.attempts[0]
    assert len(wire.attempts) == 1
    assert (method, url) == ("POST", heartbeat.get_heartbeat_url())
    payload = json.loads(kwargs["content"])
    assert set(payload) == {
        "instance_id",
        "license_id",
        "version",
        "deployment_type",
        "timestamp",
        "nonce",
        "sequence",
        "usage_data",
    }
    assert payload["instance_id"] == INSTANCE_ID
    assert payload["license_id"] == "fixture-id"
    secret = hashlib.sha256(b"fixture-license").hexdigest().encode()
    assert (
        kwargs["headers"]["X-FutureAGI-Heartbeat-Signature"]
        == hmac.new(secret, kwargs["content"], hashlib.sha256).hexdigest()
    )


@pytest.mark.parametrize("stream", [False, True])
def test_cloud_internal_key_transport_unchanged_without_license(
    wire, settings, monkeypatch, stream
):
    settings.AGENTCC_INTERNAL_API_KEY = "fixture-internal-key"
    settings.AGENTCC_INTERNAL_URL = "https://internal.example.invalid"
    monkeypatch.setattr(DeploymentMode, "is_cloud", lambda: True)
    payload = {"model": "falcon_ai", "messages": [], "stream": stream}
    if stream:
        assert asyncio.run(consume(managed_ai.stream_chat_completion(payload))) == [
            {"choices": []}
        ]
        assert wire.attempts[0][2]["authorization"] == "Bearer fixture-internal-key"
    else:
        assert managed_ai.chat_completion(payload) == wire.response
        assert (
            wire.attempts[0][2]["headers"]["Authorization"]
            == "Bearer fixture-internal-key"
        )
        assert wire.attempts[0][2]["json"] == payload
    assert len(wire.attempts) == 1
    assert wire.attempts[0][:2] == (
        "POST",
        "https://internal.example.invalid/v1/chat/completions",
    )
