"""litellm routes that drop the client FA hands them, on a tenant base URL.

``guard_litellm_kwargs`` gives litellm a guarded OpenAI/AzureOpenAI client
for an openai/azure custom model, but some routes build their own client
and never look at it:

- the Responses-API bridge: ``completion`` of a model whose litellm mode is
  ``responses`` (gpt-5-pro, gpt-5-codex, o3-pro, ...), a ``responses/``
  model name, gpt-5.4+ with tools and reasoning_effort, or
  ``litellm.route_all_chat_openai_to_responses``. The sync bridge throws the
  OpenAI client away; the async bridge never passes a client on;
- Azure (and Azure AI) image generation, which calls with ``client=None``;
- Azure AI agents (``azure_ai/agents/<id>``).

litellm's own clients follow redirects and resolve names themselves, so on
hosted a public endpoint redirecting to the metadata server reached it on
the Responses route, and Azure image generation and agents were refused
outright ("unsupported"); on a self-hosted install they ran on litellm's
own client after the pre-flight, so a redirect to a metadata host name
reached it. Now every client litellm builds during a tenant call is a
guarded one, on every deployment, and the tenant's key (or a placeholder)
is always given, so no route falls back to the platform's key.

Real litellm and the real OpenAI SDK, loopback servers only:
``public-a.test`` -> 127.0.0.1 treated as public on hosted; ``[::1]`` stands
in for the metadata server; on a self-hosted install the metadata stand-in
is reached by the name ``metadata.google.internal``, which the policy
refuses there too.
"""

import asyncio
import json

import pytest
from django.test import override_settings

from agentic_eval.core.llm import llm as llm_module
from model_hub.utils import model_endpoints
from model_hub.utils.utils import validate_model_working
from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import FakeResolver
from tfc.utils.tests.loopback_endpoints import (
    IMAGE_BODY,
    MODEL_ANSWER,
    PUBLIC_NAME,
    SECRET_MARKER,
    LoopbackServer,
    clear_proxy_env,
    metadata_stand_in,
    model_server,
    redirector,
    treat_as_public,
)

DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"
MESSAGES = ssrf_guard.ENDPOINT_ERROR_MESSAGES
PLATFORM_OPENAI_KEY = "sk-platform-openai-test"
PLATFORM_AZURE_KEY = "platform-azure-key-test"
PLATFORM_AZURE_AD_TOKEN = "platform-azure-ad-token-test"
METADATA_NAME = "metadata.google.internal"
CHAT = [{"role": "user", "content": "hi"}]


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv(DENY_ENV, raising=False)
    monkeypatch.setattr(llm_module, "LITELLM_NUM_RETRIES", 0)
    # Platform keys a tenant endpoint must never receive, on any route.
    monkeypatch.setenv("OPENAI_API_KEY", PLATFORM_OPENAI_KEY)
    monkeypatch.setenv("AZURE_API_KEY", PLATFORM_AZURE_KEY)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", PLATFORM_AZURE_KEY)
    monkeypatch.setenv("AZURE_AD_TOKEN", PLATFORM_AZURE_AD_TOKEN)


