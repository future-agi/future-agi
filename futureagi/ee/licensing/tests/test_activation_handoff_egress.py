"""Key removal while activation is in flight must stop the subsequent AI POST."""

import asyncio

import httpx
import pytest

from ee.licensing import activation_client, managed_ai
from ee.licensing.tests import test_keyless_egress as keyless_tests
from ee.licensing.tests.test_retained_gateway_egress import (
    KEYLESS,
    RESPONSE,
    assert_denial,
    remove_key,
)
from ee.usage.services import gateway_llm_client

# Fixtures from test_keyless_egress, used by name below (isolated_install is
# autouse there and here).
isolated_install = keyless_tests.isolated_install
wire = keyless_tests.wire

ENTRIES = [
    "call",
    "stream",
    "falcon_ai",
    "turing_small",
    "protect_flash",
    "stream_falcon_ai",
    "stream_turing_small",
    "stream_protect_flash",
    "sdk_sync",
    "sdk_async",
]


async def consume(stream):
    return [chunk async for chunk in stream]


def invoke(entry):
    model = (
        entry.removeprefix("stream_")
        if entry not in {"call", "stream"}
        else "falcon_ai"
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "fixture prompt"}],
    }
    if entry == "call":
        return activation_client.call_managed_service(json_body=payload)
    if entry == "stream":
        return asyncio.run(
            consume(activation_client.stream_managed_service(json_body=payload))
        )
    if entry == "sdk_sync":
        return gateway_llm_client.call_llm("gpt-4o", payload["messages"])
    if entry == "sdk_async":
        return asyncio.run(gateway_llm_client.acall_llm("gpt-4o", payload["messages"]))
    if entry.startswith("stream_"):
        return asyncio.run(consume(managed_ai.stream_chat_completion(payload)))
    return managed_ai.chat_completion(payload)


def install(monkeypatch, settings, on_activation):
    attempts = []

    def boundary(method, url, kwargs):
        attempts.append((method, str(url)))
        if str(url).endswith("/v1/self-hosted/activations"):
            assert settings.EE_LICENSE_KEY == "fixture-license"
            on_activation()
            return httpx.Response(
                200,
                json={
                    "access_token": "fixture-service-token",
                    "scope": "enterprise",
                    "gateway_url": "https://gateway.example.invalid",
                    "expires_in": 3600,
                },
                request=httpx.Request(method, url),
            )
        if not getattr(settings, "EE_LICENSE_KEY", "").strip():
            pytest.fail(
                f"Managed-gateway HTTP attempt after key removal during activation: {method} {url}"
            )
        request = httpx.Request(method, url)
        if "stream" in kwargs and kwargs["stream"]:
            return httpx.Response(200, content=b"data: [DONE]\n\n", request=request)
        return httpx.Response(200, json=RESPONSE, request=request)

    def post(url, **kwargs):
        return boundary("POST", url, kwargs)

    def send(client, request, **kwargs):
        return boundary(request.method, request.url, kwargs)

    async def async_send(client, request, **kwargs):
        return send(client, request, **kwargs)

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_send)
    return attempts


@pytest.mark.parametrize("entry", ENTRIES)
@KEYLESS
def test_no_prompt_send_if_key_removed_during_activation(
    wire, settings, monkeypatch, entry, removed_key
):
    settings.EE_LICENSE_KEY = "fixture-license"
    attempts = install(monkeypatch, settings, lambda: remove_key(settings, removed_key))
    try:
        with pytest.raises(activation_client.ManagedServiceError) as error:
            invoke(entry)
        assert_denial(error.value)
        assert len(attempts) == 1
    finally:
        gateway_llm_client.invalidate_client()


@pytest.mark.parametrize("entry", ENTRIES)
def test_licensed_activation_handoff_unchanged(wire, settings, monkeypatch, entry):
    settings.EE_LICENSE_KEY = "fixture-license"
    attempts = install(monkeypatch, settings, lambda: None)
    try:
        invoke(entry)
        assert len(attempts) == 2
        assert attempts[0][1].endswith("/v1/self-hosted/activations")
        assert attempts[1] == (
            "POST",
            "https://gateway.example.invalid/v1/chat/completions",
        )
    finally:
        gateway_llm_client.invalidate_client()
