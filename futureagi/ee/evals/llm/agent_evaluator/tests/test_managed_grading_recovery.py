"""Exercise grading recovery through the managed client and HTTP transport."""

import asyncio
import json
from functools import partial
from unittest.mock import AsyncMock

import httpx
import pytest
from structlog.testing import capture_logs

from ai_tools.base import ToolContext
from ee.evals.llm.agent_evaluator.evaluator import (
    USER_FACING_EVAL_FAILED,
    AgentEvaluator,
)
from ee.falcon_ai.llm_client import FalconLLMClient
from ee.licensing.activation_client import ManagedServiceError, ServiceToken

VALID = '{"result":"Pass","explanation":"Correct."}'
TRUNCATED = '{"result":"Pass","explanation":"Correct but'
TRANSIENT_FAILURES = [429, 503, "timeout", "connect"]


def terminal_code(failure):
    """A rate limit ends as rate_limited; other transient failures as an outage."""
    return "rate_limited" if failure == 429 else "upstream_unavailable"


def sse_content(content):
    chunk = {
        "choices": [{"delta": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    return ("data: " + json.dumps(chunk) + "\n\n").encode()


class InterruptedStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        # Even a complete-looking grade is unusable if the stream fails later.
        yield sse_content(VALID)
        raise httpx.ReadTimeout("synthetic interrupted stream")


@pytest.fixture(params=["cloud", "self_hosted"])
def managed_transport(monkeypatch, request):
    """Only HTTP upstream, credentials and deployment availability are substituted."""
    cloud = request.param == "cloud"
    monkeypatch.setattr("ee.usage.deployment.DeploymentMode.is_cloud", lambda: cloud)
    monkeypatch.setattr(
        "ee.licensing.managed_ai._cloud_gateway_credentials",
        lambda: ("https://judge.example.invalid", "synthetic-fixture-key"),
    )
    token = ServiceToken(
        access_token="synthetic-fixture-key",
        gateway_url="https://judge.example.invalid",
        expires_at=float("inf"),
        allowed_services=["falcon_ai"],
        allowed_models=["gpt-4o-mini"],
        scope="enterprise",
    )
    monkeypatch.setattr(
        "ee.licensing.activation_client.get_service_token", lambda: token
    )
    monkeypatch.setattr(
        AgentEvaluator, "_managed_ai_available", staticmethod(lambda: True)
    )
    monkeypatch.setattr(
        AgentEvaluator,
        "_build_tool_context",
        lambda _: ToolContext(user=None, organization=None, workspace=None),
    )
    sleep = AsyncMock()
    monkeypatch.setattr("ee.falcon_ai.llm_client.asyncio.sleep", sleep)
    original_client = httpx.AsyncClient

    def configure(responses):
        calls = []

        async def upstream(request):
            response = responses[min(len(calls), len(responses) - 1)]
            calls.append(json.loads(request.content))
            if response == "timeout":
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            if response == "connect":
                raise httpx.ConnectError(
                    "synthetic connection failure", request=request
                )
            if isinstance(response, int):
                return httpx.Response(
                    response,
                    headers={"Retry-After": "0"},
                    json={"error": {"message": "synthetic upstream failure"}},
                )
            if response == "partial":
                return httpx.Response(200, stream=InterruptedStream())
            return httpx.Response(
                200, content=sse_content(response) + b"data: [DONE]\n\n"
            )

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            partial(original_client, transport=httpx.MockTransport(upstream)),
        )
        return calls, sleep

    return configure


def evaluate():
    evaluator = AgentEvaluator(
        rule_prompt="Check that two plus two is four.",
        model="gpt-4o-mini",
        agent_mode="quick",
        output_type="Pass/Fail",
    )
    result = error = None
    with capture_logs() as logs:
        try:
            result = evaluator.run()
        except ValueError as exc:
            error = exc
    return evaluator, result, error, logs


@pytest.mark.parametrize("failure", TRANSIENT_FAILURES)
def test_managed_transient_failure_recovers_within_shared_budget(
    managed_transport, failure
):
    calls, sleep = managed_transport([failure, VALID])
    evaluator, result, error, logs = evaluate()
    assert error is None
    assert len(calls) == 2
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert evaluator.token_usage["total_tokens"] == 8
    sleep.assert_awaited_once()
    assert 0 <= sleep.await_args.args[0] <= 30
    assert not [event for event in logs if event.get("log_level") == "error"]


@pytest.mark.parametrize("failure", TRANSIENT_FAILURES)
def test_managed_transient_retry_exhaustion_is_ungraded(managed_transport, failure):
    calls, sleep = managed_transport([failure])
    _, result, error, logs = evaluate()
    assert result is None
    assert len(calls) == 2
    assert str(error) == USER_FACING_EVAL_FAILED
    assert error.code == terminal_code(failure)
    assert isinstance(error.__cause__, ManagedServiceError)
    terminal = [event for event in logs if event.get("log_level") == "error"]
    assert len(terminal) == 1
    assert terminal[0]["event"] == "agent_evaluator_error"
    assert terminal[0]["managed_error_code"] == error.__cause__.code
    sleep.assert_awaited_once()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_managed_deterministic_rejection_does_not_retry(managed_transport, status):
    calls, sleep = managed_transport([status, VALID])
    _, result, error, logs = evaluate()
    assert result is None
    assert len(calls) == 1
    assert str(error) == USER_FACING_EVAL_FAILED
    assert error.code == "upstream_rejected"
    assert isinstance(error.__cause__, (ManagedServiceError, httpx.HTTPStatusError))
    assert len([event for event in logs if event.get("log_level") == "error"]) == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize("failure", TRANSIENT_FAILURES)
def test_managed_transport_retry_consumes_structured_recovery_budget(
    managed_transport, failure
):
    calls, _ = managed_transport([failure, TRUNCATED, VALID])
    _, result, error, _ = evaluate()
    assert len(calls) == 2
    assert result is None
    assert error.code == "invalid_output"


@pytest.mark.parametrize("failure", TRANSIENT_FAILURES)
def test_managed_structured_recovery_does_not_get_a_transport_retry(
    managed_transport, failure
):
    calls, sleep = managed_transport([TRUNCATED, failure, VALID])
    evaluator, result, error, _ = evaluate()
    assert len(calls) == 2
    assert not calls[1].get("tools")
    assert calls[1]["response_format"] == calls[0]["response_format"]
    assert result is None
    assert error.code == terminal_code(failure)
    assert evaluator.token_usage["total_tokens"] == 8
    sleep.assert_not_awaited()


def test_managed_interrupted_stream_cannot_be_graded_or_retried(managed_transport):
    calls, sleep = managed_transport(["partial", VALID])
    evaluator, result, error, logs = evaluate()
    assert len(calls) == 1
    assert result is None
    assert error.code == "upstream_unavailable"
    assert isinstance(error.__cause__, ManagedServiceError)
    assert isinstance(error.__cause__.__cause__, httpx.ReadTimeout)
    assert evaluator.token_usage["total_tokens"] == 8
    assert len([event for event in logs if event.get("log_level") == "error"]) == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize("failure", TRANSIENT_FAILURES)
def test_managed_chat_keeps_existing_error_propagation(managed_transport, failure):
    calls, sleep = managed_transport([failure, VALID])
    client = FalconLLMClient()

    async def consume():
        return [
            chunk
            async for chunk in client.stream_with_retry(
                [{"role": "user", "content": "Hello"}]
            )
        ]

    with pytest.raises(ManagedServiceError):
        asyncio.run(consume())
    assert len(calls) == 1
    sleep.assert_not_awaited()
