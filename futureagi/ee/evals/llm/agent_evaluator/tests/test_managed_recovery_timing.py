"""Managed recovery with time-dependent gateways and real elapsed HTTP timeouts.

Only credentials, deployment selection and the synthetic HTTP peer are replaced;
the evaluator, agent, retry budget and managed dispatch all run normally.
"""

import asyncio
import concurrent.futures
import json
import math
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from ai_tools.base import ToolContext
from ee.evals.llm.agent_evaluator import evaluator as evaluator_module
from ee.evals.llm.agent_evaluator.evaluator import (
    AGENT_RUN_TIMEOUT_SECONDS,
    AgentEvaluator,
    GradingError,
)
from ee.falcon_ai import llm_client
from ee.licensing.activation_client import (
    MANAGED_STREAM_TIMEOUT_SECONDS,
    MAX_RETRY_AFTER_SECONDS,
    ServiceToken,
)

VALID = '{"result":"Pass","explanation":"Correct."}'
SCALE = 0.003  # 300 seconds becomes 900 ms, without faking httpcore's timers.
# Agent setup, loopback connects and thread hops cost the same at any SCALE, so
# the scaled run deadline keeps a fixed allowance for them. Without it the 30 s
# of production slack shrinks to ~90 ms and a busy CI runner fails the run on
# time rather than on the behaviour under test. Each test asserts the
# production budget itself exactly, not through the padded wall clock.
UNSCALED_OVERHEAD_ALLOWANCE = 1.0


