"""
Unit tests for tool_call_loop_repetition.

Run with:
    pytest test_tool_call_loop_repetition.py -v
"""

import pytest

from agentic_eval.metrics.tool_call_loop_repetition import (
    ToolCall,
    ToolCallLoopDetector,
    ToolCallLoopGuardrail,
    evaluate_tool_call_loop_repetition,
)


# ---------------------------------------------------------------------------
# Healthy traces (should NOT flag a loop)
# ---------------------------------------------------------------------------

class TestHealthyTraces:
    def test_empty_trace(self):
        result = ToolCallLoopDetector().evaluate([])
        assert result.loop_detected is False
        assert result.score == 1.0

    def test_single_call(self):
        result = ToolCallLoopDetector().evaluate(
            [ToolCall(name="search", arguments={"q": "hi"})]
        )
        assert result.loop_detected is False
        assert result.passed is True

    def test_distinct_sequential_calls(self):
        calls = [
            ToolCall(name="search", arguments={"q": "refund policy"}),
            ToolCall(name="read_doc", arguments={"doc_id": "123"}),
            ToolCall(name="summarize", arguments={"text": "..."}),
            ToolCall(name="respond", arguments={"answer": "..."}),
        ]
        result = ToolCallLoopDetector().evaluate(calls)
        assert result.loop_detected is False
        assert result.score == 1.0

    def test_legitimate_pagination_not_flagged(self):
        # Same tool, similar args, but results genuinely differ each time ->
        # this is real progress (e.g. paging through search results), not a
        # stuck loop, so it should NOT be flagged.
        calls = [
            ToolCall(name="list_items", arguments={"page": 1}, result="items 1-10, unique batch A"),
            ToolCall(name="list_items", arguments={"page": 2}, result="items 11-20, unique batch B"),
            ToolCall(name="list_items", arguments={"page": 3}, result="items 21-30, unique batch C"),
        ]
        result = ToolCallLoopDetector(near_duplicate_threshold=0.99).evaluate(calls)
        assert result.loop_detected is False

    def test_two_repeats_below_min_repeats_not_flagged(self):
        # Only 2 repeats; default min_repeats=3, so this should be fine
        # (agents legitimately retry once sometimes).
        calls = [
            ToolCall(name="fetch", arguments={"id": 1}),
            ToolCall(name="fetch", arguments={"id": 1}),
        ]
        result = ToolCallLoopDetector().evaluate(calls)
        assert result.loop_detected is False

    def test_ignored_keys_dont_cause_false_negative_masking(self):
        # Arguments differ ONLY in an ignored (volatile) key across many
        # calls -> should still be considered the same call and flagged.
        calls = [
            ToolCall(name="ping", arguments={"host": "api.example.com", "timestamp": t})
            for t in range(5)
        ]
        result = ToolCallLoopDetector().evaluate(calls)
        assert result.loop_detected is True


# ---------------------------------------------------------------------------
# Exact repetition loops
# ---------------------------------------------------------------------------

class TestExactRepeatLoops:
    def test_three_identical_calls_flagged(self):
        calls = [
            ToolCall(name="search_docs", arguments={"query": "refund policy"})
            for _ in range(3)
        ]
        result = ToolCallLoopDetector().evaluate(calls)
        assert result.loop_detected is True
        assert result.loop_type == "exact_repeat"
        assert result.repeat_count == 3
        assert result.passed is False

    def test_more_repeats_lower_score(self):
        few = [ToolCall(name="x", arguments={}) for _ in range(3)]
        many = [ToolCall(name="x", arguments={}) for _ in range(8)]
        few_result = ToolCallLoopDetector().evaluate(few)
        many_result = ToolCallLoopDetector().evaluate(many)
        assert many_result.score <= few_result.score

    def test_loop_after_healthy_prefix(self):
        calls = [
            ToolCall(name="search", arguments={"q": "a"}),
            ToolCall(name="read_doc", arguments={"doc_id": "1"}),
        ] + [ToolCall(name="search", arguments={"q": "a"}) for _ in range(4)]
        result = ToolCallLoopDetector().evaluate(calls)
        assert result.loop_detected is True
        assert result.first_loop_start_index == 2

    def test_custom_min_repeats_threshold(self):
        calls = [ToolCall(name="x", arguments={}) for _ in range(2)]
        strict = ToolCallLoopDetector(min_repeats=2).evaluate(calls)
        default = ToolCallLoopDetector().evaluate(calls)  # min_repeats=3
        assert strict.loop_detected is True
        assert default.loop_detected is False


# ---------------------------------------------------------------------------
# Oscillation loops (A, B, A, B, ...)
# ---------------------------------------------------------------------------

