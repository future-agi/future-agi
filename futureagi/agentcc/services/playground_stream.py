"""The console's streamed Try it: one chat completion through the gateway,
relayed to the browser as server-sent events while it arrives.

Events, in order:

- ``meta``: the gateway answered with a stream. ``status_code``, ``model``,
  ``guardrail_headers`` (its x-agentcc-* and guardrail headers, as the
  non-streamed test returns them), and ``blocked``, ``warned``,
  ``guardrail_action`` and ``triggered_checks`` read as the non-streamed test
  reads them (services/playground_outcome.py).
- ``delta``: ``{"content": "..."}``, one per piece of the answer.
- ``done``: the stream ended. The ``meta`` fields again, plus ``usage`` and
  ``agentcc_metadata`` from the gateway's final chunk (cost, latency_ms,
  request_id, billing_mode, ...) and ``finish_reason``.
- ``result``: instead of all of the above when the gateway did not stream
  (a refusal such as a guardrail block, 403, or 402): the non-streamed test's
  result, with ``body``.
- ``error``: ``{"message": ...}`` when the gateway failed mid-stream or sent
  an error chunk; nothing follows it.

The request carries a short-lived person grant, not a temporary API key.
Closing ends the read from the gateway, including before the first event.
views/event_stream.py serves the events piece by piece under ASGI.
"""

import json
import threading

import structlog

from agentcc.services.gateway_client import GatewayClientError
from agentcc.services.playground_outcome import playground_outcome

logger = structlog.get_logger(__name__)


def sse(event, data):
    """One server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def agentcc_headers(headers):
    """The gateway's own response headers: x-agentcc-* and guardrail ones."""
    return {
        key: value
        for key, value in dict(headers).items()
        if "guardrail" in key.lower() or "x-agentcc" in key.lower()
    }


def _outcome(status_code, model, headers, body=None):
    return {
        "status_code": status_code,
        "model": model,
        "guardrail_headers": agentcc_headers(headers),
        **playground_outcome(status_code, body or {}, headers),
    }


def _json_body(response):
    raw = response.read()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {"error": {"message": raw.decode("utf-8", "replace")[:500]}}


def _error_message(error):
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or "The gateway failed.")
    return str(error or "The gateway failed.")


def _chunks(response):
    """The JSON chunks of an OpenAI-style SSE stream, up to [DONE]."""
    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            yield json.loads(payload)
        except ValueError:
            continue


def _relay(response, model):
    streamed = "text/event-stream" in response.headers.get("content-type", "")
    if not (200 <= response.status_code < 300) or not streamed:
        body = _json_body(response)
        outcome = _outcome(response.status_code, model, response.headers, body)
        yield sse("result", {**outcome, "body": body})
        return
    outcome = _outcome(response.status_code, model, response.headers)
    yield sse("meta", outcome)
    usage, metadata, finish = {}, {}, None
    for chunk in _chunks(response):
        if chunk.get("error"):
            yield sse("error", {"message": _error_message(chunk["error"])})
            return
        for choice in chunk.get("choices") or []:
            content = (choice.get("delta") or {}).get("content")
            if content:
                yield sse("delta", {"content": content})
            finish = choice.get("finish_reason") or finish
        usage = chunk.get("usage") or usage
        metadata = chunk.get("agentcc_metadata") or metadata
    yield sse(
        "done",
        {
            **outcome,
            "usage": usage,
            "agentcc_metadata": metadata,
            "finish_reason": finish,
        },
    )


class PlaygroundEvents:
    """A closeable stream. The lock serializes reads and disconnect cleanup."""

    def __init__(self, client, *, model, request):
        self._client = client
        self._lock = threading.Lock()
        self._events = self._run(model, request)

    def __iter__(self):
        return self

    def __next__(self):
        with self._lock:
            return next(self._events)

    def close(self):
        with self._lock:
            self._events.close()

    def _run(self, model, request):
        client = self._client
        try:
            with client.stream_delegated_chat_completion(
                request["delegation"],
                prompt=request["prompt"],
                model=model,
                system_prompt=request["system_prompt"],
                params=request["params"],
            ) as response:
                yield from _relay(response, model)
        except GatewayClientError as e:
            yield sse("error", {"message": str(e)})
        except Exception as e:  # the stream has started: say so in it
            logger.exception("test_playground_stream_error", error=str(e))
            yield sse("error", {"message": "The stream failed. Try again."})


def playground_events(client, *, model, request):
    """The request holds prompt, parameters, and a person delegation."""
    return PlaygroundEvents(client, model=model, request=request)
