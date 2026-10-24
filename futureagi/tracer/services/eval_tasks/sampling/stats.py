"""Pure design-based statistics for evaluation sampling.

Every statistical formula the sampling feature reports or plans with lives in
this module: no Django, no I/O, no third-party dependency. The task editor's
precision planner mirrors the planning functions (margin of error, minimum n,
stratified margin, allocation, Kish) in
``frontend/src/sections/tasks/components/samplingStats.js``. Both
implementations are checked against the same hand-derived vectors in
``futureagi/tracer/tests/fixtures/sampling_stats_vectors.json``, so they
cannot drift apart silently.

Conventions:

* Proportions are in [0, 1]; populations and sample sizes are integers.
* Margins of error are for a proportion, two-sided, with the finite-population
  correction ``(N - n) / (N - 1)``; ``p = 0.5`` is the conservative default.
* Allocations are integers that sum to the (reachable) budget, never exceed a
  stratum's population and never fall below the per-stratum minimum.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

# Two-sided standard normal quantiles, Phi^-1(1 - alpha / 2).
Z_VALUES = {
    0.8: 1.2815515655446004,
    0.9: 1.644853626951472,
    0.95: 1.959963984540054,
    0.99: 2.5758293035489004,
}
# Sample-ratio-mismatch threshold (Microsoft ExP gates results at p < 0.0005).
SRM_ALPHA = 0.0005
# |standardized difference| above this is a conventional imbalance flag.
BALANCE_THRESHOLD = 0.1
ALLOCATIONS = ("proportional", "balanced", "neyman")
# Fractional parts are compared on a 1e-9 grid so that ties (and float noise
# around them) break by stratum order identically in Python and JavaScript.
_FRACTION_GRID = 1_000_000_000
# Above this variance the exact binomial walk is replaced by the normal tail.
_EXACT_BINOMIAL_MAX_VARIANCE = 1_000_000


class StatsInputError(ValueError):
    """An input outside the documented domain of a formula."""


def z_value(confidence: float) -> float:
    try:
        return Z_VALUES[confidence]
    except (KeyError, TypeError):
        raise StatsInputError(
            "confidence must be one of 0.8, 0.9, 0.95 or 0.99."
        ) from None


def _count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StatsInputError(f"{name} must be a non-negative integer.")
    return value


def _unit_interval(value: object, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise StatsInputError(f"{name} must be a number from 0 to 1.")
    return float(value)


# --- Precision of a proportion ------------------------------------------------


def margin_of_error(
    n: int, population: int, *, proportion: float = 0.5, confidence: float = 0.95
) -> float | None:
    """Half-width of the confidence interval for a proportion, with FPC.

    ``None`` when nothing is sampled (no estimate exists); ``0.0`` for a
    census (n = N), whose proportion is known exactly.
    """

    n, population = _count(n, "n"), _count(population, "population")
    if n > population:
        raise StatsInputError("n cannot exceed the population.")
    p = _unit_interval(proportion, "proportion")
    z = z_value(confidence)
    if n == 0:
        return None
    if n == population:
        return 0.0
    return z * math.sqrt(p * (1 - p) / n * (population - n) / (population - 1))


def required_sample_size(
    margin: float,
    population: int,
    *,
    proportion: float = 0.5,
    confidence: float = 0.95,
) -> int:
    """Smallest n whose margin of error (with FPC) is at most ``margin``.

    Exact inversion of :func:`margin_of_error`:
    ``n = n0 * N / (n0 + N - 1)`` with ``n0 = z^2 p (1 - p) / margin^2``.
    """

    if (
        isinstance(margin, bool)
        or not isinstance(margin, (int, float))
        or not math.isfinite(margin)
        or not 0 < margin < 1
    ):
        raise StatsInputError("margin must be a number between 0 and 1.")
    population = _count(population, "population")
    p = _unit_interval(proportion, "proportion")
    z = z_value(confidence)
    if population == 0:
        return 0
    variance = p * (1 - p)
    if variance == 0:
        return 1
    z_variance = z * z * variance
    # Algebraic inversion without dividing by margin^2 (which may underflow).
    exact = z_variance * population / (z_variance + margin * margin * (population - 1))
    n = max(1, min(population, math.ceil(exact)))
    # Resolve a floating-point boundary using the reported margin itself,
    # rather than subtracting an epsilon that can understate the required n.
    if (
        n > 1
        and margin_of_error(n - 1, population, proportion=p, confidence=confidence)
        <= margin
    ):
        n -= 1
    if margin_of_error(n, population, proportion=p, confidence=confidence) > margin:
        n += 1
    return n


def stratified_margin_of_error(
    cells: Sequence[tuple[int, int, float]], *, confidence: float = 0.95
) -> float | None:
    """Margin of error of the stratified (population-weighted) proportion.

    ``cells`` holds ``(N_h, n_h, p_h)``. ``Var = sum W_h^2 p_h q_h / n_h *
    (N_h - n_h) / (N_h - 1)`` with ``W_h = N_h / N``. ``None`` when the
    population is empty or a non-empty stratum has no sampled unit (its share
    of the population cannot be estimated at all).
    """

    z = z_value(confidence)
    checked = []
    for population, n, proportion in cells:
        population, n = _count(population, "population"), _count(n, "n")
        if n > population:
            raise StatsInputError("n cannot exceed the population.")
        checked.append((population, n, _unit_interval(proportion, "proportion")))
    total = sum(population for population, _n, _p in checked)
    if total == 0:
        return None
    variance = 0.0
    for population, n, p in checked:
        if population == 0:
            continue
        if n == 0:
            return None
        if n == population:
            continue
        share = population / total
        variance += (
            share * share * p * (1 - p) / n * (population - n) / (population - 1)
        )
    return z * math.sqrt(variance)


# --- Allocation of a unit budget across strata -------------------------------


def allocation_weights(
    populations: Sequence[int],
    *,
    method: str,
    proportions: Sequence[float] | None = None,
) -> list[float]:
    """Relative allocation weight of each stratum.

    proportional: ``N_h``; balanced: 1 per non-empty stratum; neyman:
    ``N_h * S_h`` with ``S_h = sqrt(p_h (1 - p_h))`` from the anticipated
    proportion (0.5 when unknown, which makes Neyman equal to proportional).
    """

    if method not in ALLOCATIONS:
        raise StatsInputError("method must be proportional, balanced or neyman.")
    populations = [_count(value, "population") for value in populations]
    if proportions is None:
        proportions = [0.5] * len(populations)
    if len(proportions) != len(populations):
        raise StatsInputError("Give one anticipated proportion per stratum.")
    anticipated = [_unit_interval(value, "proportion") for value in proportions]
    if method == "proportional":
        return [float(value) for value in populations]
    if method == "balanced":
        return [1.0 if value > 0 else 0.0 for value in populations]
    return [
        value * math.sqrt(p * (1 - p))
        for value, p in zip(populations, anticipated, strict=True)
    ]


def _level(lam: float, weights, lows, highs) -> float:
    total = 0.0
    for weight, low, high in zip(weights, lows, highs, strict=True):
        total += min(high, max(low, lam * weight)) if weight > 0 else low
    return total


def _water_fill(weights, lows, highs, total: int) -> list[float]:
    """Continuous x_h = clamp(lambda * w_h, lo_h, hi_h) with sum x_h = total.

    The level is piecewise linear in lambda with breakpoints lo/w and hi/w; the
    segment containing ``total`` fixes which strata are clamped, and lambda
    then follows from one division (no iteration tolerance).
    """

    if total <= sum(lows):
        return [float(low) for low in lows]
    free = [
        index
        for index, weight in enumerate(weights)
        if weight > 0 and highs[index] > lows[index]
    ]
    points = sorted(
        {lows[i] / weights[i] for i in free} | {highs[i] / weights[i] for i in free}
    )
    previous, point = 0.0, points[-1]
    for candidate in points:
        if _level(candidate, weights, lows, highs) >= total:
            point = candidate
            break
        previous = candidate
    fixed, weight_sum = 0.0, 0.0
    for index, weight in enumerate(weights):
        low, high = lows[index], highs[index]
        if weight <= 0 or high == low or low / weight >= point:
            fixed += low
        elif high / weight <= previous:
            fixed += high
        else:
            weight_sum += weight
    lam = point if weight_sum == 0 else (total - fixed) / weight_sum
    return [
        min(high, max(low, lam * weight)) if weight > 0 else float(low)
        for weight, low, high in zip(weights, lows, highs, strict=True)
    ]


def _integerize(values: list[float], lows, highs, total: int) -> list[int]:
    """Largest-remainder rounding inside the bounds; ties break by order."""

    result = [
        max(low, min(high, math.floor(value)))
        for value, low, high in zip(values, lows, highs, strict=True)
    ]
    remainder = total - sum(result)
    ranked = sorted(
        (index for index in range(len(values)) if result[index] < highs[index]),
        key=lambda index: (
            -math.floor((values[index] - result[index]) * _FRACTION_GRID + 0.5),
            index,
        ),
    )
    if remainder < 0 or remainder > len(ranked):
        raise StatsInputError("Allocation rounding could not meet the budget.")
    for index in ranked[:remainder]:
        result[index] += 1
    return result


def allocate(
    populations: Sequence[int],
    total: int,
    *,
    method: str = "proportional",
    minimum: int = 0,
    proportions: Sequence[float] | None = None,
) -> list[int]:
    """Integer sample sizes per stratum for a total unit budget.

    Bounded optimum: ``n_h = clamp(lambda * w_h, min(minimum, N_h), N_h)``
    with lambda chosen so the sizes sum to the budget, then largest-remainder
    rounding. A budget above what the method can place (the population, or
    strata whose Neyman weight is zero) is capped, so the result may sum to
    less than ``total``; a budget below the sum of the minimums is an error.
    """

    total, minimum = _count(total, "total"), _count(minimum, "minimum")
    weights = allocation_weights(populations, method=method, proportions=proportions)
    populations = [int(value) for value in populations]
    lows = [min(minimum, population) for population in populations]
    highs = populations
    floor_total = sum(lows)
    if total < floor_total:
        raise StatsInputError(
            f"The budget ({total}) is below the per-stratum minimum total "
            f"({floor_total})."
        )
    reachable = sum(
        high if weight > 0 else low
        for weight, low, high in zip(weights, lows, highs, strict=True)
    )
    target = min(total, reachable)
    return _integerize(_water_fill(weights, lows, highs, target), lows, highs, target)


# --- Design weights ------------------------------------------------------------


def kish_effective_n(weighted_counts: Iterable[tuple[float, int]]) -> float | None:
    """Kish effective sample size ``(sum w)^2 / sum w^2`` over sampled units.

    ``weighted_counts`` holds ``(design weight, sampled units with it)``.
    """

    first = second = 0.0
    for weight, count in weighted_counts:
        count = _count(count, "count")
        if (
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not math.isfinite(weight)
            or weight <= 0
        ):
            raise StatsInputError("Design weights must be positive and finite.")
        first += count * weight
        second += count * weight * weight
    if second == 0:
        return None
    return first * first / second


def design_effect(weighted_counts: Sequence[tuple[float, int]]) -> float | None:
    """Kish design effect from unequal weighting, ``n / n_eff = 1 + CV^2``."""

    effective = kish_effective_n(weighted_counts)
    if effective is None:
        return None
    return sum(count for _weight, count in weighted_counts) / effective


def weight_cv(weighted_counts: Sequence[tuple[float, int]]) -> float | None:
    """Coefficient of variation of the weights (population form)."""

    effect = design_effect(weighted_counts)
    if effect is None:
        return None
    return math.sqrt(max(0.0, effect - 1))


# --- Balance and integrity tests ---------------------------------------------


def standardized_difference(
    sample_share: float, population_share: float
) -> float | None:
    """Standardized difference of two shares (binary-indicator form).

    ``(s - P) / sqrt((s(1 - s) + P(1 - P)) / 2)``. ``None`` when one share is
    0 and the other 1 (the difference is unbounded).
    """

    s = _unit_interval(sample_share, "sample_share")
    p = _unit_interval(population_share, "population_share")
    pooled = (s * (1 - s) + p * (1 - p)) / 2
    if pooled == 0:
        return 0.0 if s == p else None
    return (s - p) / math.sqrt(pooled)


def chi_square_sf(statistic: float, degrees_of_freedom: int) -> float:
    """Upper tail probability of a chi-square variable, Q(df / 2, x / 2)."""

    if (
        isinstance(degrees_of_freedom, bool)
        or not isinstance(degrees_of_freedom, int)
        or degrees_of_freedom < 1
    ):
        raise StatsInputError("degrees_of_freedom must be a positive integer.")
    if not math.isfinite(statistic) or statistic < 0:
        raise StatsInputError("statistic must be a non-negative finite number.")
    if statistic == 0:
        return 1.0
    return _gamma_q(degrees_of_freedom / 2, statistic / 2)


def _gamma_q(a: float, x: float) -> float:
    """Regularized upper incomplete gamma: series below a + 1, else Lentz."""

    log_prefix = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1:
        term = total = 1.0 / a
        shape = a
        for _ in range(100_000):
            shape += 1
            term *= x / shape
            total += term
            if abs(term) < abs(total) * 1e-16:
                break
        return min(1.0, max(0.0, 1.0 - total * math.exp(log_prefix)))
    tiny = 1e-300
    b = x + 1 - a
    c, d = 1 / tiny, 1 / b
    h = d
    for index in range(1, 100_000):
        an = -index * (index - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-16:
            break
    return min(1.0, max(0.0, math.exp(log_prefix) * h))


def chi_square_goodness_of_fit(
    observed: Sequence[int], expected_shares: Sequence[float]
) -> dict:
    """Pearson goodness of fit of sampled counts to population shares.

    Categories with zero population share are excluded; a sampled unit in one
    of them is impossible and makes the statistic infinite (``p_value = 0``).
    """

    if len(observed) != len(expected_shares):
        raise StatsInputError("Give one population share per category.")
    counts = [_count(value, "observed") for value in observed]
    shares = [_unit_interval(value, "share") for value in expected_shares]
    n = sum(counts)
    if n == 0:
        return {"statistic": None, "degrees_of_freedom": None, "p_value": None}
    if any(
        count > 0 and share == 0 for count, share in zip(counts, shares, strict=True)
    ):
        return {"statistic": None, "degrees_of_freedom": None, "p_value": 0.0}
    terms = [
        (count - n * share) ** 2 / (n * share)
        for count, share in zip(counts, shares, strict=True)
        if share > 0
    ]
    degrees = len(terms) - 1
    statistic = sum(terms)
    if degrees < 1:
        return {"statistic": statistic, "degrees_of_freedom": 0, "p_value": 1.0}
    return {
        "statistic": statistic,
        "degrees_of_freedom": degrees,
        "p_value": chi_square_sf(statistic, degrees),
    }


def _log_binomial_pmf(k: int, n: int, p: float) -> float:
    return (
        math.lgamma(n + 1)
        - math.lgamma(k + 1)
        - math.lgamma(n - k + 1)
        + k * math.log(p)
        + (n - k) * math.log1p(-p)
    )


def _binomial_tail(start: int, step: int, n: int, p: float) -> float:
    """Sum of pmf from ``start`` outward (away from the mode) until negligible."""

    total, k = 0.0, start
    while 0 <= k <= n:
        term = math.exp(_log_binomial_pmf(k, n, p))
        total += term
        if term == 0 or (total > 0 and term < total * 1e-17):
            break
        k += step
    return total


def binomial_two_sided_p(k: int, n: int, p: float) -> float:
    """Exact two-sided binomial test (outcomes no likelier than the observed).

    The same "minimum likelihood" definition as R ``binom.test``; above a
    variance of one million the normal tail with continuity correction is used.
    """

    k, n = _count(k, "k"), _count(n, "n")
    if k > n:
        raise StatsInputError("k cannot exceed n.")
    p = _unit_interval(p, "p")
    if p in (0.0, 1.0):
        return 1.0 if k == round(n * p) else 0.0
    variance = n * p * (1 - p)
    if variance > _EXACT_BINOMIAL_MAX_VARIANCE:
        z = max(0.0, abs(k - n * p) - 0.5) / math.sqrt(variance)
        return min(1.0, math.erfc(z / math.sqrt(2)))
    mode = min(n, math.floor((n + 1) * p))
    limit = _log_binomial_pmf(k, n, p) + math.log1p(1e-7)
    # The pmf is unimodal: every outcome on k's side of the mode, beyond k,
    # is no likelier than k. On the other side, walk from k's neighbour (not
    # from the mode, which would skip a tied second mode) to the first outcome
    # that is no likelier. If that is the neighbour itself, k is a mode and
    # every outcome qualifies: the p-value is exactly 1.
    step = 1 if k <= mode else -1
    neighbour = k + step
    if not 0 <= neighbour <= n or _log_binomial_pmf(neighbour, n, p) <= limit:
        return 1.0
    # Locate the qualifying tail by binary search on the opposite side of
    # the mode. A linear walk can take millions of iterations for severe SRM.
    if step == 1:
        low, high = mode, n + 1
        while low < high:
            middle = (low + high) // 2
            if middle <= n and _log_binomial_pmf(middle, n, p) > limit:
                low = middle + 1
            else:
                high = middle
    else:
        low, high = -1, mode
        while low < high:
            middle = (low + high + 1) // 2
            if middle >= 0 and _log_binomial_pmf(middle, n, p) > limit:
                high = middle - 1
            else:
                low = middle
    boundary = low
    near = _binomial_tail(k, -step, n, p)
    far = _binomial_tail(boundary, step, n, p) if 0 <= boundary <= n else 0.0
    return min(1.0, near + far)


def srm_test(selected: int, population: int, rate: float) -> dict:
    """Sample-ratio-mismatch test of realized selections against the rate.

    Flags when the exact two-sided binomial p-value is below ``SRM_ALPHA``.
    ``z`` is reported for scale (``None`` when the rate is 0 or 1).
    """

    selected, population = (
        _count(selected, "selected"),
        _count(population, "population"),
    )
    rate = _unit_interval(rate, "rate")
    if selected > population:
        raise StatsInputError("selected cannot exceed the population.")
    if population == 0:
        return {"z": None, "p_value": None, "flagged": False}
    p_value = binomial_two_sided_p(selected, population, rate)
    variance = population * rate * (1 - rate)
    z = None if variance == 0 else (selected - population * rate) / math.sqrt(variance)
    return {"z": z, "p_value": p_value, "flagged": p_value < SRM_ALPHA}
