"""Optimizer templates in the f-string format are rendered by the strict renderer.

A field inserts a named value, one of its dictionary keys or a list index. Any
other field is refused with a plain message.
"""

import json

import litellm
import pytest

from ee.agent_opt.generators.litellm_generator import LiteLLMGenerator
from ee.agent_opt.optimizers.bayesian_search import BayesianSearchOptimizer

REFUSED = "This template can only insert a named value or one of its keys."
EXAMPLE = {"question": "What is two plus two?", "answer": "Four"}


class FakeResponse:
    def __init__(self, content):
        message = type("M", (), {"content": content})()
        self.choices = [type("C", (), {"message": message})()]


@pytest.fixture
def completions(monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return FakeResponse("reply")

    monkeypatch.setattr(litellm, "completion", fake_completion)
    return calls


class TestGeneratorTemplate:
    def test_plain_names_render_as_before(self, completions):
        generator = LiteLLMGenerator("gpt-4o-mini", "Summarize {text} for {reader}")

        assert generator.generate({"text": "the report", "reader": "Ana"}) == "reply"
        content = completions[0]["messages"][0]["content"]
        assert content == "Summarize the report for Ana"

    def test_a_field_that_is_not_a_key_or_index_is_refused(self, completions):
        generator = LiteLLMGenerator("gpt-4o-mini", "Summarize {text.__class__}")

        with pytest.raises(ValueError) as refused:
            generator.generate({"text": "the report"})
        assert str(refused.value) == REFUSED
        assert completions == []


class TestBayesianExampleTemplate:
    def _optimizer(self, template):
        return BayesianSearchOptimizer(
            example_template=template, synthesize_examples=False
        )

    def test_plain_names_render_as_before(self):
        optimizer = self._optimizer("Question: {question}\nAnswer: {answer}")

        formatted = optimizer._format_example(EXAMPLE)
        assert formatted == "Question: What is two plus two?\nAnswer: Four"

    def test_a_field_that_is_not_a_key_or_index_falls_back_to_the_example(self):
        optimizer = self._optimizer("Question: {question.__class__}")

        formatted = optimizer._format_example(EXAMPLE)
        assert "class" not in formatted
        assert formatted == json.dumps(EXAMPLE, indent=2, ensure_ascii=False)
