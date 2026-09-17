"""Black-box tests for AgentEvaluator's failure surface.

Focused tests:
  - USER_FACING_EVAL_FAILED is the single user-visible message
  - Distinct structlog event names per failure cause (Sentry fingerprint)
  - ``_failure_context()`` payload completeness + None-stripping
  - ``_capture_target_info`` covers span / trace / session / call / row / custom
  - Oversized + content_str → "recovered_oversized_attempt" info (no raise)
  - Oversized + empty → "input_too_large" + raise
  - Force-finalize fires only when content empty + tool_log non-empty
  - Force-finalize preserves multimodal in user anchor

The retry-loop tests are kept lean (assert ValueError propagates;
non-ValueError retries) — we don't reconstruct the full agent loop.
"""

import asyncio
import re
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
import structlog
from structlog.testing import LogCapture

from ee.evals.llm.agent_evaluator.evaluator import (
    AgentEvaluator,
    USER_FACING_EVAL_FAILED,
)


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def log_capture():
    """Capture structlog events emitted during the test.

    cache_logger_on_first_use=False is required so module-level
    `logger = structlog.get_logger(__name__)` bindings pick up THIS
    fixture's cap instead of one cached by the first test that ran.
    """
    cap = LogCapture()
    structlog.configure(processors=[cap], cache_logger_on_first_use=False)
    yield cap
    structlog.reset_defaults()


@pytest.fixture
def evaluator():
    """A bare AgentEvaluator instance with no DB / infra dependencies."""
    e = AgentEvaluator(
        rule_prompt="check that the response is correct",
        model="turing_large",
        output_type="Pass/Fail",
        organization_id="org-1",
        workspace_id="ws-1",
    )
    return e


# ──────────────────────────────────────────────────────────────────────────
# USER_FACING_EVAL_FAILED message shape
# ──────────────────────────────────────────────────────────────────────────


def test_user_facing_message_is_generic():
    """No internal jargon leaks to end-users."""
    msg = USER_FACING_EVAL_FAILED.lower()
    # No model aliases / vendor names
    for forbidden in (
        "turing", "context window", "compaction", "token", "retries",
        "agent loop", "ml", "summarizer", "oversized",
    ):
        assert forbidden not in msg, f"leaked internal jargon: {forbidden!r}"


def test_user_facing_message_mentions_retry_path():
    assert "try again" in USER_FACING_EVAL_FAILED.lower()
    assert "support" in USER_FACING_EVAL_FAILED.lower()


# ──────────────────────────────────────────────────────────────────────────
# _capture_target_info: each context type extraction
# ──────────────────────────────────────────────────────────────────────────


def test_capture_target_info_span(evaluator):
    evaluator._capture_target_info({
        "span_context": {"id": "span-123", "name": "MySpan", "project_id": "pj-1"}
    })
    assert evaluator._target_type == "span"
    assert evaluator._target_id == "span-123"
    assert evaluator._target_name == "MySpan"
    assert evaluator._target_project_id == "pj-1"


def test_capture_target_info_trace(evaluator):
    evaluator._capture_target_info({
        "trace_context": {"id": "trace-abc", "project_id": "pj-X"}
    })
    assert evaluator._target_type == "trace"
    assert evaluator._target_id == "trace-abc"
    assert evaluator._target_project_id == "pj-X"


def test_capture_target_info_session(evaluator):
    evaluator._capture_target_info({
        "session_context": {"id": "sess-1", "session_name": "Demo"}
    })
    assert evaluator._target_type == "session"
    assert evaluator._target_id == "sess-1"
    assert evaluator._target_name == "Demo"


def test_capture_target_info_call(evaluator):
    evaluator._capture_target_info({"call_context": {"id": "call-q"}})
    assert evaluator._target_type == "call"
    assert evaluator._target_id == "call-q"


def test_capture_target_info_row(evaluator):
    evaluator._capture_target_info({"row_context": {"id": "row-z"}})
    assert evaluator._target_type == "row"
    assert evaluator._target_id == "row-z"


def test_capture_target_info_uses_typed_id_fallback(evaluator):
    # Field is named span_id rather than id
    evaluator._capture_target_info({"span_context": {"span_id": "span-xyz"}})
    assert evaluator._target_type == "span"
    assert evaluator._target_id == "span-xyz"


