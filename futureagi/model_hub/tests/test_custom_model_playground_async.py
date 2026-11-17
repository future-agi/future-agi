"""A custom model (Settings > AI Providers, provider "custom") runs in the
Prompts Playground.

The Playground runs a prompt over its WebSocket: PromptStreamConsumer awaits
``run_template_async``, which awaits ``RunPrompt.litellm_response_async``
(always streaming) on the event loop. There the handler factory's
CustomAIModel lookup raises SynchronousOnlyOperation, which the factory
swallows, so the custom model goes to LLMHandler, and LLMHandler's async
streaming fallback built CustomModelHandler, whose ``__init__`` reads the
CustomAIModel row, on the event loop as well. Every Playground run of a
custom model answered "Custom model <name> not found or invalid: You cannot
call this from an async context - use a thread or sync_to_async." and never
reached the endpoint. The handler is now built in a worker thread, as the
non-streaming async path already builds it; dataset cells, preview, edit and
direct runs are synchronous and unchanged.

Real: the Playground runner, RunPrompt, the handler factory, LLMHandler,
CustomModelHandler, the credit meter, and the organization, workspace, user
and CustomAIModel rows. Doubles: the HTTP endpoint (DNS and transport,
``tfc.utils.tests.guarded_post_fakes``; nothing leaves the process), the
WebSocket sink, and plain stand-ins for the prompt template and version the
consumer loads.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from django.test import override_settings

from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
from model_hub.models.custom_models import CustomAIModel
from model_hub.utils.async_prompt_runner import run_template_async
from tfc.utils.tests.guarded_post_fakes import (
    JSON_HEADERS,
    FakeResolver,
    FakeResponse,
    FakeTransport,
    UnguardedHttp,
)
from tfc.utils.tests.loopback_endpoints import clear_proxy_env

# The handlers read their rows from asgiref worker threads: the rows must be
# committed to be seen there.
pytestmark = pytest.mark.django_db(transaction=True)

PUBLIC_IP = "93.184.216.34"
PUBLIC_URL = "https://api.example.com/v1/chat/completions"
DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"
MODEL = "playground-custom"
PROMPT = "Reply with one short friendly sentence about green tea."
ANSWER = "Green tea is a calm cup of fresh leaves."
MESSAGES = [{"role": "user", "content": [{"type": "text", "text": PROMPT}]}]
# The frames a Playground run sends, in order (the WebSocket sink records
# any other call too).
PLAYGROUND_FRAMES = [
    "process_started",
    "started",
    "running",
    "completed",
    "all_completed",
]


def _completion_body():
    # A chat-completions body with the endpoint's own usage, so no tokens
    # are counted in the worker thread.
    return json.dumps(
        {
            "id": "c1",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ANSWER},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 9},
        }
    ).encode()


@pytest.fixture(autouse=True)
def _django_refuses_orm_on_the_event_loop(monkeypatch):
    # With DJANGO_ALLOW_ASYNC_UNSAFE set, Django runs the ORM on the event
    # loop and these tests could not see the failure.
    monkeypatch.delenv("DJANGO_ALLOW_ASYNC_UNSAFE", raising=False)


@pytest.fixture(autouse=True)
def _no_env_proxy(monkeypatch):
    # guarded_post honours HTTP(S)_PROXY; these tests use the fake transport.
    clear_proxy_env(monkeypatch)


@pytest.fixture(autouse=True)
def unguarded(monkeypatch):
    return UnguardedHttp().install(monkeypatch)


@pytest.fixture
def resolver(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    resolver.add("api.example.com", PUBLIC_IP)
    return resolver


@pytest.fixture
def transport(monkeypatch):
    transport = FakeTransport().install(monkeypatch)
    transport.respond(FakeResponse(200, JSON_HEADERS, _completion_body()))
    return transport


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.delenv(DENY_ENV, raising=False)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def custom_model(user, workspace):
    # What Settings > AI Providers saves for a custom model.
    return CustomAIModel.objects.create(
        user_model_id=MODEL,
        provider="custom",
        input_token_cost=0,
        output_token_cost=0,
        organization=user.organization,
        workspace=workspace,
        key_config={
            "api_base": PUBLIC_URL,
            "headers": {"Authorization": "Bearer tenant"},
            "custom_provider": True,
        },
        user=user,
        deleted=False,
    )


class _PlaygroundWs:
    """The Playground's WebSocket manager: every call awaited and recorded."""

    def __init__(self):
        self.sent = []

    async def notify_process_started(self, **kwargs):
        self.sent.append(("process_started", kwargs))

    async def send_started_message(self, **kwargs):
        self.sent.append(("started", kwargs))

    async def send_running_message(self, **kwargs):
        self.sent.append(("running", kwargs))

    async def send_completed_message(self, **kwargs):
        self.sent.append(("completed", kwargs))

    async def send_error_message(self, **kwargs):
        self.sent.append(("error", kwargs))

    async def send_all_completed_message(self, **kwargs):
        self.sent.append(("all_completed", kwargs))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def record(*args, **kwargs):
            self.sent.append((name, kwargs))

        return record

    def kinds(self):
        return [kind for kind, _ in self.sent]

    def frames(self, kind):
        return [kwargs for sent_kind, kwargs in self.sent if sent_kind == kind]


