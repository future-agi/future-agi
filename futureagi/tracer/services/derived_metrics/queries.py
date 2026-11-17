"""Scoped ClickHouse plans. No raw telemetry leaves the database for computation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from tracer.services.clickhouse.eval_logger_table import (
    EVAL_COMPLETED_PREDICATE,
    eval_logger_live_state_columns,
    eval_logger_source,
    eval_logger_version_column,
)
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
    survivor_map_subquery,
)
from tracer.services.derived_metrics.formula import Formula, compile_formula
from tracer.services.derived_metrics.sampling import (
    population_size_sql,
    probability_sql,
    sampling_field,
    sampling_lineage_valid_sql,
    sampling_scope_sql,
)
from tracer.services.derived_metrics.typed_fields import (
    compile_conditions,
    eval_value_sql,
    order_sql,
    reduce_sql,
    reduced_type,
    span_observation_sql,
    span_value_sql,
)

# Explicit abort budgets on the native job lane; interactive analytics strips
# these settings, so this service deliberately does not use that read wrapper.
QUERY_SETTINGS = {
    "max_execution_time": 60,
    "max_memory_usage": 536870912,
    "max_rows_to_read": 20000000,
    "max_bytes_to_read": 8589934592,
    "read_overflow_mode": "throw",
    "max_rows_to_group_by": 1000000,
    "group_by_overflow_mode": "throw",
    "max_result_rows": 101,
    "result_overflow_mode": "throw",
    "max_threads": 2,
    # Parallel primary-key/skip-index analysis keeps untracked per-thread
    # memory proportional to parts x IN-set size and charges it at once: one
    # 1,000-unit page on 773 spans parts peaked at 1.19 GiB (max_threads 2)
    # versus 102 MB with a single index-analysis thread.
    "max_threads_for_indexes": 1,
    "join_use_nulls": 1,
}


@dataclass(frozen=True)
class Scope:
    organization: UUID
    workspace: UUID
    project: UUID

    def parameters(self) -> dict:
        return {
            "org_id": str(self.organization),
            "workspace_id": str(self.workspace),
            "project_id": str(self.project),
        }


SCOPE_SQL = "org_id = %(org_id)s AND workspace_id = %(workspace_id)s AND project_id = %(project_id)s"

# Interactive previews run synchronously on a web worker, so they get a much
# smaller abort budget than the single-slot background lane. Every limit throws
# (never truncates); a preview that exceeds one is rejected with guidance to run
# and persist the window instead. A preview returns exactly one overall row.
PREVIEW_QUERY_SETTINGS = {
    **QUERY_SETTINGS,
    "max_execution_time": 15,
    "max_memory_usage": 268435456,
    "max_rows_to_read": 5000000,
    "max_bytes_to_read": 2147483648,
    "max_rows_to_group_by": 250000,
    "max_result_rows": 10,
    "timeout_overflow_mode": "throw",
    "max_threads": 2,
}

# The v2 spans store keeps these measures in non-Nullable columns with DEFAULT 0,
# so the physical zero written for "the collector never saw a value" is identical
# to a measured zero. Rules (documented in docs/derived-metrics.rst):
#   * latency_ms is missing when the span never ended (end_time IS NULL) and no
#     latency was stored for it: the collector writes latency only for an ended
#     span, so that 0 is the column DEFAULT. A span that ended with 0 ms keeps a
#     real 0, and a non-zero latency reported without an end time (a provider
#     import for a call that has no end timestamp yet) is a reported measurement.
#   * cost and token counts of exactly 0 are treated as NOT REPORTED (missing), never
#     as a measurement. Only non-zero stored values are measurements. When an
#     explicit zero must count, bind an attribute_number input to the reported
#     attribute instead: an absent attribute is missing and a present 0 is a 0.
# Missing values are excluded as complete-case omissions and counted in
# missing_count; they are never silently averaged in as zero.
SPAN_MEASURE_SQL = {
    "latency_ms": "if(isNull(s.end_time) AND s.latency_ms = 0, NULL, toFloat64(s.latency_ms))",
    "cost": "nullIf(toFloat64(s.cost), 0)",
    "total_tokens": "nullIf(toFloat64(s.total_tokens), 0)",
    "prompt_tokens": "nullIf(toFloat64(s.prompt_tokens), 0)",
    "completion_tokens": "nullIf(toFloat64(s.completion_tokens), 0)",
}


# Persisted after the original result columns, in this order (028 ADD COLUMNs).
UNCERTAINTY_COLUMNS = (
    "std_error",
    "ci_low",
    "ci_high",
    "effective_n",
    "ci_df",
    "ci_method",
    "ci_status",
    "ci_reason",
    "ci_interval",
)


def utc_timestamp(value: datetime) -> str:
    # clickhouse-driver formats datetime parameters only to seconds. Bind a
    # canonical UTC DateTime64 string so half-open windows retain microseconds.
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


# The existing window-wide mixed-scope rejection: a read window holding more than
# one nonempty sampling scope reports invalid_probability instead of pooling.
# Live bucket parts carry the same evidence (parts.py) and re-apply it at read time.
WINDOW_SCOPE_GUARD_SQL = (
    "local_invalid_probability OR (notEmpty(sampling_scope) "
    "AND uniqExactIf(sampling_scope, notEmpty(sampling_scope)) OVER () > 1)"
)


def snapshot_select(
    definition,
    scope: Scope,
    start: datetime,
    end: datetime,
    as_of: datetime,
    filters: list[dict],
    source_run_id: str,
    *,
    unit_predicate: str | None = None,
    source_ctes: tuple[str, ...] = (),
    source_parameters: dict | None = None,
    include_evidence: bool = False,
    quarter_ambiguity: bool = False,
    sampling_context: str | None = None,
    ambiguity_only: bool = False,
) -> tuple[str, dict]:
    """Freeze one row per canonical selection unit, independently reducing inputs.

    Session evals join a distinct session membership relation, never spans. Each
    input then groups by unit_id BEFORE the final left joins (no score fanout).
    """
    params = {
        **scope.parameters(),
        "start": utc_timestamp(start),
        "end": utc_timestamp(end),
        "as_of": utc_timestamp(as_of),
        "source_run_id": source_run_id,
    }
    params.update(source_parameters or {})
    eval_table, live = eval_logger_source(include_cdc_tombstone_guard=True)
    version = eval_logger_version_column()
    eval_columns = ", ".join(
        [
            "id",
            "custom_eval_config_id",
            "config_hash",
            "trace_id",
            "observation_span_id",
            "trace_session_id",
            "target_type",
            "created_at",
            "updated_at",
            version,
            *eval_logger_live_state_columns(),
            "error",
            "status",
            "output_float",
            "output_bool",
            "output_str",
            "output_metadata",
            "eval_task_id",
        ]
    )
    conditions = []
    for index, item in enumerate(filters):
        # Fields come from an allowlist even when called by a worker, not only DRF.
        if item["field"] not in {"model", "provider", "status", "name", "service_name"}:
            raise ValueError("Unsupported metric filter field")
        params[f"filter_{index}"] = item["value"]
        conditions.append(f"s.{item['field']} = %(filter_{index})s")
    filter_sql = " AND ".join(conditions) or "1"
    session = f"ifNull(toString({resolved_id_expr('s.trace_session_id', 'sm')}), '')"
    user = f"ifNull(toString({resolved_id_expr('s.end_user_id', 'um')}), '')"
    params["cohort_key"] = definition.cohort_key
    span_identity = "toJSONString(tuple(toString(s.trace_id), toString(s.id)))"
    unit = {"span": span_identity, "session": session, "user": user}[
        definition.selection_unit
    ]
    sampling_identity = (
        "concat('span:', toString(s.id))"
        if definition.selection_unit == "span"
        else f"concat('{definition.selection_unit}:', {unit})"
    )
    # Span-id sampling ambiguity is scoped to the window; a caller tiling one
    # read over adjacent 15-minute windows keeps that scope per quarter.
    ambiguity_scope = (
        "s.id, toStartOfFifteenMinutes(s.start_time)" if quarter_ambiguity else "s.id"
    )
    sampling_ambiguity = (
        f"uniqExact(s.trace_id) OVER (PARTITION BY {ambiguity_scope}) > 1"
        if definition.selection_unit == "span" and not ambiguity_only
        else "0"
    )
    if sampling_context is not None:
        # The writer freezes the complete quarter's filtered sampler collisions
        # before paging identities. Never recompute this window over one page.
        sampling_ambiguity = (
            "(s.id, toStartOfFifteenMinutes(s.start_time)) IN "
            f"(SELECT span_id, bucket_start FROM ({sampling_context}))"
        )
    span_inputs = []
    for item in definition.inputs:
        if item["source"] != "span":
            continue
        value = span_value_sql(item, params, SPAN_MEASURE_SQL)
        span_inputs.append(f"{value} AS v_{item['alias']}")
    projections = ", " + ", ".join(span_inputs) if span_inputs else ""
    group_dims = getattr(definition, "group_dims", ()) or ()
    if len(group_dims) > 2:
        raise ValueError("Use at most two group dimensions")
    groups = []
    for index, dimension in enumerate(group_dims):
        if dimension in {"model", "provider", "status", "name", "service_name"}:
            groups.append(f"toJSONString(tuple('value', s.{dimension}))")
        elif isinstance(dimension, str) and dimension.startswith("attribute:"):
            params[f"group_dimension_{index}"] = dimension.removeprefix("attribute:")
            key = f"%(group_dimension_{index})s"
            groups.append(
                f"if(mapContains(s.attrs_string, {key}), toJSONString(tuple('value', s.attrs_string[{key}])), toJSONString(tuple('missing')))"
            )
        else:
            raise ValueError("Unsupported metric group dimension")
    group_expression = "toJSONString([" + ", ".join(groups) + "])" if groups else "''"
    # Window membership is mutable: a later span revision can move its start
    # outside this window. Collapse every revision of the immutable identity first,
    # then apply the window below. Unit reads may push only their immutable source
    # identity candidate predicate into PREWHERE; start/end are not a unit bound.
    source_prewhere = unit_predicate or "1"
    member_window = (
        ""
        if unit_predicate
        else "AND s.start_time >= %(start)s AND s.start_time < %(end)s"
    )
    ctes = [
        *source_ctes,
        f"""span_versions AS (
        SELECT id, project_id, org_id, trace_id, trace_session_id, end_user_id, start_time, end_time, created_at, updated_at,
            _version, is_deleted, model, provider, status, name, service_name,
            latency_ms, cost, total_tokens, prompt_tokens, completion_tokens,
            attrs_number, attrs_bool, attrs_string
        FROM spans
        PREWHERE project_id = %(project_id)s AND ({source_prewhere})
        WHERE (org_id = %(org_id)s OR isNull(org_id)) AND created_at <= %(as_of)s AND updated_at <= %(as_of)s
        ORDER BY _version DESC LIMIT 1 BY project_id, trace_id, id
    )""",
        f"""members AS (
        SELECT {span_identity} AS span_id, s.id AS raw_span_id, s.trace_id AS raw_trace_id, s.trace_session_id AS raw_session_id,
            {sampling_identity} AS unit_sampling_id, {sampling_ambiguity} AS unit_sampling_ambiguous,
            {session} AS session_id, {user} AS user_id, {unit} AS unit_id,
            s.start_time AS start_time, s.end_time AS end_time,
            s.updated_at AS updated_at, s._version AS _version, {group_expression} AS metric_group,
            if(%(cohort_key)s = '', '', if(mapContains(s.attrs_string, %(cohort_key)s), toJSONString(tuple('value', s.attrs_string[%(cohort_key)s])), '["missing"]')) AS cohort
            {projections}
        FROM span_versions s
        {remap_left_join("s.trace_session_id", "trace_session_id_remap", "sm")}
        {remap_left_join("s.end_user_id", "end_user_id_remap", "um")}
        WHERE s.is_deleted = 0 AND ({filter_sql}) AND notEmpty({unit}) AND {unit} != '00000000-0000-0000-0000-000000000000' {member_window}
    )""",
    ]
    if ambiguity_only:
        if definition.selection_unit != "span" or not quarter_ambiguity:
            raise ValueError("sampling context requires quarter-scoped spans")
        # Reuse exactly the compiler's winning, live, filtered membership, not
        # physical revisions or a separate approximation of membership. Two
        # extrema prove a trace collision without an unbounded per-id set.
        return (
            "WITH " + ", ".join(ctes) + " SELECT raw_span_id AS span_id, "
            "toStartOfFifteenMinutes(start_time) AS bucket_start FROM members "
            "GROUP BY raw_span_id, bucket_start "
            "HAVING min(raw_trace_id) != max(raw_trace_id)",
            params,
        )
    span_values = [
        reduce_sql(
            f"m.v_{item['alias']}",
            item,
            params,
            order=order_sql(item, source="span"),
            where=span_observation_sql(item, f"m.v_{item['alias']}"),
        )
        + f" AS v_{item['alias']}"
        for item in definition.inputs
        if item["source"] == "span"
    ]
    ctes.append(f"""units AS (
        SELECT m.unit_id AS unit_id,
            if(uniqExact(m.session_id) = 1, any(m.session_id), '') AS session_id,
            if(uniqExactIf(m.user_id, notEmpty(m.user_id) AND m.user_id != '00000000-0000-0000-0000-000000000000') = 1, anyIf(m.user_id, notEmpty(m.user_id) AND m.user_id != '00000000-0000-0000-0000-000000000000'), '') AS user_id,
            if(uniqExact(m.cohort) = 1, any(m.cohort), '["mixed"]') AS cohort,
            if(uniqExact(m.metric_group) = 1, any(m.metric_group), '["mixed"]') AS metric_group,
            toDate(min(m.start_time), 'UTC') AS day, count() AS source_rows,
            min(m.start_time) AS unit_start, min(isNotNull(m.end_time)) AS span_ended,
            max(toUInt64(toUnixTimestamp64Nano(m.updated_at))) AS last_arrival_ns
            {"," + ",".join(span_values) if span_values else ""}
        FROM members m GROUP BY m.unit_id
    )""")
    eval_inputs = [item for item in definition.inputs if item["source"] == "eval"]
    if eval_inputs:
        params["configs"] = tuple(str(item["config_id"]) for item in eval_inputs)
        canonical_eval_session = resolved_id_expr("e.trace_session_id", "em")
        # Mutable targets only NOMINATE immutable evaluation ids. Collapse every
        # eligible revision of each id, including retargets and tombstones outside
        # these members, before joining the winning target to selection units.
        # Filtering targets inside eval_versions would resurrect stale scores.
        ctes.append(f"""member_keys AS (
            SELECT {span_identity} AS span_id, s.trace_session_id AS raw_session_id
            FROM span_versions s WHERE s.is_deleted = 0 AND ({filter_sql})
        )""")
        # Every alias of a member session: the raw IDs plus every ID that shares a
        # remap survivor with one of them.
        survivors = survivor_map_subquery("trace_session_id_remap")
        ctes.append(f"""session_aliases AS (
            SELECT raw_session_id AS alias_id FROM member_keys WHERE isNotNull(raw_session_id)
            UNION DISTINCT SELECT any_id AS alias_id FROM ({survivors})
                WHERE survivor_id IN (SELECT survivor_id FROM ({survivors})
                    WHERE any_id IN (SELECT raw_session_id FROM member_keys WHERE isNotNull(raw_session_id)))
        )""")
        ctes.extend(
            [
                f"""eval_candidate_ids AS (
            SELECT DISTINCT id FROM {eval_table}
            PREWHERE custom_eval_config_id IN %(configs)s
            WHERE created_at <= %(as_of)s AND updated_at <= %(as_of)s
              AND (%(source_run_id)s = '' OR eval_task_id = %(source_run_id)s)
              AND ((toJSONString(tuple(toString(trace_id), observation_span_id)) IN (SELECT span_id FROM member_keys))
                   OR trace_session_id IN (SELECT alias_id FROM session_aliases))
        )""",
                f"""eval_versions AS (
            SELECT {eval_columns} FROM {eval_table}
            PREWHERE custom_eval_config_id IN %(configs)s AND id IN (SELECT id FROM eval_candidate_ids)
            WHERE created_at <= %(as_of)s AND updated_at <= %(as_of)s
              AND (%(source_run_id)s = '' OR eval_task_id = %(source_run_id)s)
            ORDER BY {version} DESC LIMIT 1 BY id
        )""",
                f"""eval_current AS (
            SELECT e.*, if(e.target_type = 'session', toString({canonical_eval_session}), toJSONString(tuple(toString(e.trace_id), e.observation_span_id))) AS target_id,
                coalesce(output_float, toFloat64(output_bool), toFloat64OrNull(output_str)) AS score
            FROM eval_versions e
            {remap_left_join("e.trace_session_id", "trace_session_id_remap", "em")}
            WHERE {live}
            ORDER BY created_at DESC, toString(id) DESC
            LIMIT 1 BY custom_eval_config_id, ifNull(config_hash, ''), target_type, target_id
        )""",
            ]
        )
        ctes.append(
            f"eval_rows AS (SELECT * FROM eval_current WHERE {EVAL_COMPLETED_PREDICATE})"
        )
        # Every evaluation target maps to at most one selection unit through ONE
        # keyed relation, so eval_rows (and the source scan behind it) is
        # referenced once. A span target is the (trace_id, span_id) identity; a
        # session target is the canonical session, joined only when it belongs to
        # exactly one unit. A session assigned to multiple user units is not
        # guessed: its input is absent in those units and stays visible as missing.
        # "Exactly one unit" is min = max of the unit key: exact, and only two
        # string states per group (a uniqExact set would be allocated for every
        # span key too, since span and session keys share this aggregation).
        # Output aliases differ from the member columns they aggregate, so HAVING
        # compares member units rather than resolving to an aggregate.
        kinds = (
            "[(0, m.span_id)]"
            if definition.selection_unit == "span"
            else "[(0, m.span_id), (1, m.session_id)]"
        )
        ctes.append(f"""eval_targets AS (
            SELECT tupleElement(target, 1) AS session_target, tupleElement(target, 2) AS target_key,
                min(m.unit_id) AS target_unit_id, any(m.unit_sampling_id) AS target_sampling_id,
                max(m.unit_sampling_ambiguous) AS target_sampling_ambiguous
            FROM members m ARRAY JOIN {kinds} AS target
            GROUP BY session_target, target_key
            HAVING session_target = 0 OR (notEmpty(target_key) AND min(m.unit_id) = max(m.unit_id))
        )""")
        ctes.append(
            """eval_units AS (
            SELECT e.*, t.target_unit_id AS unit_id, t.target_sampling_id AS unit_sampling_id,
                t.target_sampling_ambiguous AS unit_sampling_ambiguous
            FROM eval_rows e
            INNER JOIN eval_targets t
                ON e.target_id = t.target_key AND toUInt8(e.target_type = 'session') = t.session_target
        )"""
        )
    values, probabilities, invalids, scopes, strata, evaluation_values = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    modes, populations = [], []
    for item in definition.inputs:
        alias = item["alias"]
        if item["source"] == "span":
            values.append(f"u.v_{alias}")
            continue
        params[f"config_{alias}"] = str(item["config_id"])
        params[f"hash_{alias}"] = item["config_hash"]
        params["selection_unit"] = definition.selection_unit
        condition = f"custom_eval_config_id = %(config_{alias})s AND ifNull(config_hash, '') = %(hash_{alias})s"
        probability = probability_sql()
        metadata_unit = sampling_field("sampling_unit")
        metadata_id = sampling_field("sampling_unit_id")
        metadata_scope = sampling_scope_sql()
        metadata_stratum = sampling_field("stratum")
        metadata_mode = sampling_field("membership_mode")
        metadata_population = population_size_sql()
        lineage_valid = sampling_lineage_valid_sql()
        value = reduce_sql(
            eval_value_sql(item),
            item,
            params,
            order=order_sql(item, source="eval"),
            where=condition,
        )
        evaluation_values.extend(
            [
                f"{value} AS v_{alias}",
                f"minOrNullIf({probability}, {condition}) AS probability_{alias}",
                f"anyIf({metadata_scope}, {condition}) AS sampling_scope_{alias}",
                f"anyIf({metadata_stratum}, {condition}) AS sampling_stratum_{alias}",
                # Design facts for the uncertainty estimate. The mode is part of the
                # lineage scope, so a conflicting mode already invalidates the unit;
                # a conflicting population size only withholds the interval.
                f"anyIf({metadata_mode}, {condition}) AS sampling_mode_{alias}",
                f"minIf({metadata_population}, {condition}) AS population_min_{alias}",
                f"maxIf({metadata_population}, {condition}) AS population_max_{alias}",
                f"""toUInt8(countIf(({condition}) AND (isNull({probability}) OR NOT isFinite({probability})
                OR {probability} <= 0 OR {probability} > 1 OR {metadata_unit} != %(selection_unit)s
                OR {metadata_id} != unit_sampling_id OR unit_sampling_ambiguous OR empty({metadata_scope}) OR NOT ({lineage_valid}))) > 0
                OR uniqExactIf({probability}, {condition}) > 1 OR uniqExactIf({metadata_scope}, {condition}) > 1
                OR uniqExactIf({metadata_stratum}, {condition}) > 1) AS invalid_probability_{alias}""",
            ]
        )
        values.append(f"e.v_{alias}")
        probabilities.append(f"e.probability_{alias}")
        invalids.append(f"ifNull(e.invalid_probability_{alias}, 0)")
        scopes.append(f"ifNull(e.sampling_scope_{alias}, '')")
        strata.append(f"ifNull(e.sampling_stratum_{alias}, '')")
        modes.append(f"ifNull(e.sampling_mode_{alias}, '')")
        populations.extend([f"e.population_min_{alias}", f"e.population_max_{alias}"])
    join = ""
    if evaluation_values:
        # One conditional reduction per evaluator in a single unit-keyed pivot.
        # No evaluation row is ever joined to another evaluation row, and the
        # source relation is not rescanned once for every input alias.
        ctes.append(
            "evaluation_values AS (SELECT unit_id, max(greatest(toUInt64("
            + version
            + "), toUInt64(toUnixTimestamp64Nano(updated_at)))) AS last_arrival_ns, "
            + ", ".join(evaluation_values)
            + " FROM eval_units GROUP BY unit_id)"
        )
        join = "LEFT JOIN evaluation_values e ON u.unit_id = e.unit_id"
    input_items = list(definition.inputs)
    numeric_pairs = [
        (item["alias"], value)
        for item, value in zip(input_items, values, strict=True)
        if reduced_type(item) == "number" or "value_type" not in item
    ]
    bool_pairs = [
        (item["alias"], value)
        for item, value in zip(input_items, values, strict=True)
        if reduced_type(item) == "boolean" and "value_type" in item
    ]
    text_pairs = [
        (item["alias"], value)
        for item, value in zip(input_items, values, strict=True)
        if reduced_type(item) == "text"
    ]
    spec = getattr(definition, "field_spec", {}) or {}
    conditions = spec.get("conditions", [])
    if conditions:
        if any(item["alias"] == "field_condition" for item in input_items):
            raise ValueError(
                "field_condition is reserved for compiled typed conditions"
            )
        compiled = compile_conditions(conditions, input_items)
        params.update(compiled.parameters)
        predicate = compiled.sql
        # Values are independent unit reductions, never a span/eval fanout.
        by_alias = {
            item["alias"]: value
            for item, value in zip(input_items, values, strict=True)
        }
        predicate = re.sub(
            r"\bv_([a-z][a-z0-9_]*)\b", lambda m: by_alias[m.group(1)], predicate
        )
        numeric_pairs.append(("field_condition", predicate))

    def evidence_map(pairs, kind):
        names = ", ".join(f"'{alias}'" for alias, _ in pairs)
        expressions = ", ".join(
            f"CAST({value}, 'Nullable({kind})')" for _, value in pairs
        )
        return (
            f"mapFromArrays([{names}], [{expressions}])"
            if pairs
            else f"CAST(map(), 'Map(String, Nullable({kind}))')"
        )

    numeric_map = evidence_map(numeric_pairs, "Float64")
    boolean_map = evidence_map(bool_pairs, "UInt8")
    text_map = evidence_map(text_pairs, "String")
    arrival = "u.last_arrival_ns"
    if definition.weighting == "inverse_probability":
        if not probabilities:
            raise ValueError(
                "Inverse probability weighting requires a persisted evaluation input."
            )
        probs = "[" + ", ".join(probabilities) + "]"
        p = f"arrayMin({probs})"
        scope_array = "arrayFilter(x -> notEmpty(x), [" + ", ".join(scopes) + "])"
        stratum_array = "arrayFilter(x -> notEmpty(x), [" + ", ".join(strata) + "])"
        scope_value = f"arrayElement({scope_array}, 1)"
        stratum_value = f"arrayElement({stratum_array}, 1)"
        invalid = (
            " OR ".join(invalids)
            + f" OR length(arrayDistinct(arrayFilter(x -> isNotNull(x), {probs}))) > 1"
            + f" OR length(arrayDistinct({scope_array})) > 1 OR length(arrayDistinct({stratum_array})) > 1"
        )
        mode_value = (
            f"arrayElement(arrayFilter(x -> notEmpty(x), [{', '.join(modes)}]), 1)"
        )
        design = f"if({mode_value} IN ('closed', 'live'), {mode_value}, '')"
        sizes = (
            f"arrayDistinct(arrayFilter(x -> isNotNull(x), [{', '.join(populations)}]))"
        )
        population = (
            f"if(length({sizes}) = 1 AND assumeNotNull(arrayElement({sizes}, 1)) > 0, "
            f"CAST(assumeNotNull(arrayElement({sizes}, 1)), 'Nullable(UInt64)'), CAST(NULL, 'Nullable(UInt64)'))"
        )
    else:
        p, invalid, scope_value, stratum_value = "1.0", "0", "''", "''"
        design, population = "'unweighted'", "CAST(NULL, 'Nullable(UInt64)')"
    cohort_projection = "u.metric_group" if groups else "u.cohort"
    ctes.append(f"""projected_inputs AS (
        SELECT u.unit_id AS unit_id, u.session_id AS session_id, u.user_id AS user_id, {cohort_projection} AS cohort, u.day AS day,
            {numeric_map} AS input_values,
            {boolean_map} AS inputs_bool, {text_map} AS inputs_text,
            u.unit_start AS unit_start, u.span_ended AS span_ended, {arrival} AS last_arrival_ns,
            {p} AS probability, toUInt8({invalid}) AS local_invalid_probability, u.source_rows AS source_rows,
            {scope_value} AS sampling_scope, {stratum_value} AS sampling_stratum,
            {design} AS sampling_design, {population} AS population_size
        FROM units u {join}
    )""")
    # Unit reads keep LOCAL evidence only: the window guard is re-applied when
    # parts are combined over the read window, never frozen per batch.
    invalid_output = (
        "local_invalid_probability" if include_evidence else WINDOW_SCOPE_GUARD_SQL
    )
    evidence_output = (
        ", inputs_bool, inputs_text, unit_start, last_arrival_ns"
        + (", span_ended" if definition.selection_unit == "span" else "")
        if include_evidence
        else ""
    )
    query = (
        "WITH "
        + ",\n".join(ctes)
        + f"""
        SELECT unit_id, session_id, user_id,
            if(%(cohort_key)s = '' AND notEmpty(p.sampling_stratum), p.sampling_stratum, p.cohort) AS cohort,
            day, input_values, probability,
            toUInt8({invalid_output}) AS invalid_probability,
            source_rows, sampling_scope, sampling_stratum, sampling_design, population_size{evidence_output}
        FROM projected_inputs p
        WHERE throwIf(length(if(%(cohort_key)s = '' AND notEmpty(p.sampling_stratum), p.sampling_stratum, p.cohort)) > 2048,
            'Metric cohort key exceeds 2048 bytes') = 0
    """
    )
    return query, params


ROLLUP_GROUPS = {
    "overall": "''",
    "session": "session_id",
    "user": "user_id",
    "cohort": "cohort",
    "day": "toString(day)",
}


def rollup_group_sql(rollup: str) -> tuple[str, str]:
    """The group key of a rollup and the filter that keeps units it can represent."""
    group = ROLLUP_GROUPS[rollup]
    return group, f"notEmpty({group})" if rollup in {"session", "user"} else "1"


def aggregate_select(source_sql: str, formula: Formula, *, rollup: str) -> str:
    """Ratios are ratios of weighted sums, never averages of subgroup ratios.

    One pass over the units computes the point estimate and its design-based
    uncertainty (see ``uncertainty.py``) through the same additive parts the live
    bucket reads combine (``parts.py``). The source's window scope guard already
    ran (snapshot_select), so parts are keyed by stratum alone, as before.
    """
    from tracer.services.derived_metrics.parts import (
        LEGACY_SCOPE,
        combine_parts_select,
        stratum_parts_select,
    )

    parts = stratum_parts_select(
        source_sql, formula, rollup=rollup, scope_column=LEGACY_SCOPE
    )
    return combine_parts_select(parts, formula, rollup=rollup)


def rollup_insert(
    definition,
    run,
    result_id: UUID,
    rollup: str,
    *,
    group_range=None,
    mixed_scope=False,
    unit_range=False,
) -> tuple[str, dict]:
    metric = definition.metric
    scope = Scope(metric.organization_id, metric.workspace_id, metric.project_id)
    if getattr(run, "kind", "run") == "snapshot":
        from tracer.services.derived_metrics.unit_reads import field_recipe

        formula = field_recipe(definition).formula
        # Sketch snapshots use the same committed-state finalizer as KPI reads.
    else:
        formula = compile_formula(
            definition.formula, [item["alias"] for item in definition.inputs]
        )
    predicate = "1"
    if group_range is not None:
        group, _ = rollup_group_sql(rollup)
        predicate = f"{group} >= %(group_min)s AND {group} <= %(group_max)s"
        if unit_range:
            predicate += " AND unit_id >= %(group_min)s AND unit_id <= %(group_max)s"
    # Scope validation is WINDOW-wide, not page-local. Otherwise paging session
    # groups could accidentally turn a mixed-scope snapshot into valid estimates.
    columns = (
        "* REPLACE(toUInt8(if(notEmpty(sampling_scope), 1, invalid_probability)) AS invalid_probability)"
        if mixed_scope
        else "*"
    )
    source = f"SELECT {columns} FROM derived_metric_inputs WHERE {SCOPE_SQL} AND snapshot_id = %(snapshot_id)s AND ({predicate}) ORDER BY unit_id"
    params = {
        **scope.parameters(),
        **(formula.parameters if formula else {}),
        "snapshot_id": str(run.snapshot_id),
        "result_id": str(result_id),
        "metric_id": str(metric.id),
        "metric_version": definition.version,
        "run_id": str(run.id),
        "rollup": rollup,
        "start": utc_timestamp(run.window_start),
        "end": utc_timestamp(run.window_end),
        "as_of": utc_timestamp(run.as_of),
        "source_run_id": run.source_run_id,
        "definition_hash": definition.definition_hash,
        "sampling_scope": run.source_manifest.get("sampling_scope", ""),
    }
    if group_range is not None:
        params.update(group_min=group_range[0], group_max=group_range[1])
    if getattr(run, "kind", "run") == "snapshot" and definition.kind in {
        "unique_count",
        "percentile",
    }:
        from tracer.services.derived_metrics.snapshot_sketches import (
            aggregate_select as sketch_select,
        )

        aggregate = sketch_select(
            run,
            rollup,
            params,
            group_range=group_range,
            mixed_scope=mixed_scope,
            unit_range=unit_range,
        )
    elif getattr(run, "kind", "run") == "snapshot":
        from tracer.services.derived_metrics.parts import (
            combine_parts_select,
            stratum_parts_select,
        )

        aggregate = combine_parts_select(
            stratum_parts_select(
                source, formula, rollup=rollup, scope_column="sampling_scope"
            ),
            formula,
            rollup=rollup,
        )
    else:
        aggregate = aggregate_select(source, formula, rollup=rollup)
    return (
        f"""INSERT INTO derived_metric_results
        SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(metric_id)s, %(metric_version)s,
            %(run_id)s, %(result_id)s, %(rollup)s, group_key, value, numerator, denominator, status,
            unit_count, valid_count, missing_count, invalid_probability_count,
            %(start)s, %(end)s, %(as_of)s, %(source_run_id)s, %(definition_hash)s, %(sampling_scope)s,
            {", ".join(UNCERTAINTY_COLUMNS)}
        FROM ({aggregate})""",
        params,
    )
