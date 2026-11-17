"""Tenant-endpoint errors are generic before any handler log, WebSocket
message or cache write.

RunPrompt's outer layer turns a failed call to a tenant base URL into
generic text, but the handlers act on the error first: LLMHandler's
streaming paths (sync and async) logged the raw SDK error, sent it over the
WebSocket and cached it; ImageHandler, the dormant RunPrompt image path and
the audio-preview handler logged ``str(e)`` (with the traceback); and
``litellm_try_except`` logged the provider's error. An upstream error body
(here a 401 answer, and an error event in the middle of a stream) can carry
anything the endpoint chooses, so none of it may reach a log, the
WebSocket, the cache or the exception chain.

Real handlers, PayloadBuilder, litellm and the OpenAI SDK against loopback
servers; the WebSocket manager and the loggers record every call.
"""

import asyncio
import json
from unittest import mock

import pytest
from django.test import override_settings

from agentic_eval.core_evals.run_prompt import error_handler, litellm_response
from agentic_eval.core_evals.run_prompt.runprompt_handlers import base_handler
from agentic_eval.core_evals.run_prompt.runprompt_handlers.base_handler import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
    image_handler,
    llm_handler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.image_handler import (
    ImageHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.llm_handler import (
    LLMHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts import (
    audio_preview_handler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.tts.audio_preview_handler import (
    AudioPreviewHandler,
)
from tfc.utils import ssrf_guard
from tfc.utils.tests.guarded_post_fakes import FakeResolver
from tfc.utils.tests.loopback_endpoints import (
    PUBLIC_NAME,
    SECRET_MARKER,
    LoopbackServer,
    clear_proxy_env,
    treat_as_public,
)

URL_SECRET = "URL-CREDENTIAL-MARKER"
MARKERS = (SECRET_MARKER, URL_SECRET)
UNAUTHORIZED = ssrf_guard.endpoint_status_message(401)


class RecordingLogger:
    """Stands in for a structlog logger; records every call."""

    def __init__(self):
        self.calls = []

    def bind(self, **_kwargs):
        return self

    def __getattr__(self, level):
        def record(*args, **kwargs):
            self.calls.append((level, args, kwargs))

        return record

    def with_traceback(self):
        return [
            call
            for call in self.calls
            if call[0] == "exception" or call[2].get("exc_info")
        ]


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    clear_proxy_env(monkeypatch)
    monkeypatch.delenv("CUSTOM_MODEL_DENY_PRIVATE_ENDPOINTS", raising=False)


@pytest.fixture
def hosted(monkeypatch):
    FakeResolver().install(monkeypatch).add(PUBLIC_NAME, "127.0.0.1")
    treat_as_public(monkeypatch)
    with override_settings(CLOUD_DEPLOYMENT="US"):
        yield


@pytest.fixture
def logs(monkeypatch):
    """One recorder behind every logger these paths use."""
    recorder = RecordingLogger()
    for module in (
        error_handler,
        base_handler,
        llm_handler,
        image_handler,
        audio_preview_handler,
        litellm_response,
    ):
        name = "_logger" if module is error_handler else "logger"
        monkeypatch.setattr(module, name, recorder)
    return recorder


def _error_body():
    return json.dumps(
        {"error": {"message": f"upstream says {SECRET_MARKER}", "type": "x"}}
    ).encode()


def unauthorized_server():
    """A public endpoint that refuses every call with a marker in the body."""
    return LoopbackServer(
        lambda request: (401, {"Content-Type": "application/json"}, _error_body())
    )


def _sse(*events):
    return b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)


def mid_stream_error_server():
    """A public endpoint that starts a stream, then sends an error event."""
    chunk = {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": "gpt-x",
        "choices": [{"index": 0, "delta": {"content": "par"}, "finish_reason": None}],
    }
    body = _sse(chunk, {"error": {"message": f"stream broke {SECRET_MARKER}"}})
    return LoopbackServer(
        lambda request: (200, {"Content-Type": "text/event-stream"}, body)
    )


def _context(server, model="gpt-x", ws_manager=None, path="/v1", **extra):
    return ModelHandlerContext(
        model=model,
        messages=[{"role": "user", "content": "hello"}],
        organization_id="00000000-0000-0000-0000-000000000001",
        provider="openai",
        api_key={
            "api_key": "sk-tenant",
            "api_base": f"http://user:{URL_SECRET}@{PUBLIC_NAME}:{server.port}{path}",
        },
        ws_manager=ws_manager,
        template_id="t1",
        version="v1",
        result_index=0,
        num_results=1,
        **extra,
    )


def _sync_ws():
    ws = mock.MagicMock()
    ws.is_streaming_stopped.return_value = False
    return ws


def _async_ws():
    ws = mock.AsyncMock()
    ws.is_streaming_stopped.return_value = False
    return ws


def _chain(exc):
    seen, pending = [], [exc]
    while pending:
        current = pending.pop()
        if current is None or any(current is s for s in seen):
            continue
        seen.append(current)
        pending.extend([current.__cause__, current.__context__])
    return seen


def _clean(*things):
    text = repr(things)
    return all(marker not in text for marker in MARKERS)


def _assert_clean_failure(exc, logs, *payloads):
    assert exc is not None
    assert _clean(*[str(e) for e in _chain(exc)])
    assert _clean(logs.calls), logs.calls
    assert logs.with_traceback() == []
    assert _clean(*payloads)


def _error_messages(ws):
    return [call.kwargs["error"] for call in ws.send_error_message.call_args_list]


def _cached_errors(ws):
    return [
        call.kwargs["response_data"].get("error")
        for call in ws.set_cached_response.call_args_list
        if call.kwargs["response_data"].get("error")
    ]


def _raised(callable_):
    try:
        callable_()
    except Exception as exc:  # the handler raised, as it must
        return exc
    return None


# -------------------------------------------------------------- streaming ---


@pytest.mark.django_db
@pytest.mark.parametrize(
    "server_factory", [unauthorized_server, mid_stream_error_server]
)
def test_sync_streaming_error_is_generic_everywhere(hosted, logs, server_factory):
    ws = _sync_ws()
    with server_factory() as server:
        handler = LLMHandler(_context(server, ws_manager=ws))
        handler.logger = logs
        exc = _raised(lambda: handler.execute_sync(streaming=True))
    _assert_clean_failure(exc, logs, ws.mock_calls)
    assert _error_messages(ws) and _cached_errors(ws)
    if server_factory is unauthorized_server:
        assert _error_messages(ws) == [UNAUTHORIZED]
        assert _cached_errors(ws) == [UNAUTHORIZED]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "server_factory", [unauthorized_server, mid_stream_error_server]
)
def test_async_streaming_error_is_generic_everywhere(hosted, logs, server_factory):
    ws = _async_ws()
    with server_factory() as server:
        handler = LLMHandler(_context(server, ws_manager=ws))
        handler.logger = logs
        exc = _raised(lambda: asyncio.run(handler.execute_async(streaming=True)))
    _assert_clean_failure(exc, logs, ws.mock_calls)
    assert _error_messages(ws) and _cached_errors(ws)
    if server_factory is unauthorized_server:
        assert _error_messages(ws) == [UNAUTHORIZED]
        assert _cached_errors(ws) == [UNAUTHORIZED]


# -------------------------------------------------------------- regular ---


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_regular_completion_error_is_generic_in_logs(hosted, logs, mode):
    with unauthorized_server() as server:
        handler = LLMHandler(_context(server))
        handler.logger = logs
        if mode == "sync":
            exc = _raised(handler.execute_sync)
        else:
            exc = _raised(lambda: asyncio.run(handler.execute_async()))
    _assert_clean_failure(exc, logs)
    assert str(exc) == UNAUTHORIZED


# ------------------------------------------------------------------ image ---


@pytest.mark.django_db
def test_image_handler_error_is_generic_in_logs(hosted, logs):
    with unauthorized_server() as server:
        handler = ImageHandler(_context(server, model="dall-e-3"))
        handler.logger = logs
        exc = _raised(lambda: handler._image_generation_response(0.0))
    _assert_clean_failure(exc, logs)
    assert str(exc) == UNAUTHORIZED


@pytest.mark.django_db
def test_image_handler_websocket_error_is_generic(hosted, logs):
    ws = _sync_ws()
    with unauthorized_server() as server:
        handler = ImageHandler(_context(server, model="dall-e-3", ws_manager=ws))
        handler.logger = logs
        exc = _raised(handler.execute_with_ws_lifecycle_sync)
    _assert_clean_failure(exc, logs, ws.mock_calls)
    assert _error_messages(ws) == [UNAUTHORIZED]


def test_dormant_run_prompt_image_path_error_is_generic_in_logs(hosted, logs):
    with unauthorized_server() as server:
        run_prompt = litellm_response.RunPrompt(
            model="dall-e-3",
            messages=[{"role": "user", "content": "a cat"}],
            organization_id="00000000-0000-0000-0000-000000000001",
            output_format="image",
            temperature=None,
            frequency_penalty=None,
            presence_penalty=None,
            max_tokens=None,
            top_p=None,
            response_format=None,
            tool_choice=None,
            tools=None,
        )
        api_key = {
            "api_key": "sk-tenant",
            "api_base": f"http://user:{URL_SECRET}@{PUBLIC_NAME}:{server.port}/v1",
        }
        exc = _raised(
            lambda: run_prompt._image_generation_response(0.0, api_key, "openai")
        )
    _assert_clean_failure(exc, logs)


# ---------------------------------------------------------- audio preview ---


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_audio_preview_error_is_generic_in_logs_and_failure(hosted, logs, mode):
    with unauthorized_server() as server:
        handler = AudioPreviewHandler(_context(server, model="gpt-4o-audio-preview"))
        handler.logger = logs
        if mode == "sync":
            result = handler.execute_sync()
        else:
            result = asyncio.run(handler.execute_async())
    assert result.failure == UNAUTHORIZED
    assert _clean(logs.calls), logs.calls
    assert logs.with_traceback() == []
