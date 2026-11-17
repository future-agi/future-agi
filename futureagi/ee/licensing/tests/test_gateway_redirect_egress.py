"""Exercise real SDK + httpx redirect handling below Client.send (no sockets)."""

import json

import httpx
import pytest

from ee.licensing import activation_client
from ee.licensing.tests import test_retained_gateway_egress as retained_tests
from ee.licensing.tests.test_retained_gateway_egress import (
    ENTRIES,
    HELPER_ENTRIES,
    KEYLESS,
    RESPONSE,
    assert_denial,
    assert_no_fallback,
    close,
    invoke,
    remove_key,
)
from ee.usage.services import gateway_llm_client

retained = retained_tests.retained  # Fixture, used by name below.

# Capture before the imported fixture patches the outer HTTP send methods.
SYNC_SEND = httpx.Client.send
ASYNC_SEND = httpx.AsyncClient.send


def install_redirect(monkeypatch, state, on_first, on_later):
    monkeypatch.setattr(httpx.Client, "send", SYNC_SEND)
    monkeypatch.setattr(httpx.AsyncClient, "send", ASYNC_SEND)

    def boundary(client, request):
        request.read()
        state.attempts.append(request)
        if len(state.attempts) == 1:
            on_first()
            return httpx.Response(
                307, headers={"location": "/v1/redirected-completion"}, request=request
            )
        return on_later(request)

    async def async_boundary(client, request):
        await request.aread()
        return boundary(client, request)

    # This is below the real httpx redirect loop, not above it at send().
    monkeypatch.setattr(httpx.Client, "_send_single_request", boundary)
    monkeypatch.setattr(httpx.AsyncClient, "_send_single_request", async_boundary)


@pytest.mark.parametrize("entry", [*ENTRIES, *HELPER_ENTRIES])
@KEYLESS
def test_redirect_never_sends_after_key_removal(
    retained, settings, monkeypatch, entry, removed_key
):
    def forbidden(request):
        assert not getattr(settings, "EE_LICENSE_KEY", "").strip()
        assert request.headers["authorization"] == "Bearer fixture-service-token"
        assert json.loads(request.content)["messages"][0]["content"] == "fixture prompt"
        pytest.fail(
            f"Managed-gateway redirect HTTP attempt after license-key removal: {request.method} {request.url}"
        )

    install_redirect(
        monkeypatch, retained, lambda: remove_key(settings, removed_key), forbidden
    )
    with pytest.raises(activation_client.ManagedServiceError) as error:
        invoke(retained.llm, entry)
    assert_denial(error.value)
    assert len(retained.attempts) == 1
    assert_no_fallback(retained, retained.llm)


@pytest.mark.parametrize("entry", [*ENTRIES, *HELPER_ENTRIES])
@pytest.mark.parametrize("mode", ["licensed", "cloud"])
def test_authorized_redirect_control(retained, settings, monkeypatch, entry, mode):
    llm = retained.llm
    if mode == "cloud":
        settings.EE_LICENSE_KEY = ""
        settings.AGENTCC_INTERNAL_API_KEY = "fixture-internal-key"
        settings.AGENTCC_INTERNAL_URL = "https://internal.example.invalid"
        gateway_llm_client.invalidate_client()
        llm = retained.make()

    def response(request):
        if json.loads(request.content).get("stream"):
            return httpx.Response(200, content=b"data: [DONE]\n\n", request=request)
        return httpx.Response(200, json=RESPONSE, request=request)

    install_redirect(monkeypatch, retained, lambda: None, response)
    try:
        invoke(llm, entry)
        assert len(retained.attempts) == 2
        first, redirect = retained.attempts
        assert first.method == redirect.method == "POST"
        assert first.content == redirect.content
        assert first.headers["authorization"] == redirect.headers["authorization"]
        assert str(redirect.url).endswith("/v1/redirected-completion")
        assert_no_fallback(retained, llm)
    finally:
        if llm is not retained.llm:
            close(llm)
