"""Regression tests for Spearman rank correlation.

Spearman's rho is Pearson's r over the ranks. The familiar
``1 - 6*sum(d^2)/(n*(n^2-1))`` shortcut only holds when both rank vectors are
permutations of 1..n, which ties break. The evaluator assigned midranks to ties
and then applied the shortcut anyway, so any input containing a duplicate scored
wrong, and a constant input scored a perfect 1.0.

Expected rho values below were cross-checked against ``scipy.stats.spearmanr``.
scipy is not imported here, so these tests add no dependency.
"""

import pytest

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_pearson_correlation,
    calculate_spearman_correlation,
)


def _rho(output, expected):
    """Return rho, undoing the [-1, 1] -> [0, 1] normalisation applied to result."""
    return calculate_spearman_correlation(output, expected)["result"] * 2 - 1


class TestNoTies:
    """The shortcut and the definition agree here; these must not regress."""

    def test_perfect_positive(self):
        assert _rho([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert _rho([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)

    def test_monotone_nonlinear_is_still_perfect(self):
        # Spearman measures monotonicity, not linearity.
        assert _rho([1, 2, 3, 4, 5], [1, 4, 9, 16, 25]) == pytest.approx(1.0)

    def test_normalisation_maps_to_unit_interval(self):
        assert calculate_spearman_correlation([1, 2, 3, 4], [4, 3, 2, 1])[
            "result"
        ] == pytest.approx(0.0)
        assert calculate_spearman_correlation([1, 2, 3, 4], [1, 2, 3, 4])[
            "result"
        ] == pytest.approx(1.0)


class TestTiedRanks:
    """Every case here was wrong before: midranks + the d^2 shortcut."""

    @pytest.mark.parametrize(
        "x, y, expected_rho, shortcut_rho",
        [
            ([1, 1, 2, 3], [1, 2, 2, 3], 0.8333333333, 0.85),
            ([1, 1, 1, 2], [1, 2, 3, 4], 0.7745966692, 0.80),
            ([1, 1, 1, 1, 2], [5, 4, 3, 2, 1], -0.7071067812, -0.25),
            ([1, 2, 2, 3], [1, 2, 3, 4], 0.9486832981, 0.95),
        ],
    )
    def test_matches_definition_not_shortcut(self, x, y, expected_rho, shortcut_rho):
        actual = _rho(x, y)
        assert actual == pytest.approx(
            expected_rho
        ), f"rho for {x} vs {y} should be {expected_rho}, got {actual}"
        # Guard against a silent revert to the shortcut.
        assert actual != pytest.approx(shortcut_rho, abs=1e-6)

    def test_heavy_ties_error_was_large(self):
        # The worst case found: the shortcut reported -0.25 for data whose true
        # rank correlation is -0.707 -- a 0.46 error in rho, 0.23 in the score.
        x, y = [1, 1, 1, 1, 2], [5, 4, 3, 2, 1]
        assert _rho(x, y) == pytest.approx(-0.7071067812)
        assert calculate_spearman_correlation(x, y)["result"] == pytest.approx(
            0.1464466094
        )

    def test_all_values_tied_on_one_side_only(self):
        # x is constant -> its ranks are constant -> rho undefined.
        result = calculate_spearman_correlation([2, 2, 2, 2], [1, 2, 3, 4])
        assert result["result"] == pytest.approx(0.5)
        assert "Zero variance" in result["reason"]

    def test_ties_do_not_push_score_outside_unit_interval(self):
        # With enough ties the shortcut could leave [-1, 1], and the normalised
        # score with it.
        for x, y in [
            ([1, 1, 1, 1, 1, 2], [6, 5, 4, 3, 2, 1]),
            ([1, 1, 2, 2, 3, 3], [3, 3, 2, 2, 1, 1]),
            ([1, 1, 1, 2, 2, 2], [1, 2, 3, 4, 5, 6]),
        ]:
            score = calculate_spearman_correlation(x, y)["result"]
            assert 0.0 <= score <= 1.0, f"{x} vs {y} scored {score}"


class TestZeroVariance:
    """A collapsed model must not score as a perfect correlation."""

    def test_both_inputs_constant(self):
        # Regression: d^2 was 0 for every pair, so the shortcut returned rho=1.0
        # and a perfect score of 1.0 -- the exact failure this metric is run to
        # detect, inverted into a pass.
        result = calculate_spearman_correlation([3, 3, 3, 3], [3, 3, 3, 3])
        assert result["result"] == pytest.approx(0.5)
        assert result["result"] != pytest.approx(1.0)
        assert "Zero variance" in result["reason"]

    def test_predictions_collapsed_against_varied_truth(self):
        # A model emitting the same value every row.
        result = calculate_spearman_correlation([0.7] * 5, [1, 2, 3, 4, 5])
        assert result["result"] == pytest.approx(0.5)

    def test_reason_explains_why(self):
        reason = calculate_spearman_correlation([1, 1, 1], [1, 1, 1])["reason"]
        assert "undefined" in reason.lower()


class TestInputHandling:
    def test_json_string_inputs(self):
        assert _rho("[1, 2, 3, 4]", "[1, 2, 3, 4]") == pytest.approx(1.0)

    def test_comma_separated_string_inputs(self):
        assert _rho("1, 1, 2, 3", "1, 2, 2, 3") == pytest.approx(0.8333333333)

    def test_length_mismatch_returns_zero(self):
        result = calculate_spearman_correlation([1, 2, 3], [1, 2])
        assert result["result"] == 0.0
        assert "3 vs 2" in result["reason"]

    def test_single_pair_is_rejected(self):
        # n < 2 also protects the old shortcut's n*(n^2-1) denominator.
        assert calculate_spearman_correlation([1], [1])["result"] == 0.0

    def test_empty_inputs_are_rejected(self):
        assert calculate_spearman_correlation([], [])["result"] == 0.0

    def test_order_of_arguments_is_symmetric(self):
        x, y = [1, 1, 2, 3], [1, 2, 2, 3]
        assert _rho(x, y) == pytest.approx(_rho(y, x))


class TestPearsonUnchanged:
    """`_pearson_r` was factored out of Pearson; its behaviour must not move."""

    def test_perfect_positive(self):
        assert calculate_pearson_correlation([1, 2, 3], [1, 2, 3])[
            "result"
        ] == pytest.approx(1.0)

    def test_perfect_negative(self):
        assert calculate_pearson_correlation([1, 2, 3], [3, 2, 1])[
            "result"
        ] == pytest.approx(0.0)

    def test_known_value(self):
        # scipy.stats.pearsonr([1,2,3,4], [2,4,5,9]).statistic == 0.9647638212
        assert calculate_pearson_correlation([1, 2, 3, 4], [2, 4, 5, 9])[
            "result"
        ] == pytest.approx((0.9647638212 + 1) / 2)

    def test_zero_variance_still_returns_zero(self):
        # Deliberately left as-is. Note this differs from Spearman above and
        # from the platform YAML, both of which report 0.5. See PR notes.
        result = calculate_pearson_correlation([1, 1, 1], [1, 2, 3])
        assert result["result"] == 0.0
        assert "Zero variance" in result["reason"]

    def test_length_mismatch_returns_zero(self):
        assert calculate_pearson_correlation([1, 2], [1, 2, 3])["result"] == 0.0
