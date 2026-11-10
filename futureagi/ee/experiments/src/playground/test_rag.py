import logging
import os

import pytest

pytestmark = [pytest.mark.external, pytest.mark.live_llm]
if os.getenv("RUN_EXPERIMENT_EXAMPLES") != "1":
    pytest.skip(
        "Experimental live LLM example; set RUN_EXPERIMENT_EXAMPLES=1 to run.",
        allow_module_level=True,
    )

import dspy
from llm import OpenRouterLLM
from scorer import RAGAnswerEvaluator

logging.basicConfig(level=logging.INFO)
openrouter_llm = OpenRouterLLM(temperature=0.5, model_name="anthropic/claude-3-opus")
dspy.settings.configure(lm=openrouter_llm)


def test_rag_answer_evaluator():
    evaluation_instructions = {
        "1": "Evaluate the answer based on its relevance to the query.",
        "2": "Evaluate the answer based on its coherence and clarity.",
        "3": "Evaluate the answer based on its helpfulness in the given context.",
    }

    evaluator = RAGAnswerEvaluator(evaluation_instructions)

    context = "The user is trying to reset their password but did not receive the reset email."
    query = "How can I reset my password?"
    answer = "You can reset your password by clicking on 'Forgot Password' at the login screen."

    results = evaluator.evaluate_answer(context, query, answer)

    assert isinstance(results, dict)
    for _key, result in results.items():
        assert "question" in result
        assert "answer" in result
        assert "context" in result
        assert "evaluation_judgement" in result
        assert "evaluation_instruction" in result
        assert "score" in result
        assert result["question"] == query
        assert result["answer"] == answer
        assert result["context"] == context

    print("RAGAnswerEvaluator test passed!")


if __name__ == "__main__":
    test_rag_answer_evaluator()
