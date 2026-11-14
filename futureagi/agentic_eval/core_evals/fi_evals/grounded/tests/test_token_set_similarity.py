import pytest

from agentic_eval.core_evals.fi_evals.grounded.similarity import (
    JaccardSimilarity,
    SorensenDiceSimilarity,
)
from agentic_eval.core_evals.fi_evals.grounded.wrapper import AnswerSimilarity
from agentic_eval.core_evals.fi_metrics.metric_type import MetricType
from agentic_eval.core_evals.fi_utils.fi_logging_helper import FiLoggingHelper
from agentic_eval.core_evals.llm_services.fi_api_service import FiApiService


@pytest.mark.parametrize(
    "comparator",
    [JaccardSimilarity(), SorensenDiceSimilarity()],
)
def test_token_set_similarity_is_case_insensitive(comparator):
    assert comparator.compare("Cat", "cat") == 1.0


@pytest.mark.parametrize(
    ("comparator", "expected"),
    [
        (JaccardSimilarity(), 1 / 3),
        (SorensenDiceSimilarity(), 1 / 2),
    ],
)
def test_token_set_similarity_normalizes_case_before_partial_overlap(
    comparator, expected
):
    assert comparator.compare("Red Cat", "cat blue") == pytest.approx(expected)


@pytest.mark.parametrize(
    ("comparator", "partial_score"),
    [("JaccardSimilarity", 1 / 3), ("SorensenDiceSimilarity", 1 / 2)],
)
@pytest.mark.parametrize(
    ("response", "expected_response", "partial"),
    [
        pytest.param("Cat", "cat", False, id="case-only"),
        pytest.param("Red Cat", "cat blue", True, id="partial-overlap"),
        pytest.param("Cat CAT cat", "CAT", False, id="case-folded-duplicates"),
        pytest.param("\tRED\nCat  ", "cat\tblue", True, id="whitespace"),
    ],
)
@pytest.mark.parametrize("threshold", [0.25, 0.75])
def test_answer_similarity_normalizes_tokens_in_evaluation(
    monkeypatch,
    comparator,
    partial_score,
    response,
    expected_response,
    partial,
    threshold,
):
    # Keep usage reporting local; use the real evaluator and result construction.
    monkeypatch.setattr(FiApiService, "log_usage", lambda **kwargs: None)
    monkeypatch.setattr(FiLoggingHelper, "create_eval_request", lambda **kwargs: None)
    evaluator = AnswerSimilarity(
        comparator={"default": comparator}, failure_threshold={"default": threshold}
    )
    data = {"response": response, "expected_response": expected_response}

    batch = evaluator.run(**data)

    score = partial_score if partial else 1.0
    assert len(batch.eval_results) == 1
    result = batch.eval_results[0]
    assert result["metrics"] == [
        {"id": MetricType.SIMILARITY_SCORE.value, "value": pytest.approx(score)}
    ]
    assert result["failure"] is (score < threshold)
    assert result["data"] == data
    assert result["name"] == comparator
    assert str(score) in result["reason"]
    assert (
        "Evaluation failed" if score < threshold else "Evaluation succeeded"
    ) in result["reason"]
