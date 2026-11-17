"""Launch t56 final-dispatch gaps, with synthetic DNS and provider recorders.

These exercise production kwargs-taking entrypoints, not tenant HTTP routes.
Tenant write provenance for these precautionary services remains unproven.
The real launch URL/address/credential guard runs; only client construction,
transport hooks and the outbound provider calls are replaced. No egress.
"""

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock

import litellm
import pytest

from agentic_eval.core_evals.llm_services.litellm_api import LitellmService
from ee.agent_opt.optimizers import random_search
from ee.usage.services import gateway_llm_client
from model_hub.utils import model_endpoints
from tfc.utils import ssrf_guard

OWN = "SYNTHETIC-TENANT-CREDENTIAL"
PUBLIC = "https://provider.example.test/v1"
CONTENT = '{"variations": ["synthetic variation"]}'
CHAT = [{"role": "user", "content": "synthetic input"}]
ROUTES = ("legacy", "random_search", "gateway_sync", "gateway_async")


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    client = object()
    monkeypatch.setattr(LitellmService, "_instance", None)
    monkeypatch.setattr(random_search, "_get_max_output_tokens", lambda model: 128)
    monkeypatch.setattr(gateway_llm_client, "_deployment_mode", lambda: "oss")
    monkeypatch.setattr(gateway_llm_client, "get_gateway_client", lambda: None)
    monkeypatch.setattr(gateway_llm_client, "get_async_gateway_client", lambda: None)
    monkeypatch.setattr(
        model_endpoints, "model_endpoint_policy",
        lambda: ssrf_guard.EndpointPolicy.PUBLIC_ONLY,
    )
    # Keep _guard/check_endpoint_url/address classification real. Public
    # controls need no SDK object and scope entry must not install global hooks.
    monkeypatch.setattr(
        model_endpoints, "_guarded_litellm_client",
        lambda *args, **kwargs: client,
    )
    monkeypatch.setattr(model_endpoints, "_install_litellm_client_hooks", lambda: None)

    def resolve(host, port, *args, **kwargs):
        assert host == "provider.example.test", "unexpected DNS request"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def no_connect(*args, **kwargs):
        raise AssertionError("network contact forbidden in endpoint-guard regression")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "create_connection", no_connect)
    monkeypatch.setattr(socket.socket, "connect", no_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", no_connect)

    def record(**kwargs):
        calls.append(dict(kwargs))
        return litellm.ModelResponse(
            model="gpt-4o",
            choices=[{"message": {"role": "assistant", "content": CONTENT}}],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    async def record_async(**kwargs):
        return record(**kwargs)

    monkeypatch.setattr(litellm, "completion", record)
    monkeypatch.setattr(litellm, "acompletion", record_async)
    return calls, client


def dispatch(route, key, options):
    if route == "legacy":
        return LitellmService(key).chat_completion(CHAT, "openai/gpt-4o", **options)
    if route == "random_search":
        return random_search._generate_variations(
            "synthetic prompt", 1, "openai/gpt-4o", dict(options), key,
        )
    arguments = {"model": "openai/gpt-4o", "messages": CHAT, "api_key": key, **options}
    if route == "gateway_sync":
        return gateway_llm_client.call_llm(**arguments)
    return asyncio.run(gateway_llm_client.acall_llm(**arguments))


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("endpoint", ["api_base", "base_url"])
@pytest.mark.parametrize("target", [
    "http://127.0.0.1/v1", "http://10.0.0.8/v1", "http://169.254.169.254/v1",
])
def test_launch_private_endpoint_refused_before_provider(route, endpoint, target, recorded):
    calls, _ = recorded
    error = None
    try:
        dispatch(route, OWN, {endpoint: target})
    except Exception as exc:
        error = exc
    # On BASE this fails because the real entrypoint invokes the recorder.
    # A collection/fixture error or a different exception is not valid RED.
    assert calls == [], "provider invoked before tenant endpoint refusal"
    assert isinstance(error, ssrf_guard.EndpointBlocked)
    assert str(error) == str(ssrf_guard.EndpointBlocked("blocked"))


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("endpoint", ["api_base", "base_url"])
def test_launch_public_endpoint_preserves_selected_own_key(route, endpoint, recorded):
    calls, client = recorded
    result = dispatch(route, OWN, {endpoint: PUBLIC})
    assert result == (["synthetic variation"] if route == "random_search" else CONTENT)
    assert len(calls) == 1
    assert calls[0]["api_key"] == OWN
    assert calls[0]["api_base"] == PUBLIC
    assert "base_url" not in calls[0]
    assert calls[0]["client"] is client


@pytest.mark.parametrize("route", ROUTES)
def test_launch_no_user_url_platform_call_unchanged(route, recorded):
    calls, _ = recorded
    result = dispatch(route, None, {})
    assert result == (["synthetic variation"] if route == "random_search" else CONTENT)
    assert len(calls) == 1
    assert calls[0].get("api_key") is None
    assert "api_base" not in calls[0]
    assert "base_url" not in calls[0]
    assert "client" not in calls[0]


@pytest.mark.parametrize("endpoint", ["api_base", "base_url"])
def test_launch_teacher_final_key_override_is_preserved(endpoint, recorded):
    calls, _ = recorded
    # The final teacher kwargs override the positional api_key in production.
    selected = "SYNTHETIC-SELECTED-TEACHER-CREDENTIAL"
    assert dispatch("random_search", OWN, {endpoint: PUBLIC, "api_key": selected}) == [
        "synthetic variation"
    ]
    assert calls[0]["api_key"] == selected


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("endpoint", ["api_base", "base_url"])
def test_launch_unused_alias_cannot_select_environment_key(route, endpoint, recorded):
    calls, _ = recorded
    # Existing launch behavior intentionally substitutes a non-secret placeholder
    # for a missing final api_key. An unused alias must not become the credential.
    dispatch(route, None, {endpoint: PUBLIC, "key": OWN})
    assert len(calls) == 1
    assert calls[0]["api_key"] == model_endpoints._NO_TENANT_KEY


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.8", "169.254.169.254"])
@pytest.mark.parametrize("output_format", ["string", "audio"])
def test_launch_retained_runprompt_rechecks_after_lookup(
    asynchronous, streaming, address, output_format, recorded, monkeypatch,
):
    """Public RunPrompt -> real retained mapping -> real final guard.

    Lookup is a seam: it checks a public address, then DNS changes before
    dispatch. This is not a claim that a literal private URL passes lookup.
    """
    from agentic_eval.core_evals.run_prompt import litellm_response as run_module

    calls, _ = recorded
    rebound = False

    def resolve(host, port, *args, **kwargs):
        assert host == "provider.example.test"
        ip = address if rebound else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

    def lookup(**kwargs):
        nonlocal rebound
        ssrf_guard.check_endpoint_url(PUBLIC, policy=ssrf_guard.EndpointPolicy.PUBLIC_ONLY)
        rebound = True
        return {"key": OWN, "api_base": PUBLIC}

    manager = MagicMock()
    manager.get_provider.return_value = "openai"
    manager.get_api_key.side_effect = lookup
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(run_module, "USE_NEW_RUNPROMPT_HANDLERS", False)
    monkeypatch.setattr(run_module, "LiteLLMModelManager", lambda *args, **kwargs: manager)
    monkeypatch.setattr(run_module, "get_model_mode", lambda model: "chat")
    ws = AsyncMock() if asynchronous else MagicMock()
    monkeypatch.setattr(run_module, "get_websocket_manager", lambda *args: ws)
    prompt = run_module.RunPrompt(
        model="gpt-4o-audio-preview" if output_format == "audio" else "gpt-4o",
        messages=CHAT, organization_id="synthetic-org",
        workspace_id="synthetic-workspace", output_format=output_format,
        temperature=None, frequency_penalty=None, presence_penalty=None,
        max_tokens=None, top_p=None, response_format=None, tool_choice=None,
        tools=[], ws_manager=ws,
    )
    error = None
    try:
        if asynchronous:
            asyncio.run(prompt.litellm_response_async(streaming=streaming))
        else:
            prompt.litellm_response(streaming=streaming)
    except Exception as exc:
        error = exc
    assert rebound, "fixture did not reach credential lookup"
    assert calls == [], "retained RunPrompt dispatched after DNS became private"
    assert isinstance(error, ssrf_guard.EndpointBlocked)
    assert str(error) == str(ssrf_guard.EndpointBlocked("blocked"))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_launch_retained_public_endpoint_works(asynchronous, recorded, monkeypatch):
    from agentic_eval.core_evals.run_prompt import litellm_response as run_module

    calls, client = recorded
    manager = MagicMock()
    manager.get_provider.return_value = "openai"
    manager.get_api_key.side_effect = lambda **kwargs: {"key": OWN, "api_base": PUBLIC}
    monkeypatch.setattr(run_module, "USE_NEW_RUNPROMPT_HANDLERS", False)
    monkeypatch.setattr(run_module, "LiteLLMModelManager", lambda *args, **kwargs: manager)
    monkeypatch.setattr(run_module, "get_model_mode", lambda model: "chat")
    monkeypatch.setattr(run_module, "calculate_total_cost", lambda *args: 0.0)
    prompt = run_module.RunPrompt(
        model="gpt-4o", messages=CHAT, organization_id="synthetic-org",
        workspace_id="synthetic-workspace", output_format="string",
        temperature=None, frequency_penalty=None, presence_penalty=None,
        max_tokens=None, top_p=None, response_format=None, tool_choice=None,
        tools=[], ws_manager=AsyncMock() if asynchronous else MagicMock(),
    )
    if asynchronous:
        result, info = asyncio.run(prompt.litellm_response_async())
    else:
        result, info = prompt.litellm_response()
    assert result == CONTENT
    assert info["failure"] is None
    assert len(calls) == 1
    assert calls[0]["api_key"] == OWN
    assert calls[0]["api_base"] == PUBLIC
    assert calls[0]["client"] is client


def _provider_bad_request(text, status=400):
    import httpx
    import openai

    response = httpx.Response(status, request=httpx.Request("POST", PUBLIC))
    return openai.APIStatusError(text, response=response, body={"error": text})


@pytest.mark.parametrize("text,status,expected", [
    ("value must be a string SYNTHETIC-PRIVATE-DETAIL", 400, True),
    ("another bad request SYNTHETIC-PRIVATE-DETAIL", 400, False),
    ("value must be a string SYNTHETIC-PRIVATE-DETAIL", 403, False),
])
def test_launch_format_retry_signal_is_coarse_and_private(text, status, expected, recorded, monkeypatch):
    def fail(**kwargs):
        raise _provider_bad_request(text, status)

    monkeypatch.setattr(litellm, "completion", fail)
    with pytest.raises(model_endpoints.EndpointCallError) as caught:
        model_endpoints.guarded_completion(model="openai/gpt-4o", api_key=OWN, api_base=PUBLIC, messages=CHAT)
    error = caught.value
    assert getattr(error, "requires_text_messages", False) is expected
    assert "SYNTHETIC-PRIVATE-DETAIL" not in str(error)
    assert "SYNTHETIC-PRIVATE-DETAIL" not in repr(vars(error))
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("outcome", ["converted", "non_format", "private_on_retry"])
def test_launch_retained_format_retry_remains_guarded(asynchronous, outcome, recorded, monkeypatch):
    from agentic_eval.core_evals.run_prompt import litellm_response as run_module

    calls, client = recorded
    private = False

    def resolve(host, port, *args, **kwargs):
        assert host == "provider.example.test"
        ip = "127.0.0.1" if private else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

    def record(**kwargs):
        nonlocal private
        calls.append(dict(kwargs))
        if len(calls) == 1:
            assert isinstance(kwargs["messages"][0]["content"], list)
            private = outcome == "private_on_retry"
            text = "unrelated failure" if outcome == "non_format" else "value must be a string"
            raise _provider_bad_request(text + " SYNTHETIC-PRIVATE-DETAIL")
        assert kwargs["messages"][0] == {"role": "system", "content": "synthetic input"}
        return litellm.ModelResponse(
            model="gpt-4o", choices=[{"message": {"role": "assistant", "content": CONTENT}}],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    async def record_async(**kwargs):
        return record(**kwargs)

    manager = MagicMock()
    manager.get_provider.return_value = "openai"
    manager.get_api_key.side_effect = lambda **kwargs: {"key": OWN, "api_base": PUBLIC}
    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(litellm, "completion", record)
    monkeypatch.setattr(litellm, "acompletion", record_async)
    monkeypatch.setattr(run_module, "USE_NEW_RUNPROMPT_HANDLERS", False)
    monkeypatch.setattr(run_module, "LiteLLMModelManager", lambda *args, **kwargs: manager)
    monkeypatch.setattr(run_module, "get_model_mode", lambda model: "chat")
    monkeypatch.setattr(run_module, "calculate_total_cost", lambda *args: 0.0)
    prompt = run_module.RunPrompt(
        model="gpt-4o", messages=[{"role": "system", "content": [{"type": "text", "text": "synthetic input"}]}, {"role": "user", "content": "synthetic question"}],
        organization_id="synthetic-org", workspace_id="synthetic-workspace", output_format="string",
        temperature=None, frequency_penalty=None, presence_penalty=None, max_tokens=None,
        top_p=None, response_format=None, tool_choice=None, tools=[],
        ws_manager=AsyncMock() if asynchronous else MagicMock(),
    )

    def invoke():
        return asyncio.run(prompt.litellm_response_async()) if asynchronous else prompt.litellm_response()

    if outcome == "converted":
        result, info = invoke()
        assert result == CONTENT
        assert info["failure"] is None
        assert len(calls) == 2
    else:
        expected = ssrf_guard.EndpointBlocked if outcome == "private_on_retry" else model_endpoints.EndpointCallError
        with pytest.raises(expected) as caught:
            invoke()
        assert "SYNTHETIC-PRIVATE-DETAIL" not in str(caught.value)
        assert len(calls) == 1
    for call in calls:
        assert call["api_key"] == OWN
        assert call["api_base"] == PUBLIC
        assert call["client"] is client
