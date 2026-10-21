"""Scoped ClickHouse plans. No raw telemetry leaves the database for computation."""

from __future__ import annotations

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
    probability_sql,
    sampling_field,
    sampling_lineage_valid_sql,
    sampling_scope_sql,
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


def utc_timestamp(value: datetime) -> str:
    # clickhouse-driver formats datetime parameters only to seconds. Bind a
    # canonical UTC DateTime64 string so half-open windows retain microseconds.
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def _reduce(value: str, reducer: str, *, order: str) -> str:
    if reducer == "latest":
        return f"tupleElement(argMax(tuple({value}), {order}), 1)"
    return f"{ {'mean': 'avgOrNull', 'min': 'minOrNull', 'max': 'maxOrNull'}[reducer] }({value})"


def snapshot_select(
    definition,
    scope: Scope,
    start: datetime,
    end: datetime,
    as_of: datetime,
    filters: list[dict],
    source_run_id: str,
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
    sampling_ambiguity = (
        "uniqExact(s.trace_id) OVER (PARTITION BY s.id) > 1"
        if definition.selection_unit == "span"
        else "0"
    )
    span_inputs = []
    for item in definition.inputs:
        if item["source"] != "span":
            continue
        field = item["field"]
        if field in {
            "latency_ms",
            "cost",
            "total_tokens",
            "prompt_tokens",
            "completion_tokens",
        }:
            value = f"toFloat64(s.{field})"
        else:
            if field not in {"attribute_number", "attribute_bool"}:
                raise ValueError("Unsupported span input")
            key = f"attr_{item['alias']}"
            params[key] = item["attribute_key"]
            mapping = "attrs_number" if field == "attribute_number" else "attrs_bool"
            value = f"if(mapContains(s.{mapping}, %({key})s), toFloat64(s.{mapping}[%({key})s]), NULL)"
        span_inputs.append(f"{value} AS v_{item['alias']}")
    projections = ", " + ", ".join(span_inputs) if span_inputs else ""
    ctes = [
        """span_versions AS (
        SELECT id, project_id, org_id, trace_id, trace_session_id, end_user_id, start_time, created_at, updated_at,
            _version, is_deleted, model, provider, status, name, service_name,
            latency_ms, cost, total_tokens, prompt_tokens, completion_tokens,
            attrs_number, attrs_bool, attrs_string
        FROM spans
        PREWHERE project_id = %(project_id)s AND start_time >= %(start)s AND start_time < %(end)s
        WHERE (org_id = %(org_id)s OR isNull(org_id)) AND created_at <= %(as_of)s AND updated_at <= %(as_of)s
        ORDER BY _version DESC LIMIT 1 BY project_id, trace_id, id
    )""",
        f"""members AS (
        SELECT {span_identity} AS span_id, s.trace_id AS raw_trace_id, s.trace_session_id AS raw_session_id,
            {sampling_identity} AS unit_sampling_id, {sampling_ambiguity} AS unit_sampling_ambiguous,
            {session} AS session_id, {user} AS user_id, {unit} AS unit_id,
            s.start_time, s.updated_at,
            if(%(cohort_key)s = '', '', if(mapContains(s.attrs_string, %(cohort_key)s), toJSONString(tuple('value', s.attrs_string[%(cohort_key)s])), '["missing"]')) AS cohort
            {projections}
        FROM span_versions s
        {remap_left_join("s.trace_session_id", "trace_session_id_remap", "sm")}
        {remap_left_join("s.end_user_id", "end_user_id_remap", "um")}
        WHERE s.is_deleted = 0 AND ({filter_sql}) AND notEmpty({unit}) AND {unit} != '00000000-0000-0000-0000-000000000000'
    )""",
    ]
    span_values = [
        f"{_reduce('m.v_' + item['alias'], item['reducer'], order='tuple(m.updated_at, m.span_id)')} AS v_{item['alias']}"
        for item in definition.inputs
        if item["source"] == "span"
    ]
    ctes.append(f"""units AS (
        SELECT m.unit_id AS unit_id,
            if(uniqExact(m.session_id) = 1, any(m.session_id), '') AS session_id,
            if(uniqExact(m.user_id) = 1, any(m.user_id), '') AS user_id,
            if(uniqExact(m.cohort) = 1, any(m.cohort), '["mixed"]') AS cohort,
            toDate(min(m.start_time), 'UTC') AS day, count() AS source_rows
            {"," + ",".join(span_values) if span_values else ""}
        FROM members m GROUP BY m.unit_id
    )""")
    eval_inputs = [item for item in definition.inputs if item["source"] == "eval"]
    if eval_inputs:
        params["configs"] = tuple(str(item["config_id"]) for item in eval_inputs)
        canonical_eval_session = resolved_id_expr("e.trace_session_id", "em")
        ctes.append(f"""session_aliases AS (
            SELECT raw_session_id AS alias_id FROM members WHERE isNotNull(raw_session_id)
            UNION DISTINCT SELECT any_id AS alias_id FROM ({survivor_map_subquery("trace_session_id_remap")})
                WHERE toString(survivor_id) IN (SELECT session_id FROM members)
        )""")
        ctes.extend(
            [
                f"""eval_versions AS (
            SELECT {eval_columns} FROM {eval_table}
            PREWHERE custom_eval_config_id IN %(configs)s
            WHERE created_at <= %(as_of)s AND updated_at <= %(as_of)s
              AND (%(source_run_id)s = '' OR eval_task_id = %(source_run_id)s)
              AND ((toJSONString(tuple(toString(trace_id), observation_span_id)) IN (SELECT span_id FROM members))
                   OR trace_session_id IN (SELECT alias_id FROM session_aliases))
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
        if definition.selection_unit != "span":
            # A session assigned to multiple user units is not guessed. Its input
            # is absent in those units and remains visible as missing data.
            ctes.append("""session_members AS (
                SELECT m.session_id, any(m.unit_id) AS unit_id,
                    any(m.unit_sampling_id) AS unit_sampling_id, max(m.unit_sampling_ambiguous) AS unit_sampling_ambiguous
                FROM members m
                WHERE notEmpty(m.session_id) GROUP BY m.session_id
                HAVING uniqExact(m.unit_id) = 1
            )""")
        ctes.append(
            """eval_units AS (
            SELECT e.*, m.unit_id, m.unit_sampling_id, m.unit_sampling_ambiguous FROM eval_rows e
            INNER JOIN (SELECT span_id, any(unit_id) AS unit_id,
                any(unit_sampling_id) AS unit_sampling_id, max(unit_sampling_ambiguous) AS unit_sampling_ambiguous
                FROM members GROUP BY span_id) m
                ON e.target_id = m.span_id
            WHERE e.target_type != 'session'
            """
            + (
                """UNION ALL
            SELECT e.*, m.unit_id, m.unit_sampling_id, m.unit_sampling_ambiguous FROM eval_rows e
            INNER JOIN session_members m ON e.target_id = m.session_id
            WHERE e.target_type = 'session'"""
                if definition.selection_unit != "span"
                else ""
            )
            + ")"
        )
    values, probabilities, invalids, scopes, strata, evaluation_values = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
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
        lineage_valid = sampling_lineage_valid_sql()
        if item["reducer"] == "latest":
            value = f"if(countIf({condition}) > 0, tupleElement(argMaxIf(tuple(score), tuple(created_at, toString(id)), {condition}), 1), NULL)"
        else:
            function = {
                "mean": "avgOrNullIf",
                "min": "minOrNullIf",
                "max": "maxOrNullIf",
            }[item["reducer"]]
            value = f"{function}(score, {condition})"
        evaluation_values.extend(
            [
                f"{value} AS v_{alias}",
                f"minOrNullIf({probability}, {condition}) AS probability_{alias}",
                f"anyIf({metadata_scope}, {condition}) AS sampling_scope_{alias}",
                f"anyIf({metadata_stratum}, {condition}) AS sampling_stratum_{alias}",
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
    join = ""
    if evaluation_values:
        # One conditional reduction per evaluator in a single unit-keyed pivot.
        # No evaluation row is ever joined to another evaluation row, and the
        # source relation is not rescanned once for every input alias.
        ctes.append(
            "evaluation_values AS (SELECT unit_id, "
            + ", ".join(evaluation_values)
            + " FROM eval_units GROUP BY unit_id)"
        )
        join = "LEFT JOIN evaluation_values e ON u.unit_id = e.unit_id"
    names = ", ".join(f"'{item['alias']}'" for item in definition.inputs)
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
    else:
        p, invalid, scope_value, stratum_value = "1.0", "0", "''", "''"
    ctes.append(f"""projected_inputs AS (
        SELECT u.unit_id AS unit_id, u.session_id AS session_id, u.user_id AS user_id, u.cohort AS cohort, u.day AS day,
            mapFromArrays([{names}], [{", ".join(values)}]) AS input_values,
            {p} AS probability, toUInt8({invalid}) AS local_invalid_probability, u.source_rows AS source_rows,
            {scope_value} AS sampling_scope, {stratum_value} AS sampling_stratum
        FROM units u {join}
    )""")
    query = (
        "WITH "
        + ",\n".join(ctes)
        + """
        SELECT unit_id, session_id, user_id,
            if(%(cohort_key)s = '' AND notEmpty(p.sampling_stratum), p.sampling_stratum, p.cohort) AS cohort,
            day, input_values, probability,
            toUInt8(local_invalid_probability OR (notEmpty(sampling_scope)
                AND uniqExactIf(sampling_scope, notEmpty(sampling_scope)) OVER () > 1)) AS invalid_probability,
            source_rows, sampling_scope, sampling_stratum
        FROM projected_inputs p
        WHERE throwIf(length(if(%(cohort_key)s = '' AND notEmpty(p.sampling_stratum), p.sampling_stratum, p.cohort)) > 2048,
            'Metric cohort key exceeds 2048 bytes') = 0
    """
    )
    return query, params


def aggregate_select(source_sql: str, formula: Formula, *, rollup: str) -> str:
    """Ratios are ratios of weighted sums, never averages of subgroup ratios."""
    group = {
        "overall": "''",
        "session": "session_id",
        "user": "user_id",
        "cohort": "cohort",
        "day": "toString(day)",
    }[rollup]
    group_clause = "" if rollup == "overall" else "GROUP BY group_key"
    group_filter = f"notEmpty({group})" if rollup in {"session", "user"} else "1"
    unpack = ", ".join(
        f"input_values['{alias}'] AS v_{alias}" for alias in formula.required
    )
    valid = f"({formula.complete_sql}) AND isNotNull(n) AND isFinite(n) AND isNotNull(d) AND isFinite(d)"
    value = (
        "weighted_n"
        if formula.kind in {"sum", "count_if"}
        else "weighted_n / nullIf(weighted_d, 0)"
    )
    denominator = "NULL" if formula.kind in {"sum", "count_if"} else "weighted_d"
    zero = "0" if formula.kind in {"sum", "count_if"} else "weighted_d = 0"
    denominator_aggregate = (
        "0.0"
        if formula.kind in {"sum", "count_if"}
        else "sumIf(d / nullIf(probability, 0), complete AND invalid_probability = 0)"
    )
    return f"""
        SELECT group_key,
            if(status = 'ok', computed_value, NULL) AS value,
            if(valid_count > 0 AND isFinite(weighted_n), weighted_n, NULL) AS numerator,
            if(valid_count > 0 AND isFinite({denominator}), {denominator}, NULL) AS denominator,
            multiIf(unit_count = 0, 'no_data', invalid_probability_count > 0, 'invalid_probability',
                valid_count = 0, 'missing_inputs', {zero}, 'zero_denominator',
                NOT isFinite(weighted_n) OR NOT isFinite(weighted_d) OR NOT isFinite(computed_value), 'non_finite', 'ok') AS status,
            unit_count, valid_count, missing_count, invalid_probability_count
        FROM (
            SELECT {group} AS group_key, count() AS unit_count,
                countIf(complete AND invalid_probability = 0) AS valid_count,
                countIf(NOT complete) AS missing_count,
                countIf(invalid_probability != 0) AS invalid_probability_count,
                sumIf(n / nullIf(probability, 0), complete AND invalid_probability = 0) AS weighted_n,
                {denominator_aggregate} AS weighted_d,
                {value} AS computed_value
            FROM (
                SELECT *, {formula.numerator} AS n, {formula.denominator} AS d,
                    ifNull({valid}, 0) AS complete
                FROM (SELECT *, {unpack} FROM ({source_sql}))
            ) WHERE {group_filter} {group_clause}
        )
    """


def rollup_insert(definition, run, result_id: UUID, rollup: str) -> tuple[str, dict]:
    metric = definition.metric
    scope = Scope(metric.organization_id, metric.workspace_id, metric.project_id)
    formula = compile_formula(
        definition.formula, [item["alias"] for item in definition.inputs]
    )
    source = f"SELECT * FROM derived_metric_inputs WHERE {SCOPE_SQL} AND snapshot_id = %(snapshot_id)s ORDER BY unit_id"
    params = {
        **scope.parameters(),
        **formula.parameters,
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
    return (
        f"""INSERT INTO derived_metric_results
        SELECT %(org_id)s, %(workspace_id)s, %(project_id)s, %(metric_id)s, %(metric_version)s,
            %(run_id)s, %(result_id)s, %(rollup)s, group_key, value, numerator, denominator, status,
            unit_count, valid_count, missing_count, invalid_probability_count,
            %(start)s, %(end)s, %(as_of)s, %(source_run_id)s, %(definition_hash)s, %(sampling_scope)s
        FROM ({aggregate_select(source, formula, rollup=rollup)})""",
        params,
    )
