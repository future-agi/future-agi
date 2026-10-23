"""Design-based uncertainty for persisted derived-metric rollups.

Every rollup row carries a standard error and a two-sided 95% interval when the
frozen snapshot holds enough design information, and an explicit
``not_estimable`` status with a reason when it does not. Nothing is guessed: a
missing population size, a stratum with a single sampled unit, a snapshot that
predates design capture or a mix of designs all produce no interval.

Estimators (PSU = the metric's selection unit, so spans inside a session are
clustered correctly). For a ratio or mean ``R = sum(w*y) / sum(w*x)`` with
``w = 1/p``, the linearized variable is ``u = y - R*x``; for a total (``sum``,
``count_if``) ``u = y`` and the divisor ``X`` is 1.

``stratified_wor_fpc`` (closed cohorts). A closed sampling window draws a fixed
``n_h = p_h * N_h`` units without replacement inside each stratum, and both
``p_h`` and the stratum ``population_size`` ``N_h`` are persisted with every
sampled result::

    V = 1/X^2 * sum_h (1 - p_h) * n_h/(n_h - 1)
            * [ sum_i (w_i u_i)^2 - (sum_i w_i u_i)^2 / n_h ]

The sum runs over the units of the group; sampled units outside the group (a
narrower window, a filter, a rollup group or a unit whose inputs are missing)
contribute ``u = 0``, which is the standard domain estimator. A stratum with
``p_h = 1`` is a census and contributes no variance.

``poisson`` (live policies). A live policy keeps each unit independently by a
hash threshold with probability ``p_i``, so the design is Poisson sampling::

    V = 1/X^2 * sum_i (1 - p_i) * (w_i u_i)^2

``iid_units`` (unweighted ratios and means). There is no sampling design, so this
is a model-based interval that treats the window's units as independent draws
from the underlying process (the delta method on unit-level sums)::

    V = 1/X^2 * m/(m - 1) * sum_i (u_i - mean(u))^2

An unweighted total of every unit in the window is an exact count, so it has no
sampling interval (``unweighted_total``).

Intervals use ``t(0.975, df)`` with ``df = units - strata`` (one stratum for the
Poisson and iid designs):

* ``wilson`` for rates (a ratio or mean whose numerator is 0/1 inside a 0/1
  denominator): the Wilson score interval at the design-effective sample size
  ``n* = p(1-p)/V`` (the Kish size of the denominator units when ``V`` is 0, i.e.
  every unit agrees). It stays inside [0, 1] and keeps its coverage near 0 and 1,
  where a symmetric interval does not.
* ``wald_t`` otherwise: ``value +/- t * SE``. Without census proof, zero or
  numerically negligible variance withholds this interval (``unstable_variance``).
  This includes an all-zero sample: no observed events do not establish that the
  population total is zero. The point estimate remains valid; no boundary-aware
  total interval is implemented.

A weighted window whose units were all kept with certainty (p = 1) is a census:
its standard error is 0 and its interval is the value itself.

The interval covers sampling error only: evaluator (judge) error is not modelled.
"""

from __future__ import annotations

CONFIDENCE_LEVEL = 0.95
Z_975 = 1.959963984540054

# Exact two-sided 95% Student t quantiles for df 1..60 (scipy.stats.t.ppf(0.975)).
# Include df=49 so small rare-event total intervals match the centered numeric
# oracle even at their lower endpoint. Above 60 use the Cornish-Fisher expansion.
T_975 = (
    12.7062047362,
    4.30265272975,
    3.18244630528,
    2.7764451052,
    2.57058183564,
    2.44691185114,
    2.36462425159,
    2.3060041352,
    2.2621571628,
    2.22813885199,
    2.20098516009,
    2.17881282967,
    2.16036865646,
    2.14478668792,
    2.13144954556,
    2.11990529922,
    2.10981557783,
    2.10092204024,
    2.09302405441,
    2.08596344727,
    2.07961384473,
    2.0738730679,
    2.06865761042,
    2.06389856163,
    2.05953855275,
    2.05552943864,
    2.05183051648,
    2.0484071418,
    2.04522964213,
    2.0422724563,
    2.0395134464,
    2.03693334346,
    2.03451529745,
    2.03224450932,
    2.03010792825,
    2.02809400098,
    2.02619246303,
    2.02439416391,
    2.02269092004,
    2.02107539031,
    2.01954097044,
    2.01808170282,
    2.01669219923,
    2.01536757444,
    2.01410338888,
    2.01289559892,
    2.01174051373,
    2.01063475762,
    2.00957523713,
    2.0085591121,
    2.00758377032,
    2.00664680506,
    2.00574599532,
    2.00487928819,
    2.00404478329,
    2.00324071885,
    2.00246545929,
    2.00171748415,
    2.00099537809,
    2.00029782201,
)

_Z = Z_975
_G = (
    (_Z**3 + _Z) / 4,
    (5 * _Z**5 + 16 * _Z**3 + 3 * _Z) / 96,
    (3 * _Z**7 + 19 * _Z**5 + 17 * _Z**3 - 15 * _Z) / 384,
    (79 * _Z**9 + 776 * _Z**7 + 1482 * _Z**5 - 1920 * _Z**3 - 945 * _Z) / 92160,
)

CI_METHODS = ("stratified_wor_fpc", "poisson", "iid_units")
CI_INTERVALS = ("wilson", "wald_t")
CI_STATUSES = ("ok", "not_estimable")
# Why an interval is absent, in the order they are checked.
CI_REASONS = (
    "no_value",
    "design_not_captured",
    "mixed_design",
    "unweighted_total",
    "fewer_than_two_units",
    "population_size_missing",
    "inconsistent_stratum_design",
    "single_unit_stratum",
    "no_degrees_of_freedom",
    "non_finite",
    "unstable_variance",
)
# A rollup row persisted before intervals existed (API only; the SQL view shows
# its empty ci_status as stored).
NOT_COMPUTED = "not_computed"


