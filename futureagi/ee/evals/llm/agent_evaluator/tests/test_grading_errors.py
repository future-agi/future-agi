"""Grading failures must not become verdicts or replay an investigation."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

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
            interim = None
            if isinstance(response, tuple) and response[0] == "TOOL":
                # A turn that writes text and then calls a tool.
                interim = response[1]
                response = "TOOL"
            if interim:
                yield {"choices": [{"delta": {"content": interim}}]}
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


FAIL_VERDICT = '{"result": "Fail", "explanation": "Not checked yet."}'


def test_interim_verdict_is_not_graded_when_the_final_answer_is_invalid(drive):
    """Turn 1 writes a Pass and calls a tool; the final answer is truncated.

    The loop joins every turn's text, so the interim Pass used to be graded.
    """
    _, result, error, calls, _ = drive(
        [("TOOL", VALID), TRUNCATED, TRUNCATED], with_tool=True
    )
    assert result is None
    assert error.code == "invalid_output"
    # The interim turn, the truncated final answer, one structured recovery.
    assert len(calls) == 3


def test_final_answer_is_graded_over_an_interim_verdict(drive):
    _, result, error, calls, _ = drive([("TOOL", FAIL_VERDICT), VALID], with_tool=True)
    assert error is None
    assert result.eval_results[0]["data"]["result"] == "Pass"
    assert len(calls) == 2


def test_chat_rate_limit_ignores_an_http_date_retry_after(monkeypatch):
    """Chat honours only seconds; an HTTP date keeps its normal backoff."""
    sleep = AsyncMock()
    monkeypatch.setattr("ee.falcon_ai.llm_client.asyncio.sleep", sleep)
    monkeypatch.setattr("ee.falcon_ai.llm_client.random.random", lambda: 0.0)
    calls = []
    in_an_hour = format_datetime(datetime.now(UTC) + timedelta(hours=1), usegmt=True)

    async def upstream(client, messages, tools=None):
        calls.append(messages)
        if len(calls) == 1:
            raise http_error(429, retry_after=in_an_hour)
        yield {"choices": [{"delta": {"content": "Hello"}, "finish_reason": "stop"}]}

    client = FalconLLMClient()

    async def consume():
        return [chunk async for chunk in client.stream_with_retry([])]

    with patch.object(FalconLLMClient, "stream_completion", upstream):
        chunks = asyncio.run(consume())
    assert len(chunks) == 1
    assert len(calls) == 2
    sleep.assert_awaited_once_with(1.0)


FLASH_SAFE = "Harmful request: no\nResponse refusal: n/a\nHarmful response: n/a"
FLASH_HARMFUL = "Harmful request: yes\nResponse refusal: n/a\nHarmful response: n/a"

# All of these match the parser's three-line structure. Their unknown tokens
# must be rejected as grades, without changing the runtime's legacy labels.
FLASH_INVALID_STRUCTURED_VERDICTS = [
    pytest.param(
        "Harmful request: maybe\nResponse refusal: n/a\nHarmful response: n/a",
        "Passed",
        id="unknown-request",
    ),
    pytest.param(
        "Harmful request: n/a\nResponse refusal: n/a\nHarmful response: n/a",
        "Passed",
        id="request-cannot-be-na",
    ),
    pytest.param(
        "Harmful request: \nResponse refusal: n/a\nHarmful response: n/a",
        "Passed",
        id="empty-request",
    ),
    pytest.param(
        "Harmful request: no\nResponse refusal: maybe\nHarmful response: n/a",
        "Passed",
        id="unknown-refusal",
    ),
    pytest.param(
        "Harmful request: yes\nResponse refusal: n/a\nHarmful response: maybe",
        "Failed",
        id="unknown-response",
    ),
    pytest.param(
        "Harmful request: no\nResponse refusal: \nHarmful response: n/a",
        "Passed",
        id="empty-refusal",
    ),
    pytest.param(
        "Harmful request: yes\nResponse refusal: n/a\nHarmful response: ",
        "Failed",
        id="empty-response",
    ),
    pytest.param(
        "Harmful request: no.\nResponse refusal: n/a\nHarmful response: n/a",
        "Passed",
        id="punctuation-is-not-normalized",
    ),
    pytest.param(
        "Harmful request: yes\nResponse refusal: n/a\nHarmful response: n / a",
        "Failed",
        id="internal-whitespace-is-not-normalized",
    ),
]
FLASH_VALID_STRUCTURED_VERDICTS = [
    pytest.param(FLASH_SAFE, "Passed", id="safe"),
    pytest.param(FLASH_HARMFUL, "Failed", id="harmful"),
    pytest.param(
        "Harmful request: no\nResponse refusal: yes\nHarmful response: no",
        "Passed",
        id="safe-with-response",
    ),
    pytest.param(
        "Harmful request: yes\nResponse refusal: no\nHarmful response: yes",
        "Failed",
        id="harmful-with-response",
    ),
    pytest.param(
        "Harmful request:\t No \t\r\n"
        "Response refusal:\tN/A \t\r\nHarmful response:\tn/A \t\r\n",
        "Passed",
        id="safe-case-and-whitespace",
    ),
    pytest.param(
        "Harmful request:\tYeS \t\r\n"
        "Response refusal:\tNo \t\r\nHarmful response:\tYES \t\r\n",
        "Failed",
        id="harmful-case-and-whitespace",
    ),
]


def _protect_response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None,
    )


def _run_protect(call_type, content, *, protect_runtime=False, reverse=False):
    """Enter through run(), with only the gateway response replaced."""
    evaluator = AgentEvaluator(
        rule_prompt="Toxicity",
        model=call_type,
        output_type="Pass/Fail",
        reverse_output=reverse,
    )
    with patch("agentic_eval.core.llm.llm.LLM") as llm:
        llm.return_value._try_gateway_completion.return_value = _protect_response(
            content
        )
        inputs = {"eval_name": "Toxicity", "input": "hello", "call_type": call_type}
        result = (
            evaluator.run_protect_runtime(inputs)
            if protect_runtime
            else evaluator.run(**inputs)
        )
        return result.eval_results[0]


@pytest.mark.parametrize("call_type", ["protect", "protect_flash"])
@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("", "empty_output"),
        (None, "empty_output"),
        ("I cannot classify this.", "invalid_output"),
        ("<label>maybe</label>", "invalid_output"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_unreadable_protect_output_is_ungraded(call_type, content, code, reverse):
    from ee.evals.llm.agent_evaluator.evaluator import GradingError

    with capture_logs() as logs, pytest.raises(GradingError) as exc:
        _run_protect(call_type, content, reverse=reverse)
    assert exc.value.code == code
    terminal = [event for event in logs if event.get("log_level") == "error"]
    assert len(terminal) == 1
    assert terminal[0]["event"] == "agent_evaluator_error"
    assert terminal[0]["error_code"] == code


@pytest.mark.parametrize(
    ("call_type", "content", "label"),
    [
        ("protect", "<label>Passed</label><explanation>Fine.</explanation>", "Passed"),
        ("protect", "<label>Failed</label><explanation>Toxic.</explanation>", "Failed"),
        ("protect_flash", FLASH_SAFE, "Passed"),
        ("protect_flash", FLASH_HARMFUL, "Failed"),
    ],
)
@pytest.mark.parametrize("protect_runtime", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_readable_protect_output_is_graded(
    call_type, content, label, protect_runtime, reverse
):
    result = _run_protect(
        call_type, content, protect_runtime=protect_runtime, reverse=reverse
    )
    assert result["output"] == label
    assert result["failure"] is ((label == "Failed") != reverse)


@pytest.mark.parametrize(
    ("call_type", "label"), [("protect", "Failed"), ("protect_flash", "Passed")]
)
@pytest.mark.parametrize("content", ["", None, "garbage", "<label>maybe</label>"])
def test_protect_runtime_run_keeps_parse_defaults(call_type, content, label):
    with capture_logs() as logs:
        result = _run_protect(call_type, content, protect_runtime=True)
    assert result["output"] == label
    assert result["failure"] is (label == "Failed")
    assert not [event for event in logs if event.get("log_level") == "error"]


@pytest.mark.parametrize(("content", "label"), FLASH_INVALID_STRUCTURED_VERDICTS)
@pytest.mark.parametrize("protect_runtime", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_flash_structured_verdict_invalid_tokens_require_runtime_context(
    content, label, protect_runtime, reverse
):
    from ee.evals.llm.agent_evaluator.evaluator import GradingError

    with capture_logs() as logs:
        if protect_runtime:
            result = _run_protect(
                "protect_flash", content, protect_runtime=True, reverse=reverse
            )
            assert result["output"] == label
            assert result["failure"] is ((label == "Failed") != reverse)
        else:
            with pytest.raises(GradingError) as exc:
                _run_protect("protect_flash", content, reverse=reverse)
            assert exc.value.code == "invalid_output"

    terminal = [event for event in logs if event.get("log_level") == "error"]
    if protect_runtime:
        assert not terminal
    else:
        assert len(terminal) == 1
        assert terminal[0]["event"] == "agent_evaluator_error"
        assert terminal[0]["error_code"] == "invalid_output"


@pytest.mark.parametrize(("content", "label"), FLASH_VALID_STRUCTURED_VERDICTS)
@pytest.mark.parametrize("protect_runtime", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_flash_structured_verdict_recognized_tokens_are_graded(
    content, label, protect_runtime, reverse
):
    with capture_logs() as logs:
        result = _run_protect(
            "protect_flash", content, protect_runtime=protect_runtime, reverse=reverse
        )
    assert result["output"] == label
    assert result["failure"] is ((label == "Failed") != reverse)
    assert not [event for event in logs if event.get("log_level") == "error"]


@pytest.fixture
def protect_engine(monkeypatch):
    """Real run_eval/run/formatting; replace DB-backed instance resolution only."""
    from evaluations.engine import runner

    template = SimpleNamespace(
        id="synthetic-protect-template",
        name="Toxicity",
        config={
            "eval_type_id": "AgentEvaluator",
            "output": "Pass/Fail",
            "required_keys": ["input"],
        },
        choice_scores={},
        choices=[],
        pass_threshold=0.5,
        organization=None,
        workspace=None,
    )

    def create_instance(*, model, config, **kwargs):
        evaluator = AgentEvaluator(
            rule_prompt="Toxicity",
            model=model,
            output_type="Pass/Fail",
            **config,
        )
        return evaluator, None

    monkeypatch.setattr(runner, "create_eval_instance", create_instance)
    monkeypatch.setattr(
        "evaluations.engine.params._get_few_shot_examples", Mock(return_value=[])
    )
    return template


@pytest.mark.parametrize("call_type", ["protect", "protect_flash"])
@pytest.mark.parametrize("skip_params_preparation", [False, True])
@pytest.mark.parametrize("mapped_value", [True, "mapped-runtime-value", False])
@pytest.mark.parametrize("protect_runtime", [False, True])
def test_protect_engine_context_is_independent_of_runtime_named_input(
    protect_engine, call_type, skip_params_preparation, mapped_value, protect_runtime
):
    from ee.evals.llm.agent_evaluator.evaluator import GradingError
    from evaluations.engine import EvalRequest, run_eval

    required_keys = ["input", "protect_runtime"]
    protect_engine.config["required_keys"] = required_keys
    inputs = {
        "input": "hello",
        "call_type": call_type,
        "protect_runtime": mapped_value,
        "required_keys": required_keys,
    }
    request = EvalRequest(
        eval_template=protect_engine,
        inputs=inputs,
        # Neither generic constructor config nor mapped data identifies
        # an SDK runtime caller. Only the separate request field does.
        config_overrides={"protect_runtime": not protect_runtime},
        skip_params_preparation=skip_params_preparation,
        protect_runtime=protect_runtime,
    )
    with (
        patch("agentic_eval.core.llm.llm.LLM") as llm,
        patch.object(
            AgentEvaluator,
            "_render_prompt",
            autospec=True,
            side_effect=AgentEvaluator._render_prompt,
        ) as render,
    ):
        llm.return_value._try_gateway_completion.return_value = _protect_response(
            "garbage"
        )
        if protect_runtime:
            result = run_eval(request)
            assert result.value == (
                "Passed" if call_type == "protect_flash" else "Failed"
            )
        else:
            with pytest.raises(GradingError) as exc:
                run_eval(request)
            assert exc.value.code == "invalid_output"
        llm.return_value._try_gateway_completion.assert_called_once()

    render.assert_called_once()
    assert render.call_args.args[2]["protect_runtime"] == mapped_value
    assert inputs["protect_runtime"] == mapped_value


@pytest.mark.parametrize("call_type", ["protect", "protect_flash"])
@pytest.mark.parametrize("unavailable", [False, True])
def test_protect_runtime_context_does_not_leak_to_next_run(call_type, unavailable):
    from ee.evals.llm.agent_evaluator.evaluator import GradingError

    evaluator = AgentEvaluator(rule_prompt="Toxicity", model=call_type)
    inputs = {"input": "hello", "call_type": call_type, "protect_runtime": True}
    with patch("agentic_eval.core.llm.llm.LLM") as llm:
        completion = llm.return_value._try_gateway_completion
        if unavailable:
            completion.return_value = None
            with pytest.raises(ValueError, match="gateway unavailable"):
                evaluator.run_protect_runtime(inputs)
        else:
            completion.return_value = _protect_response("garbage")
            evaluator.run_protect_runtime(inputs)
        completion.return_value = _protect_response("garbage")
        with pytest.raises(GradingError) as exc:
            evaluator.run(**inputs)
    assert exc.value.code == "invalid_output"
    assert inputs == {"input": "hello", "call_type": call_type, "protect_runtime": True}


@pytest.mark.parametrize("call_type", ["protect", "protect_flash"])
def test_protect_runtime_context_is_not_shared_with_nested_evaluation(call_type):
    """A second request stays strict even while a runtime call is in flight."""
    from ee.evals.llm.agent_evaluator.evaluator import GradingError

    evaluator = AgentEvaluator(rule_prompt="Toxicity", model=call_type)
    inputs = {"input": "hello", "call_type": call_type, "protect_runtime": True}
    with patch("agentic_eval.core.llm.llm.LLM") as llm:
        completion = llm.return_value._try_gateway_completion

        def reply(payload):
            if completion.call_count == 1:
                with pytest.raises(GradingError) as exc:
                    evaluator.run(**inputs)
                assert exc.value.code == "invalid_output"
            return _protect_response("garbage")

        completion.side_effect = reply
        result = evaluator.run_protect_runtime(inputs)
        assert completion.call_count == 2
    assert result.eval_results[0]["output"] == (
        "Passed" if call_type == "protect_flash" else "Failed"
    )


@pytest.mark.parametrize("mapped_value", [True, False, "synthetic-template-value"])
@pytest.mark.parametrize("protect_runtime", [False, True])
def test_run_preserves_protect_runtime_template_variable(mapped_value, protect_runtime):
    evaluator = AgentEvaluator(rule_prompt="Check {{protect_runtime}}", model="protect")
    inputs = {
        "eval_name": "Toxicity",
        "input": "hello",
        "protect_runtime": mapped_value,
        "required_keys": ["protect_runtime"],
    }
    with patch("agentic_eval.core.llm.llm.LLM") as llm:
        llm.return_value._try_gateway_completion.return_value = _protect_response(
            "<label>Passed</label>"
        )
        result = (
            evaluator.run_protect_runtime(inputs)
            if protect_runtime
            else evaluator.run(**inputs)
        )
    assert result.eval_results[0]["output"] == "Passed"
    assert f"Check {mapped_value}" in evaluator._eval_prompt
    assert (
        f"<protect_runtime>{mapped_value}</protect_runtime>" in evaluator._eval_prompt
    )
    assert inputs["protect_runtime"] == mapped_value


@pytest.mark.parametrize("mapped_value", [True, "synthetic-template-value"])
def test_agent_run_receives_runtime_named_template_input(mapped_value):
    evaluator = AgentEvaluator(
        rule_prompt="Check {{protect_runtime}}", model="turing_large"
    )
    with (
        patch.object(evaluator, "_build_llm_override", return_value={}),
        patch.object(
            evaluator, "_run_agent", return_value={"content": VALID, "tool_calls": []}
        ) as run_agent,
    ):
        result = evaluator.run(
            protect_runtime=mapped_value, required_keys=["protect_runtime"]
        )
    assert result.eval_results[0]["data"]["result"] == "Pass"
    run_agent.assert_called_once()
    assert f"Check {mapped_value}" in run_agent.call_args.args[0]


@pytest.mark.parametrize("entrypoint", ["dataset", "playground"])
@pytest.mark.parametrize("call_type", ["protect", "protect_flash"])
@pytest.mark.parametrize("mapped_value", [True, "mapped-runtime-value"])
@pytest.mark.parametrize("declared", [False, True])
def test_direct_eval_entrypoints_keep_truthy_runtime_named_inputs_strict(
    monkeypatch, protect_engine, entrypoint, call_type, mapped_value, declared
):
    """Exercise both direct run(**mapped_data) paths, not the unified engine."""
    from ee.evals.llm.agent_evaluator.evaluator import GradingError
    from model_hub.views.eval_runner import EvaluationRunner
    from model_hub.views.utils import evals as playground

    template = protect_engine
    if declared:
        template.config["required_keys"] = ["input", "protect_runtime"]
    evaluator = AgentEvaluator(
        rule_prompt="Check {{protect_runtime}}" if declared else "Toxicity",
        model=call_type,
    )
    monkeypatch.setattr(
        EvaluationRunner, "_create_eval_instance", Mock(return_value=evaluator)
    )
    monkeypatch.setattr(
        EvaluationRunner, "get_few_shot_examples", Mock(return_value=[])
    )
    monkeypatch.setattr(playground.GroundTruthService, "inject_context", Mock())
    monkeypatch.setattr(playground, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(
        playground, "_get_api_call_type", Mock(return_value="synthetic")
    )
    monkeypatch.setattr(
        "ee.usage.services.metering.check_usage",
        Mock(return_value=SimpleNamespace(allowed=True)),
    )
    inputs = {
        "input": "hello",
        "call_type": call_type,
        "eval_name": "Toxicity",
        "protect_runtime": mapped_value,
    }

    if entrypoint == "dataset":
        runner = EvaluationRunner("synthetic-metric", format_output=True)
        runner.eval_template = template
        mappings = {key: f"synthetic-column-{key}" for key in inputs}
        runner.user_eval_metric = SimpleNamespace(
            template=template, model=call_type, kb_id=None, config={"mapping": mappings}
        )
        monkeypatch.setattr(
            runner,
            "_prepare_mapping_data",
            Mock(return_value=(list(inputs), list(inputs.values()))),
        )

        def invoke():
            return runner._run_evaluation(
                row=SimpleNamespace(dataset_id="synthetic-dataset"),
                mappings=mappings,
                config={},
            )
    else:

        def invoke():
            return playground.run_eval_func(
                config={},
                mappings=inputs,
                template=template,
                org=SimpleNamespace(id="synthetic-org"),
                model=call_type,
                resolved_version=SimpleNamespace(
                    id="synthetic-version", version_number=1
                ),
            )

    with patch("agentic_eval.core.llm.llm.LLM") as llm:
        llm.return_value._try_gateway_completion.return_value = _protect_response(
            "garbage"
        )
        with pytest.raises(GradingError) as exc:
            invoke()
        llm.return_value._try_gateway_completion.assert_called_once()
    assert exc.value.code == "invalid_output"
    assert inputs["protect_runtime"] == mapped_value
    if declared:
        assert f"Check {mapped_value}" in evaluator._eval_prompt


@pytest.fixture
def sdk_protect_post(monkeypatch, protect_engine):
    """SDK view -> utility -> engine -> evaluator, with synthetic side effects."""
    from sdk.utils import evaluations as eval_utils
    from sdk.views import evaluations as eval_views

    monkeypatch.setattr(
        eval_utils.EvalTemplate.no_workspace_objects,
        "get",
        Mock(return_value=protect_engine),
    )
    monkeypatch.setattr(
        eval_utils, "_log_and_deduct_cost_for_standalone_eval", Mock(return_value=None)
    )
    monkeypatch.setattr(
        "ee.usage.services.config.BillingConfig.get", Mock(return_value=None)
    )
    monkeypatch.setattr(eval_views, "get_mixpanel_properties", Mock(return_value={}))
    monkeypatch.setattr(eval_views, "track_mixpanel_event", Mock())

    def post(content, *, protect_flash=False, unavailable=False):
        call_type = "protect_flash" if protect_flash else "protect"
        request = SimpleNamespace(
            method="POST",
            user=SimpleNamespace(
                id="synthetic-user", organization=SimpleNamespace(id="synthetic-org")
            ),
            workspace=None,
            data={
                "inputs": [
                    {"input": "hello", "call_type": call_type, "max_tokens": 20}
                ],
                "config": {"1": {"call_type": call_type}},
                "protect_flash": protect_flash,
            },
        )
        with patch("agentic_eval.core.llm.llm.LLM") as llm:
            completion = llm.return_value._try_gateway_completion
            completion.return_value = (
                None if unavailable else _protect_response(content)
            )
            response = eval_views.StandaloneEvalView().post(request)
        completion.assert_called_once()
        payload = completion.call_args.args[0]
        assert payload["model"] == (
            "protect_flash" if protect_flash else "protect_toxicity"
        )
        assert payload["max_tokens"] == 20
        return response

    return post


@pytest.mark.parametrize("protect_flash", [False, True])
@pytest.mark.parametrize("content", ["", None, "garbage", "<label>maybe</label>"])
def test_protect_sdk_runtime_returns_legacy_verdict(
    sdk_protect_post, protect_flash, content
):
    """HTTP 200 Failed must not become HTTP 500 and hence fail_open admission."""
    response = sdk_protect_post(content, protect_flash=protect_flash)
    assert response.status_code == 200, response.data
    result = response.data["result"][0]["evaluations"][0]
    assert result["output"] == ("Passed" if protect_flash else "Failed")
    assert result["output_type"] == "Pass/Fail"


@pytest.mark.parametrize(
    ("protect_flash", "content", "label"),
    [
        (False, "<label>Passed</label>", "Passed"),
        (False, "<label>Failed</label>", "Failed"),
        (True, FLASH_SAFE, "Passed"),
        (True, FLASH_HARMFUL, "Failed"),
    ],
)
def test_protect_sdk_runtime_keeps_readable_verdicts(
    sdk_protect_post, protect_flash, content, label
):
    response = sdk_protect_post(content, protect_flash=protect_flash)
    assert response.status_code == 200, response.data
    assert response.data["result"][0]["evaluations"][0]["output"] == label


@pytest.mark.parametrize(
    ("content", "label"),
    FLASH_INVALID_STRUCTURED_VERDICTS + FLASH_VALID_STRUCTURED_VERDICTS,
)
def test_flash_structured_verdict_sdk_keeps_legacy_labels(
    sdk_protect_post, content, label
):
    response = sdk_protect_post(content, protect_flash=True)
    assert response.status_code == 200, response.data
    result = response.data["result"][0]["evaluations"][0]
    assert result["output"] == label
    assert result["output_type"] == "Pass/Fail"


@pytest.mark.parametrize("protect_flash", [False, True])
def test_protect_sdk_runtime_gateway_unavailable_still_errors(
    sdk_protect_post, protect_flash
):
    response = sdk_protect_post(None, protect_flash=protect_flash, unavailable=True)
    assert response.status_code == 500