@pytest.fixture
def resolver(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    resolver.add(PUBLIC_NAME, "127.0.0.1")
    resolver.add(METADATA_NAME, "::1")
    return resolver


@pytest.fixture
def hosted(monkeypatch):
    treat_as_public(monkeypatch)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def self_hosted():
    with override_settings(CLOUD_DEPLOYMENT=""):
        yield


@pytest.fixture
def metadata():
    with metadata_stand_in() as server:
        yield server


@pytest.fixture(params=[302, 307])
def internal_redirect(request, metadata):
    """Hosted: a public endpoint that redirects to the [::1] stand-in."""
    target = f"http://[::1]:{metadata.port}/latest/meta-data/iam/security-credentials/r"
    with redirector(target, request.param) as server:
        yield server


@pytest.fixture(params=[302, 307])
def metadata_name_redirect(request, metadata, resolver):
    """Self-hosted: a permitted endpoint that redirects to the metadata host
    name (mapped to the [::1] stand-in), which the self-hosted policy refuses
    on its own."""
    target = f"http://{METADATA_NAME}:{metadata.port}/computeMetadata/v1/token"
    with pytest.raises(ssrf_guard.EndpointBlocked):
        ssrf_guard.check_endpoint_url(target)
    with redirector(target, request.param) as server:
        yield server


def _base(server, path="/v1"):
    return f"http://{PUBLIC_NAME}:{server.port}{path}"


def _no_secret(*texts):
    return all(SECRET_MARKER not in str(text) for text in texts)


def _call(function, **kwargs):
    """Run a guarded entry point (awaiting the async one, draining a
    stream); return the exception it raised, or None."""
    try:
        if asyncio.iscoroutinefunction(function):

            async def run():
                response = await function(**kwargs)
                if kwargs.get("stream"):
                    async for _chunk in response:
                        pass
                return response

            asyncio.run(run())
        else:
            response = function(**kwargs)
            if kwargs.get("stream"):
                for _chunk in response:
                    pass
    except Exception as exc:  # the call failed, as it must
        return exc
    return None


ENTRY_POINTS = {
    "sync": model_endpoints.guarded_completion,
    "async": model_endpoints.guarded_acompletion,
}


# ------------------------------------------------------- Responses bridge ---


def _responses_body(text=MODEL_ANSWER, model="gpt-5-pro"):
    return json.dumps(
        {
            "id": "resp_1",
            "object": "response",
            "created_at": 1700000000,
            "status": "completed",
            "model": model,
            "output": [
                {
                    "type": "message",
                    "id": "msg_1",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": text, "annotations": []}
                    ],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
    ).encode()


def responses_server():
    """A public OpenAI-compatible endpoint that answers the Responses API."""
    return LoopbackServer(
        lambda request: (200, {"Content-Type": "application/json"}, _responses_body())
    )


def _responses_kwargs(provider, model, server):
    if provider == "openai":
        return {"model": model, "api_key": "sk-tenant", "api_base": _base(server)}
    return {
        "model": model,
        "api_key": "azure-tenant",
        "api_base": _base(server, ""),
        "api_version": "2024-02-01",
    }


RESPONSES_ROUTE_MODELS = [
    ("openai", "openai/gpt-5-pro"),
    ("openai", "openai/responses/x"),
    ("azure", "azure/gpt-5-pro"),
    ("azure", "azure/responses/x"),
]


def test_these_models_take_litellm_responses_bridge():
    """The premise: litellm sends these models to its Responses route."""
    from litellm.main import responses_api_bridge_check

    for provider, model in RESPONSES_ROUTE_MODELS:
        info, _name = responses_api_bridge_check(model.split("/", 1)[1], provider)
        assert info.get("mode") == "responses", model


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("provider, model", RESPONSES_ROUTE_MODELS)
def test_responses_route_redirect_to_metadata_is_refused(
    hosted, resolver, metadata, internal_redirect, provider, model, mode
):
    error = _call(
        ENTRY_POINTS[mode],
        messages=CHAT,
        **_responses_kwargs(provider, model, internal_redirect),
    )
    assert error is not None
    assert metadata.requests == []
    assert len(internal_redirect.requests) == 1
    assert str(error) == MESSAGES["redirect"]
    # Generic, and nothing of litellm's error rides along in a chain.
    assert error.__cause__ is None and error.__context__ is None


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_responses_route_streaming_redirect_is_refused(
    hosted, resolver, metadata, internal_redirect, mode
):
    error = _call(
        ENTRY_POINTS[mode],
        messages=CHAT,
        stream=True,
        **_responses_kwargs("openai", "openai/gpt-5-pro", internal_redirect),
    )
    assert error is not None
    assert metadata.requests == []


def test_responses_route_for_every_openai_chat_model_is_guarded(
    hosted, resolver, metadata, internal_redirect, monkeypatch
):
    import litellm

    monkeypatch.setattr(litellm, "route_all_chat_openai_to_responses", True)
    error = _call(
        model_endpoints.guarded_completion,
        messages=CHAT,
        **_responses_kwargs("openai", "openai/gpt-x", internal_redirect),
    )
    assert error is not None
    assert metadata.requests == []


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_responses_route_public_endpoint_works(hosted, resolver, mode):
    with responses_server() as server:
        kwargs = _responses_kwargs("openai", "openai/gpt-5-pro", server)
        if mode == "sync":
            response = model_endpoints.guarded_completion(messages=CHAT, **kwargs)
        else:
            response = asyncio.run(
                model_endpoints.guarded_acompletion(messages=CHAT, **kwargs)
            )
    assert response.choices[0].message.content == MODEL_ANSWER
    [seen] = server.requests
    assert seen.target == "/v1/responses"
    assert seen.headers["authorization"] == "Bearer sk-tenant"


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_responses_route_never_sends_the_platform_key(hosted, resolver, mode):
    with responses_server() as server:
        kwargs = _responses_kwargs("openai", "openai/gpt-5-pro", server)
        kwargs.pop("api_key")
        _call(ENTRY_POINTS[mode], messages=CHAT, **kwargs)
    assert server.requests
    assert PLATFORM_OPENAI_KEY not in str(server.requests[0].headers)


@pytest.mark.parametrize(
    "provider, model, path",
    [("openai", "gpt-5-pro", "/v1"), ("azure", "gpt-5-pro", "")],
)
def test_validate_responses_model_redirect_is_refused(
    hosted, resolver, metadata, internal_redirect, provider, model, path
):
    api_key = {"api_key": "sk-tenant", "api_base": _base(internal_redirect, path)}
    if provider == "azure":
        api_key.update({"azure_endpoint_type": "legacy", "api_version": "2024-02-01"})
    result = validate_model_working(
        model_name=model, api_key=api_key, provider=provider
    )
    assert isinstance(result, Exception)
    assert metadata.requests == []
    assert _no_secret(result)


def test_call_llm_responses_model_redirect_is_refused(
    hosted, resolver, metadata, internal_redirect
):
    llm = llm_module.LLM(
        provider="openai",
        model_name="openai/gpt-5-pro",
        api_key={"key": "sk-tenant", "api_base": _base(internal_redirect)},
    )
    with pytest.raises(ValueError) as excinfo:
        llm.call_llm(CHAT, provider="openai")
    assert metadata.requests == []
    assert _no_secret(excinfo.value)


# --------------------------------------------- Azure / Azure AI images ---


def _azure_image_kwargs(server, **extra):
    return {
        "model": "azure/dall-e-3",
        "prompt": "a cat",
        "api_key": "azure-tenant",
        "api_base": _base(server, ""),
        "api_version": "2024-02-01",
        **extra,
    }


def test_azure_image_public_endpoint_works_on_hosted(hosted, resolver):
    with model_server() as server:
        response = model_endpoints.guarded_image_generation(
            **_azure_image_kwargs(server)
        )
    assert response.data[0].b64_json
    [seen] = server.requests
    assert seen.target.startswith("/openai/deployments/dall-e-3/images/generations")
    assert seen.headers["api-key"] == "azure-tenant"


def test_azure_image_redirect_to_metadata_is_refused_on_hosted(
    hosted, resolver, metadata, internal_redirect
):
    error = _call(
        model_endpoints.guarded_image_generation,
        **_azure_image_kwargs(internal_redirect),
    )
    assert error is not None
    assert metadata.requests == []
    assert len(internal_redirect.requests) == 1
    assert _no_secret(error)


def test_azure_image_internal_api_base_is_refused_on_hosted(hosted, resolver):
    with model_server() as server:
        kwargs = _azure_image_kwargs(server)
        kwargs["api_base"] = f"http://[::1]:{server.port}"
        with pytest.raises(ssrf_guard.EndpointBlocked) as excinfo:
            model_endpoints.guarded_image_generation(**kwargs)
    assert excinfo.value.category == "blocked"


def test_azure_image_never_sends_the_platform_key(hosted, resolver):
    with model_server() as server:
        kwargs = _azure_image_kwargs(server)
        kwargs.pop("api_key")
        _call(model_endpoints.guarded_image_generation, **kwargs)
    assert server.requests
    assert PLATFORM_AZURE_KEY not in str(server.requests[0].headers)


def test_azure_ai_image_redirect_to_metadata_is_refused_on_hosted(
    hosted, resolver, metadata, internal_redirect
):
    error = _call(
        model_endpoints.guarded_image_generation,
        model="azure_ai/flux-1.1-pro",
        prompt="a cat",
        api_key="foundry-tenant",
        api_base=_base(internal_redirect, ""),
    )
    assert error is not None
    assert metadata.requests == []
    assert len(internal_redirect.requests) == 1


def test_azure_ai_image_public_endpoint_works_on_hosted(hosted, resolver):
    with model_server() as server:
        response = model_endpoints.guarded_image_generation(
            model="azure_ai/flux-1.1-pro",
            prompt="a cat",
            api_key="foundry-tenant",
            api_base=_base(server, ""),
        )
    assert response.data[0].b64_json
    assert "images/generations" in server.requests[0].target


# ------------------------------------------------------- Azure AI agents ---


def agents_server():
    """A public Azure AI Foundry Agents API: thread, message, run, poll,
    messages."""

    def answer(request):
        path = request.target.split("?", 1)[0]
        headers = {"Content-Type": "application/json"}
        if request.method == "POST" and path.endswith("/threads"):
            return 200, headers, b'{"id": "thread_1"}'
        if request.method == "POST" and path.endswith("/threads/thread_1/runs"):
            return 200, headers, b'{"id": "run_1"}'
        if request.method == "POST":
            return 200, headers, b'{"id": "msg_1"}'
        if path.endswith("/runs/run_1"):
            return 200, headers, b'{"status": "completed"}'
        body = {
            "data": [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": {"value": MODEL_ANSWER}}],
                }
            ]
        }
        return 200, headers, json.dumps(body).encode()

    return LoopbackServer(answer)


def _agents_kwargs(server):
    return {
        "model": "azure_ai/agents/asst_1",
        "messages": CHAT,
        "api_key": "foundry-tenant",
        "api_base": _base(server, "/api/projects/p1"),
    }


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_azure_ai_agents_public_endpoint_works_on_hosted(hosted, resolver, mode):
    with agents_server() as server:
        if mode == "sync":
            response = model_endpoints.guarded_completion(**_agents_kwargs(server))
        else:
            response = asyncio.run(
                model_endpoints.guarded_acompletion(**_agents_kwargs(server))
            )
    assert response.choices[0].message.content == MODEL_ANSWER
    assert server.requests[0].target.startswith("/api/projects/p1/threads")


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_azure_ai_agents_redirect_to_metadata_is_refused_on_hosted(
    hosted, resolver, metadata, internal_redirect, mode
):
    error = _call(ENTRY_POINTS[mode], **_agents_kwargs(internal_redirect))
    assert error is not None
    assert metadata.requests == []
    assert len(internal_redirect.requests) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_azure_ai_agents_stream_redirect_is_refused_on_hosted(
    hosted, resolver, metadata, internal_redirect, mode
):
    """The async agents stream is a generator that only connects when it is
    first iterated, after the call has returned."""
    error = _call(ENTRY_POINTS[mode], stream=True, **_agents_kwargs(internal_redirect))
    assert str(error) == MESSAGES["redirect"]
    assert metadata.requests == []
    assert len(internal_redirect.requests) == 1


# ----------------------------------------- self-hosted: no unguarded client ---


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "provider, model", [("openai", "openai/gpt-5-pro"), ("azure", "azure/gpt-5-pro")]
)
def test_self_hosted_responses_route_redirect_to_metadata_name_is_refused(
    self_hosted, metadata, metadata_name_redirect, provider, model, mode
):
    error = _call(
        ENTRY_POINTS[mode],
        messages=CHAT,
        **_responses_kwargs(provider, model, metadata_name_redirect),
    )
    assert error is not None
    assert metadata.requests == []
    assert len(metadata_name_redirect.requests) == 1


def test_self_hosted_azure_image_redirect_to_metadata_name_is_refused(
    self_hosted, metadata, metadata_name_redirect
):
    error = _call(
        model_endpoints.guarded_image_generation,
        **_azure_image_kwargs(metadata_name_redirect),
    )
    assert error is not None
    assert metadata.requests == []
    assert len(metadata_name_redirect.requests) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_self_hosted_azure_ai_agents_redirect_to_metadata_name_is_refused(
    self_hosted, metadata, metadata_name_redirect, mode
):
    error = _call(ENTRY_POINTS[mode], **_agents_kwargs(metadata_name_redirect))
    assert error is not None
    assert metadata.requests == []
    assert len(metadata_name_redirect.requests) == 1


def test_self_hosted_local_azure_image_still_works(self_hosted, resolver):
    with model_server() as server:
        kwargs = _azure_image_kwargs(server)
        kwargs["api_base"] = f"http://localhost:{server.port}"
        resolver.add("localhost", "127.0.0.1")
        response = model_endpoints.guarded_image_generation(**kwargs)
    assert response.data[0].b64_json


@pytest.mark.parametrize("deployment", ["hosted", "self_hosted"])
@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": "anthropic/claude-3-5-sonnet-20240620"},
        {"model": "some-model", "custom_llm_provider": "hosted_vllm"},
    ],
)
def test_other_providers_with_a_base_url_are_refused_everywhere(
    request, resolver, deployment, kwargs
):
    request.getfixturevalue(deployment)
    with model_server() as server:
        with pytest.raises(ssrf_guard.EndpointBlocked) as excinfo:
            model_endpoints.guarded_completion(
                messages=CHAT, api_key="k", api_base=_base(server), **kwargs
            )
        assert server.requests == []
    assert excinfo.value.category == "unsupported"


