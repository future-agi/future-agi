"""A custom model's answer is the assistant's message text, not the HTTP body.

CustomModelHandler returned the endpoint's whole chat-completions body
(``response.text``) as the answer. The Prompts output panel, the dataset Run
prompt cells and the experiment Output column showed the provider's raw JSON
with the answer three levels down, and tokens and cost were counted on that
envelope. It now reads ``choices[0].message`` the way the LiteLLM path does:
the message text, tool calls, JSON-mode text and output_format parsing, and
the provider's ``usage`` when it sends one. The Playground (WebSocket) path
sends that text as its chunk. The response time is in milliseconds on every
path, the Playground's included, as LLM streaming records it, and
``response_time_unit`` "ms" says so: the Prompts time chip shows 750 ms as
"0.75s".

DNS and the transport are faked (``tfc.utils.tests.guarded_post_fakes``);
nothing leaves the process.
"""

import asyncio
import itertools
import json
from types import SimpleNamespace

import pytest
from django.test import override_settings

from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
    custom_model_handler as handler_module,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.custom_model_handler import (
    CustomModelHandler,
)
from model_hub.models.custom_models import CustomAIModel
from tfc.utils.tests.guarded_post_fakes import (
    COMPLETION_BODY,
    JSON_HEADERS,
    FakeResolver,
    FakeResponse,
    FakeTransport,
    UnguardedHttp,
)
from tfc.utils.tests.loopback_endpoints import clear_proxy_env

PUBLIC_IP = "93.184.216.34"
PUBLIC_URL = "https://api.example.com/v1/chat/completions"
DENY_ENV = "CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS"
MODEL = "output-custom"
# The assistant message content inside COMPLETION_BODY.
ANSWER = "public model answer"

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
TOOL_CALLS = [
    {
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
    }
]


@pytest.fixture
def resolver(monkeypatch):
    resolver = FakeResolver().install(monkeypatch)
    resolver.add("api.example.com", PUBLIC_IP)
    return resolver


@pytest.fixture
def transport(monkeypatch):
    return FakeTransport().install(monkeypatch)


@pytest.fixture(autouse=True)
def _no_env_proxy(monkeypatch):
    # guarded_post honours HTTP(S)_PROXY; these tests use the fake transport.
    clear_proxy_env(monkeypatch)


@pytest.fixture(autouse=True)
def unguarded(monkeypatch):
    return UnguardedHttp().install(monkeypatch)


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.delenv(DENY_ENV, raising=False)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


class _RecordingWs:
    """The WebSocket manager methods the handlers call, recorded in order."""

    def __init__(self):
        self.sent = []

    def send_started_message(self, **kwargs):
        self.sent.append(("started", kwargs))

    def send_running_message(self, **kwargs):
        self.sent.append(("running", kwargs))

    def send_completed_message(self, **kwargs):
        self.sent.append(("completed", kwargs))

    def send_error_message(self, **kwargs):
        self.sent.append(("error", kwargs))

    def frames(self, kind):
        return [kwargs for sent_kind, kwargs in self.sent if sent_kind == kind]


class _AsyncRecordingWs(_RecordingWs):
    """The async WebSocket manager: the same methods, awaited."""

    async def send_started_message(self, **kwargs):
        self.sent.append(("started", kwargs))

    async def send_running_message(self, **kwargs):
        self.sent.append(("running", kwargs))

    async def send_completed_message(self, **kwargs):
        self.sent.append(("completed", kwargs))

    async def send_error_message(self, **kwargs):
        self.sent.append(("error", kwargs))


def _body(message=None, **top):
    """A chat-completions body with one choice and the given message fields."""
    body = {
        "id": "c1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", **(message or {})},
                "finish_reason": "stop",
            }
        ],
    }
    body.update(top)
    return json.dumps(body).encode()


def _handler(user, workspace, **context):
    CustomAIModel.objects.create(
        user_model_id=MODEL,
        provider="custom",
        input_token_cost=0,
        output_token_cost=0,
        organization=user.organization,
        workspace=workspace,
        key_config={"api_base": PUBLIC_URL, "headers": {}},
        user=user,
        deleted=False,
    )
    return CustomModelHandler(
        ModelHandlerContext(
            model=MODEL,
            messages=[{"role": "user", "content": "What is the capital of France?"}],
            organization_id=str(user.organization.id),
            workspace_id=str(workspace.id),
            provider="custom",
            **context,
        )
    )


