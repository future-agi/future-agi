"""Tests for ``extract_eval_json`` — the robust multi-stage JSON extractor.

Covers all four extraction stages plus every known shape an LLM tends to
emit: bare JSON, surrounded by prose, in markdown fences, with multiple
JSON blocks, with nested objects, with arrays, and with various failure
modes (malformed, no result key, empty, non-string input).
"""

from __future__ import annotations

import pytest

from agentic_eval.core.utils.json_utils import extract_eval_json


# ──────────────────────────────────────────────────────────────────────
# Stage 1 — direct JSON parse
# ──────────────────────────────────────────────────────────────────────


class TestDirectExtraction:
    def test_bare_object(self):
        assert extract_eval_json('{"result": "Pass", "explanation": "ok"}') == {
            "result": "Pass",
            "explanation": "ok",
        }

    @pytest.mark.parametrize(
        "content",
        [
            '  \n  {"result": 0.7}  \n  ',
            '\t{"result": 0.7}\t',
            '{"result": 0.7}\r\n',
            '\n\n\n{"result": 0.7}',
        ],
    )
    def test_whitespace_around_object(self, content):
        out = extract_eval_json(content)
        assert out == {"result": 0.7}

    @pytest.mark.parametrize(
        "value,expected",
        [
            ('{"result": "Pass"}', "Pass"),
            ('{"result": "Fail"}', "Fail"),
            ('{"result": 0.5}', 0.5),
            ('{"result": 1}', 1),
            ('{"result": true}', True),
            ('{"result": false}', False),
            ('{"result": null}', None),
            ('{"result": ["a", "b"]}', ["a", "b"]),
            ('{"result": []}', []),
        ],
    )
    def test_various_result_value_types(self, value, expected):
        out = extract_eval_json(value)
        assert out is not None
        assert out["result"] == expected

    def test_nested_objects_preserved(self):
        content = '{"result": {"choice": "A", "confidence": 0.9}, "explanation": "x"}'
        out = extract_eval_json(content)
        assert out == {
            "result": {"choice": "A", "confidence": 0.9},
            "explanation": "x",
        }

    def test_object_without_result_key_returns_none(self):
        assert extract_eval_json('{"foo": "bar"}') is None

    def test_object_with_other_keys_plus_result(self):
        out = extract_eval_json('{"result": "Pass", "score": 0.9, "extra": "x"}')
        assert out == {"result": "Pass", "score": 0.9, "extra": "x"}

    def test_unicode_in_strings(self):
        out = extract_eval_json('{"result": "✓", "explanation": "✨ great"}')
        assert out == {"result": "✓", "explanation": "✨ great"}


# ──────────────────────────────────────────────────────────────────────
# Stage 2 — markdown code fences
# ──────────────────────────────────────────────────────────────────────


class TestMarkdownFence:
    def test_json_language_tag(self):
        content = '```json\n{"result": "Fail", "explanation": "x"}\n```'
        assert extract_eval_json(content) == {"result": "Fail", "explanation": "x"}

    def test_plain_fence(self):
        content = '```\n{"result": "Pass"}\n```'
        assert extract_eval_json(content) == {"result": "Pass"}

    @pytest.mark.parametrize(
        "lang_tag",
        ["json", "JSON", "Json", " json", "json ", ""],
    )
    def test_fence_language_variants(self, lang_tag):
        content = f'```{lang_tag}\n{{"result": "Pass"}}\n```'
        out = extract_eval_json(content)
        assert out == {"result": "Pass"}

    def test_prose_before_fence(self):
        content = (
            "Sure! Here is my evaluation:\n\n"
            '```json\n{"result": "Pass", "explanation": "looks good"}\n```\n'
            "Let me know if you need anything else."
        )
        out = extract_eval_json(content)
        assert out == {"result": "Pass", "explanation": "looks good"}

    def test_prose_after_fence(self):
        content = (
            '```json\n{"result": "Fail"}\n```\n'
            "I hope this helps."
        )
        out = extract_eval_json(content)
        assert out == {"result": "Fail"}

    def test_multiline_value_in_fence(self):
        content = (
            '```json\n'
            '{\n'
            '  "result": "Pass",\n'
            '  "explanation": "multi\\nline\\nexplanation"\n'
            '}\n'
            '```'
        )
        out = extract_eval_json(content)
        assert out is not None
        assert out["result"] == "Pass"


# ──────────────────────────────────────────────────────────────────────
# Stage 3 — inline regex for {"result": ...}
# ──────────────────────────────────────────────────────────────────────