def _run_prompt(user, workspace, **overrides):
    params = {
        "model": MODEL,
        "messages": json.loads(json.dumps(MESSAGES)),
        "organization_id": user.organization.id,
        "output_format": "string",
        "temperature": None,
        "frequency_penalty": None,
        "presence_penalty": None,
        "max_tokens": None,
        "top_p": None,
        "response_format": None,
        "tool_choice": None,
        "tools": None,
        "workspace_id": workspace.id,
    }
    params.update(overrides)
    return RunPrompt(**params)


def _assert_one_guarded_call(transport, unguarded):
    assert [pool["host"] for pool in transport.pools] == [PUBLIC_IP]
    assert len(transport.requests) == 1
    assert transport.sent_headers["authorization"] == "Bearer tenant"
    sent = json.loads(transport.requests[0]["body"])
    assert sent["stream"] is False
    assert PROMPT in json.dumps(sent["messages"])
    assert unguarded.calls == []


def test_playground_run_of_a_custom_model_returns_its_answer(
    hosted, resolver, transport, unguarded, user, workspace, custom_model
):
    # Base: the output is the async-context error, the Playground gets an
    # error frame and the endpoint is never called.
    ws = _PlaygroundWs()
    execution = SimpleNamespace(
        id=uuid.uuid4(),
        variable_names={},
        output=None,
        metadata=None,
        template_version="v1",
        prompt_config_snapshot={
            "messages": json.loads(json.dumps(MESSAGES)),
            "configuration": {"model": MODEL, "output_format": "string"},
        },
        save=lambda update_fields=None: None,
    )
    template = SimpleNamespace(id=uuid.uuid4())

    asyncio.run(
        run_template_async(
            template=template,
            execution=execution,
            organization_id=user.organization.id,
            version_to_run="v1",
            is_run="prompt",
            run_index=None,
            workspace=workspace,
            ws_manager=ws,
        )
    )

    assert execution.output == [ANSWER], execution.output
    assert ws.frames("error") == [], ws.frames("error")
    assert ws.kinds() == PLAYGROUND_FRAMES, ws.kinds()
    assert ws.frames("running")[0]["chunk"] == ANSWER
    completed = ws.frames("completed")[0]["metadata"]
    assert completed.get("response_time_unit") == "ms", completed
    assert completed["usage"]["total_tokens"] == 21, completed
    _assert_one_guarded_call(transport, unguarded)


def test_streaming_async_run_prompt_of_a_custom_model_returns_its_answer(
    hosted, resolver, transport, unguarded, user, workspace, custom_model
):
    # The call the Playground runner makes. Base: it raises the
    # async-context error.
    ws = _PlaygroundWs()
    run_prompt = _run_prompt(user, workspace, ws_manager=ws)

    try:
        response, value_info = asyncio.run(
            run_prompt.litellm_response_async(
                streaming=True,
                template_id="t1",
                version="v1",
                index=0,
                max_index=1,
                type="run_prompt",
            )
        )
    except Exception as error:
        pytest.fail(f"the Playground run raised: {error}")

    assert response == ANSWER
    assert value_info["metadata"].get("response_time_unit") == "ms", value_info
    assert ws.kinds() == ["started", "running", "completed"], ws.kinds()
    assert ws.frames("running")[0]["chunk"] == ANSWER
    _assert_one_guarded_call(transport, unguarded)


def test_non_streaming_async_run_prompt_of_a_custom_model_is_unchanged(
    hosted, resolver, transport, unguarded, user, workspace, custom_model
):
    # Async and not streaming (e.g. the agent evaluator): the handler was
    # already built in a worker thread, so this passes on base too.
    response, _value_info = asyncio.run(
        _run_prompt(user, workspace).litellm_response_async(type="run_prompt")
    )

    assert response == ANSWER
    _assert_one_guarded_call(transport, unguarded)
