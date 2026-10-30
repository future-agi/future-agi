"""Mergeable, uncorrected sufficient statistics for the numeric metric compiler.

Parts are additive across disjoint unit batches and time buckets. Merge them by
(group, scope, stratum) BEFORE applying closed-sample corrections or support
checks. Overlapping reruns must first select the one committed row per unit (or
one replacement per bucket); these functions never sum revision history.
"""

from tracer.services.derived_metrics.formula import Formula
from tracer.services.derived_metrics.queries import (
    UNCERTAINTY_COLUMNS,
    rollup_group_sql,
)
from tracer.services.derived_metrics.storage import BUCKET_STAT_SCHEMA
from tracer.services.derived_metrics.support import (
    SUPPORT_REDUCERS,
    pooled_support_sql,
    support_aggregates_sql,
    support_failure_sql,
)
from tracer.services.derived_metrics.uncertainty import uncertainty_array_sql

# Scalar readers deliberately do not fetch sketch states (DCM4).
SCALAR_PART_COLUMNS = tuple(
    name
    for name, _ in BUCKET_STAT_SCHEMA
    if name not in {"uniq_state", "quantile_state"}
)
# Legacy snapshot sources already folded >1 window scope into invalid_probability
# (snapshot_select's WINDOW_SCOPE_GUARD_SQL), and some carry no scope column.
LEGACY_SCOPE = None


def field_rows_as_units(rows_sql: str) -> str:
    """Committed metric_field_values rows in the unit shape stratum_parts_select reads.

    ``rows_sql`` must already be ONE logical current row per unit from a pinned
    manifest (unit_reads.committed_unit_values_select or the writer's staged
    overlay). Only settled, live units count: open units are not "Doesn't count"
    and tombstones contribute nothing. Unit evidence, not the stored value, feeds
    the estimator, so custom formulas combine parts and never average values.
    """
    return f"""SELECT unit_id, session_id, user_id, group_key AS cohort,
            toDate(unit_start, 'UTC') AS day, bucket_start, inputs AS input_values,
            sample_probability AS probability, invalid_probability, sampling_scope,
            sampling_stratum, sampling_design, population_size
        FROM ({rows_sql}) WHERE is_deleted = 0 AND status != 'open'"""