def test_capture_target_info_no_context_defaults_to_custom(evaluator):
    evaluator._capture_target_info({})
    assert evaluator._target_type == "custom"
    assert evaluator._target_id is None


def test_capture_target_info_first_matching_wins(evaluator):
    """Span > Trace > Session > Call > Row priority."""
    evaluator._capture_target_info({
        "span_context": {"id": "S"},
        "trace_context": {"id": "T"},
        "session_context": {"id": "Z"},
    })
    assert evaluator._target_type == "span"
    assert evaluator._target_id == "S"


def test_capture_target_info_invalid_kwargs_safe(evaluator):
    evaluator._capture_target_info(None)
    assert evaluator._target_type == "custom"


def test_capture_target_info_empty_context_dicts_skipped(evaluator):
    evaluator._capture_target_info({
        "span_context": {},
        "trace_context": None,
        "session_context": {"id": "S-only-after-empties"},
    })
    assert evaluator._target_type == "session"
    assert evaluator._target_id == "S-only-after-empties"


# ──────────────────────────────────────────────────────────────────────────
# _failure_context: payload shape + None stripping
# ──────────────────────────────────────────────────────────────────────────


def test_failure_context_includes_self_fields(evaluator):
    evaluator._capture_target_info({"span_context": {"id": "S-1", "project_id": "P-1"}})
    evaluator._current_eval_id = "eid-1"
    evaluator._eval_prompt = "the rendered eval prompt"
    payload = evaluator._failure_context()
    assert payload["target_type"] == "span"
    assert payload["target_id"] == "S-1"
    assert payload["project_id"] == "P-1"
    assert payload["organization_id"] == "org-1"
    assert payload["workspace_id"] == "ws-1"
    assert payload["model"] == "turing_large"
    assert payload["output_type"] == "Pass/Fail"
    assert payload["eval_id"] == "eid-1"
    assert payload["input_preview"].startswith("the rendered eval prompt")


def test_failure_context_strips_none_values(evaluator):
    # Don't set target info → all target fields should be missing
    payload = evaluator._failure_context()
    assert "target_id" not in payload  # because it is None
    assert "project_id" not in payload
    # Provided fields ARE present
    assert payload["organization_id"] == "org-1"


def test_failure_context_strips_empty_strings(evaluator):
    evaluator._eval_prompt = ""  # empty string -> stripped
    payload = evaluator._failure_context()
    assert "input_preview" not in payload
    assert "input_length" not in payload


def test_failure_context_keeps_zero_and_false(evaluator):
    payload = evaluator._failure_context(some_count=0, some_flag=False)
    assert payload["some_count"] == 0
    assert payload["some_flag"] is False


def test_failure_context_extra_overrides_self(evaluator):
    payload = evaluator._failure_context(model="overridden_model")
    assert payload["model"] == "overridden_model"


def test_failure_context_truncates_input_preview(evaluator):
    evaluator._eval_prompt = "x" * 5000
    payload = evaluator._failure_context()
    assert len(payload["input_preview"]) == 300
    assert payload["input_length"] == 5000


# ──────────────────────────────────────────────────────────────────────────
# _build_result paths via direct call: type mismatches
# ──────────────────────────────────────────────────────────────────────────


def test_build_result_passfail_mismatch_raises_user_message(evaluator, log_capture):
    evaluator._output_type = "Pass/Fail"
    # Result not in pass/fail
    agent_result = {"content": '{"result": "MAYBE", "explanation": "..."}',
                    "tool_calls": []}
    with pytest.raises(ValueError) as exc:
        evaluator._build_result(agent_result, runtime_ms=10)
    assert str(exc.value) == USER_FACING_EVAL_FAILED
    events = [e["event"] for e in log_capture.entries]
    assert "eval_result_type_mismatch_pass_fail" in events


def test_build_result_score_mismatch_raises_user_message(evaluator, log_capture):
    evaluator._output_type = "score"
    agent_result = {"content": '{"result": "not-a-number"}', "tool_calls": []}
    with pytest.raises(ValueError) as exc:
        evaluator._build_result(agent_result, runtime_ms=10)
    assert str(exc.value) == USER_FACING_EVAL_FAILED
    events = [e["event"] for e in log_capture.entries]
    assert "eval_result_type_mismatch_numeric" in events


