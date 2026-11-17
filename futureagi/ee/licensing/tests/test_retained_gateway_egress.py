"""Retained core LLM clients must not outlive self-hosted license-key removal.

``LLM._init_client`` keeps the gateway SDK clients it acquires on the
instance, and the gateway completion paths send through them directly. These
tests use the production ``LLM`` class with real OpenAI SDK clients; only the
HTTP boundary (a recording fake that fails the test on any attempt), the
direct-provider fallback and usage logging are replaced. Routing, retries and
the broad fallback handlers stay real.

Key removal in a running process is the only way a keyless install can hold a
retained client (construction while keyless retains none), so this guards
against stale gateway access rather than a fresh-install path.

The OpenAI SDK also retries on its own (``max_retries=2`` in the production
factories), below every application-level check. The SDK-retry tests keep that
default, remove the key while a request is in flight and fail on any later
HTTP attempt, for the core ``LLM`` paths and for the shared gateway helpers.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from agentic_eval.core.llm import llm as llm_module
from ee.licensing import activation_client
from ee.usage.deployment import _detect_mode
from ee.usage.services import gateway_llm_client

MESSAGES = [{"role": "user", "content": "fixture prompt"}]
ENTRIES = [
    "sync",
    "async",
    "sync_stream",
    "async_stream",
    "content",
    "async_content",
    "tools",
    "bounded_tools",
]
# Callers that send through the factory-made gateway clients without the core
# LLM retry loop: call_llm_raw, the shared call_llm/acall_llm helpers and
# LLM.call_llm.
HELPER_ENTRIES = ["raw", "helper_sync", "helper_async", "llm_call"]
KEYLESS = pytest.mark.parametrize(
    "removed_key", [None, "", " \t\n"], ids=["unset", "empty", "whitespace"]
)
# Every authorized request body below, whichever caller sent it.
EXPECTED_BODY = {
    "model": "gpt-4o",
    "messages": MESSAGES,
    "temperature": 0.7,
    "max_tokens": 50,
}
RESPONSE = {
    "id": "chatcmpl-fixture",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4o",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "fixture response"},
            "finish_reason": "stop",
        }
    ],
}


def remove_key(settings, removed_key):
    if removed_key is None:
        del settings.EE_LICENSE_KEY
    else:
        settings.EE_LICENSE_KEY = removed_key


def close(llm):
    if llm._gateway_client is not None:
        llm._gateway_client.close()
    if llm._async_gateway_client is not None:
        asyncio.run(llm._async_gateway_client.close())


@pytest.fixture
def retained(monkeypatch, settings):
    settings.EE_LICENSE_KEY = "fixture-license"
    settings.AGENTCC_INTERNAL_API_KEY = ""
    settings.CLOUD_DEPLOYMENT = ""
    monkeypatch.delenv("EE_LICENSE_KEY", raising=False)
    monkeypatch.delenv("AGENTCC_INTERNAL_API_KEY", raising=False)
    _detect_mode.cache_clear()
    monkeypatch.setattr(gateway_llm_client, "_client", None)
    monkeypatch.setattr(gateway_llm_client, "_async_client", None)
    # A service token cached during the licensed period. Key removal must stop
    # gateway access even though this token has not expired.
    monkeypatch.setattr(
        activation_client,
        "_cached_token",
        activation_client.ServiceToken(
            access_token="fixture-service-token",
            gateway_url="https://gateway.example.invalid",
            expires_at=time.time() + 3600,
            allowed_services=[],
            allowed_models=[],
            scope="enterprise",
        ),
    )
    attempts, sockets = [], []

    def deny_socket(*args, **kwargs):
        sockets.append("unexpected socket/DNS attempt")
        raise AssertionError("Real networking is forbidden")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, deny_socket)
    monkeypatch.setattr(socket, "getaddrinfo", deny_socket)
    monkeypatch.setattr(socket, "create_connection", deny_socket)

    def record(client, request, **kwargs):
        attempts.append(request)
        body = json.loads(request.content)
        if body.get("stream"):
            return httpx.Response(200, content=b"data: [DONE]\n\n", request=request)
        return httpx.Response(200, json=RESPONSE, request=request)

    async def async_record(client, request, **kwargs):
        return record(client, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", record)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_record)

    # Direct-provider (litellm) fallback: a well-formed response, so reaching
    # it shows up as a missing denial rather than as an unrelated error.
    fallback_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="unexpected provider fallback"),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    provider = Mock()
    provider.completion.return_value = fallback_response
    provider.acompletion = AsyncMock(return_value=fallback_response)
    monkeypatch.setattr(llm_module, "litellm", provider)
    monkeypatch.setattr(llm_module, "log_to_clickhouse", Mock())
    monkeypatch.setattr(llm_module, "RETRY_DELAY", 0)
    monkeypatch.setattr(llm_module.LLM, "GATEWAY_RETRY_BACKOFF", (0, 0, 0))

    def make(expect_gateway=True):
        # The neutral provider avoids creating unrelated direct-provider
        # clients; the real constructor still acquires and retains both
        # gateway clients from the production factory.
        llm = llm_module.LLM(
            provider="fixture",
            model_name="gpt-4o",
            temperature=0.7,
            max_tokens=50,
            org_id="fixture-org",
        )
        llm.provider = "openai"
        if expect_gateway:
            assert llm._gateway_client is not None
            assert llm._async_gateway_client is not None
        llm._prepare_completion_payload = Mock(
            return_value={"model": "gpt-4o", "messages": MESSAGES}
        )
        llm._update_cost = Mock()
        llm._handle_final_fallback = Mock(return_value="unexpected fallback")
        llm._handle_final_fallback_async = AsyncMock(return_value="unexpected fallback")
        return llm

    llm = make()
    try:
        yield SimpleNamespace(llm=llm, make=make, attempts=attempts, provider=provider)
    finally:
        close(llm)
        _detect_mode.cache_clear()
        assert sockets == [], "Even a caught real network attempt fails the test"


def invoke(llm, entry):
    payload = {"model": "gpt-4o", "messages": MESSAGES}
    if entry.endswith("stream"):
        payload["stream"] = True
    if entry in {"sync", "sync_stream"}:
        return llm._try_gateway_completion(payload)
    if entry in {"async", "async_stream"}:
        return asyncio.run(llm._try_gateway_completion_async(payload))
    if entry == "content":
        return llm._get_completion_content(MESSAGES)
    if entry == "async_content":
        return asyncio.run(llm._get_completion_content_async(MESSAGES))
    if entry == "raw":
        return gateway_llm_client.call_llm_raw(
            llm._gateway_client,
            model="gpt-4o",
            messages=MESSAGES,
            temperature=0.7,
            max_tokens=50,
        )
    if entry == "helper_sync":
        return gateway_llm_client.call_llm(
            "gpt-4o", MESSAGES, temperature=0.7, max_tokens=50
        )
    if entry == "helper_async":
        return asyncio.run(
            gateway_llm_client.acall_llm(
                "gpt-4o", MESSAGES, temperature=0.7, max_tokens=50
            )
        )
    if entry == "llm_call":
        return llm.call_llm(MESSAGES, provider="openai")
    return llm._get_completion_with_tools(
        MESSAGES, tools=[], timeout_ms=5000 if entry == "bounded_tools" else None
    )


def assert_denial(error):
    assert error.code == "NO_ENTERPRISE_LICENSE"
    assert error.detail == "Managed AI requires an Enterprise license"
    assert (
        str(error) == "NO_ENTERPRISE_LICENSE: Managed AI requires an Enterprise license"
    )


def assert_no_fallback(retained, llm):
    llm._handle_final_fallback.assert_not_called()
    llm._handle_final_fallback_async.assert_not_called()
    retained.provider.completion.assert_not_called()
    retained.provider.acompletion.assert_not_called()


@pytest.mark.parametrize("entry", ENTRIES)
@KEYLESS
def test_retained_core_gateway_denies_key_removal(
    retained, settings, entry, removed_key
):
    remove_key(settings, removed_key)
    with pytest.raises(activation_client.ManagedServiceError) as error:
        invoke(retained.llm, entry)
    assert_denial(error.value)
    assert retained.attempts == []
    assert_no_fallback(retained, retained.llm)


@KEYLESS
def test_keyless_construction_retains_no_gateway_client(
    retained, settings, removed_key
):
    # Even with an unexpired service token still cached, an LLM built while
    # keyless must not hold any gateway client, so it never reaches the guard.
    remove_key(settings, removed_key)
    llm = retained.make(expect_gateway=False)
    try:
        assert llm._gateway_client is None
        assert llm._async_gateway_client is None
        assert invoke(llm, "sync") is None
        assert invoke(llm, "async") is None
        assert retained.attempts == []
    finally:
        close(llm)


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("mode", ["licensed", "cloud"])
def test_retained_gateway_authorized_payload_is_unchanged(
    retained, settings, entry, mode
):
    llm = retained.llm
    if mode == "cloud":
        settings.EE_LICENSE_KEY = ""
        settings.AGENTCC_INTERNAL_API_KEY = "fixture-internal-key"
        settings.AGENTCC_INTERNAL_URL = "https://internal.example.invalid"
        gateway_llm_client.invalidate_client()
        llm = retained.make()
    try:
        invoke(llm, entry)
        assert len(retained.attempts) == 1
        request = retained.attempts[0]
        assert request.method == "POST"
        host = "internal" if mode == "cloud" else "gateway"
        assert str(request.url) == f"https://{host}.example.invalid/v1/chat/completions"
        key = "fixture-internal-key" if mode == "cloud" else "fixture-service-token"
        assert request.headers["authorization"] == f"Bearer {key}"
        assert request.headers["x-org-id"] == "fixture-org"
        expected = {
            "model": "gpt-4o",
            "messages": MESSAGES,
            "temperature": 0.7,
            "max_tokens": 50,
        }
        if entry.endswith("stream"):
            expected["stream"] = True
        assert json.loads(request.content) == expected
        assert_no_fallback(retained, llm)
    finally:
        if llm is not retained.llm:
            close(llm)


@pytest.mark.parametrize("entry", ["sync", "async"])
def test_explicit_byok_still_bypasses_retained_gateway(retained, settings, entry):
    settings.EE_LICENSE_KEY = ""
    retained.llm.api_key = "fixture-provider-key"
    assert invoke(retained.llm, entry) is None
    assert retained.attempts == []


@pytest.mark.parametrize("entry", ["sync", "async"])
def test_ordinary_model_without_gateway_still_allows_provider_fallback(
    retained, settings, entry
):
    settings.EE_LICENSE_KEY = ""
    llm = retained.llm
    client_attr = "_async_gateway_client" if entry == "async" else "_gateway_client"
    client = getattr(llm, client_attr)
    try:
        setattr(llm, client_attr, None)
        assert invoke(llm, entry) is None
        assert retained.attempts == []
    finally:
        setattr(llm, client_attr, client)


@pytest.mark.parametrize(
    "entry", ["sync", "async", "content", "async_content", "tools", "bounded_tools"]
)
def test_core_retry_rechecks_license_before_next_gateway_attempt(
    retained, settings, monkeypatch, entry
):
    llm = retained.llm
    # Isolate the core retry loop rather than OpenAI's separate internal retries.
    llm._gateway_client.max_retries = 0
    llm._async_gateway_client.max_retries = 0

    def fail_and_remove_key(client, request, **kwargs):
        retained.attempts.append(request)
        settings.EE_LICENSE_KEY = ""
        return httpx.Response(
            503, json={"error": {"message": "fixture unavailable"}}, request=request
        )

    async def async_fail(client, request, **kwargs):
        return fail_and_remove_key(client, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", fail_and_remove_key)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_fail)
    with pytest.raises(activation_client.ManagedServiceError) as error:
        invoke(llm, entry)
    assert_denial(error.value)
    assert len(retained.attempts) == 1
    assert_no_fallback(retained, llm)


def install_unavailable_once(monkeypatch, retained, on_first_attempt, on_later):
    """Replace the HTTP boundary: a retryable 503 first, then ``on_later``.

    Every attempt is recorded in ``retained.attempts``. The 503 carries
    ``retry-after-ms`` so the SDK retries almost at once.
    """

    def send(client, request, **kwargs):
        retained.attempts.append(request)
        if len(retained.attempts) > 1:
            return on_later(request)
        on_first_attempt()
        return httpx.Response(
            503,
            headers={"retry-after-ms": "1"},
            json={"error": {"message": "fixture unavailable"}},
            request=request,
        )

    async def async_send(client, request, **kwargs):
        return send(client, request, **kwargs)

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_send)
    # The shared helpers import litellm lazily for their provider fallback;
    # route it to the same provider fake that assert_no_fallback checks.
    monkeypatch.setitem(sys.modules, "litellm", retained.provider)


@pytest.mark.parametrize("entry", [*ENTRIES, *HELPER_ENTRIES])
@KEYLESS
def test_sdk_retry_never_sends_after_key_removal(
    retained, settings, monkeypatch, entry, removed_key
):
    llm = retained.llm
    # The production default. These retries run inside one SDK call, below
    # the core loop's per-attempt check.
    assert llm._gateway_client.max_retries == 2
    assert llm._async_gateway_client.max_retries == 2

    def forbidden_after_removal(request):
        # pytest.fail raises a BaseException subclass, so no `except
        # Exception` in the SDK or the application can swallow it: any HTTP
        # attempt after the key is removed fails the test right here.
        pytest.fail(
            "Managed-gateway HTTP attempt after license-key removal: "
            f"{request.method} {request.url}"
        )

    install_unavailable_once(
        monkeypatch,
        retained,
        on_first_attempt=lambda: remove_key(settings, removed_key),
        on_later=forbidden_after_removal,
    )
    logger = Mock()
    monkeypatch.setattr(llm_module, "logger", logger)
    with pytest.raises(activation_client.ManagedServiceError) as error:
        invoke(llm, entry)
    assert_denial(error.value)
    assert len(retained.attempts) == 1
    assert_no_fallback(retained, llm)
    # The denial ends the core retry loop at once: it is not logged as a
    # failed gateway attempt and retried (or, under a deadline, turned into
    # a timeout). The 503 itself may be, where SDK retries are off.
    denial_logged_as_failed_attempt = [
        call
        for call in logger.warning.call_args_list
        if call.args
        and call.args[0] in {"gateway_attempt_failed", "async_gateway_attempt_failed"}
        and "NO_ENTERPRISE_LICENSE" in str(call.kwargs.get("error"))
    ]
    assert denial_logged_as_failed_attempt == []


@pytest.mark.parametrize("entry", ["sync", "async", *HELPER_ENTRIES])
@pytest.mark.parametrize("mode", ["licensed", "cloud"])
def test_sdk_retry_unchanged_for_authorized_clients(
    retained, settings, monkeypatch, entry, mode
):
    llm = retained.llm
    if mode == "cloud":
        settings.EE_LICENSE_KEY = ""
        settings.AGENTCC_INTERNAL_API_KEY = "fixture-internal-key"
        settings.AGENTCC_INTERNAL_URL = "https://internal.example.invalid"
        gateway_llm_client.invalidate_client()
        llm = retained.make()

    def succeed(request):
        return httpx.Response(200, json=RESPONSE, request=request)

    install_unavailable_once(
        monkeypatch, retained, on_first_attempt=lambda: None, on_later=succeed
    )
    try:
        invoke(llm, entry)
        # One SDK retry after the 503, as before.
        assert len(retained.attempts) == 2
        first, retry = retained.attempts
        assert first.headers["x-stainless-retry-count"] == "0"
        assert retry.headers["x-stainless-retry-count"] == "1"

        def other_headers(request):
            return {
                name: value
                for name, value in request.headers.items()
                if name != "x-stainless-retry-count"
            }

        assert other_headers(first) == other_headers(retry)
        assert first.content == retry.content
        host = "internal" if mode == "cloud" else "gateway"
        key = "fixture-internal-key" if mode == "cloud" else "fixture-service-token"
        for request in retained.attempts:
            assert request.method == "POST"
            assert (
                str(request.url)
                == f"https://{host}.example.invalid/v1/chat/completions"
            )
            assert request.headers["authorization"] == f"Bearer {key}"
            if entry in {"sync", "async"}:
                assert request.headers["x-org-id"] == "fixture-org"
            else:
                assert "x-org-id" not in request.headers
            assert json.loads(request.content) == EXPECTED_BODY
        assert_no_fallback(retained, llm)
    finally:
        if llm is not retained.llm:
            close(llm)


@pytest.mark.parametrize("async_client", [False, True], ids=["sync", "async"])
def test_license_bound_client_sends_what_a_plain_client_sends(retained, async_client):
    # The self-hosted factory client now takes its token from a license-bound
    # callable. While the key is set, the wire request must be the one a
    # client built with the plain token string sends.
    import openai

    llm = retained.llm
    factory = llm._async_gateway_client if async_client else llm._gateway_client
    plain_class = openai.AsyncOpenAI if async_client else openai.OpenAI
    plain = plain_class(
        api_key="fixture-service-token",
        base_url="https://gateway.example.invalid/v1",
        timeout=300.0,
        max_retries=2,
    )
    request_kwargs = {
        "model": "gpt-4o",
        "messages": MESSAGES,
        "temperature": 0.7,
        "max_tokens": 50,
        "extra_headers": {"X-Org-Id": "fixture-org"},
    }
    try:
        for client in (factory, plain):
            if async_client:
                asyncio.run(client.chat.completions.create(**request_kwargs))
            else:
                client.chat.completions.create(**request_kwargs)
    finally:
        if async_client:
            asyncio.run(plain.close())
        else:
            plain.close()
    licensed, baseline = retained.attempts
    assert str(licensed.url) == str(baseline.url)
    assert licensed.method == baseline.method
    assert dict(licensed.headers.items()) == dict(baseline.headers.items())
    assert licensed.content == baseline.content
    assert licensed.headers["authorization"] == "Bearer fixture-service-token"
