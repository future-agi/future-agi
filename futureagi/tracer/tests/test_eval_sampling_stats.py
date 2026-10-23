"""Pure statistics for sampling audits and the precision planner.

The expected values live in ``fixtures/sampling_stats_vectors.json``, shared
with the frontend mirror's vitest suite. Each vector is checked twice: against
this module, and against an independent oracle (closed form, exact fraction,
brute force or scipy) so that the shared file itself cannot be wrong in a way
both implementations would agree on.
"""

import json
import math
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import binomtest, chi2, norm

from tracer.services.eval_tasks.sampling import stats

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "sampling_stats_vectors.json").read_text()
)
TOLERANCE = VECTORS["tolerance"]


def close(actual, expected):
    if expected is None:
        return actual is None
    if isinstance(expected, list):
        return actual == expected
    return actual is not None and math.isclose(
        actual, expected, rel_tol=TOLERANCE, abs_tol=1e-300
    )


def test_z_table_is_exact_normal_quantiles():
    for key, value in VECTORS["z_values"].items():
        confidence = float(key)
        assert stats.z_value(confidence) == value
        assert value == float(norm.ppf(1 - (1 - confidence) / 2))
    with pytest.raises(stats.StatsInputError):
        stats.z_value(0.97)


@pytest.mark.parametrize("vector", VECTORS["margin_of_error"])
def test_margin_of_error_vectors(vector):
    actual = stats.margin_of_error(
        vector["n"],
        vector["population"],
        proportion=vector["proportion"],
        confidence=vector["confidence"],
    )
    assert close(actual, vector["expected"]), (vector, actual)
    n, population, p = vector["n"], vector["population"], vector["proportion"]
    if 0 < n < population:  # independent closed form with scipy's quantile
        z = norm.ppf(1 - (1 - vector["confidence"]) / 2)
        oracle = z * math.sqrt(p * (1 - p) / n * (population - n) / (population - 1))
        assert math.isclose(vector["expected"], oracle, rel_tol=1e-12)


@pytest.mark.parametrize("vector", VECTORS["required_sample_size"])
def test_required_sample_size_vectors_are_minimal(vector):
    kwargs = {"proportion": vector["proportion"], "confidence": vector["confidence"]}
    actual = stats.required_sample_size(
        vector["margin"], vector["population"], **kwargs
    )
    assert actual == vector["expected"]
    population = vector["population"]
    if 0 < population <= 100_000:
        # Brute force: the smallest n whose margin is within the requested one.
        brute = next(
            n
            for n in range(1, population + 1)
            if stats.margin_of_error(n, population, **kwargs) <= vector["margin"]
        )
        assert brute == actual


def test_required_sample_size_is_minimal_on_a_grid():
    for population in (1, 2, 7, 50, 333, 1000, 4096):
        for margin in (0.01, 0.025, 0.05, 0.1, 0.3):
            for p in (0.5, 0.2, 0.05):
                n = stats.required_sample_size(margin, population, proportion=p)
                assert stats.margin_of_error(n, population, proportion=p) <= margin
                if n > 1:
                    assert (
                        stats.margin_of_error(n - 1, population, proportion=p) > margin
                    )


@pytest.mark.parametrize("vector", VECTORS["stratified_margin_of_error"])
def test_stratified_margin_vectors(vector):
    cells = [tuple(cell) for cell in vector["cells"]]
    actual = stats.stratified_margin_of_error(cells, confidence=vector["confidence"])
    assert close(actual, vector["expected"]), actual


def test_stratified_margin_of_one_stratum_is_the_simple_margin():
    for n, population, p in ((10, 100, 0.5), (99, 100, 0.3), (5, 6, 0.9)):
        assert math.isclose(
            stats.stratified_margin_of_error([(population, n, p)]),
            stats.margin_of_error(n, population, proportion=p),
            rel_tol=1e-15,
        )


@pytest.mark.parametrize("vector", VECTORS["allocate"])
def test_allocation_vectors(vector):
    call = {
        "method": vector["method"],
        "minimum": vector["minimum"],
        "proportions": vector["proportions"],
    }
    if "error" in vector:
        with pytest.raises(stats.StatsInputError, match=vector["error"]):
            stats.allocate(vector["populations"], vector["total"], **call)
        return
    assert (
        stats.allocate(vector["populations"], vector["total"], **call)
        == vector["expected"]
    )


