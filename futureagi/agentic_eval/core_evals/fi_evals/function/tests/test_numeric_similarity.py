import pytest

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_numeric_similarity,
)


def _score(output, expected):
    return calculate_numeric_similarity(output, expected)["result"]


class TestNumericSimilarityNegatives:
    def test_close_negatives_score_high(self):
        assert _score(-100, -110) == pytest.approx(1 - 10 / 110)
        assert _score(-40, -38) == pytest.approx(0.95)
        assert _score(-1, -2) == pytest.approx(0.5)

    def test_mixed_sign_clamps_to_zero(self):
        assert _score(5, -5) == 0.0
        assert _score(1, -1) == 0.0

    @pytest.mark.parametrize(
        "a,b", [(-100, -110), (5, -5), (-7, 3), (-0.5, -2.5), (-50, 50)]
    )
    def test_score_stays_in_unit_interval(self, a, b):
        assert 0.0 <= _score(a, b) <= 1.0


class TestNumericSimilarityUnchangedBehaviour:
    def test_identical_values_are_fully_similar(self):
        assert _score(7, 7) == pytest.approx(1.0)
        assert _score(-7, -7) == pytest.approx(1.0)
        assert _score(0, 0) == pytest.approx(1.0)

    def test_positive_inputs_unchanged(self):
        assert _score(100, 110) == pytest.approx(1 - 10 / 110)
        assert _score(0.5, 0.6) == pytest.approx(0.9)

    def test_string_inputs_are_parsed(self):
        assert _score("-40", "-38") == pytest.approx(0.95)
