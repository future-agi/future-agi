"""Frozen committed sketches plus typed, removable conversation contributions.

Initial buckets are copied from the exact KPI manifest, including its folded
cohorts and opaque states. Erasure discards ALL frozen states before rebuilding
from surviving typed units; sketches are not subtractable. No telemetry or live
manifest is consulted on that path. Native row/memory/time limits still throw.
"""

from tracer.services.derived_metrics.buckets import live_parts_select
from tracer.services.derived_metrics.formula import compile_formula
from tracer.services.derived_metrics.frozen_relations import (
    FrozenRelationOverflow,
    FrozenRelations,
)
from tracer.services.derived_metrics.parts import stratum_parts_select
from tracer.services.derived_metrics.queries import (
    QUERY_SETTINGS,
    SCOPE_SQL,
    rollup_group_sql,
)
from tracer.services.derived_metrics.unit_reads import field_recipe

BUCKET_TABLE = "derived_metric_snapshot_buckets"
UNIT_TABLE = "derived_metric_snapshot_units"
UNIT_COLUMNS = (
    "org_id",
    "workspace_id",
    "project_id",
    "snapshot_id",
    "unit_id",
    "session_id",
    "user_id",
    "cohort",
    "unit_start",
    "bucket_start",
    "status",
    "value_bool",
    "value_number",
    "value_text",
    "input_number",
    "probability",
    "invalid_probability",
    "sampling_scope",
    "sampling_stratum",
    "sampling_design",
    "population_size",
)
BUCKET_COLUMNS = (
    "bucket_start",
    "group_key",
    "scope_key",
    "stratum_key",
    "unit_count",
    "valid_count",
    "missing_count",
    "invalid_count",
    "known_sampled",
    "nonempty_scope_count",
    "scope_min",
    "scope_max",
    "uniq_state",
    "quantile_state",
)
INPUT_COLUMNS = (
    "org_id",
    "workspace_id",
    "project_id",
    "snapshot_id",
    "unit_id",
    "session_id",
    "user_id",
    "cohort",
    "day",
    "input_values",
    "probability",
    "invalid_probability",
    "source_rows",
    "sampling_scope",
    "sampling_stratum",
    "sampling_design",
    "population_size",
    "unit_start",
    "bucket_start",
    "status",
    "value_bool",
    "value_number",
    "value_text",
)


class _BoundedStore:
    """Scratch statements retain the snapshot lane's read/group/result limits."""

    def __init__(self, store):
        self.client = store.client

    def execute(self, sql, params=None):
        return self.client.execute(
            sql,
            params or {},
            settings={**QUERY_SETTINGS, "log_queries": 0, "use_query_cache": 0},
        )


def is_sketch(definition):
    return definition.kind in {"unique_count", "percentile"}


def bucket_insert(source):
    columns = ", ".join(BUCKET_COLUMNS)
    return (
        f"INSERT INTO {BUCKET_TABLE} "
        f"(org_id, workspace_id, project_id, snapshot_id, {columns}) "
        f"SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(snapshot_id)s, "
        f"{columns} FROM ({source})"
    )


def capture_units_select(run, start, end):
    from tracer.services.derived_metrics.snapshots import scope_for

    columns = ", ".join(UNIT_COLUMNS)
    return (
        f"INSERT INTO {UNIT_TABLE} ({columns}) SELECT {columns} FROM "
        "(SELECT *, input_values[%(value_alias)s] AS input_number FROM derived_metric_inputs "
        f"WHERE {SCOPE_SQL} AND snapshot_id = %(snapshot_id)s AND unit_start >= %(start)s AND unit_start < %(end)s)",
        {
            **scope_for(run).parameters(),
            "snapshot_id": str(run.snapshot_id),
            "value_alias": field_recipe(run.definition).value_alias,
            "start": start,
            "end": end,
        },
    )


