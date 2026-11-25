"""Necessary observed support, separate from the pooled variance estimator.

All statistics here count actual complete non-certainty selection units (or one
explicit iid sample). Certainty rows, probability weights and domain padding
never supply outcome support. State is constant-sized per existing stratum.
"""

from tracer.services.derived_metrics.formula import Formula

# Merge these sufficient statistics only for the explicitly unweighted iid model.
# Weighted designs must test each frozen stratum before pooling its failure flag.
SUPPORT_REDUCERS = {
    "m": "sum",
    "q": "sum",
    "success": "sum",
    "failure": "sum",
    "n_min": "minIf",
    "n_max": "maxIf",
    "d_min": "minIf",
    "d_max": "maxIf",
    "n": "sumKahan",
    "d": "sumKahan",
    "nn": "sumKahan",
    "nd": "sumKahan",
    "abs_nd": "sumKahan",
    "dd": "sumKahan",
}


def support_aggregates_sql() -> str:
    """First level: no extra grouping keys, sets, joins or source reads."""
    fields = [
        "countIf(support_unit) AS support_m_h",
        "countIf(support_unit AND d = 1) AS support_q_h",
        "countIf(support_unit AND d = 1 AND n = 1) AS support_success_h",
        "countIf(support_unit AND d = 1 AND n = 0) AS support_failure_h",
    ]
    for component in ("n", "d"):
        for bound in ("min", "max"):
            fields.append(
                f"{bound}If({component}, support_unit) AS support_{component}_{bound}_h"
            )
    for name, expression in (
        ("n", "n"),
        ("d", "d"),
        ("nn", "n*n"),
        ("nd", "n*d"),
        ("abs_nd", "abs(n*d)"),
        ("dd", "d*d"),
    ):
        fields.append(f"sumKahanIf({expression}, support_unit) AS support_{name}_h")
    return ",\n".join(fields)


def pooled_support_sql() -> str:
    """Second level: one support cell for iid units even across arbitrary labels."""
    return ",\n".join(
        f"{reducer}(support_{name}_h{', support_m_h > 0' if reducer.endswith('If') else ''}) AS support_{name}"
        for name, reducer in SUPPORT_REDUCERS.items()
    )


def support_failure_sql(formula: Formula, *, suffix: str = "") -> tuple[str, str]:
    """Return (hard failure, absent tail); only the latter can use boundary fallback.

    A proportionality fact may waive a residual check, not design or component
    checks. Structural rate 0/1 identities have no unknown conditional tail. They
    still need two complete units and the existing numerical/boundary policy.
    """

    def col(name):
        return f"support_{name}{suffix}"

    bad = [f"{col('m')} < 2"]
    tail = "0"
    if formula.rate_eligible:
        if formula.proportionality not in (0, 1):
            bad.append(f"{col('q')} < 2")
            tail = f"{col('success')} = 0 OR {col('failure')} = 0"
    else:
        ratio = formula.kind in {"mean", "ratio"}
        if formula.numerator_constant is None:
            bad.append(f"{col('n_min')} = {col('n_max')}")
        if ratio and formula.denominator_constant is None:
            bad.append(f"{col('d_min')} = {col('d_max')}")
        if ratio and formula.proportionality is None:
            # Bind local R, Q and the absolute-moment scale, never pooled R.
            # Expanded moments can cancel/overflow: that is an abstention, not
            # evidence for proportionality. Every division is null-protected.
            bad.append(f"""arrayMap(local_r -> arrayMap((local_q, local_scale) ->
                ifNull(NOT isFinite(local_r) OR NOT isFinite(local_q)
                    OR NOT isFinite(local_scale) OR local_q <= 1e-12*local_scale, 1),
                [{col("nn")} - 2*local_r*{col("nd")} + local_r*local_r*{col("dd")}],
                [{col("nn")} + 2*abs(local_r)*{col("abs_nd")} + local_r*local_r*{col("dd")}])[1],
                [{col("n")} / nullIf({col("d")}, 0)])[1]""")
    return " OR ".join(f"({part})" for part in bad), tail
