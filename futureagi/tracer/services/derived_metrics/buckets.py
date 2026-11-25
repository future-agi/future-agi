"""Whole dirty-bucket replacements from logical fields and anonymous parts.

No subtraction of old revisions and no averaging of subgroup averages. Raw
extrema/moments are preserved before any read-window scope/support correction.
User-level metrics have conversation fields but deliberately no bucket rows.
"""

from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.parts import stratum_parts_select
from tracer.services.derived_metrics.storage import BUCKET_STAT_SCHEMA
from tracer.services.derived_metrics.support import SUPPORT_REDUCERS
from tracer.services.derived_metrics.unit_reads import field_recipe

KEYS = ("bucket_start", "group_key", "scope_key", "stratum_key")
STATS = tuple(name for name, _ in BUCKET_STAT_SCHEMA)
OTHER_GROUP = '["other"]'


def merge_parts_select(parts_sql):
    """Merge disjoint parts, without premature mixed-window scope rejection."""
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
    expressions = []
    for name, kind in BUCKET_STAT_SCHEMA:
        if name in bounds:
            reducer, gate = bounds[name]
            expr = f"{reducer}({name}, {gate})"
        elif name == "uniq_state":
            expr = "uniqCombined64MergeState(uniq_state)"
        elif name == "quantile_state":
            expr = "quantileTDigestMergeState(quantile_state)"
        elif kind == "String":
            expr = f"{'min' if name.endswith('min') else 'max'}({name})"
        else:
            expr = f"{'sumKahan' if kind == 'Float64' else 'sum'}({name})"
        expressions.append(f"{expr} AS {name}_merged")
    renamed = ", ".join(f"{name}_merged AS {name}" for name in STATS)
    return (
        f"SELECT {', '.join(KEYS)}, {renamed} FROM ("
        f"SELECT {', '.join(KEYS)}, {', '.join(expressions)} FROM ({parts_sql}) "
        f"GROUP BY {', '.join(KEYS)})"
    )


def live_parts_select(definition, field_sql):
    recipe = field_recipe(definition)
    # Sketch-only metrics still need missing/sampling/support population evidence.
    formula = recipe.formula or compile_formula(
        "mean(sketch_present)", ["sketch_present"]
    )
    source = f"""SELECT unit_id, session_id, user_id, group_key AS cohort,
        toDate(unit_start, 'UTC') AS day, bucket_start,
        mapUpdate(inputs, map('sketch_present', if(status = 'ok', 1., NULL))) AS input_values,
        sample_probability AS probability, invalid_probability, sampling_scope,
        sampling_stratum, sampling_design, population_size,
        status, value_number, value_text, value_bool
        FROM ({field_sql}) WHERE is_deleted = 0 AND status != 'open'"""
    parts = stratum_parts_select(
        source,
        formula,
        rollup="cohort",
        bucket_column="bucket_start",
        sketch_value=recipe.value_column,
        sketch_kind=recipe.kind
        if recipe.kind in {"unique_count", "percentile"}
        else None,
    )
    columns = [*KEYS]
    columns += [
        name
        if kind.startswith("AggregateFunction(")
        else f"CAST({name}, '{kind}') AS {name}"
        for name, kind in BUCKET_STAT_SCHEMA
    ]
    return f"SELECT {', '.join(columns)} FROM ({parts})"


def rebuilt_parts_select(definition, field_sql, retired_sql, *, fold_groups=True):
    """Retired parts are PRE-FOLD, so top groups can be ranked again safely."""
    if getattr(definition, "level", "conversation") == "user":
        raise ValueError("user-level metrics have no buckets")
    columns = ", ".join(KEYS + STATS)
    raw = merge_parts_select(
        f"SELECT {columns} FROM ({live_parts_select(definition, field_sql)}) "
        f"UNION ALL SELECT {columns} FROM ({retired_sql}) WHERE is_deleted = 0"
    )
    return fold_parts_select(raw) if fold_groups else raw


def fold_parts_select(raw):
    """Rank/fold already merged anonymous parts, including direct span parts."""
    # B03 tags literal values as ["value", ...], so a real label named Other
    # is distinct from this structural marker. Fail closed for legacy raw
    # sampling-stratum keys that collide instead of silently merging the groups.
    raw = f"SELECT * FROM ({raw}) WHERE throwIf(group_key = '{OTHER_GROUP}', 'metric_group_key_reserved') = 0"
    rank = f"""SELECT bucket_start, group_key,
        row_number() OVER (PARTITION BY bucket_start ORDER BY sum(unit_count) DESC, group_key) AS group_rank
        FROM ({raw}) GROUP BY bucket_start, group_key"""
    remapped = (
        f"SELECT p.* REPLACE(if(r.group_rank <= 50, p.group_key, '{OTHER_GROUP}') AS group_key) "
        f"FROM ({raw}) p INNER JOIN ({rank}) r USING (bucket_start, group_key)"
    )
    return merge_parts_select(remapped)


def replacement_select(parts_sql, old_buckets_sql):
    """Write explicit empty/tombstone rows for every old key that vanished."""
    keys = ", ".join(KEYS)
    stats = ", ".join(STATS)
    # Copying opaque old sketches into a tombstone is safe: logical readers must
    # drop it AFTER collapse; compaction must retain it while old values exist.
    return f"""SELECT {keys}, {stats}, toUInt8(0) AS is_deleted FROM ({parts_sql})
        UNION ALL SELECT {keys}, {stats}, toUInt8(1) AS is_deleted
        FROM ({old_buckets_sql}) WHERE ({keys}) NOT IN (SELECT {keys} FROM ({parts_sql}))"""