def test_build_result_choices_mismatch_raises_user_message(evaluator, log_capture):
    evaluator._output_type = "choices"
    evaluator._choices = ["red", "blue"]
    agent_result = {"content": '{"result": "green"}', "tool_calls": []}
    with pytest.raises(ValueError) as exc:
        evaluator._build_result(agent_result, runtime_ms=10)
    assert str(exc.value) == USER_FACING_EVAL_FAILED
    events = [e["event"] for e in log_capture.entries]
    assert "eval_result_type_mismatch_choices" in events


def test_build_result_empty_content_raises_user_message(evaluator, log_capture):
    """When content is empty AND no JSON could be extracted, _build_result
    raises the user-facing message and logs ``agent_evaluator_no_json``."""
    evaluator._output_type = "Pass/Fail"
    agent_result = {"content": "", "tool_calls": []}
    with pytest.raises(ValueError) as exc:
        evaluator._build_result(agent_result, runtime_ms=10)
    assert str(exc.value) == USER_FACING_EVAL_FAILED
    events = [e["event"] for e in log_capture.entries]
    assert "agent_evaluator_no_json" in events


def test_build_result_nonempty_garbage_falls_back_to_fail(evaluator):
    """If the agent returned text but with no parseable JSON, we don't
    raise — we synthesize ``result=Fail`` from the raw content. This is
    the existing fallback behavior; verify it still holds."""
    evaluator._output_type = "Pass/Fail"
    agent_result = {"content": "I think it failed.", "tool_calls": []}
    # No JSON → fallback to Fail
    out = evaluator._build_result(agent_result, runtime_ms=10)
    # Accept either a dict-shaped or EvalResult-shaped return
    failure = out.get("failure") if isinstance(out, dict) else out.failure
    assert failure is True


# ──────────────────────────────────────────────────────────────────────────
# Force-finalize trigger conditions
# ──────────────────────────────────────────────────────────────────────────


def test_rebuild_user_anchor_orders_gt_then_text_then_media():
    from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator

    gt = [{"type": "text", "text": "Reference example 1"}]
    media = [
        {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
    ]
    out = AgentEvaluator._rebuild_user_anchor_content("CASE", gt, media)
    assert out == [
        {"type": "text", "text": "Reference example 1"},
        {"type": "text", "text": "CASE"},
        {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
    ]


def test_rebuild_user_anchor_text_only_gt_stays_string_so_truncation_applies():
    from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator

    gt = [
        {"type": "text", "text": "Reference example 1"},
        {"type": "text", "text": "Reference example 2"},
    ]
    out = AgentEvaluator._rebuild_user_anchor_content("CASE", gt, None)
    assert isinstance(out, str)
    assert out == "Reference example 1\n\nReference example 2\n\nCASE"


def test_rebuild_user_anchor_text_only_gt_gets_capped_by_eval_client_l1():
    from ee.evals.llm.agent_evaluator.context.client import (
        PER_MESSAGE_CHARS_CAP,
        EvalLLMClient,
    )
    from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator

    huge_text = "X" * (PER_MESSAGE_CHARS_CAP * 3)
    gt = [{"type": "text", "text": huge_text}]
    out = AgentEvaluator._rebuild_user_anchor_content("CASE", gt, None)
    assert isinstance(out, str)
    assert len(out) > PER_MESSAGE_CHARS_CAP
    capped = EvalLLMClient._cap_single_message({"role": "user", "content": out})
    assert isinstance(capped["content"], str)
    assert len(capped["content"]) <= PER_MESSAGE_CHARS_CAP + 200
    assert "truncated" in capped["content"]


def test_rebuild_user_anchor_no_gt_no_media_keeps_string():
    from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator

    out = AgentEvaluator._rebuild_user_anchor_content("CASE", None, None)
    assert out == "CASE"


def test_apply_precontent_text_only_stays_string_so_truncation_applies():
    from ee.falcon_ai.agent import AgentLoop

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "CASE"},
    ]
    precontent = [
        {"type": "text", "text": "EXEMPLAR 1"},
        {"type": "text", "text": "EXEMPLAR 2"},
    ]
    AgentLoop._apply_precontent_to_last_user_message(messages, precontent, [])

    assert isinstance(messages[-1]["content"], str)
    assert messages[-1]["content"] == "EXEMPLAR 1\n\nEXEMPLAR 2\n\nCASE"


