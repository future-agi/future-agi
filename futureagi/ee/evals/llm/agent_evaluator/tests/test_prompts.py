"""Tests for ``context/prompts.output_format_instruction``.

Output-type-specific hints must mention ONLY the relevant output shape
— never spam the agent with "Pass/Fail or score or choice" all at once.
"""

from ee.evals.llm.agent_evaluator.context.prompts import (
    COMPACTION_PROMPT,
    output_format_instruction,
)


# ── COMPACTION_PROMPT shape ──────────────────────────────────────────────


def test_compaction_prompt_is_nonempty_string():
    assert isinstance(COMPACTION_PROMPT, str)
    assert len(COMPACTION_PROMPT) > 100


def test_compaction_prompt_is_eval_type_neutral():
    # No span/trace/session jargon
    lo = COMPACTION_PROMPT.lower()
    assert "span" not in lo
    assert "trace" not in lo
    assert "verdict" not in lo


# ── output_format_instruction: Pass/Fail ─────────────────────────────────


def test_output_format_passfail_mentions_only_passfail():
    out = output_format_instruction("Pass/Fail", None)
    assert "'Pass'" in out and "'Fail'" in out
    assert "score" not in out.lower()
    assert "choice" not in out.lower()
    assert "numeric" not in out.lower()


# ── output_format_instruction: score / numeric ───────────────────────────


def test_output_format_score_mentions_numeric_range():
    out = output_format_instruction("score", None)
    assert "0.0" in out and "1.0" in out
    assert "Pass/Fail" not in out
    assert "Pass'" not in out


def test_output_format_numeric_alias():
    out = output_format_instruction("numeric", None)
    assert "0.0" in out and "1.0" in out
    assert "Pass/Fail" not in out


# ── output_format_instruction: choices ───────────────────────────────────


def test_output_format_choices_lists_each_choice():
    out = output_format_instruction("choices", ["good", "bad", "ugly"])
    assert "'good'" in out
    assert "'bad'" in out
    assert "'ugly'" in out
    assert "Pass/Fail" not in out


def test_output_format_choices_empty_list_falls_back_to_plain_text():
    out = output_format_instruction("choices", [])
    # The condition is `output_type == "choices" and choices`; empty list fails it
    assert "plain text" in out.lower()


def test_output_format_choices_none_falls_back_to_plain_text():
    out = output_format_instruction("choices", None)
    assert "plain text" in out.lower()


# ── output_format_instruction: choices + multi_choice ────────────────────


def test_output_format_choices_multi_returns_array_instruction():
    out = output_format_instruction(
        "choices", ["High", "Medium", "Low"], multi_choice=True
    )
    lo = out.lower()
    assert "array" in lo
    assert "one or more" in lo
    assert "'High'" in out
    assert "'Medium'" in out
    assert "'Low'" in out


def test_output_format_choices_multi_does_not_say_exactly_one():
    out = output_format_instruction(
        "choices", ["A", "B"], multi_choice=True
    )
    assert "exactly one" not in out.lower()


def test_output_format_choices_single_default_says_exactly_one():
    out = output_format_instruction("choices", ["A", "B"])
    assert "exactly one" in out.lower()
    assert "array" not in out.lower()


def test_output_format_choices_multi_with_empty_list_still_plain_text():
    out = output_format_instruction("choices", [], multi_choice=True)
    assert "plain text" in out.lower()


# ── output_format_instruction: unknown / None ────────────────────────────


def test_output_format_none_falls_back_to_plain_text():
    out = output_format_instruction(None, None)
    assert "plain text" in out.lower()


def test_output_format_unknown_type_falls_back_to_plain_text():
    out = output_format_instruction("weird_unsupported_type", None)
    assert "plain text" in out.lower()


def test_output_format_empty_string_falls_back_to_plain_text():
    out = output_format_instruction("", None)
    assert "plain text" in out.lower()


# ── Cross-type: each branch returns a non-empty single-line string ───────


def test_output_format_all_branches_return_nonempty_strings():
    for ot, ch in [
        ("Pass/Fail", None),
        ("score", None),
        ("numeric", None),
        ("choices", ["a", "b"]),
        ("choices", []),
        (None, None),
        ("unknown", None),
    ]:
        out = output_format_instruction(ot, ch)
        assert isinstance(out, str) and out.strip()