def _bisection_reference(populations, total, method, minimum, proportions):
    """Independent allocation: bisection on lambda, then the same rounding."""
    weights = stats.allocation_weights(
        populations, method=method, proportions=proportions
    )
    lows = [min(minimum, value) for value in populations]
    target = min(
        total,
        sum(
            high if weight > 0 else low
            for weight, low, high in zip(weights, lows, populations, strict=True)
        ),
    )

    def level(lam):
        return sum(
            min(high, max(low, lam * weight)) if weight > 0 else low
            for weight, low, high in zip(weights, lows, populations, strict=True)
        )

    low_lam, high_lam = 0.0, 1.0
    while level(high_lam) < target:
        high_lam *= 2
    for _ in range(200):
        middle = (low_lam + high_lam) / 2
        low_lam, high_lam = (
            (middle, high_lam) if level(middle) < target else (low_lam, middle)
        )
    return [
        min(high, max(low, high_lam * weight)) if weight > 0 else float(low)
        for weight, low, high in zip(weights, lows, populations, strict=True)
    ], target


def test_allocation_properties_against_a_bisection_reference():
    rng = np.random.default_rng(20261023)
    for _case in range(400):
        strata = int(rng.integers(1, 12))
        populations = [int(value) for value in rng.integers(0, 400, size=strata)]
        method = stats.ALLOCATIONS[int(rng.integers(0, 3))]
        proportions = [float(value) for value in rng.uniform(0, 1, size=strata)]
        minimum = int(rng.integers(0, 20))
        floor_total = sum(min(minimum, value) for value in populations)
        total = floor_total + int(rng.integers(0, 600))
        result = stats.allocate(
            populations,
            total,
            method=method,
            minimum=minimum,
            proportions=proportions,
        )
        continuous, target = _bisection_reference(
            populations, total, method, minimum, proportions
        )
        assert sum(result) == target
        for size, population, value in zip(
            result, populations, continuous, strict=True
        ):
            assert min(minimum, population) <= size <= population
            # Rounding moves each stratum by less than one unit.
            assert abs(size - value) < 1 + 1e-6


@pytest.mark.parametrize("vector", VECTORS["kish"])
def test_kish_vectors(vector):
    pairs = [tuple(pair) for pair in vector["weighted_counts"]]
    assert close(stats.kish_effective_n(pairs), vector["effective_n"])
    assert close(stats.design_effect(pairs), vector["design_effect"])
    assert close(stats.weight_cv(pairs), vector["weight_cv"])
    if pairs:  # exact rational oracle
        first = sum(Fraction(weight) * count for weight, count in pairs)
        second = sum(Fraction(weight) ** 2 * count for weight, count in pairs)
        assert math.isclose(
            vector["effective_n"], float(first**2 / second), rel_tol=1e-15
        )


def test_kish_design_effect_is_one_plus_weight_cv_squared():
    pairs = [(1 / 0.125, 5), (1 / 0.5, 4), (1.0, 7)]
    weights = np.repeat([w for w, _c in pairs], [c for _w, c in pairs])
    cv = weights.std() / weights.mean()
    assert math.isclose(stats.design_effect(pairs), 1 + cv * cv, rel_tol=1e-12)


@pytest.mark.parametrize("vector", VECTORS["chi_square_sf"])
def test_chi_square_sf_vectors(vector):
    actual = stats.chi_square_sf(vector["statistic"], vector["degrees_of_freedom"])
    assert close(actual, vector["expected"]), actual
    oracle = float(chi2.sf(vector["statistic"], vector["degrees_of_freedom"]))
    assert math.isclose(actual, oracle, rel_tol=1e-10)


def test_chi_square_sf_matches_scipy_across_branches():
    for degrees in (1, 2, 3, 5, 10, 31, 99, 400):
        for statistic in (
            0.01,
            0.7,
            degrees * 0.5,
            degrees,
            degrees + 1,
            degrees * 2,
            degrees * 4 + 30,
        ):
            assert math.isclose(
                stats.chi_square_sf(statistic, degrees),
                float(chi2.sf(statistic, degrees)),
                rel_tol=1e-9,
                abs_tol=1e-300,
            ), (statistic, degrees)


