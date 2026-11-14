"""Regression coverage for progress, full traces and Gateway call envelopes."""

import json

import pytest

from agentic_eval.metrics.tool_call_loop_repetition import (
    ToolCall,
    ToolCallLoopDetector,
    ToolCallLoopGuardrail,
    evaluate_tool_call_loop_repetition,
)


@pytest.mark.parametrize("cycle_length", [1, 2, 3])
def test_changed_results_are_progress_for_exact_cycles(cycle_length):
    calls = [
        ToolCall(
            f"tool-{step}", {"query": "poll"}, {"status": "pending", "progress": turn}
        )
        for turn in range(3)
        for step in range(cycle_length)
    ]
    assert not ToolCallLoopDetector().evaluate(calls).loop_detected
    assert (
        ToolCallLoopDetector(also_compare_results=False).evaluate(calls).loop_detected
    )


def test_small_result_change_is_progress_for_near_duplicates():
    calls = [
        ToolCall(
            "search",
            {"query": f"long similar search request {i}"},
            {"data": "x" * 500, "page": i},
        )
        for i in range(3)
    ]
    assert not ToolCallLoopDetector().evaluate(calls).loop_detected


@pytest.mark.parametrize("key", ["id", "record_id", "cursor", "page"])
def test_similar_semantic_identifiers_are_not_cosmetic(key):
    calls = [
        ToolCall(
            "fetch",
            {key: f"record-with-long-common-prefix-{i}", "format": "json"},
            "ok",
        )
        for i in range(3)
    ]
    assert not ToolCallLoopDetector().evaluate(calls).loop_detected


@pytest.mark.parametrize("length", [2, 3, 4])
def test_completed_cycle_before_healthy_tail_is_reported(length):
    pattern = [ToolCall(f"tool-{i}", {}, "same") for i in range(length)]
    calls = [ToolCall("initial")] + pattern * 3 + [ToolCall("finished")]
    result = ToolCallLoopDetector().evaluate(calls)
    assert result.loop_type == "oscillation"
    assert result.first_loop_start_index == 1
    assert result.cycle_length == length
    assert result.repeat_count == 3
    assert result.involved_tools == sorted({c.name for c in pattern})


def test_falsy_results_take_precedence_over_output_alias():
    result = evaluate_tool_call_loop_repetition(
        [
            {
                "name": "poll",
                "arguments": {},
                "result": value,
                "output": "unchanged fallback",
            }
            for value in [False, 0, "", [], {}]
        ]
    )
    assert not result["loop_detected"]


def test_streaming_indices_survive_window_trimming_and_reset():
    observer = ToolCallLoopGuardrail(window_size=5)
    for i in range(20):
        observer.observe(ToolCall(f"healthy-{i}"))
    for _ in range(3):
        result = observer.observe(ToolCall("stuck"))
    assert result.first_loop_start_index == 20
    assert "step 20" in result.reason
    observer.reset()
    for _ in range(3):
        result = observer.observe(ToolCall("stuck"))
    assert result.first_loop_start_index == 0


def test_explicit_trace_indices_are_preserved():
    calls = [ToolCall("stuck", index=100 + i) for i in range(3)]
    result = ToolCallLoopDetector().evaluate(calls)
    assert result.first_loop_start_index == 100


@pytest.mark.parametrize("serialized", [False, True])
def test_gateway_function_envelopes_keep_names_arguments_and_results(serialized):
    calls = [
        {
            "id": f"call-{i}",
            "type": "function",
            "function": {"name": "poll", "arguments": '{"id": "job-1"}'},
            "result": {"progress": i},
        }
        for i in range(3)
    ]
    assert not evaluate_tool_call_loop_repetition(
        json.dumps(calls) if serialized else calls
    )["loop_detected"]
    for call in calls:
        call["result"] = "still waiting"
    result = evaluate_tool_call_loop_repetition(
        json.dumps(calls) if serialized else calls
    )
    assert result["involved_tools"] == ["poll"]
    assert result["loop_detected"]


@pytest.mark.parametrize(
    "field,value",
    [("max_cycle_length", 0), ("max_cycle_length", -1), ("min_repeats", 2.5)],
)
def test_invalid_search_parameters_are_rejected(field, value):
    with pytest.raises(ValueError):
        ToolCallLoopDetector(**{field: value})


@pytest.mark.parametrize("size", [0, -1, 2])
def test_window_must_hold_the_detection_threshold(size):
    with pytest.raises(ValueError):
        ToolCallLoopGuardrail(window_size=size)


@pytest.mark.parametrize(
    "call",
    [
        {},
        {"name": "fetch", "arguments": []},
        {"name": "fetch", "arguments": "not json"},
    ],
)
def test_malformed_call_data_is_not_scored_as_healthy(call):
    with pytest.raises(ValueError):
        evaluate_tool_call_loop_repetition([call])