class TestInlineResult:
    def test_inline_json_string_result(self):
        content = 'Analysis complete. {"result": "Pass"} done.'
        assert extract_eval_json(content) == {"result": "Pass"}

    def test_inline_json_score_result(self):
        content = 'I rate this 0.8 out of 1.0. {"result": 0.8}'
        out = extract_eval_json(content)
        assert out is not None
        assert out["result"] == 0.8

    def test_inline_array_result(self):
        content = 'Picked: {"result": ["joy", "fear"]}'
        assert extract_eval_json(content) == {"result": ["joy", "fear"]}

    def test_inline_with_explanation(self):
        content = (
            'After review: {"result": "Pass", "explanation": "Good response"}.'
        )
        out = extract_eval_json(content)
        assert out is not None
        assert out["result"] == "Pass"
        assert "explanation" in out

    def test_inline_at_start(self):
        content = '{"result": "Pass"} followed by prose.'
        assert extract_eval_json(content) == {"result": "Pass"}

    def test_inline_at_end(self):
        content = 'Prose first. Final answer: {"result": "Pass"}'
        assert extract_eval_json(content) == {"result": "Pass"}


# ──────────────────────────────────────────────────────────────────────
# Stage 4 — last JSON object fallback
# ──────────────────────────────────────────────────────────────────────


class TestLastJsonFallback:
    def test_multiple_objects_last_wins(self):
        content = (
            'First {"foo": 1} then {"bar": 2}. '
            'Final answer: {"result": "Pass"}'
        )
        assert extract_eval_json(content) == {"result": "Pass"}

    def test_multiple_result_keys_first_wins(self):
        """Stage 3 inline regex picks the FIRST `{"result": ...}` block —
        which keeps weaker judges that hedge ("draft answer then final
        answer") from breaking parse. Documented behaviour, not bug."""
        content = (
            '{"result": "Tentative"} then refined to {"result": "Pass"}'
        )
        assert extract_eval_json(content) == {"result": "Tentative"}


# ──────────────────────────────────────────────────────────────────────
# Non-extractable / failure modes
# ──────────────────────────────────────────────────────────────────────


class TestNonExtractable:
    @pytest.mark.parametrize(
        "value",
        ["", "   ", "\n\n", "\t"],
    )
    def test_empty_or_whitespace_returns_none(self, value):
        assert extract_eval_json(value) is None

    @pytest.mark.parametrize(
        "value",
        [None, 123, 0.5, True, [], {}, object()],
    )
    def test_non_string_returns_none(self, value):
        # Defensive: extractor must not crash on non-string input.
        assert extract_eval_json(value) is None

    def test_pure_prose_returns_none(self):
        assert extract_eval_json("Just some prose with no JSON at all.") is None

    def test_malformed_json_returns_none(self):
        # Truncated JSON, missing closing brace.
        assert extract_eval_json('{"result": "Pass"') is None

    def test_object_without_result_key_returns_none(self):
        assert extract_eval_json('{"foo": "bar", "baz": 1}') is None

    def test_array_only_returns_none(self):
        # Top-level array (not an object with "result") → None.
        assert extract_eval_json('["a", "b"]') is None


# ──────────────────────────────────────────────────────────────────────
# Realistic LLM response shapes the extractor must handle
# ──────────────────────────────────────────────────────────────────────


class TestRealisticLLMShapes:
    def test_thinking_block_then_fenced_json(self):
        content = (
            "<thinking>The user is asking me to evaluate...</thinking>\n\n"
            "Based on the criteria, I assess this as:\n\n"
            '```json\n'
            '{"result": "Pass", "explanation": "The response is polite and helpful."}\n'
            '```'
        )
        out = extract_eval_json(content)
        assert out is not None
        assert out["result"] == "Pass"

    def test_bare_compact_json(self):
        # Structured-output strict mode returns bare JSON, no whitespace.
        out = extract_eval_json('{"result":"Pass","explanation":"OK"}')
        assert out == {"result": "Pass", "explanation": "OK"}

    def test_leading_whitespace_only(self):
        out = extract_eval_json('\n  {"result": 0.85, "explanation": "Good"}\n')
        assert out is not None
        assert out["result"] == 0.85

    def test_judge_picks_multi_choice_array(self):
        content = (
            "After reviewing the emotions in the response, I identify the "
            "following:\n\n"
            '{"result": ["joy", "neutral"], "explanation": "Mixed but positive"}'
        )
        out = extract_eval_json(content)
        assert out is not None
        assert out["result"] == ["joy", "neutral"]

    def test_judge_emits_score_with_prose(self):
        content = (
            "Quality assessment:\n"
            "- Clarity: high\n"
            "- Politeness: high\n"
            "- Helpfulness: medium\n\n"
            'Overall: {"result": 0.7, "explanation": "Three of four dimensions strong"}'
        )
        out = extract_eval_json(content)
        assert out is not None
        assert out["result"] == 0.7

    def test_explanation_with_quotes_escaped(self):
        content = '{"result": "Pass", "explanation": "The user said \\"hello\\"."}'
        out = extract_eval_json(content)
        assert out is not None
        assert out["result"] == "Pass"
        assert "hello" in out["explanation"]