# --------------------------------- the clients litellm builds in the call ---


def test_litellm_clients_built_inside_a_tenant_call_are_guarded():
    import litellm
    from litellm.caching.llm_caching_handler import LLMClientCache
    from litellm.llms.custom_httpx import http_handler
    from litellm.llms.openai.common_utils import BaseOpenAILLM

    assert isinstance(litellm.in_memory_llm_clients_cache, LLMClientCache)
    module_client = litellm.module_level_client
    module_aclient = litellm.module_level_aclient
    outside = http_handler._get_httpx_client()
    policy = ssrf_guard.EndpointPolicy.PUBLIC_ONLY
    with model_endpoints.tenant_endpoint_scope(policy):
        sync_handler = http_handler._get_httpx_client()
        async_handler = http_handler.get_async_httpx_client(
            llm_provider=litellm.LlmProviders.AZURE
        )
        direct = http_handler.HTTPHandler(client=litellm.client_session)
        sdk_sync = BaseOpenAILLM._get_sync_http_client()
        sdk_async = BaseOpenAILLM._get_async_http_client()
        assert http_handler._get_httpx_client() is not sync_handler  # not cached
    for client in (sync_handler.client, direct.client, sdk_sync):
        assert isinstance(client._transport, ssrf_guard.GuardedHTTPTransport)
    for client in (async_handler.client, sdk_async):
        assert isinstance(client._transport, ssrf_guard.AsyncGuardedHTTPTransport)
    # Outside the call nothing changed: cached and module-level clients are
    # litellm's own, and nothing built inside was cached.
    assert http_handler._get_httpx_client() is outside
    assert not isinstance(outside.client._transport, ssrf_guard.GuardedHTTPTransport)
    assert litellm.module_level_client is module_client
    assert litellm.module_level_aclient is module_aclient
    # ...and they were never built inside a scope (an earlier test's call
    # would have made them guarded ones for good).
    assert not isinstance(
        litellm.module_level_client.client._transport, ssrf_guard.GuardedHTTPTransport
    )
    assert not isinstance(
        litellm.module_level_aclient.client._transport,
        ssrf_guard.AsyncGuardedHTTPTransport,
    )
    assert not isinstance(
        http_handler.HTTPHandler().client._transport, ssrf_guard.GuardedHTTPTransport
    )


