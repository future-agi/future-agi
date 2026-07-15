"""
Regression tests for the rank/linear correlation evaluators.

``calculate_spearman_correlation`` computed rho with the
``1 - 6*sum(d^2)/(n*(n^2-1))`` shortcut. That identity is an algebraic
simplification of Pearson-over-ranks that holds only when both rank vectors are
permutations of 1..n. ``_rank`` correctly assigns midranks to tied values —
which breaks the assumption — so any input containing ties produced a wrong rho,
roughly 10% of the time with the sign inverted. Ties dominate this evaluator's
realistic inputs: Likert responses, 1-5 judge scores, star ratings, ordinal
labels.

The same shortcut had no zero-variance guard, so a constant series (a collapsed
model emitting the same value on every row) scored a perfect 1.0 — the exact
failure the metric is run to catch, inverted into a pass.

Both are fixed by computing rho as Pearson's r over the midranks, which is the
definition the shortcut was standing in for. Expected values below come from
``scipy.stats.spearmanr`` / ``pearsonr``; they are inlined rather than imported
so the suite carries no test-only dependency.
"""

import pytest

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_pearson_correlation,
    calculate_spearman_correlation,
)


def _rho(result):
    """Recover raw rho from the normalized [0,1] score at full precision."""
    return result["result"] * 2 - 1


class TestSpearmanWithTies:
    """Tied values must not break rho. Reference: scipy.stats.spearmanr."""

    @pytest.mark.parametrize(
        "x, y, expected_rho",
        [
            # A perfect inverse relationship, previously reported as no correlation.
            ([2, 1, 1, 1, 1], [1, 2, 2, 2, 2], -1.0),
            # Previously returned +0.1250 — the wrong sign.
            ([2, 2, 3, 3, 2], [2, 3, 2, 2, 2], -0.4082482904638631),
            # Previously returned 0.1571 for genuinely uncorrelated series.
            ([1, 1, 2, 2, 3, 3], [1, 2, 1, 2, 1, 2], 0.0),
            # Likert-style ratings, the common real-world shape.
            ([5, 4, 4, 3, 5, 2], [5, 5, 4, 3, 4, 1], 0.7575757575757573),
        ],
    )
    def test_tied_inputs_match_scipy(self, x, y, expected_rho):
        assert _rho(calculate_spearman_correlation(x, y)) == pytest.approx(
            expected_rho, abs=1e-9
        )

    def test_sign_is_not_inverted(self):
        """The starkest case: a perfect inverse must read as strongly negative."""
        assert (
            _rho(calculate_spearman_correlation([2, 1, 1, 1, 1], [1, 2, 2, 2, 2])) < 0
        )

    def test_rho_stays_within_bounds_on_tied_input(self):
        for x, y in [
            ([1, 1, 2], [2, 2, 1]),
            ([3, 3, 3, 1], [1, 2, 2, 2]),
            ([1, 2, 2, 2, 3], [3, 2, 2, 1, 1]),
        ]:
            assert -1.0 <= _rho(calculate_spearman_correlation(x, y)) <= 1.0


class TestSpearmanZeroVariance:
    """A constant series has undefined correlation; it must not score as perfect."""

    @pytest.mark.parametrize(
        "x, y",
        [
            ([1, 1, 1], [5, 5, 5]),  # previously 1.0 — a maximum score
            ([5, 5, 5, 5, 5], [1, 2, 3, 4, 5]),  # previously 0.75
            ([1, 2, 3], [5, 5, 5]),
        ],
    )
    def test_constant_series_is_not_a_perfect_score(self, x, y):
        result = calculate_spearman_correlation(x, y)
        assert result["result"] == 0.0
        assert "Zero variance" in result["reason"]

    def test_matches_the_pearson_sibling_convention(self):
        """Both correlation evaluators should answer zero variance the same way."""
        assert calculate_spearman_correlation([1, 1, 1], [5, 5, 5]) == (
            calculate_pearson_correlation([1, 1, 1], [5, 5, 5])
        )


class TestSpearmanUntiedBehaviourPreserved:
    """Untied input already matched scipy exactly and must not shift."""

    @pytest.mark.parametrize(
        "x, y, expected_rho",
        [
            ([1, 2, 3, 4, 5], [1, 2, 3, 4, 5], 1.0),
            ([1, 2, 3, 4, 5], [5, 4, 3, 2, 1], -1.0),
            ([1, 2, 3, 4, 5], [2, 1, 4, 3, 5], 0.7999999999999999),
            ([10, 20, 30], [30, 10, 20], -0.5),
        ],
    )
    def test_untied_inputs_still_match_scipy(self, x, y, expected_rho):
        assert _rho(calculate_spearman_correlation(x, y)) == pytest.approx(
            expected_rho, abs=1e-9
        )

    def test_monotone_nonlinear_is_still_a_perfect_rank_correlation(self):
        """Spearman's whole point: monotone but nonlinear reads as 1.0."""
        assert _rho(
            calculate_spearman_correlation([1, 2, 3, 4], [1, 4, 9, 16])
        ) == pytest.approx(1.0, abs=1e-9)


class TestCorrelationInputHandling:
    """Behaviour that predates the fix and must be preserved."""

    @pytest.mark.parametrize(
        "x, y",
        [([1, 2], [1, 2, 3]), ([1], [2]), ([], [])],
    )
    def test_mismatched_or_too_short_inputs_are_rejected(self, x, y):
        for fn in (calculate_spearman_correlation, calculate_pearson_correlation):
            result = fn(x, y)
            assert result["result"] == 0.0
            assert "Invalid input" in result["reason"]

    @pytest.mark.parametrize(
        "series", ["1,2,3,4,5", "[1, 2, 3, 4, 5]", [1, 2, 3, 4, 5]]
    )
    def test_csv_json_and_list_inputs_are_all_accepted(self, series):
        assert _rho(calculate_spearman_correlation(series, series)) == pytest.approx(
            1.0
        )

    def test_reason_string_still_reports_rho_and_normalized_score(self):
        reason = calculate_spearman_correlation([1, 2, 3], [1, 2, 3])["reason"]
        assert "rho=" in reason and "normalized=" in reason


class TestPearsonUnchanged:
    """_pearson_r was extracted from this function; its behaviour must not move."""

    @pytest.mark.parametrize(
        "x, y, expected_r",
        [
            ([1, 2, 3, 4], [2, 4, 6, 8], 1.0),
            ([1, 2, 3, 4], [8, 6, 4, 2], -1.0),
            ([1, 2, 3, 4], [2, 4, 6, 9], 0.9943767126843689),
        ],
    )
    def test_pearson_matches_scipy(self, x, y, expected_r):
        assert _rho(calculate_pearson_correlation(x, y)) == pytest.approx(
            expected_r, abs=1e-9
        )

    def test_pearson_zero_variance_still_guarded(self):
        result = calculate_pearson_correlation([1, 1, 1], [5, 5, 5])
        assert result["result"] == 0.0
        assert "Zero variance in one or both inputs" == result["reason"]

    def test_pearson_reason_string_unchanged(self):
        assert (
            "Pearson r="
            in calculate_pearson_correlation([1, 2, 3], [1, 2, 3])["reason"]
        )