def stratum_parts_select(
    source_sql: str,
    formula: Formula,
    *,
    rollup: str = "overall",
    scope_column: str | None = "sampling_scope",
    bucket_column: str | None = None,
) -> str:
    """One uncorrected part per [bucket,] group, scope and stratum.

    NULL extrema use count-gated bounds when merged. ``scope_column`` keys the
    closed moments by sampling scope as well as stratum, so two independent
    scopes that share a stratum label are never pooled and a later read window
    can reject them (live buckets). ``LEGACY_SCOPE`` is only for sources whose
    window guard already ran upstream (frozen snapshots and ad hoc units).
    ``bucket_column`` adds a ``bucket_start`` key for per-15-minute parts.
    """
    if scope_column not in {"sampling_scope", LEGACY_SCOPE}:
        raise ValueError("Unsupported sampling scope column.")
    if bucket_column not in {"bucket_start", None}:
        raise ValueError("Unsupported bucket column.")
    group, _ = rollup_group_sql(rollup)
    scope = scope_column or "''"
    bucket_output = f"{bucket_column} AS bucket_start, " if bucket_column else ""
    bucket_key = "bucket_start, " if bucket_column else ""
    unpack = ", ".join(
        f"input_values['{alias}'] AS v_{alias}" for alias in formula.required
    )
    valid = f"({formula.complete_sql}) AND isNotNull(n) AND isFinite(n) AND isNotNull(d) AND isFinite(d)"
    ratio_kind = formula.kind not in {"sum", "count_if"}
    denominator_aggregate = (
        "sumIf(d / nullIf(probability, 0), ok)" if ratio_kind else "0.0"
    )
    moments = []
    for prefix, factor in (
        ("b", "w * w"),
        ("c", "(1 - ifNull(probability, 1)) * w * w"),
    ):
        for name, product in (("yy", "y0 * y0"), ("xy", "y0 * x0"), ("xx", "x0 * x0")):
            moments.append(f"sumKahan({factor} * {product}) AS {prefix}_{name}_h")
    columns = ", ".join(
        f"ifNull({name}_h, 0) AS {name}" if kind != "String" else f"{name}_h AS {name}"
        for name, kind in BUCKET_STAT_SCHEMA
        if name in SCALAR_PART_COLUMNS
    )
    return f"""SELECT {bucket_key}group_key, scope_key, stratum_key, {columns} FROM (
                        SELECT {bucket_output}{group} AS group_key, {scope} AS scope_key, sampling_stratum AS stratum_key,
                            count() AS unit_count_h, countIf(ok) AS valid_count_h,
                            countIf(NOT complete) AS missing_count_h,
                            countIf(invalid_probability != 0) AS invalid_count_h,
                            sumIf(n / nullIf(probability, 0), ok) AS weighted_n_h,
                            {denominator_aggregate} AS weighted_d_h,
                            {", ".join(moments)},
                            sumKahan(w) AS sum_w_h, sumKahan(w * w) AS sum_w2_h,
                            minIf(probability, ok) AS p_min_h, maxIf(probability, ok) AS p_max_h,
                            minIf(population_size, ok) AS population_min_h,
                            maxIf(population_size, ok) AS population_max_h,
                            countIf(ok AND isNull(population_size)) AS population_missing_h,
                            minIf(sampling_design, ok) AS design_min_h,
                            maxIf(sampling_design, ok) AS design_max_h,
                            countIf(ok AND NOT (y0 IN (0, 1) AND x0 IN (0, 1) AND y0 <= x0)) AS non_binary_h,
                            countIf(ok AND probability < 1) AS sampled_h,
                            countIf(known_sampled) AS known_sampled_h,
                            minIf(probability, known_sampled) AS support_p_min_h,
                            maxIf(probability, known_sampled) AS support_p_max_h,
                            countIf(invalid_probability != 0 OR ifNull(probability != 1, 1)
                                OR sampling_design NOT IN ('closed', 'live')) AS census_disqualifiers_h,
                            countIf(invalid_probability != 0 OR ifNull(NOT isFinite(probability)
                                OR probability <= 0 OR probability > 1, 1)
                                OR sampling_design NOT IN ('closed', 'live', 'unweighted')) AS support_invalid_h,
                            min(sampling_design) AS raw_design_min_h, max(sampling_design) AS raw_design_max_h,
                            {support_aggregates_sql()},
                            countIf(notEmpty(scope_key)) AS nonempty_scope_count_h,
                            minIf(scope_key, notEmpty(scope_key)) AS scope_min_h,
                            maxIf(scope_key, notEmpty(scope_key)) AS scope_max_h
                        FROM (
                            SELECT *, {formula.numerator} AS n, {formula.denominator} AS d,
                                ifNull({valid}, 0) AS complete,
                                complete AND invalid_probability = 0 AS ok,
                                invalid_probability = 0 AND sampling_design IN ('closed', 'live')
                                    AND ifNull(isFinite(probability) AND probability > 0 AND probability < 1, 0) AS known_sampled,
                                ok AND (known_sampled OR sampling_design = 'unweighted') AS support_unit,
                                if(ok, ifNull(1 / probability, 0), 0) AS w,
                                if(ok, assumeNotNull(n), 0) AS y0, if(ok, assumeNotNull(d), 0) AS x0
                            FROM (SELECT *, {unpack} FROM ({source_sql}))
                        )
                        GROUP BY {bucket_key}scope_key, stratum_key, group_key
    )"""


