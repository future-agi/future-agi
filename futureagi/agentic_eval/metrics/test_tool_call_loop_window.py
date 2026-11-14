import pytest

from agentic_eval.metrics.tool_call_loop_repetition import (
    evaluate_tool_call_loop_repetition,
)


def test_window_excludes_old_loop_and_reports_evaluated_count():
    calls = [{"name": "poll", "arguments": {"id": "job-1"}, "result": "waiting"}] * 3
    calls += [
        {"name": "poll", "arguments": {"id": "job-1"}, "result": i} for i in range(3)
    ]
    recent = evaluate_tool_call_loop_repetition(calls, window_size=3)
    assert recent["passed"] is True
    assert recent["evaluated_calls"] == 3
    assert recent["window_size"] == 3
    assert "window_size=3" in recent["reason"]
    full = evaluate_tool_call_loop_repetition(calls, window_size=6)
    assert full["passed"] is False


def test_reason_names_repeated_arguments_and_window():
    result = evaluate_tool_call_loop_repetition(
        [{"name": "poll", "arguments": {"id": "job-1"}, "result": "waiting"}] * 3,
        window_size=4,
    )
    assert 'poll: {"id": "job-1"}' in result["reason"]
    assert "Evaluated 3 most recent tool calls (window_size=4)" in result["reason"]


@pytest.mark.parametrize("window", [True, 1, 2, 1001, "4", 4.5])
def test_invalid_windows_are_rejected(window):
    with pytest.raises(ValueError, match="window_size must be an integer"):
        evaluate_tool_call_loop_repetition([], window_size=window)


def test_window_preserves_full_input_position():
    calls = [{"name": "other", "arguments": {}, "result": i} for i in range(4)]
    calls += [{"name": "poll", "arguments": {}, "result": "waiting"}] * 3
    result = evaluate_tool_call_loop_repetition(calls, window_size=3)
    assert result["first_loop_start_index"] == 4


def test_configured_two_repeat_loop_has_failing_score():
    calls = [
        {"name": name, "arguments": {}, "result": "waiting"}
        for name in ["read", "write"] * 2
    ]
    result = evaluate_tool_call_loop_repetition(calls, min_repeats=2)
    assert result["loop_detected"] is True
    assert result["passed"] is False
    assert result["score"] < 0.5


def test_argument_reason_is_bounded():
    result = evaluate_tool_call_loop_repetition(
        [{"name": "poll", "arguments": {"text": "x" * 10000}, "result": "waiting"}] * 3
    )
    assert len(result["reason"]) < 600
