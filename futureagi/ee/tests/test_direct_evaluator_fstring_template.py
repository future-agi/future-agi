"""Dataset optimization renders f-string templates with the strict renderer."""

import asyncio
from unittest.mock import AsyncMock

from ee.agenthub.fix_your_agent.direct_evaluator import DirectEvaluator

REFUSED = "This template can only insert a named value or one of its keys."


def test_fstring_template_renders_names_and_refuses_other_fields():
    evaluator = DirectEvaluator([], template_format="f-string")
    evaluator._call_llm_async = AsyncMock(return_value="answer")
    row = {"question": "ping", "account": '{"name": "Ada"}'}

    def run(template):
        return asyncio.run(
            evaluator._evaluate_single_input_impl(
                0, {"agent_prompt": template, "input": row}
            )
        )

    rendered = run("Reply to {question} for {account[name]}")
    assert rendered.metadata["filled_prompt"] == "Reply to ping for Ada"
    evaluator._call_llm_async.assert_awaited_once_with("Reply to ping for Ada")

    evaluator._call_llm_async.reset_mock()
    refused = run("Reply to {question.__class__}")
    evaluator._call_llm_async.assert_not_awaited()
    assert refused.score == 0.0
    assert refused.reason == f"Evaluation failed: {REFUSED}"