class TestOscillationLoops:
    def test_two_step_oscillation_flagged(self):
        calls = []
        for _ in range(4):
            calls.append(ToolCall(name="search", arguments={"q": "refund"}))
            calls.append(ToolCall(name="read_faq", arguments={"section": "billing"}))
        result = ToolCallLoopDetector().evaluate(calls)
        assert result.loop_detected is True
        assert result.loop_type == "oscillation"
        assert result.cycle_length == 2

    def test_three_step_oscillation_flagged(self):
        pattern = [
            ToolCall(name="a", arguments={}),
            ToolCall(name="b", arguments={}),
            ToolCall(name="c", arguments={}),
        ]
        calls = pattern * 3
        result = ToolCallLoopDetector(max_cycle_length=4).evaluate(calls)
        assert result.loop_detected is True
        assert result.cycle_length == 3
        assert result.repeat_count == 3

    def test_cycle_longer_than_max_cycle_length_not_flagged_as_oscillation(self):
        pattern = [ToolCall(name=f"tool_{i}", arguments={}) for i in range(6)]
        calls = pattern * 3
        result = ToolCallLoopDetector(max_cycle_length=4, near_duplicate_threshold=1.0).evaluate(calls)
        assert result.loop_type != "oscillation"


# ---------------------------------------------------------------------------
# Near-duplicate loops
# ---------------------------------------------------------------------------

class TestNearDuplicateLoops:
    def test_slightly_reworded_repeated_query_flagged(self):
        calls = [
            ToolCall(name="search", arguments={"query": "refund policy for electronics"}),
            ToolCall(name="search", arguments={"query": "refund policy electronics"}),
            ToolCall(name="search", arguments={"query": "refund policy electronic items"}),
        ]
        result = ToolCallLoopDetector(near_duplicate_threshold=0.7).evaluate(calls)
        assert result.loop_detected is True
        assert result.loop_type == "near_duplicate"

    def test_near_duplicate_with_diverging_results_not_flagged(self):
        calls = [
            ToolCall(
                name="search",
                arguments={"query": "refund policy a"},
                result="Completely different result content number one about warranties",
            ),
            ToolCall(
                name="search",
                arguments={"query": "refund policy b"},
                result="An entirely unrelated second result about shipping timelines",
            ),
            ToolCall(
                name="search",
                arguments={"query": "refund policy c"},
                result="A third, again unrelated result about account settings",
            ),
        ]
        result = ToolCallLoopDetector(near_duplicate_threshold=0.7).evaluate(calls)
        assert result.loop_detected is False


# ---------------------------------------------------------------------------
# Functional wrapper (dict-based calling convention)
# ---------------------------------------------------------------------------

class TestFunctionalWrapper:
    def test_dict_input_supported(self):
        trace = [
            {"name": "search", "arguments": {"q": "x"}},
            {"name": "search", "arguments": {"q": "x"}},
            {"name": "search", "arguments": {"q": "x"}},
        ]
        result = evaluate_tool_call_loop_repetition(trace)
        assert result["loop_detected"] is True
        assert result["loop_type"] == "exact_repeat"

    def test_alternate_key_names_supported(self):
        trace = [
            {"tool_name": "search", "args": {"q": "x"}, "output": "same"},
            {"tool_name": "search", "args": {"q": "x"}, "output": "same"},
            {"tool_name": "search", "args": {"q": "x"}, "output": "same"},
        ]
        result = evaluate_tool_call_loop_repetition(trace)
        assert result["loop_detected"] is True


# ---------------------------------------------------------------------------
# Streaming guardrail
# ---------------------------------------------------------------------------

class TestStreamingGuardrail:
    def test_flags_as_soon_as_threshold_crossed(self):
        guardrail = ToolCallLoopGuardrail(min_repeats=3)
        results = []
        for _ in range(5):
            results.append(
                guardrail.observe(ToolCall(name="ping", arguments={"host": "x"}))
            )
        # First two observations shouldn't trigger; from the 3rd onward they should.
        assert results[0].loop_detected is False
        assert results[1].loop_detected is False
        assert results[2].loop_detected is True
        assert results[3].loop_detected is True
        assert results[4].loop_detected is True

    def test_reset_clears_state(self):
        guardrail = ToolCallLoopGuardrail(min_repeats=3)
        for _ in range(3):
            guardrail.observe(ToolCall(name="ping", arguments={}))
        guardrail.reset()
        result = guardrail.observe(ToolCall(name="ping", arguments={}))
        assert result.loop_detected is False

    def test_window_size_bounds_memory(self):
        guardrail = ToolCallLoopGuardrail(window_size=5, min_repeats=3)
        for i in range(20):
            guardrail.observe(ToolCall(name=f"tool_{i}", arguments={}))
        assert len(guardrail._buffer) <= 5


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_min_repeats_must_be_at_least_two(self):
        with pytest.raises(ValueError):
            ToolCallLoopDetector(min_repeats=1)

    def test_threshold_must_be_in_unit_range(self):
        with pytest.raises(ValueError):
            ToolCallLoopDetector(near_duplicate_threshold=1.5)
