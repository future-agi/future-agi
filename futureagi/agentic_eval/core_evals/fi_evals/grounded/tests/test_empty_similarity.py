import pytest

from agentic_eval.core_evals.fi_evals.grounded.similarity import (
    JaccardSimilarity,
    NormalisedLevenshteinSimilarity,
    SorensenDiceSimilarity,
)
from agentic_eval.core_evals.fi_evals.grounded.wrapper import AnswerSimilarity
from agentic_eval.core_evals.fi_metrics.metric_type import MetricType
from agentic_eval.core_evals.fi_utils.fi_logging_helper import FiLoggingHelper
from agentic_eval.core_evals.llm_services.fi_api_service import FiApiService


@pytest.mark.parametrize(
    ("comparator", "left", "right", "expected"),
    [
        (JaccardSimilarity, "", "", 1.0),
        (SorensenDiceSimilarity, "", "", 1.0),
        (NormalisedLevenshteinSimilarity, "", "", 1.0),
        (JaccardSimilarity, "   ", "\t\n", 1.0),
        (SorensenDiceSimilarity, "   ", "\t\n", 1.0),
        (NormalisedLevenshteinSimilarity, "   ", "\t\n", 0.0),
        (JaccardSimilarity, "", "abc", 0.0),
        (SorensenDiceSimilarity, "", "abc", 0.0),
        (NormalisedLevenshteinSimilarity, "", "abc", 0.0),
        (JaccardSimilarity, "Red Cat", "cat blue", 1 / 3),
        (SorensenDiceSimilarity, "Red Cat", "cat blue", 1 / 2),
        (NormalisedLevenshteinSimilarity, "kitten", "sitting", 1 - 3 / 7),
    ],
)
def test_similarity_score(comparator, left, right, expected):
    assert comparator().compare(left, right) == expected


@pytest.mark.parametrize(
    "name",
    ["JaccardSimilarity", "SorensenDiceSimilarity", "NormalisedLevenshteinSimilarity"],
)
def test_empty_answer_similarity(monkeypatch, name):
    monkeypatch.setattr(FiApiService, "log_usage", lambda **kwargs: None)
    monkeypatch.setattr(FiLoggingHelper, "create_eval_request", lambda **kwargs: None)
    evaluator = AnswerSimilarity(
        comparator={"default": name}, failure_threshold={"default": 0.5}
    )
    (result,) = evaluator.run(response="", expected_response="").eval_results
    assert result["metrics"] == [
        {"id": MetricType.SIMILARITY_SCORE.value, "value": 1.0}
    ]
    assert result["failure"] is False