def test_litellm_clients_in_a_tenant_call_use_the_deployment_policy(
    self_hosted, resolver
):
    """A self-hosted call's clients are guarded too (metadata refused,
    loopback allowed): PRIVATE_ALLOWED does not mean unguarded."""
    from litellm.llms.custom_httpx import http_handler

    resolver.add("localhost", "127.0.0.1")
    policy = ssrf_guard.model_endpoint_policy()
    assert policy is ssrf_guard.EndpointPolicy.PRIVATE_ALLOWED
    with model_endpoints.tenant_endpoint_scope(policy):
        handler = http_handler._get_httpx_client()
    with model_server(image_body=IMAGE_BODY) as server:
        ok = handler.client.post(f"http://localhost:{server.port}/x")
    assert ok.status_code == 200
    with pytest.raises(Exception) as excinfo:
        handler.client.get(f"http://{METADATA_NAME}/computeMetadata/v1/")
    assert (
        model_endpoints.endpoint_call_error_message(excinfo.value)
        == (MESSAGES["blocked"])
    )


# ------------------------------------------------- what a failed call raises ---


def test_a_failed_tenant_call_raises_generic_categorised_errors():
    import httpx
    import openai

    request = httpx.Request("POST", f"http://user:{SECRET_MARKER}@{PUBLIC_NAME}/v1")
    timeout = model_endpoints.endpoint_call_error(
        openai.APITimeoutError(request=request)
    )
    status = model_endpoints.endpoint_call_error(
        openai.AuthenticationError(
            f"upstream says {SECRET_MARKER}",
            response=httpx.Response(401, request=request),
            body={"error": SECRET_MARKER},
        )
    )
    blocked = model_endpoints.endpoint_call_error(ssrf_guard.EndpointBlocked())
    # A timeout stays a TimeoutError, so callers that retry timeouts still do.
    assert isinstance(timeout, TimeoutError)
    assert (timeout.category, str(timeout)) == ("timeout", MESSAGES["timeout"])
    assert (status.category, status.status_code) == ("status", 401)
    assert str(status) == ssrf_guard.endpoint_status_message(401)
    assert status.cause_type == "AuthenticationError"
    assert (blocked.category, str(blocked)) == ("blocked", MESSAGES["blocked"])
    assert _no_secret(timeout, status, blocked, vars(status))


def _platform_credentials_absent(server):
    seen = str([request.headers for request in server.requests])
    return all(
        secret not in seen
        for secret in (PLATFORM_OPENAI_KEY, PLATFORM_AZURE_KEY, PLATFORM_AZURE_AD_TOKEN)
    )


@pytest.mark.parametrize(
    "call, kwargs",
    [
        (
            "guarded_completion",
            {"model": "azure/gpt-x", "messages": CHAT, "api_version": "2024-02-01"},
        ),
        (
            "guarded_completion",
            {"model": "azure/gpt-5-pro", "messages": CHAT, "api_version": "2024-02-01"},
        ),
        (
            "guarded_image_generation",
            {"model": "azure/dall-e-3", "prompt": "a cat", "api_version": "2024-02-01"},
        ),
    ],
)
def test_azure_routes_never_send_platform_credentials(hosted, resolver, call, kwargs):
    with model_server() as server:
        _call(getattr(model_endpoints, call), api_base=_base(server, ""), **kwargs)
    assert server.requests
    assert _platform_credentials_absent(server)