def _fixed_clock(monkeypatch):
    """The handler's clock: the call starts at 1000.0 and ends 0.75 s later."""
    ticks = itertools.chain([1000.0], itertools.repeat(1000.75))
    monkeypatch.setattr(
        handler_module, "time", SimpleNamespace(time=lambda: next(ticks))
    )


@pytest.mark.django_db
def test_answer_is_the_assistant_message_text(
    hosted, user, workspace, resolver, transport
):
    # Base: the whole COMPLETION_BODY JSON, in both places.
    result = _handler(user, workspace).execute_sync()
    assert result.response == ANSWER
    _, value_info = result.to_value_info()
    assert value_info["data"]["response"] == ANSWER


@pytest.mark.django_db
def test_tool_calls_return_the_tool_call_list(
    hosted, user, workspace, resolver, transport
):
    # Base: the whole body, with the tool call nested under choices.
    transport.respond(
        FakeResponse(
            200, JSON_HEADERS, _body({"content": None, "tool_calls": TOOL_CALLS})
        )
    )
    result = _handler(user, workspace, tools=[WEATHER_TOOL]).execute_sync()
    assert json.loads(result.response) == TOOL_CALLS
    sent = json.loads(transport.requests[0]["body"])
    assert sent["tools"] == [WEATHER_TOOL]


@pytest.mark.django_db
def test_json_mode_returns_content_not_envelope(
    hosted, user, workspace, resolver, transport
):
    # Base: the envelope, whose content is the JSON the model wrote.
    transport.respond(
        FakeResponse(200, JSON_HEADERS, _body({"content": '{"capital": "Paris"}'}))
    )
    handler = _handler(user, workspace, response_format={"type": "json_object"})
    assert handler.execute_sync().response == '{"capital": "Paris"}'


@pytest.mark.django_db
def test_output_format_object_parses_content(
    hosted, user, workspace, resolver, transport
):
    # Base: the envelope string, never a parsed object.
    transport.respond(
        FakeResponse(200, JSON_HEADERS, _body({"content": '{"capital": "Paris"}'}))
    )
    handler = _handler(user, workspace, output_format="object")
    assert handler.execute_sync().response == {"capital": "Paris"}


@pytest.mark.django_db
def test_content_parts_are_joined(hosted, user, workspace, resolver, transport):
    # Base: the envelope with the parts list inside it.
    transport.respond(
        FakeResponse(
            200,
            JSON_HEADERS,
            _body(
                {
                    "content": [
                        {"type": "text", "text": "Hello "},
                        {"type": "text", "text": "world"},
                    ]
                }
            ),
        )
    )
    assert _handler(user, workspace).execute_sync().response == "Hello world"