def _merge_parts_sql(parts_sql: str) -> str:
    """Merge raw extrema with presence gates, then enforce the WINDOW scope guard.

    Missing and locally invalid rows carry scope evidence too. Reject scoped
    rows before pooling their points/moments, just as snapshot_select does, even
    if two closed scopes share a stratum label or occur in different batches.
    """
    bounds = {
        "p_min": ("minIf", "valid_count > 0"),
        "p_max": ("maxIf", "valid_count > 0"),
        "population_min": ("minIf", "valid_count > population_missing"),
        "population_max": ("maxIf", "valid_count > population_missing"),
        "design_min": ("minIf", "valid_count > 0"),
        "design_max": ("maxIf", "valid_count > 0"),
        "support_p_min": ("minIf", "known_sampled > 0"),
        "support_p_max": ("maxIf", "known_sampled > 0"),
        "scope_min": ("minIf", "nonempty_scope_count > 0"),
        "scope_max": ("maxIf", "nonempty_scope_count > 0"),
    }
    for name, reducer in SUPPORT_REDUCERS.items():
        if reducer.endswith("If"):
            bounds[f"support_{name}"] = (reducer, "support_m > 0")
    fields = []
    for name, kind in BUCKET_STAT_SCHEMA:
        if name not in SCALAR_PART_COLUMNS:
            continue
        if name in bounds:
            reducer, gate = bounds[name]
            expression = f"{reducer}({name}, {gate})"
        elif name in {"raw_design_min", "raw_design_max"}:
            expression = f"{'min' if name.endswith('min') else 'max'}({name})"
        else:
            expression = f"{'sumKahan' if kind == 'Float64' else 'sum'}({name})"
        fields.append(f"{expression} AS {name}_merged")
    masked = []
    preserved = {
        "unit_count",
        "missing_count",
        "nonempty_scope_count",
        "scope_min",
        "scope_max",
        "raw_design_min",
        "raw_design_max",
    }
    for name, kind in BUCKET_STAT_SCHEMA:
        if name not in SCALAR_PART_COLUMNS:
            continue
        expr = f"{name}_merged"
        if name not in preserved:
            rejected = (
                "unit_count_merged"
                if name in {"invalid_count", "census_disqualifiers", "support_invalid"}
                else ("''" if kind == "String" else "0")
            )
            expr = f"if(mixed_scope AND notEmpty(scope_key), {rejected}, {expr})"
        masked.append(f"{expr} AS {name}_h")
    return f"""SELECT group_key, scope_key, stratum_key, {", ".join(masked)} FROM (
        SELECT *, minIf(scope_min_merged, nonempty_scope_count_merged > 0) OVER () !=
                  maxIf(scope_max_merged, nonempty_scope_count_merged > 0) OVER () AS mixed_scope
        FROM (SELECT group_key, scope_key, stratum_key, {", ".join(fields)}
              FROM ({parts_sql}) GROUP BY scope_key, stratum_key, group_key)
    )"""


