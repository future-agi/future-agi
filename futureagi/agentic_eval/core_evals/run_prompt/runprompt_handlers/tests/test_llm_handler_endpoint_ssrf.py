"""Run-prompt handlers call a tenant ``api_base`` through the guarded client.

LLMHandler (sync and async), the audio-preview handler and ImageHandler pass
an openai/azure custom model's ``api_base`` to litellm. litellm's stock
clients follow redirects, so a hosted public endpoint redirecting to the
metadata server reached it. These run the real handler, PayloadBuilder,
litellm and OpenAI SDK against loopback servers: ``public-a.test`` ->
127.0.0.1 treated as public; ``[::1]`` stands in for the metadata server.
"""

import asyncio

import pytest
from django.test import override_settings

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler import (
    ImageHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.llm_handler import (
    LLMHandler,
)
from tfc.utils.tests.guarded_post_fakes import FakeResolver
from tfc.utils.tests.loopback_endpoints import (
    MODEL_ANSWER,
    PUBLIC_NAME,
    SECRET_MARKER,
    clear_proxy_env,
    metadata_stand_in,
    model_server,
    redirector,
    treat_as_public,
)

DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv(DENY_ENV, raising=False)


@pytest.fixture
def resolver(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    resolver.add(PUBLIC_NAME, "127.0.0.1")
    return resolver


@pytest.fixture
def hosted(monkeypatch):
    treat_as_public(monkeypatch)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def metadata():
    with metadata_stand_in() as server:
        yield server


@pytest.fixture(params=[302, 307])
def internal_redirect(request, metadata):
    target = f"http://[::1]:{metadata.port}/latest/meta-data/iam/security-credentials/r"
    with redirector(target, request.param) as server:
        yield server


def _context(server, model="gpt-x", provider="openai", path="/v1"):
    return ModelHandlerContext(
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        organization_id="00000000-0000-0000-0000-000000000001",
        provider=provider,
        api_key={
            "api_key": "sk-tenant",
            "api_base": f"http://{PUBLIC_NAME}:{server.port}{path}",
        },
    )


def _failure_text(callable_):
    try:
        result = callable_()
    except Exception as exc:  # the handler raised
        return str(exc)
    return str(getattr(result, "failure", "") or "")


@pytest.mark.django_db
def test_llm_handler_sync_redirect_to_metadata_is_refused(
    hosted, resolver, metadata, internal_redirect
):
    handler = LLMHandler(_context(internal_redirect))
    text = _failure_text(handler.execute_sync)
    assert metadata.requests == []
    assert SECRET_MARKER not in text


@pytest.mark.django_db
def test_llm_handler_async_redirect_to_metadata_is_refused(
    hosted, resolver, metadata, internal_redirect
):
    handler = LLMHandler(_context(internal_redirect))
    text = _failure_text(lambda: asyncio.run(handler.execute_async()))
    assert metadata.requests == []
    assert SECRET_MARKER not in text


@pytest.mark.django_db
def test_llm_handler_sync_public_endpoint_works(hosted, resolver):
    with model_server() as server:
        result = LLMHandler(_context(server)).execute_sync()
    assert MODEL_ANSWER in str(result.response)
    assert server.requests[0].headers["authorization"] == "Bearer sk-tenant"


@pytest.mark.django_db
def test_llm_handler_async_public_endpoint_works(hosted, resolver):
    with model_server() as server:
        result = asyncio.run(LLMHandler(_context(server)).execute_async())
    assert MODEL_ANSWER in str(result.response)


@pytest.mark.django_db
def test_image_handler_redirect_to_metadata_is_refused(
    hosted, resolver, metadata, internal_redirect
):
    handler = ImageHandler(_context(internal_redirect, model="dall-e-3"))
    with pytest.raises(Exception) as excinfo:
        handler._image_generation_response(0.0)
    assert metadata.requests == []
    assert SECRET_MARKER not in str(excinfo.value)