def sse(content=VALID):
    chunk = {
        "choices": [{"delta": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    return ("data: " + json.dumps(chunk) + "\n\n").encode()


def valid_response():
    return httpx.Response(200, content=sse() + b"data: [DONE]\n\n")


@pytest.fixture(params=["cloud", "self_hosted"])
def managed(monkeypatch, request):
    peer = SimpleNamespace(
        url="https://judge.example.invalid",
        calls=[],
        sleeps=[],
        clock=0.0,
        original_client=httpx.AsyncClient,
        original_sleep=asyncio.sleep,
    )
    monkeypatch.setattr(
        "ee.usage.deployment.DeploymentMode.is_cloud",
        lambda: request.param == "cloud",
    )
    monkeypatch.setattr(
        "ee.licensing.managed_ai._cloud_gateway_credentials",
        lambda: (peer.url, "synthetic-fixture-key"),
    )
    monkeypatch.setattr(
        "ee.licensing.activation_client.get_service_token",
        lambda: ServiceToken(
            access_token="synthetic-fixture-key",
            gateway_url=peer.url,
            expires_at=float("inf"),
            allowed_services=["falcon_ai"],
            allowed_models=["gpt-4o-mini"],
            scope="enterprise",
        ),
    )
    monkeypatch.setattr(
        AgentEvaluator, "_managed_ai_available", staticmethod(lambda: True)
    )
    monkeypatch.setattr(
        AgentEvaluator,
        "_build_tool_context",
        lambda _: ToolContext(user=None, organization=None, workspace=None),
    )
    peer.evaluator = AgentEvaluator(
        rule_prompt="Check that two plus two is four.",
        model="gpt-4o-mini",
        agent_mode="quick",
        output_type="Pass/Fail",
    )

    async def sleep(delay):
        peer.sleeps.append(delay)
        peer.clock += delay

    # Do not patch asyncio.sleep globally: httpx/anyio must keep real timers.
    proxy = SimpleNamespace(**vars(asyncio))
    proxy.sleep = sleep
    monkeypatch.setattr(llm_client, "asyncio", proxy)

    def upstream(handler):
        async def record(req):
            peer.calls.append(json.loads(req.content))
            return await handler(req)

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            partial(peer.original_client, transport=httpx.MockTransport(record)),
        )

    peer.upstream = upstream
    return peer


def test_retry_after_waits_for_upstream_reset(managed):
    request_times = []

    async def upstream(request):
        request_times.append(managed.clock)
        # Readiness depends on elapsed time, not the number of attempts.
        if managed.clock < 2:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return valid_response()

    managed.upstream(upstream)
    result = managed.evaluator.run()
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert request_times == [0, 2]
    assert managed.sleeps == [2]
    assert managed.evaluator.token_usage["total_tokens"] == 8


def rate_window(managed, reset):
    """A gateway request window: refused with an honest Retry-After until it
    reopens at t=reset, then served. Returns the times requests arrived."""
    request_times = []

    async def upstream(request):
        request_times.append(managed.clock)
        if managed.clock < reset:
            return httpx.Response(
                429,
                headers={"Retry-After": str(math.ceil(reset - managed.clock))},
                json={"error": {"type": "rate_limit_error"}},
            )
        return valid_response()

    managed.upstream(upstream)
    return request_times


# A gateway's request windows are a minute long, and its Redis sliding window
# admits the next request one second after the boundary, so a refusal early in
# a window names up to 61 s. Waiting less retries inside the window that
# refused it and spends the one recovery on a second 429.
@pytest.mark.parametrize("reset", [31, 45, 60, 61])
def test_rate_limit_waits_until_the_window_reopens(managed, reset):
    request_times = rate_window(managed, reset)
    result = managed.evaluator.run()
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert request_times == [0, reset]
    assert managed.sleeps == [reset]
    assert managed.evaluator.token_usage["total_tokens"] == 8


# Past one window the wait is a quota or a provider back-off, not a window the
# run should sit out: it fails at once, unscored, with the recovery unspent.
@pytest.mark.parametrize("retry_after", [62, 120, 3600])
def test_rate_limit_longer_than_a_window_fails_fast_unscored(managed, retry_after):
    request_times = rate_window(managed, retry_after)
    with pytest.raises(GradingError) as exc:
        managed.evaluator.run()
    assert exc.value.code == "rate_limited"
    assert exc.value.diagnostics["retry_after_seconds"] == retry_after
    assert request_times == [0]
    assert managed.sleeps == []
    assert managed.evaluator.token_usage["total_tokens"] == 0
    assert MAX_RETRY_AFTER_SECONDS < retry_after


@pytest.mark.parametrize("failure", [httpx.RemoteProtocolError, httpx.ReadError])
@pytest.mark.parametrize("persistent", [False, True])
def test_pre_response_failure_uses_one_recovery(managed, failure, persistent):
    async def upstream(request):
        if persistent or len(managed.calls) == 1:
            raise failure("synthetic disconnect before response", request=request)
        return valid_response()

    managed.upstream(upstream)
    if persistent:
        with pytest.raises(GradingError) as exc:
            managed.evaluator.run()
        assert isinstance(exc.value.__cause__, failure)
        assert managed.evaluator.token_usage["total_tokens"] == 0
    else:
        result = managed.evaluator.run()
        assert result.eval_results[0]["data"]["result"] == "Pass"
        assert managed.evaluator.token_usage["total_tokens"] == 8
    assert len(managed.calls) == 2
    assert len(managed.sleeps) == 1


@pytest.mark.parametrize("failure", [httpx.RemoteProtocolError, httpx.ReadError])
def test_partial_stream_is_never_replayed_or_graded(managed, failure):
    class Interrupted(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse()  # Complete-looking JSON is still an interrupted verdict.
            raise failure("synthetic interruption after content")

    async def upstream(request):
        return httpx.Response(200, stream=Interrupted())

    managed.upstream(upstream)
    with pytest.raises(GradingError) as exc:
        managed.evaluator.run()
    assert isinstance(exc.value.__cause__, failure)
    assert len(managed.calls) == 1
    assert managed.sleeps == []
    assert managed.evaluator.token_usage["total_tokens"] == 8


@pytest.mark.parametrize("failure", [httpx.RemoteProtocolError, httpx.ReadError])
def test_connection_recovery_does_not_reset_invalid_output_budget(managed, failure):
    async def upstream(request):
        if len(managed.calls) == 1:
            raise failure("synthetic disconnect before response", request=request)
        return httpx.Response(
            200, content=sse('{"result":"Pass"') + b"data: [DONE]\n\n"
        )

    managed.upstream(upstream)
    with pytest.raises(GradingError) as exc:
        managed.evaluator.run()
    assert exc.value.code == "invalid_output"
    assert len(managed.calls) == 2
    assert len(managed.sleeps) == 1
    assert managed.evaluator.token_usage["total_tokens"] == 8


@pytest.mark.parametrize("failure", [httpx.RemoteProtocolError, httpx.ReadError])
def test_connection_failures_keep_chat_error_propagation(managed, failure):
    async def upstream(request):
        raise failure("synthetic disconnect before response", request=request)

    managed.upstream(upstream)
    client = llm_client.FalconLLMClient()

    async def consume():
        return [chunk async for chunk in client.stream_with_retry([])]

    with pytest.raises(failure):
        asyncio.run(consume())
    assert len(managed.calls) == 1
    assert managed.sleeps == []


@pytest.fixture
def live_gateway(managed, monkeypatch):
    """Scale application deadlines, but exercise actual loopback HTTP I/O."""
    trace = SimpleNamespace(
        hold_requests=1,
        http_timeouts=[],
        read_errors=[],
        completions=[],
        deadline_override=None,
        deadlines=[],
        workers=[],
    )
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            managed.calls.append(json.loads(body))
            number = len(managed.calls)
            if number <= trace.hold_requests:
                stop.wait(2)
                self.close_connection = True
                return
            content = sse() + b"data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            self.wfile.flush()
            trace.completions.append(number)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
    )
    thread.start()
    managed.url = f"http://127.0.0.1:{server.server_port}"

    class ScaledClient(managed.original_client):
        def __init__(self, *args, **kwargs):
            timeout = httpx.Timeout(kwargs["timeout"])
            trace.http_timeouts.append(timeout.read)
            kwargs["timeout"] = httpx.Timeout(
                **{
                    key: None if value is None else value * SCALE
                    for key, value in timeout.as_dict().items()
                }
            )
            kwargs["trust_env"] = False
            # Plain HTTP loopback needs no TLS; CA loading is not scaled and
            # would otherwise consume most of the reduced deadline margin.
            kwargs["verify"] = False
            super().__init__(*args, **kwargs)

        async def send(self, *args, **kwargs):
            started = time.monotonic()
            try:
                return await super().send(*args, **kwargs)
            except httpx.ReadTimeout:
                trace.read_errors.append(time.monotonic() - started)
                raise

    monkeypatch.setattr(httpx, "AsyncClient", ScaledClient)

    async def sleep(delay):
        managed.sleeps.append(delay)
        await managed.original_sleep(delay * SCALE)

    monkeypatch.setattr(llm_client.asyncio, "sleep", sleep)
    original_result = concurrent.futures.Future.result
    original_wait_for = asyncio.wait_for

    def scaled_deadline(timeout):
        trace.deadlines.append(timeout)
        return trace.deadline_override or (
            timeout * SCALE + UNSCALED_OVERHEAD_ALLOWANCE
        )

    def result(future, timeout=None):
        trace.workers.append(future)
        # Exercise the old caller deadline as well as the worker-owned fix.
        return original_result(
            future, timeout=None if timeout is None else scaled_deadline(timeout)
        )

    async def wait_for(awaitable, timeout):
        return await original_wait_for(awaitable, timeout=scaled_deadline(timeout))

    monkeypatch.setattr(concurrent.futures.Future, "result", result)
    proxy = SimpleNamespace(**vars(asyncio))
    proxy.wait_for = wait_for
    monkeypatch.setattr(evaluator_module, "asyncio", proxy)
    try:
        yield trace
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_elapsed_http_timeout_returns_recovered_verdict(managed, live_gateway):
    result = managed.evaluator.run()
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert len(managed.calls) == 2
    assert live_gateway.completions == [2]
    assert len(live_gateway.read_errors) == 1
    assert live_gateway.read_errors[0] >= live_gateway.http_timeouts[0] * SCALE * 0.9
    assert managed.evaluator.token_usage["total_tokens"] == 8
    assert all(worker.done() for worker in live_gateway.workers)
    # Only the worker's whole-run deadline bounds the run: a caller deadline of
    # one request timeout would cut recovery off after the first elapsed one.
    assert live_gateway.deadlines == [AGENT_RUN_TIMEOUT_SECONDS]


def test_fast_http_success_needs_no_recovery(managed, live_gateway):
    live_gateway.hold_requests = 0
    result = managed.evaluator.run()
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert len(managed.calls) == 1
    assert live_gateway.read_errors == []
    assert managed.sleeps == []


# A per-request delay stands in for a loaded CI runner: it adds wall-clock time
# that SCALE does not shrink.
@pytest.mark.parametrize("send_overhead", [0.0, 0.1])
def test_elapsed_http_timeout_exhaustion_is_ungraded(
    managed, live_gateway, monkeypatch, send_overhead
):
    scaled_client = httpx.AsyncClient

    class LoadedRunnerClient(scaled_client):
        async def send(self, *args, **kwargs):
            await asyncio.sleep(send_overhead)
            return await super().send(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", LoadedRunnerClient)
    live_gateway.hold_requests = 2
    with pytest.raises(GradingError) as exc:
        managed.evaluator.run()
    assert exc.value.code == "upstream_unavailable"
    assert len(managed.calls) == 2
    assert len(live_gateway.read_errors) == 2
    assert live_gateway.completions == []
    assert managed.evaluator.token_usage["total_tokens"] == 0
    # Production budget: two full request timeouts plus the longest backoff
    # the client accepts fit inside the run deadline the worker asked for.
    assert live_gateway.deadlines == [AGENT_RUN_TIMEOUT_SECONDS]
    assert live_gateway.http_timeouts == [MANAGED_STREAM_TIMEOUT_SECONDS] * 2
    assert sum(managed.sleeps) <= MAX_RETRY_AFTER_SECONDS
    assert AGENT_RUN_TIMEOUT_SECONDS >= (
        sum(live_gateway.http_timeouts) + MAX_RETRY_AFTER_SECONDS
    )


def test_deadline_cancels_rate_limit_backoff(managed, live_gateway, monkeypatch):
    live_gateway.deadline_override = 0.15

    async def upstream(request):
        if len(managed.calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "30"})
        return valid_response()

    async def sleep(delay):
        managed.sleeps.append(delay)
        await managed.original_sleep(0.3)

    managed.upstream(upstream)
    monkeypatch.setattr(llm_client.asyncio, "sleep", sleep)
    with pytest.raises(GradingError):
        managed.evaluator.run()
    assert len(managed.calls) == 1
    assert managed.sleeps == [30]
    assert all(worker.done() for worker in live_gateway.workers)
    assert managed.evaluator.token_usage["total_tokens"] == 0


def test_overall_deadline_cancels_worker_before_recovery(managed, live_gateway):
    live_gateway.deadline_override = 0.15
    with pytest.raises(GradingError) as exc:
        managed.evaluator.run()
    assert exc.value.code == "upstream_error"
    assert len(managed.calls) == 1
    assert managed.sleeps == []
    assert live_gateway.completions == []
    assert all(worker.done() for worker in live_gateway.workers)
    assert managed.evaluator.token_usage["total_tokens"] == 0