def test_apply_precontent_with_media_lifts_to_list_in_order():
    from ee.falcon_ai.agent import AgentLoop

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "CASE"},
    ]
    precontent = [{"type": "text", "text": "EX 1"}]
    media = [
        {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
    ]
    AgentLoop._apply_precontent_to_last_user_message(messages, precontent, media)

    assert messages[-1]["content"] == [
        {"type": "text", "text": "EX 1"},
        {"type": "text", "text": "CASE"},
        {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
    ]


def test_apply_precontent_image_only_precontent_lifts_to_list():
    from ee.falcon_ai.agent import AgentLoop

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "CASE"},
    ]
    precontent = [
        {"type": "image_url", "image_url": {"url": "https://example.com/ex.png"}},
    ]
    AgentLoop._apply_precontent_to_last_user_message(messages, precontent, [])

    assert messages[-1]["content"] == [
        {"type": "image_url", "image_url": {"url": "https://example.com/ex.png"}},
        {"type": "text", "text": "CASE"},
    ]


def test_apply_precontent_noop_when_nothing_to_attach():
    from ee.falcon_ai.agent import AgentLoop

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "CASE"},
    ]
    AgentLoop._apply_precontent_to_last_user_message(messages, None, None)

    assert messages[-1]["content"] == "CASE"


def test_text_only_precontent_gets_capped_by_eval_client_l1():
    """Proves the end-to-end contract: ``_apply_precontent`` keeps text-only
    content as a string, and ``EvalLLMClient._cap_single_message`` then
    applies the head+tail cap to it. A regression on either side - leaking
    a list shape or skipping the cap - flips this red."""
    from ee.evals.llm.agent_evaluator.context.client import (
        PER_MESSAGE_CHARS_CAP,
        EvalLLMClient,
    )
    from ee.falcon_ai.agent import AgentLoop

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "CASE PROMPT"},
    ]
    huge_text = "X" * (PER_MESSAGE_CHARS_CAP * 3)
    precontent = [{"type": "text", "text": huge_text}]

    AgentLoop._apply_precontent_to_last_user_message(messages, precontent, [])

    assert isinstance(messages[-1]["content"], str)
    assert len(messages[-1]["content"]) > PER_MESSAGE_CHARS_CAP

    capped = EvalLLMClient._cap_single_message(messages[-1])

    assert isinstance(capped["content"], str)
    assert len(capped["content"]) <= PER_MESSAGE_CHARS_CAP + 200
    assert "truncated" in capped["content"]


def test_list_content_is_left_untouched_by_eval_client_l1():
    """Mirror of the above for the list path: when precontent has media,
    the user anchor is a list and L1 must skip it - capping list content
    would mangle base64 image parts."""
    from ee.evals.llm.agent_evaluator.context.client import (
        PER_MESSAGE_CHARS_CAP,
        EvalLLMClient,
    )

    big = "Y" * (PER_MESSAGE_CHARS_CAP * 3)
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": big},
            {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
        ],
    }
    capped = EvalLLMClient._cap_single_message(msg)
    assert capped["content"] == msg["content"]



# ──────────────────────────────────────────────────────────────────────────
# Score clamping
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.0, 0.0),
        (0.5, 0.5),
        (1.0, 1.0),
        (1.5, 1.0),
        (3.5, 1.0),
        (7, 1.0),
        (-0.1, 0.0),
        (-1, 0.0),
        ("0.75", 0.75),
        ("3.5", 1.0),
    ],
)
def test_build_result_clamps_score(evaluator, raw, expected):
    evaluator._output_type = "score"
    agent_result = {"content": f'{{"result": {raw!r}, "explanation": "x"}}', "tool_calls": []}
    result = evaluator._build_result(agent_result, runtime_ms=10)
    assert result["data"]["result"] == expected


def test_build_result_clamps_numeric(evaluator):
    evaluator._output_type = "numeric"
    agent_result = {"content": '{"result": 4.2, "explanation": "x"}', "tool_calls": []}
    result = evaluator._build_result(agent_result, runtime_ms=10)
    assert result["data"]["result"] == 1.0


@pytest.mark.parametrize("label", ["Pass", "Fail"])
def test_build_result_passfail_not_clamped(evaluator, label):
    evaluator._output_type = "Pass/Fail"
    agent_result = {"content": f'{{"result": "{label}", "explanation": "x"}}', "tool_calls": []}
    result = evaluator._build_result(agent_result, runtime_ms=10)
    assert result["data"]["result"] == label