def t_quantile_975(df: int) -> float:
    """Two-sided 95% Student t multiplier for ``df`` degrees of freedom."""
    if df < 1:
        raise ValueError("Degrees of freedom must be at least 1.")
    if df <= len(T_975):
        return T_975[df - 1]
    return _Z + sum(term / df ** (power + 1) for power, term in enumerate(_G))


def t_quantile_975_sql(df: str) -> str:
    """ClickHouse expression for :func:`t_quantile_975` of a UInt64 ``df`` >= 1."""
    table = "[" + ", ".join(repr(value) for value in T_975) + "]"
    expansion = " + ".join(
        [repr(_Z)]
        + [
            f"{term!r} / pow(toFloat64({df}), {power + 1})"
            for power, term in enumerate(_G)
        ]
    )
    return (
        f"if({df} <= {len(T_975)}, arrayElement({table}, toUInt32({df})), {expansion})"
    )


def uncertainty_array_sql(ratio_kind: bool) -> str:
    """One tuple per group, using lambda bindings rather than expanded SQL aliases.

    ClickHouse substitutes SELECT aliases recursively. Reusing intermediate CI
    expressions that way creates an exponential expression tree before execution.
    Lambda arguments are actual bindings: every stage below occurs only once.
    ARRAY JOIN of the one-element result exposes the nine persisted columns.
    """
    stages = [
        {
            "design": "if(design_low = design_high, design_low, 'mixed')",
            "r": "weighted_n / weighted_d" if ratio_kind else "0.0",
            "x": "weighted_d" if ratio_kind else "1.0",
            "v": "computed_value",
            "rate": f"{int(ratio_kind)} AND non_binary = 0",
            "kish": "sum_w * sum_w / nullIf(sum_w2, 0)",
        },
        {
            "variance": """multiIf(
                design = 'unweighted', valid_count / (toInt64(valid_count) - 1)
                    * (b_yy - 2*r*b_xy + r*r*b_xx - pow(weighted_n-r*weighted_d, 2)/valid_count),
                design = 'closed', (d_yy - 2*r*d_xy + r*r*d_xx) - (e_yy - 2*r*e_xy + r*r*e_xx),
                design = 'live', c_yy - 2*r*c_xy + r*r*c_xx, NULL)""",
            "scale": """multiIf(design = 'unweighted', abs(b_yy)+abs(2*r*b_xy)+abs(r*r*b_xx),
                design = 'closed', abs(d_yy)+abs(2*r*d_xy)+abs(r*r*d_xx)+abs(e_yy)+abs(2*r*e_xy)+abs(r*r*e_xx),
                abs(c_yy)+abs(2*r*c_xy)+abs(r*r*c_xx))""",
            "df": "if(design = 'closed', toInt64(valid_count)-toInt64(strata), toInt64(valid_count)-1)",
            "census": "design IN ('closed', 'live') AND sampled = 0",
        },
        {
            "se": "sqrt(greatest(variance, 0)) / abs(x)",
            "t": t_quantile_975_sql("greatest(df, 1)"),
            "wilson": "rate AND NOT census",
        },
        {
            "wn": "if(se > 0, v*(1-v)/(se*se), weighted_d*weighted_d/nullIf(b_xx, 0))",
        },
        {"k": "t*t/wn"},
        {
            "center": "(v+k/2)/(1+k)",
            "half": "t/(1+k)*sqrt(greatest(v*(1-v)/wn+k/(4*wn), 0))",
        },
        {
            "lo": "if(wilson, center-half, v-t*se)",
            "hi": "if(wilson, center+half, v+t*se)",
        },
        {
            "reason": f"""multiIf(
                status != 'ok', 'no_value',
                design = '', 'design_not_captured',
                design NOT IN ('unweighted', 'closed', 'live'), 'mixed_design',
                design = 'unweighted' AND {int(not ratio_kind)}, 'unweighted_total',
                valid_count < 2, 'fewer_than_two_units',
                design = 'closed' AND population_missing > 0, 'population_size_missing',
                design = 'closed' AND inconsistent_strata > 0, 'inconsistent_stratum_design',
                design = 'closed' AND singleton_strata > 0, 'single_unit_stratum',
                df < 1, 'no_degrees_of_freedom',
                ifNull(NOT isFinite(se) OR NOT isFinite(lo) OR NOT isFinite(hi), 1), 'non_finite',
                NOT census AND NOT rate AND variance <= 1e-12*scale, 'unstable_variance',
                '')""",
        },
    ]
    result = """tuple(if(reason = '', se, NULL), if(reason = '', lo, NULL), if(reason = '', hi, NULL),
        if(valid_count > 0 AND isFinite(kish), kish, NULL), if(reason = '', toUInt64(greatest(df, 0)), NULL),
        multiIf(design = 'unweighted', 'iid_units', design = 'closed', 'stratified_wor_fpc', design = 'live', 'poisson', ''),
        if(reason = '', 'ok', 'not_estimable'), reason, if(reason = '', if(wilson, 'wilson', 'wald_t'), ''))"""
    for bindings in reversed(stages):
        names = ", ".join(bindings)
        arguments = ", ".join(f"[{value}]" for value in bindings.values())
        result = f"arrayMap(({names}) -> {result}, {arguments})[1]"
    return result.removesuffix("[1]")