@pytest.mark.django_db
def test_provider_usage_drives_tokens(hosted, user, workspace, resolver, transport):
    # Base: tokens counted on the JSON envelope, the provider's usage ignored.
    transport.respond(
        FakeResponse(
            200,
            JSON_HEADERS,
            _body(
                {"content": ANSWER},
                usage={"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
            ),
        )
    )
    result = _handler(user, workspace).execute_sync()
    assert result.response == ANSWER
    assert result.metadata["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 2,
        "total_tokens": 13,
    }


@pytest.mark.django_db
def test_error_object_body_succeeds_as_before(
    hosted, user, workspace, resolver, transport
):
    # A 200 whose JSON is an error object, not a chat completion: the call
    # succeeds with the body as the answer, exactly as on base (no new failure
    # after the provider has answered).
    body = b'{"error": {"message": "upstream said no", "code": 500}}'
    transport.respond(FakeResponse(200, JSON_HEADERS, body))
    result = _handler(user, workspace).execute_sync()
    assert result.response == body.decode()
    _, value_info = result.to_value_info()
    assert value_info["data"]["response"] == body.decode()


@pytest.mark.django_db
def test_other_json_body_succeeds_with_the_body_as_before(
    hosted, user, workspace, resolver, transport
):
    # An endpoint's own JSON shape: same output as base, run not failed.
    body = b'{"output": {"text": "Paris"}, "id": "x1"}'
    transport.respond(FakeResponse(200, JSON_HEADERS, body))
    result = _handler(user, workspace).execute_sync()
    assert result.response == body.decode()


@pytest.mark.django_db
def test_generated_text_body_is_read_as_its_text(
    hosted, user, workspace, resolver, transport
):
    transport.respond(
        FakeResponse(200, JSON_HEADERS, b'{"generated_text": "Paris"}'),
        FakeResponse(200, JSON_HEADERS, b'[{"generated_text": "Lyon"}]'),
    )
    handler = _handler(user, workspace)
    assert handler.execute_sync().response == "Paris"
    assert handler.execute_sync().response == "Lyon"


@pytest.mark.django_db
def test_body_without_choices_counts_tokens_on_the_body_as_before(
    hosted, user, workspace, resolver, transport, monkeypatch
):
    # Usage and cost: with no provider usage, completion tokens are counted on
    # the same text as base (the body), so recording is unchanged.
    body = b'{"output": {"text": "Paris"}}'
    transport.respond(FakeResponse(200, JSON_HEADERS, body))
    seen = {}
    handler = _handler(user, workspace)
    original = handler._calculate_token_costs

    def spy(prompt_text, completion_text, image_urls, usage=None):
        seen.update(completion_text=completion_text, usage=usage)
        return original(prompt_text, completion_text, image_urls, usage=usage)

    monkeypatch.setattr(handler, "_calculate_token_costs", spy)
    handler.execute_sync()
    assert seen == {"completion_text": body.decode(), "usage": None}


@pytest.mark.django_db
def test_empty_content_is_an_empty_answer_not_a_failure(
    hosted, user, workspace, resolver, transport
):
    # Base: returns the envelope and succeeds. The fix reads the (empty)
    # answer and still succeeds: never a failure after the provider answered.
    transport.respond(FakeResponse(200, JSON_HEADERS, _body({"content": ""})))
    assert _handler(user, workspace).execute_sync().response == ""


@pytest.mark.django_db
def test_plain_text_body_passes_through(hosted, user, workspace, resolver, transport):
    # Back-compat guard, green on base too: a body that is not JSON is the answer.
    transport.respond(
        FakeResponse(200, {"Content-Type": "text/plain"}, b"plain answer")
    )
    result = _handler(user, workspace).execute_sync()
    assert result.response == "plain answer"


@pytest.mark.django_db
def test_reasoning_goes_to_the_reason_when_asked(
    hosted, user, workspace, resolver, transport
):
    # Base: the envelope in both places.
    transport.respond(
        FakeResponse(
            200,
            JSON_HEADERS,
            _body({"content": "Paris", "reasoning_content": "France's capital."}),
        )
    )
    result = _handler(user, workspace, show_reasoning_process=True).execute_sync()
    response, value_info = result.to_value_info()
    assert response == "Paris"
    assert value_info["data"]["response"] == (
        "<thinking>\nFrance's capital.\n</thinking>\n\nParis"
    )


@pytest.mark.django_db
def test_non_streaming_response_time_stays_milliseconds(
    hosted, user, workspace, resolver, transport, monkeypatch
):
    # Dataset cells and the row footer read milliseconds. Cut 3 already
    # recorded 750.0 here but with no unit, so the Prompts time chip could
    # not tell it from an older seconds value; it now says "ms".
    _fixed_clock(monkeypatch)
    result = _handler(user, workspace).execute_sync()
    assert result.metadata["response_time"] == pytest.approx(750.0)
    assert result.metadata.get("response_time_unit") == "ms", result.metadata
    _, value_info = result.to_value_info()
    assert value_info["metadata"]["response_time"] == pytest.approx(750.0)
    assert value_info["metadata"].get("response_time_unit") == "ms"


@pytest.mark.django_db
def test_ws_lifecycle_sends_text_and_milliseconds(
    hosted, user, workspace, resolver, transport, monkeypatch
):
    # Before raw-JSON v2 the running chunk was the whole COMPLETION_BODY and
    # the completed frame said 750.0 with no unit, which the Playground
    # printed as "750.00s". Cut 3 sent 0.75 (seconds, no unit). Now the
    # frame and what Prompts saves say 750.0 with response_time_unit "ms",
    # which the time chip prints as "0.75s", like every other model.
    _fixed_clock(monkeypatch)
    ws = _RecordingWs()
    handler = _handler(user, workspace, ws_manager=ws, template_id="t1", version="v1")

    result = handler.execute_with_ws_lifecycle_sync()

    assert [kind for kind, _ in ws.sent] == ["started", "running", "completed"]
    assert ws.frames("running")[0]["chunk"] == ANSWER
    completed = ws.frames("completed")[0]["metadata"]
    assert completed["response_time"] == pytest.approx(750.0)
    assert completed.get("response_time_unit") == "ms", completed
    assert result.response == ANSWER
    assert result.metadata["response_time"] == pytest.approx(750.0)
    # What Prompts saves for this run (PromptExecution.metadata).
    _, value_info = result.to_value_info()
    assert value_info["metadata"]["response_time"] == pytest.approx(750.0)
    assert value_info["metadata"].get("response_time_unit") == "ms"


@pytest.mark.django_db
def test_ws_lifecycle_async_sends_text_and_milliseconds(
    hosted, user, workspace, resolver, transport, monkeypatch
):
    # Before raw-JSON v2: the whole body as the chunk and 750.0 with no unit.
    # Cut 3: 0.75 (seconds, no unit). Now 750.0 with "ms". The body
    # carries usage, so nothing is counted in the worker thread.
    transport.respond(
        FakeResponse(
            200,
            JSON_HEADERS,
            _body(
                {"content": ANSWER},
                usage={"prompt_tokens": 9, "completion_tokens": 3},
            ),
        )
    )
    _fixed_clock(monkeypatch)
    ws = _AsyncRecordingWs()
    handler = _handler(user, workspace, ws_manager=ws, template_id="t1", version="v1")

    result = asyncio.run(handler.execute_with_ws_lifecycle_async())

    assert [kind for kind, _ in ws.sent] == ["started", "running", "completed"]
    assert ws.frames("running")[0]["chunk"] == ANSWER
    completed = ws.frames("completed")[0]["metadata"]
    assert completed["response_time"] == pytest.approx(750.0)
    assert completed.get("response_time_unit") == "ms", completed
    assert result.response == ANSWER


@pytest.mark.django_db
def test_ws_lifecycle_streams_tool_calls_as_text(
    hosted, user, workspace, resolver, transport
):
    # Base: the whole body as the chunk. A dict chunk would show [object Object].
    transport.respond(
        FakeResponse(
            200, JSON_HEADERS, _body({"content": None, "tool_calls": TOOL_CALLS})
        )
    )
    ws = _RecordingWs()
    handler = _handler(
        user,
        workspace,
        tools=[WEATHER_TOOL],
        ws_manager=ws,
        template_id="t1",
        version="v1",
    )
    handler.execute_with_ws_lifecycle_sync()
    chunk = ws.frames("running")[0]["chunk"]
    assert isinstance(chunk, str)
    assert json.loads(chunk) == TOOL_CALLS


@pytest.mark.django_db
def test_llm_handler_custom_fallback_returns_text(
    hosted, user, workspace, resolver, transport
):
    # LLMHandler hands a "custom" payload to CustomModelHandler when the
    # factory did not route it there. Base: the whole COMPLETION_BODY.
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.llm_handler import (
        LLMHandler,
    )

    context = _handler(user, workspace).context
    assert LLMHandler(context).execute_sync(streaming=False).response == ANSWER


@pytest.mark.django_db
def test_llm_handler_custom_fallback_streams_text_and_milliseconds(
    hosted, user, workspace, resolver, transport, monkeypatch
):
    # Before raw-JSON v2 the fallback sent the whole COMPLETION_BODY and
    # 750.0 with no unit; cut 3 sent 0.75 (seconds, no unit). Now 750.0
    # with response_time_unit "ms", as the handler's own WebSocket path.
    from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.llm_handler import (
        LLMHandler,
    )

    _fixed_clock(monkeypatch)
    ws = _RecordingWs()
    context = _handler(
        user, workspace, ws_manager=ws, template_id="t1", version="v1"
    ).context

    result = LLMHandler(context).execute_sync(streaming=True)

    # One "started" (LLMHandler's own), then the answer, then completed.
    assert [kind for kind, _ in ws.sent] == ["started", "running", "completed"]
    assert ws.frames("running")[0]["chunk"] == ANSWER
    completed = ws.frames("completed")[0]["metadata"]
    assert completed["response_time"] == pytest.approx(750.0)
    assert completed.get("response_time_unit") == "ms", completed
    assert result.response == ANSWER


def test_completion_body_fixture_is_what_these_tests_assume():
    # Guards the fixture the default transport answers with.
    assert json.loads(COMPLETION_BODY)["choices"][0]["message"]["content"] == ANSWER