def capture_buckets_select(run, manifest, start, end):
    from tracer.services.derived_metrics.reads import bucket_select
    from tracer.services.derived_metrics.snapshots import scope_for

    sql, params = bucket_select(
        run.definition.metric,
        run.definition,
        manifest,
        {"window_start": start, "window_end": end},
    )
    params.update(scope_for(run).parameters(), snapshot_id=str(run.snapshot_id))
    return bucket_insert(sql), params


def assert_complete_capture(store, run):
    """Do not save a KPI whose contributing units can no longer be erased.

    Retired anonymous parts are rejected upstream. A temporarily absent bucket
    checkpoint (for example after live erasure) must also fail closed instead of
    capturing an empty KPI that later turns nonempty on snapshot recomputation.
    """
    from tracer.models.derived_metric import MetricStateConflict
    from tracer.services.derived_metrics.snapshots import scope_for

    params = {**scope_for(run).parameters(), "snapshot_id": str(run.snapshot_id)}
    where = f"{SCOPE_SQL} AND snapshot_id = %(snapshot_id)s"
    for table, count in ((UNIT_TABLE, "count()"), (BUCKET_TABLE, "sum(unit_count)")):
        mismatch = _BoundedStore(store).execute(
            "SELECT count() FROM "
            f"(SELECT bucket_start, count() AS units FROM derived_metric_inputs WHERE {where} GROUP BY bucket_start) u "
            f"FULL OUTER JOIN (SELECT bucket_start, {count} AS units FROM {table} WHERE {where} GROUP BY bucket_start) b "
            "ON u.bucket_start = b.bucket_start WHERE ifNull(u.units, 0) != ifNull(b.units, 0)",
            params,
        )[0][0]
        if mismatch:
            raise MetricStateConflict("snapshot sketch contributions are incomplete")


def rebuild_buckets(store, run):
    """Rebuild complete buckets through the live writer's bounded scratch store.

    Only bucket timestamps cross the wire (at most 8,640 in the 90-day window),
    never identities or payloads. The frozen unit index prunes each bucket.
    Capture the child once, then merge/fold checked, connection-owned relations;
    no repeated field compiler hidden inside the top-50 ranking SQL. Scratch row,
    byte and native execution budgets are the same as the live writer's.

    The caller drains submissions and deletes ALL old states before each retry.
    Interrupted rebuilds therefore cannot retain erased data or double-count.
    """
    from tracer.services.derived_metrics.snapshots import _write, scope_for

    params = {
        **scope_for(run).parameters(),
        "snapshot_id": str(run.snapshot_id),
        "start": run.window_start,
        "end": run.window_end,
    }
    bounded = _BoundedStore(store)
    starts = bounded.execute(
        f"SELECT arraySort(groupUniqArray(bucket_start)) FROM {UNIT_TABLE} "
        f"WHERE {SCOPE_SQL} AND snapshot_id = %(snapshot_id)s "
        "AND bucket_start >= %(start)s AND bucket_start < %(end)s",
        params,
    )[0][0]
    recipe = field_recipe(run.definition)
    params.update(recipe.formula.parameters if recipe.formula else {})
    params["value_alias"] = recipe.value_alias
    from clickhouse_driver.errors import ServerException

    # Amortize scratch setup across at most one day's worth of complete buckets.
    # A wide batch is split, never truncated or retried with larger budgets.
    pending = [starts[i : i + 96] for i in range(0, len(starts), 96)][::-1]
    while pending:
        buckets = pending.pop()
        params["buckets"] = tuple(buckets)
        retry = False
        # Percentile completeness uses the original numeric input, even when an
        # invalid probability masks the display value. One required input suffices;
        # there is no second copy of every input map in the bucket-indexed store.
        source = f"""SELECT unit_id, '' AS session_id, '' AS user_id,
            cohort AS group_key, unit_start, bucket_start,
            map(%(value_alias)s, input_number) AS inputs,
            probability AS sample_probability, invalid_probability, sampling_scope,
            sampling_stratum, sampling_design, population_size,
            status, value_bool, value_number, value_text, toUInt8(0) AS is_deleted
            FROM {UNIT_TABLE} WHERE {SCOPE_SQL} AND snapshot_id = %(snapshot_id)s
            AND bucket_start IN %(buckets)s ORDER BY bucket_start, unit_id"""
        with FrozenRelations(
            bounded,
            org_id=run.definition.metric.organization_id,
            project_id=run.definition.metric.project_id,
            attempt_id=run.attempt_id,
            source=[str(run.snapshot_id), [start.isoformat() for start in buckets]],
        ) as relations:
            try:
                child = relations.materialize(
                    live_parts_select(run.definition, source), params, role="child"
                )
                relations.materialize(
                    f"SELECT * FROM ({relations.select(child, role='child')}) WHERE 0",
                    {},
                    role="retired",
                )
                folded = relations.finish(expected_children=1)
            except (FrozenRelationOverflow, ServerException) as exc:
                overflow = isinstance(exc, FrozenRelationOverflow) or (
                    isinstance(exc, ServerException)
                    and (
                        exc.code in {158, 159, 241, 396}
                        or (
                            exc.code == 395
                            and "metric_frozen_relation_budget" in exc.message
                        )
                    )
                )
                if not overflow or len(buckets) == 1:
                    raise
                retry = True
            if not retry:
                # Outside the split handler: an ambiguous persisted INSERT must
                # drain and purge on retry, never replay a successful prefix.
                _write(
                    store,
                    run,
                    bucket_insert(relations.select(folded, role="complete-folded")),
                    params,
                )
        if retry:
            mid = len(buckets) // 2
            pending.extend([buckets[mid:], buckets[:mid]])


