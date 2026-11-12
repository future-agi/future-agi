"""Grading failures must not become verdicts or replay an investigation."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from structlog.testing import capture_logs

from ai_tools.base import ToolContext, ToolResult
from ee.evals.llm.agent_evaluator.evaluator import (
    USER_FACING_EVAL_FAILED,
    AgentEvaluator,
)
from ee.falcon_ai.agent import AgentLoop
from ee.falcon_ai.llm_client import FalconLLMClient

TRUNCATED = '{"result": "Pass", "explanation": "The response is correct but'
VALID = '{"result": "Pass", "explanation": "The response is correct."}'


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("content", [TRUNCATED, "I think it failed.", "", None, "{}"])
def test_invalid_output_never_becomes_a_verdict(content, reverse):
    evaluator = AgentEvaluator(rule_prompt="Check correctness", output_type="Pass/Fail")
    evaluator._reverse_output = reverse
    with pytest.raises(ValueError, match="Evaluation failed") as exc:
        evaluator._build_result({"content": content}, 10)
    from ee.evals.llm.agent_evaluator.evaluator import GradingError

    assert isinstance(exc.value, GradingError)
    assert exc.value.code in {"invalid_output", "empty_output"}


@pytest.mark.parametrize("output_type", ["score", "numeric"])
@pytest.mark.parametrize("value", [None, "NaN", "Infinity", "-Infinity", "not a score"])
def test_invalid_numeric_output_has_no_score(output_type, value):
    evaluator = AgentEvaluator(rule_prompt="Check correctness", output_type=output_type)
    with pytest.raises(ValueError, match="Evaluation failed"):
        evaluator._build_result({"content": json.dumps({"result": value})}, 10)


@pytest.fixture
def drive(monkeypatch):
    """Real evaluator, AgentLoop and retry client; only the upstream is controlled."""
    monkeypatch.setattr("ee.falcon_ai.llm_client.asyncio.sleep", AsyncMock())

    def run(responses, *, reverse=False, with_tool=False):
        calls = []
        tool = SimpleNamespace(
            name="web_search",
            description="Read synthetic evidence",
            input_schema={"type": "object"},
        )
        execute_tool = AsyncMock(return_value=ToolResult(content="The answer is four."))
        evaluator = AgentEvaluator(
            rule_prompt="Check that the answer to two plus two is four.",
            model="turing_large",
            output_type="Pass/Fail",
            agent_mode="auto" if with_tool else "quick",
            check_internet=with_tool,
        )
        evaluator._reverse_output = reverse

        async def upstream(client, messages, tools=None):
            response = responses[min(len(calls), len(responses) - 1)]
            calls.append(
                {"tools": tools, "messages": messages, "format": client.response_format}
            )
            if response == "TOOL":
                yield {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "test-call",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
                return
            if isinstance(response, Exception):
                raise response
            if response == "NO_CHUNKS":
                return
            # Each response is either text, or text followed by a stream failure.
            content, error = (
                response if isinstance(response, tuple) else (response, None)
            )
            yield {
                "choices": [{"delta": {"content": content}, "finish_reason": "stop"}]
            }
            yield {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
            if error:
                raise error

        with (
            patch.object(FalconLLMClient, "stream_completion", upstream),
            patch.object(
                evaluator,
                "_build_tool_context",
                return_value=ToolContext(user=None, organization=None, workspace=None),
            ),
            patch.object(
                evaluator,
                "_build_llm_override",
                return_value={"provider": "openai", "model": "test-model"},
            ),
            patch("ai_tools.registry.registry.get", return_value=tool),
            patch.object(AgentLoop, "_execute_tool", execute_tool),
            capture_logs() as logs,
        ):
            result = error = None
            try:
                result = evaluator.run()
            except Exception as exc:
                error = exc
        if with_tool:
            execute_tool.assert_awaited_once()
        return evaluator, result, error, calls, logs

    return run


def http_error(status, retry_after="0"):
    request = httpx.Request("POST", "https://judge.example.invalid/v1/chat/completions")
    response = httpx.Response(
        status,
        request=request,
        headers={"Retry-After": retry_after, "x-agentcc-request-id": "test-request"},
    )
    return httpx.HTTPStatusError(
        "upstream rejected request", request=request, response=response
    )


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_deterministic_http_failure_is_one_request_and_one_terminal_event(
    drive, status
):
    _, result, error, calls, logs = drive([http_error(status)])
    assert result is None
    assert len(calls) == 1
    assert str(error) == USER_FACING_EVAL_FAILED
    assert error.code == "upstream_rejected"
    assert isinstance(error.__cause__, httpx.HTTPStatusError)
    terminal = [e for e in logs if e.get("log_level") == "error"]
    assert len(terminal) == 1
    assert terminal[0]["event"] == "agent_evaluator_error"
    assert terminal[0]["upstream_status_code"] == status
    assert terminal[0]["gateway_request_id"] == "test-request"
    assert not any("empty_after_all_retries" in e["event"] for e in logs)


@pytest.mark.parametrize(
    "failure", [http_error(429), http_error(503), httpx.ReadTimeout("timeout")]
)
def test_transient_error_recovers_once_without_replaying_agent(drive, failure):
    evaluator, result, error, calls, logs = drive([failure, VALID])
    assert error is None
    assert len(calls) == 2
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert evaluator.token_usage["total_tokens"] == 8
    assert not [e for e in logs if e.get("log_level") == "error"]


@pytest.mark.parametrize(
    "failure",
    [http_error(429), http_error(503), httpx.ReadTimeout("timeout"), "NO_CHUNKS"],
)
def test_exhausted_transport_retry_does_not_restart_agent(drive, failure):
    _, result, error, calls, logs = drive([failure])
    assert result is None
    assert error is not None
    assert len(calls) == 2
    assert len([e for e in logs if e.get("log_level") == "error"]) == 1


# A provider's 429 on a bring-your-own-key judge follows the managed policy: a
# wait of up to one gateway window is sat out in full before the one retry.
@pytest.mark.parametrize("retry_after", ["45", "61"])
def test_rate_limit_retry_waits_the_full_retry_after(drive, monkeypatch, retry_after):
    sleep = AsyncMock()
    monkeypatch.setattr("ee.falcon_ai.llm_client.asyncio.sleep", sleep)
    _, result, error, calls, _ = drive(
        [http_error(429, retry_after=retry_after), VALID]
    )
    assert error is None
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert len(calls) == 2
    sleep.assert_awaited_once_with(float(retry_after))


@pytest.mark.parametrize("retry_after", ["62", "3600"])
def test_rate_limit_longer_than_a_window_fails_fast(drive, monkeypatch, retry_after):
    sleep = AsyncMock()
    monkeypatch.setattr("ee.falcon_ai.llm_client.asyncio.sleep", sleep)
    evaluator, result, error, calls, logs = drive(
        [http_error(429, retry_after=retry_after), VALID]
    )
    assert result is None
    assert len(calls) == 1
    sleep.assert_not_awaited()
    assert str(error) == USER_FACING_EVAL_FAILED
    assert error.code == "rate_limited"
    assert error.diagnostics["retry_after_seconds"] == float(retry_after)
    terminal = [e for e in logs if e.get("log_level") == "error"]
    assert len(terminal) == 1
    assert terminal[0]["error_code"] == "rate_limited"
    assert terminal[0]["retry_after_seconds"] == float(retry_after)
    assert terminal[0]["upstream_status_code"] == 429
    assert evaluator.token_usage["total_tokens"] == 0


def test_chat_rate_limit_keeps_its_own_ceiling(monkeypatch):
    """Chat has no single recovery to protect, so it keeps retrying at 30 s."""
    sleep = AsyncMock()
    monkeypatch.setattr("ee.falcon_ai.llm_client.asyncio.sleep", sleep)
    calls = []

    async def upstream(client, messages, tools=None):
        calls.append(messages)
        if len(calls) == 1:
            raise http_error(429, retry_after="3600")
        yield {"choices": [{"delta": {"content": "Hello"}, "finish_reason": "stop"}]}

    client = FalconLLMClient()

    async def consume():
        return [chunk async for chunk in client.stream_with_retry([])]

    with patch.object(FalconLLMClient, "stream_completion", upstream):
        chunks = asyncio.run(consume())
    assert len(chunks) == 1
    assert len(calls) == 2
    sleep.assert_awaited_once_with(30.0)


@pytest.mark.parametrize("content", [TRUNCATED, "", "not a verdict"])
def test_one_structured_retry_preserves_input_and_usage(drive, content):
    evaluator, result, error, calls, _ = drive([content, VALID])
    assert error is None
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert len(calls) == 2
    assert calls[1]["tools"] is None
    assert calls[1]["format"] == calls[0]["format"]
    assert calls[0]["messages"][1]["content"] == calls[1]["messages"][1]["content"]
    assert evaluator.token_usage["total_tokens"] == 16


@pytest.mark.parametrize("reverse", [False, True])
def test_exhausted_structured_retry_is_ungraded(drive, reverse):
    evaluator, result, error, calls, logs = drive([TRUNCATED], reverse=reverse)
    assert result is None
    assert error.code == "invalid_output"
    assert len(calls) == 2
    assert evaluator.token_usage["total_tokens"] == 16
    assert len([e for e in logs if e.get("log_level") == "error"]) == 1


def test_transport_and_structured_recovery_share_budget(drive):
    _, result, error, calls, _ = drive([http_error(503), TRUNCATED, VALID])
    assert result is None
    assert error.code == "invalid_output"
    assert len(calls) == 2


def test_partial_stream_failure_never_uses_partial_verdict_or_retries(drive):
    evaluator, result, error, calls, logs = drive(
        [(VALID, httpx.ReadTimeout("interrupted"))]
    )
    assert result is None
    assert error is not None
    assert len(calls) == 1
    assert evaluator.token_usage["total_tokens"] == 8
    assert len([e for e in logs if e.get("log_level") == "error"]) == 1


@pytest.mark.parametrize("response", ["", TRUNCATED])
def test_finalization_does_not_repeat_completed_tool_calls(drive, response):
    _, result, error, calls, _ = drive(["TOOL", response, VALID], with_tool=True)
    assert error is None
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert len(calls) == 3
    assert calls[-1]["tools"] is None


def test_transient_recovery_does_not_repeat_completed_tool_calls(drive):
    _, result, error, calls, _ = drive(["TOOL", http_error(503), VALID], with_tool=True)
    assert error is None
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert len(calls) == 3


def test_finalization_transport_failure_does_not_get_another_retry(drive):
    _, result, error, calls, _ = drive([TRUNCATED, http_error(503), VALID])
    assert result is None
    assert error.code == "upstream_unavailable"
    assert len(calls) == 2


def test_empty_successful_stream_exhausts_one_structured_retry(drive):
    _, result, error, calls, _ = drive([""])
    assert result is None
    assert error.code == "empty_output"
    assert len(calls) == 2


def test_grading_error_does_not_lower_simulation_pass_rate(drive):
    from simulate.utils.eval_summary import (
        _calculate_pass_fail_stats,
        _extract_config_outputs,
    )

    _, result, error, _, _ = drive([TRUNCATED])
    assert result is None
    assert error.code == "invalid_output"
    config = SimpleNamespace(id="synthetic-eval", name="Correctness")
    # The simulation exception handler stores this error/null shape. It must
    # not participate in either the numerator or denominator of pass rate.
    calls = [
        SimpleNamespace(
            id="valid",
            eval_outputs={
                config.id: {
                    "output": "Passed",
                    "output_type": "Pass/Fail",
                    "status": "completed",
                }
            },
        ),
        SimpleNamespace(
            id="ungraded",
            eval_outputs={
                config.id: {
                    "output": None,
                    "output_type": "Pass/Fail",
                    "status": "failed",
                    "error": "error",
                    "reason": str(error),
                }
            },
        ),
    ]
    outputs = _extract_config_outputs(config, calls)
    summary = _calculate_pass_fail_stats(config, outputs)
    assert summary["total_cells"] == 1
    assert summary["output"]["pass"] == 100
    assert summary["output"]["fail_count"] == 0
