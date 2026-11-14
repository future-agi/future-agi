"""Exercise the complete managed chain with real HTTPX response contexts."""

import asyncio
import json
from contextlib import aclosing

import httpx
import pytest

from ee.falcon_ai import views_quick_analysis
from ee.falcon_ai.llm_client import FalconLLMClient
from ee.licensing import activation_client, managed_ai
from ee.usage.deployment import DeploymentMode

CONTENT = {"choices": [{"delta": {"content": "Analysis"}, "finish_reason": None}]}
STOP = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
METADATA = {
    "choices": [],
    "usage": {"total_tokens": 5},
    "agentcc_metadata": {"cost": 0.25},
}


class SSEBody(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False
        self.done = False
        self.stall = False
        self.malformed = False
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        for chunk in (CONTENT, STOP):
            yield ("data: " + json.dumps(chunk) + "\n\n").encode()
        if self.stall:
            self.waiting.set()
            await asyncio.Event().wait()
        if self.malformed:
            yield b"data: not-json\n\n"
        yield ("data: " + json.dumps(METADATA) + "\n\n").encode()
        self.done = True
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        self.closed = True


@pytest.fixture(params=[True, False], ids=["cloud", "licensed"])
def managed_transport(monkeypatch, request):
    body = SSEBody()
    clients = []
    real_client = httpx.AsyncClient

    def make_client(**kwargs):
        client = real_client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=body)),
            **kwargs,
        )
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    monkeypatch.setattr(DeploymentMode, "is_cloud", lambda: request.param)
    monkeypatch.setattr(
        managed_ai,
        "_cloud_gateway_credentials",
        lambda: ("http://gateway.example.test", "synthetic-key"),
    )
    monkeypatch.setattr(
        activation_client,
        "get_service_token",
        lambda: activation_client.ServiceToken(
            "synthetic-token",
            "http://gateway.example.test",
            9999999999,
            ["falcon"],
            ["falcon_ai"],
            "enterprise",
        ),
    )
    return body, clients


def assert_closed(body, clients):
    assert body.closed, "response must close before returning to the consumer"
    assert clients and all(client.is_closed for client in clients)


def test_quick_analysis_drains_metadata_before_loop_shutdown(
    managed_transport, monkeypatch
):
    body, clients = managed_transport
    client = FalconLLMClient()
    monkeypatch.setattr(views_quick_analysis, "FalconLLMClient", lambda: client)
    errors = []

    async def run():
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, ctx: errors.append(ctx)
        )
        result = await views_quick_analysis._run_analysis("Summarize this trace")
        assert result == "Analysis"
        assert body.done, "finish_reason is not the end of the SSE response"
        assert client._gateway_cost == 0.25
        assert_closed(body, clients)

    asyncio.run(run())
    assert not errors


@pytest.mark.parametrize("retry", [False, True])
def test_explicit_close_releases_nested_streams_before_loop_shutdown(
    managed_transport, retry
):
    body, clients = managed_transport
    errors = []

    async def run():
        asyncio.get_running_loop().set_exception_handler(
            lambda loop, ctx: errors.append(ctx)
        )
        client = FalconLLMClient()
        factory = client.stream_with_retry if retry else client.stream_completion
        async with aclosing(factory([])) as stream:
            assert await anext(stream) == CONTENT
        assert_closed(body, clients)
        assert not body.done

    asyncio.run(run())
    assert not errors


def test_cancellation_closes_managed_response(managed_transport):
    body, clients = managed_transport
    body.stall = True

    async def run():
        async def consume():
            async with aclosing(FalconLLMClient().stream_with_retry([])) as stream:
                async for _ in stream:
                    pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(body.waiting.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_closed(body, clients)

    asyncio.run(run())


def test_malformed_sse_closes_response_and_propagates(managed_transport):
    body, clients = managed_transport
    body.malformed = True

    async def run():
        with pytest.raises(json.JSONDecodeError):
            await views_quick_analysis._run_analysis("Summarize")
        assert_closed(body, clients)

    asyncio.run(run())


def test_quick_analysis_deadline_cancels_stalled_trailing_stream(
    managed_transport, monkeypatch
):
    body, clients = managed_transport
    body.stall = True
    real_timeout = asyncio.timeout
    deadlines = []

    def short_timeout(seconds):
        deadlines.append(seconds)
        return real_timeout(0.02)

    monkeypatch.setattr(views_quick_analysis.asyncio, "timeout", short_timeout)

    async def run():
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                views_quick_analysis._run_analysis("Summarize"), timeout=2
            )
        assert deadlines == [45]
        assert_closed(body, clients)

    asyncio.run(run())
