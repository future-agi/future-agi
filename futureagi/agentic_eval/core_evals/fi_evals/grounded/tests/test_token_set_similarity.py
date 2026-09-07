import pytest

from agentic_eval.core_evals.fi_evals.grounded.similarity import (
    JaccardSimilarity,
    SorensenDiceSimilarity,
)


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