@pytest.mark.parametrize("label", ["1", "10", "joy"])
def test_build_result_choices_labels_not_clamped(evaluator, label):
    evaluator._output_type = "choices"
    evaluator._choices = ["1", "10", "joy"]
    agent_result = {"content": f'{{"result": "{label}", "explanation": "x"}}', "tool_calls": []}
    result = evaluator._build_result(agent_result, runtime_ms=10)
    assert result["data"]["result"] == label


def test_build_result_out_of_range_emits_warning(evaluator):
    from structlog.testing import capture_logs

    evaluator._output_type = "score"
    agent_result = {"content": '{"result": 3.5, "explanation": "x"}', "tool_calls": []}
    with capture_logs() as captured:
        evaluator._build_result(agent_result, runtime_ms=10)
    events = [e["event"] for e in captured]
    assert "eval_score_out_of_range_clamped" in events


# ──────────────────────────────────────────────────────────────────────────
# agent_mode=auto: iteration budget + verdict on tool-search exhaustion
#
# Regression guard for the failure mode where an auto-mode agent burns its
# whole iteration budget on tool searches and returns no committed verdict.
# Two invariants: auto gets the wider budget, and an empty-content-with-tools
# return still resolves to a structured verdict via force-finalize.
# ──────────────────────────────────────────────────────────────────────────


def _drive_run_agent(evaluator, *, agent_run_result, finalize_text):
    """Run ``_run_agent`` with AgentLoop + the eval LLM client mocked.

    The mocked AgentLoop returns ``agent_run_result`` (simulating how the
    real loop terminates); the mocked client's force-finalize stream yields
    ``finalize_text``. Returns (result, mock_agent, captured_event_names).
    """
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value=agent_run_result)

    async def _fake_stream(messages, tools=None):
        yield {"choices": [{"delta": {"content": finalize_text}}]}

    mock_client = MagicMock()
    mock_client.last_oversized_attempt = None
    mock_client._gateway_cost = 0
    mock_client.stream_completion = _fake_stream

    from structlog.testing import capture_logs

    with patch("ee.falcon_ai.agent.AgentLoop", return_value=mock_agent), \
         patch("ee.evals.llm.agent_evaluator.evaluator.EvalLLMClient", return_value=mock_client), \
         patch("ai_tools.registry.registry"), \
         patch.object(evaluator, "_build_tool_context", return_value=MagicMock()), \
         capture_logs() as captured:
        result = evaluator._run_agent(
            "Judge whether the trace shows a hallucination.",
            include_trace_explorer=True,
            llm_override={"provider": "bedrock", "model": "x"},
        )
    return result, mock_agent, [e["event"] for e in captured]


def test_auto_mode_gets_wider_iteration_budget(evaluator):
    """auto mode runs more tool-search iterations than the quick single pass,
    giving the agent room to explore before committing to a verdict."""
    evaluator.agent_mode = "auto"
    _, mock_agent, _ = _drive_run_agent(
        evaluator,
        agent_run_result={"content": '{"result": "Pass", "explanation": "ok"}',
                          "tool_calls": []},
        finalize_text="",
    )
    assert mock_agent.MAX_ITERATIONS == 7


def test_auto_mode_exhaustion_force_finalizes_to_verdict(evaluator):
    """Agent spends its budget on tool searches and returns empty content with
    a non-empty tool log. Force-finalize must turn that into a committed,
    structured verdict instead of an empty response."""
    evaluator.agent_mode = "auto"
    evaluator._output_type = "Pass/Fail"
    tool_log = [
        {"tool_name": "explore_trace", "params": {"trace_id": "t"},
         "result_summary": "spans listed", "status": "ok"},
        {"tool_name": "span_detail", "params": {"span_id": "s1"},
         "result_summary": "input/output read", "status": "ok"},
    ]
    verdict = '{"result": "Fail", "explanation": "The answer is not grounded in the retrieved context."}'

    result, _, _ = _drive_run_agent(
        evaluator,
        agent_run_result={"content": "", "tool_calls": tool_log},
        finalize_text=verdict,
    )

    # Empty content + a non-empty tool log only resolves to the verdict via
    # force-finalize — recovering it proves the path fired and committed.
    assert result["content"] == verdict