def rollup_source(rollup):
    """Use copied KPI buckets for overall/cohort/day; units for identity rollups."""
    if rollup in {"overall", "cohort", "day"}:
        group = (
            "toString(toDate(bucket_start, 'UTC'))" if rollup == "day" else "group_key"
        )
        return BUCKET_TABLE, group
    return "derived_metric_inputs", rollup_group_sql(rollup)[0]


def aggregate_select(
    run, rollup, params, *, group_range=None, mixed_scope=False, unit_range=False
):
    from tracer.services.derived_metrics.reads import _aggregate_select

    table, group = rollup_source(rollup)
    predicate = "1"
    if group_range is not None:
        params.update(group_min=group_range[0], group_max=group_range[1])
        predicate = f"{group} >= %(group_min)s AND {group} <= %(group_max)s"
        if unit_range:
            predicate += " AND unit_id >= %(group_min)s AND unit_id <= %(group_max)s"
    source = f"SELECT * FROM {table} WHERE {SCOPE_SQL} AND snapshot_id = %(snapshot_id)s AND ({predicate})"
    if table == BUCKET_TABLE:
        # Guard is snapshot-wide even when group keyset pages cover one scope.
        if mixed_scope:
            source = f"SELECT * REPLACE(if(notEmpty(scope_key), unit_count, invalid_count) AS invalid_count) FROM ({source})"
        # Preserve bucket_start for stable sketch merge ordering.
        if rollup == "day":
            source = f"SELECT * REPLACE({group} AS group_key) FROM ({source})"
    else:
        invalid = (
            "toUInt8(if(notEmpty(sampling_scope), 1, invalid_probability))"
            if mixed_scope
            else "invalid_probability"
        )
        source = f"""SELECT * REPLACE({invalid} AS invalid_probability,
            mapUpdate(input_values, map('sketch_present', if(status = 'ok', 1., NULL))) AS input_values)
            FROM ({source}) ORDER BY unit_id"""
        recipe = field_recipe(run.definition)
        source = stratum_parts_select(
            source,
            recipe.formula
            or compile_formula("mean(sketch_present)", ["sketch_present"]),
            rollup=rollup,
            sketch_value=recipe.value_column,
            sketch_kind=recipe.kind,
            bucket_column="bucket_start",
        )
    return _aggregate_select(
        source, run.definition, params, grouped=rollup != "overall"
    )