@pytest.mark.parametrize("vector", VECTORS["binomial_two_sided_p"])
def test_binomial_vectors(vector):
    actual = stats.binomial_two_sided_p(vector["k"], vector["n"], vector["p"])
    assert math.isclose(actual, vector["expected"], rel_tol=1e-9), actual
    oracle = binomtest(vector["k"], vector["n"], vector["p"]).pvalue
    assert math.isclose(actual, oracle, rel_tol=1e-9)


def test_binomial_matches_scipy_on_a_grid():
    for n, p in ((1, 0.5), (17, 0.3), (250, 0.02), (2_000, 0.1), (60_000, 0.25)):
        mean, sd = n * p, math.sqrt(n * p * (1 - p))
        for offset in (-4.5, -3.6, -2, -0.3, 0, 0.4, 2.2, 3.7, 5):
            k = min(n, max(0, round(mean + offset * sd)))
            assert math.isclose(
                stats.binomial_two_sided_p(k, n, p),
                binomtest(k, n, p).pvalue,
                rel_tol=1e-7,
                abs_tol=1e-300,
            ), (k, n, p)


@pytest.mark.parametrize("vector", VECTORS["standardized_difference"])
def test_standardized_difference_vectors(vector):
    actual = stats.standardized_difference(
        vector["sample_share"], vector["population_share"]
    )
    assert close(actual, vector["expected"]), actual


@pytest.mark.parametrize("vector", VECTORS["chi_square_goodness_of_fit"])
def test_goodness_of_fit_vectors(vector):
    result = stats.chi_square_goodness_of_fit(vector["observed"], vector["shares"])
    assert close(result["statistic"], vector["statistic"])
    assert result["degrees_of_freedom"] == vector["degrees_of_freedom"]
    assert close(result["p_value"], vector["p_value"])


def test_srm_false_alarm_and_power():
    """T8(a): exact SRM test at p < 0.0005, measured by seeded simulation.

    False alarms over 10,000 correct runs (2,000 units at a 10% rate) and power
    against a 5% relative under-selection (9.5% realized) in a stratum of
    200,000 units at a 10% rate over 1,000 runs.
    """
    rng = np.random.default_rng(8689)
    correct = rng.binomial(2_000, 0.10, size=10_000)
    false_alarms = sum(stats.srm_test(int(k), 2_000, 0.10)["flagged"] for k in correct)
    assert false_alarms / 10_000 <= 0.001, false_alarms
    under = rng.binomial(200_000, 0.095, size=1_000)
    detected = sum(stats.srm_test(int(k), 200_000, 0.10)["flagged"] for k in under)
    assert detected / 1_000 >= 0.99, detected
    print(f"srm false alarms {false_alarms}/10000, power {detected}/1000")


def test_srm_edge_rates():
    assert stats.srm_test(0, 0, 0.1) == {"z": None, "p_value": None, "flagged": False}
    assert stats.srm_test(0, 50, 0.0)["flagged"] is False
    assert stats.srm_test(1, 50, 0.0)["flagged"] is True
    assert stats.srm_test(50, 50, 1.0)["flagged"] is False
    assert stats.srm_test(10, 100, 0.1)["p_value"] == 1.0


@pytest.mark.parametrize(
    "call",
    [
        lambda: stats.margin_of_error(5, 4),
        lambda: stats.margin_of_error(-1, 4),
        lambda: stats.margin_of_error(1, 4, proportion=1.5),
        lambda: stats.required_sample_size(0, 10),
        lambda: stats.required_sample_size(1, 10),
        lambda: stats.allocate([10], 5, method="random"),
        lambda: stats.allocate([10, 10], 5, proportions=[0.5]),
        lambda: stats.kish_effective_n([(0, 3)]),
        lambda: stats.chi_square_sf(1, 0),
        lambda: stats.binomial_two_sided_p(6, 5, 0.5),
    ],
)
def test_inputs_outside_the_domain_are_rejected(call):
    with pytest.raises(stats.StatsInputError):
        call()