def combine_parts_select(
    parts_sql: str, formula: Formula, *, rollup: str = "overall"
) -> str:
    """Ratios are ratios of weighted sums, never averages of subgroup ratios.

    One pass over the units computes the point estimate and its design-based
    uncertainty (see ``uncertainty.py``): units are summed per (group, sampling
    stratum), then per group. The stratum sums carry the second moments of the
    linearized variable in expanded form, so the variance needs neither a second
    read of the source nor a window over every unit.
    """
    rollup_group_sql(rollup)  # validates the rollup name
    group_filter = "notEmpty(group_key)" if rollup in {"session", "user"} else "1"
    # The overall rollup aggregates without GROUP BY group_key, so an empty
    # window still yields its one 'no_data' row instead of no row at all.
    if rollup == "overall":
        group_output, group_clause = "''", ""
    else:
        group_output, group_clause = "group_key", "GROUP BY group_key"
    ratio_kind = formula.kind not in {"sum", "count_if"}
    value = "weighted_n / nullIf(weighted_d, 0)" if ratio_kind else "weighted_n"
    denominator = "weighted_d" if ratio_kind else "NULL"
    zero = "weighted_d = 0" if ratio_kind else "0"
    group_moments = [
        f"sumKahan({prefix}_{name}_h) AS {prefix}_{name}"
        for prefix in ("b", "c")
        for name in ("yy", "xy", "xx")
    ]
    # Closed strata: c_h = (1 - p_h) n_h/(n_h - 1) scales the second moments (d_*),
    # and c_h/n_h the squared stratum totals of w*u (e_*), so R factors out.
    closed_terms = [
        f"sumKahan(fpc_h * b_{name}_h) AS d_{name}" for name in ("yy", "xy", "xx")
    ] + [
        f"sumKahan(if(fpc_h = 0, 0, fpc_h / stratum_n_h * {left} * {right})) AS e_{name}"
        for name, left, right in (
            ("yy", "a_y_h", "a_y_h"),
            ("xy", "a_y_h", "a_x_h"),
            ("xx", "a_x_h", "a_x_h"),
        )
    ]
    cell_bad, cell_tail = support_failure_sql(formula, suffix="_h")
    iid_bad, iid_tail = support_failure_sql(formula)
    # For closed designs use the smallest observed non-certainty stratum's
    # (m_h - 1), a conservative lower bound on Welch-Satterthwaite effective df.
    # Pooling m_h - 1 would let a large/census stratum manufacture precision for
    # an unrelated small stratum. Domain-zero padding never manufactures df.
    ci_columns = ", ".join(
        f"tupleElement(ci, {i}) AS {name}"
        for i, name in enumerate(UNCERTAINTY_COLUMNS, 1)
    )
    return f"""
        SELECT group_key,
            if(status = 'ok', computed_value, NULL) AS value,
            if(valid_count > 0 AND isFinite(weighted_n), weighted_n, NULL) AS numerator,
            if(valid_count > 0 AND isFinite({denominator}), {denominator}, NULL) AS denominator,
            status, unit_count, valid_count, missing_count, invalid_probability_count,
            {ci_columns}
        FROM (
            SELECT *,
                {value} AS computed_value,
                multiIf(unit_count = 0, 'no_data', invalid_probability_count > 0, 'invalid_probability',
                    valid_count = 0, 'missing_inputs', {zero}, 'zero_denominator',
                    NOT isFinite(weighted_n) OR NOT isFinite(weighted_d) OR NOT isFinite(computed_value), 'non_finite', 'ok') AS status,
                arrayJoin({uncertainty_array_sql(ratio_kind, rate_eligible=formula.rate_eligible)}) AS ci
            FROM (
                SELECT {group_output} AS group_key, sum(unit_count_h) AS unit_count, sum(valid_count_h) AS valid_count,
                    sum(missing_count_h) AS missing_count, sum(invalid_count_h) AS invalid_probability_count,
                    sum(weighted_n_h) AS weighted_n, sum(weighted_d_h) AS weighted_d,
                    {", ".join(group_moments)},
                    {", ".join(closed_terms)},
                    sum(sum_w_h) AS sum_w, sum(sum_w2_h) AS sum_w2,
                    minIf(toInt64(sampled_h) - 1, sampled_h > 0) AS sampled_stratum_df,
                    countIf(valid_count_h > 0) AS strata,
                    minIf(p_min_h, valid_count_h > 0) AS probability_low,
                    maxIf(p_max_h, valid_count_h > 0) AS probability_high,
                    minIf(design_min_h, valid_count_h > 0) AS design_low,
                    maxIf(design_max_h, valid_count_h > 0) AS design_high,
                    sum(population_missing_h) AS population_missing,
                    countIf(inconsistent_h) AS inconsistent_strata,
                    countIf(singleton_h) AS singleton_strata,
                    sum(non_binary_h) AS non_binary, sum(sampled_h) AS sampled,
                    sum(census_disqualifiers_h) + toUInt64(min(raw_design_min_h) != max(raw_design_max_h)) AS census_disqualifiers,
                    {pooled_support_sql()},
                    sum(support_invalid_h) > 0 OR min(raw_design_min_h) != max(raw_design_max_h)
                        OR if(design_low = 'unweighted', {iid_bad},
                            countIf(known_sampled_h > 0 AND (support_p_min_h != support_p_max_h OR ({cell_bad}))) > 0)
                        AS support_bad,
                    if(design_low = 'unweighted', {iid_tail},
                        countIf(known_sampled_h > 0 AND ({cell_tail})) > 0) AS support_missing_tail
                FROM (
                    SELECT *,
                        ifNull(weighted_n_h, 0) AS a_y_h, ifNull(weighted_d_h, 0) AS a_x_h,
                        ifNull(p_min_h * toFloat64(population_min_h), 0) AS stratum_n_raw_h,
                        round(stratum_n_raw_h) AS stratum_n_h,
                        valid_count_h > 0 AND population_missing_h = 0 AND ifNull(
                            p_min_h != p_max_h OR population_min_h != population_max_h
                            OR valid_count_h > stratum_n_h
                            OR abs(stratum_n_raw_h - stratum_n_h) > 1e-6 * greatest(1, stratum_n_h), 1) AS inconsistent_h,
                        valid_count_h > 0 AND ifNull(p_max_h < 1, 0) AND stratum_n_h < 2 AS singleton_h,
                        if(valid_count_h = 0 OR inconsistent_h OR ifNull(p_min_h >= 1, 1) OR stratum_n_h < 2,
                            0, (1 - assumeNotNull(p_min_h)) * stratum_n_h / (stratum_n_h - 1)) AS fpc_h
                    FROM (
                        {_merge_parts_sql(parts_sql)}
                    )
                ) WHERE {group_filter} {group_clause}
            )
        )
    """
