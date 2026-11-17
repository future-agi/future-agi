"""A context ranking result names the model that wrote its summary.

The ranking evaluator writes its summary with its own completion and stores
a model with the result. When the requested model fails three times and a
fallback model writes the summary, the result names that fallback model.

The ranking agents and the score are replaced; the summary completion runs
through the real LLM with only the model library replaced.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from ee.evals.futureagi.eval_ranking import evaluator as ranking_module
from ee.evals.futureagi.eval_ranking.evaluator import RankingEvaluator

REQUESTED = ModelConfigs.CLAUDE_3_5_SONNET
VERTEX = ModelConfigs.VERTEX_GEMINI_3_7_FLASH
SUMMARY = "Document1 (Final score: 0.9)\nDocument2 (Final score: 0.4)"
CONTEXTS = ["The first context.", "The second context."]


def answer(content):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


@pytest.fixture
def library(monkeypatch):
    """The model library, and the ranking agents and score around the
    summary completion."""
    models = MagicMock()
    monkeypatch.setattr(llm_module, "litellm", models)
    monkeypatch.setattr(llm_module, "RETRY_DELAY", 0)
    monkeypatch.setattr(llm_module, "log_to_clickhouse", lambda log_data: None)

    text_agent = MagicMock()
    text_agent.format_data_for_rag_rank.return_value = [{"context": CONTEXTS}]
    monkeypatch.setattr(
        ranking_module, "EvalTextLLM", MagicMock(return_value=text_agent)
    )
    rank_agent = MagicMock()
    rank_agent.process_data.return_value = [
        {
            "question": "Which context answers the question?",
            "context": CONTEXTS,
            "subqueries": [{"subquery": "Which context answers the question?"}],
        }
    ]
    monkeypatch.setattr(
        ranking_module, "RagRankEval", MagicMock(return_value=rank_agent)
    )
    monkeypatch.setattr(
        ranking_module,
        "get_criteria_judegement_score_ragrank",
        lambda results: (0.75, [{"judgment": "Relevant"}]),
    )
    return models


def run_ranking():
    with patch.object(LLM, "_init_client"):
        evaluator = RankingEvaluator(model=REQUESTED.model_name)
    return evaluator._evaluate(
        input="Which context answers the question?",
        context=CONTEXTS,
        criteria="Rank the contexts by relevance.",
    )


def test_a_summary_written_by_a_fallback_model_names_that_model(library):
    library.completion.side_effect = [
        *[RuntimeError("requested model unavailable")] * llm_module.MAX_RETRIES,
        answer(SUMMARY),
    ]

    result = run_ranking()

    assert [c.kwargs["model"] for c in library.completion.call_args_list] == [
        REQUESTED.model_name
    ] * llm_module.MAX_RETRIES + [VERTEX.model_name]
    assert result["reason"] == SUMMARY
    assert result["model"] == VERTEX.model_name


def test_a_summary_written_by_the_requested_model_names_that_model(library):
    library.completion.return_value = answer(SUMMARY)

    result = run_ranking()

    assert [c.kwargs["model"] for c in library.completion.call_args_list] == [
        REQUESTED.model_name
    ]
    assert result["reason"] == SUMMARY
    assert result["model"] == REQUESTED.model_name
