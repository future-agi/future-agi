"""Bounded conversation recomputation and exact committed field overlays.

The writer supplies a finite candidate set and one pinned PG manifest. Unit
membership is not a time window: there is deliberately NO first_seen/start bound
(T6). start/end below validate sampling coverage only. Changed source identities
from the committed dependency/preimage ledger supplement physical span nominees.
Mutable predicates are applied only after immutable-key version collapse.

Normalized field_spec consumed here: optional inputs (typed descriptors),
conditions (after-reduction comparisons), and value_alias. B05 owns wire parsing;
this module also accepts the legacy definition.inputs/formula recipe unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
    survivor_map_subquery,
)
from tracer.services.derived_metrics.formula import (
    Formula,
    FormulaError,
    compile_formula,
)
from tracer.services.derived_metrics.queries import Scope, snapshot_select
from tracer.services.derived_metrics.storage import (
    FIELD_CHECKPOINT,
    FIELD_VALUES,
    SOURCE_DEPENDENCIES,
)
from tracer.services.derived_metrics.typed_fields import reduced_type, value_column

# Bounded source statements. 1,000 is only a provisional safety cap: M5 measured
# 6.001 s cold for one 1,000-id unbounded statement, but T3a has not compared batch
# sizes under M2's memory budget. Do not treat this as production-qualified until
# that measurement selects a bound and the release gate records it.
MAX_UNIT_IDS = 1000
NIL_ID = "00000000-0000-0000-0000-000000000000"
METRIC_KINDS = frozenset(
    {"share", "count", "average", "total", "unique_count", "percentile", "custom"}
)
# Output columns of unit_values_select that are NOT FIELD_VALUES payload.
UNIT_READ_EXTRA_COLUMNS = ("source_rows",)


def metric_value_type(kind: str, source_type: str) -> str:
    """The per-unit value type a metric kind stores, from its reduced source type.

    Share and Count store yes/no (counts / doesn't count); Average, Total and
    Percentile need a number; Custom formulas are numeric; Unique count keeps the
    source type, whose sketch math belongs to the bucket readers.
    """
    if kind not in METRIC_KINDS:
        raise FormulaError("Unsupported metric kind.")
    if kind in {"share", "count"}:
        return "boolean"
    if kind in {"average", "total", "percentile"}:
        if source_type != "number":
            raise FormulaError("This metric requires a numeric reduced source.")
        return "number"
    if kind == "unique_count":
        value_column(source_type)  # validates the source type
        return source_type
    return "number"


@dataclass(frozen=True)
class FieldRecipe:
    """One metric version's field: what snapshot_select reads and how it combines."""

    source: SimpleNamespace
    formula: Formula | None
    kind: str
    value_type: str
    value_alias: str

    @property
    def value_column(self) -> str:
        return value_column(self.value_type)

    @property
    def required_inputs(self) -> tuple[dict, ...]:
        """Source descriptors whose absence makes the unit's value No value."""
        if self.formula is None:
            names = {self.value_alias}
        else:
            names = set(self.formula.required)
            if "field_condition" in names:
                names.discard("field_condition")
                for condition in self.source.field_spec.get("conditions", []):
                    names.add(condition["alias"])
                    if condition.get("right_alias"):
                        names.add(condition["right_alias"])
        return tuple(item for item in self.source.inputs if item["alias"] in names)


def field_recipe(definition) -> FieldRecipe:
    """Normalize a version (sentence kinds or legacy custom) into a FieldRecipe.

    Typed text and yes/no conditions compile to the numeric 0/1 indicator
    ``field_condition`` before the existing numeric formula compiler runs; text is
    never coerced to numbers and legacy custom formulas compile unchanged.
    """
    spec = dict(getattr(definition, "field_spec", {}) or {})
    inputs = spec.get("inputs") or definition.inputs
    kind = getattr(definition, "kind", "custom")
    if kind not in METRIC_KINDS:
        raise FormulaError("Unsupported metric kind.")
    alias = spec.get("value_alias", inputs[0]["alias"])
    types = {item["alias"]: reduced_type(item) for item in inputs}
    if alias not in types:
        raise FormulaError("Unknown field value alias.")
    value_type = metric_value_type(kind, types[alias])
    if kind in {"share", "count"}:
        if not spec.get("conditions"):
            if types[alias] != "boolean":
                raise FormulaError(
                    "Share/Count requires a boolean field or typed conditions."
                )
            spec["conditions"] = [{"alias": alias, "operator": "eq", "value": True}]
        formula_text = (
            "mean(field_condition > 0)"
            if kind == "share"
            else "count_if(field_condition > 0)"
        )
    elif kind in {"average", "total", "percentile"}:
        formula_text = f"{'sum' if kind == 'total' else 'mean'}({alias})"
    elif kind == "unique_count":
        formula_text = None
    else:
        formula_text = definition.formula
    numeric_aliases = [
        item["alias"]
        for item in inputs
        if reduced_type(item) == "number" or "value_type" not in item
    ]
    if spec.get("conditions"):
        numeric_aliases.append("field_condition")
    formula = compile_formula(formula_text, numeric_aliases) if formula_text else None
    source = SimpleNamespace(
        inputs=inputs,
        formula=formula_text,
        selection_unit="span"
        if getattr(definition, "level", "conversation") == "span"
        else "session",
        weighting=getattr(definition, "weighting", "none"),
        cohort_key=getattr(definition, "cohort_key", ""),
        group_dims=getattr(definition, "group_dims", ()),
        field_spec=spec,
    )
    return FieldRecipe(source, formula, kind, value_type, alias)


def _ids(unit_ids) -> tuple[str, ...]:
    ids = tuple(dict.fromkeys(str(UUID(str(value))) for value in unit_ids))
    if not ids or len(ids) > MAX_UNIT_IDS:
        raise ValueError(f"Use 1–{MAX_UNIT_IDS} conversation ids per source statement.")
    return ids


def _attempts(manifest) -> tuple[str | None, tuple[str, ...]]:
    """Exact attempt IDs only; never infer commits from a numeric head."""
    if manifest is None:
        raise ValueError("A pinned committed manifest is required for stored reads.")

    def get(name, default=None):
        return (
            manifest.get(name, default)
            if isinstance(manifest, dict)
            else getattr(manifest, name, default)
        )

    checkpoint = get("checkpoint_attempt_id")
    checkpoint = str(UUID(str(checkpoint))) if checkpoint else None
    deltas = tuple(
        dict.fromkeys(str(UUID(str(value))) for value in get("delta_attempt_ids", ()))
    )
    return checkpoint, deltas


def _committed_rows_sql(
    scope: Scope, field_id, field_version: int, manifest, unit_predicate: str
) -> tuple[str, dict]:
    """Allowlisted attempts first, immutable-key collapse second, nothing else.

    ``unit_predicate`` is a builder-owned predicate on the immutable unit_id key
    (a bound tuple or a CTE subquery), pushed into PREWHERE with the scope keys.
    The latest committed revision per unit is the highest generation, then
    _version; neither FINAL nor a numeric head stands in for the allowlist.
    """
    checkpoint, deltas = _attempts(manifest)
    params = {
        **scope.parameters(),
        "field_read_id": str(UUID(str(field_id))),
        "field_read_version": int(field_version),
        "field_read_checkpoint": checkpoint or NIL_ID,
        "field_read_deltas": deltas or (NIL_ID,),
    }
    if params["field_read_version"] < 1:
        raise ValueError("Field version must be positive.")
    prewhere = (
        "project_id = %(project_id)s AND org_id = %(org_id)s AND field_id = %(field_read_id)s "
        f"AND field_version = %(field_read_version)s AND level = 'conversation' AND {unit_predicate}"
    )
    columns = ", ".join(FIELD_VALUES.columns)
    branches = []
    if checkpoint:
        branches.append(
            f"SELECT {columns} FROM {FIELD_CHECKPOINT.name} PREWHERE {prewhere} "
            "WHERE attempt_id = %(field_read_checkpoint)s AND workspace_id = %(workspace_id)s"
        )
    if deltas:
        branches.append(
            f"SELECT {columns} FROM {FIELD_VALUES.name} PREWHERE {prewhere} "
            "WHERE attempt_id IN %(field_read_deltas)s AND workspace_id = %(workspace_id)s"
        )
    if not branches:
        branches.append(
            f"SELECT {columns} FROM {FIELD_VALUES.name} PREWHERE {prewhere} WHERE 0"
        )
    latest = (
        f"SELECT {columns} FROM ({' UNION ALL '.join(branches)}) "
        f"ORDER BY generation DESC, {FIELD_VALUES.version_column} DESC "
        f"LIMIT 1 BY {', '.join(FIELD_VALUES.logical_key)}"
    )
    return latest, params


def committed_unit_values_select(
    scope: Scope,
    field_id: UUID,
    field_version: int,
    unit_ids,
    manifest,
    *,
    include_deleted: bool = True,
) -> tuple[str, dict]:
    """Logical current field rows through one checkpoint plus exact allowed deltas.

    The caller (publication.py) proves visibility, pins/revocation and retention.
    This pure SQL builder never reads a project head or substitutes CH max(version).
    Only immutable scope/field/unit keys are pushed inside the collapse (DCM3).
    """
    latest, params = _committed_rows_sql(
        scope, field_id, field_version, manifest, "unit_id IN %(field_read_units)s"
    )
    params["field_read_units"] = _ids(unit_ids)
    deleted = "" if include_deleted else " WHERE is_deleted = 0"
    return f"SELECT * FROM ({latest}){deleted}", params


def _source_plan(
    scope: Scope, ids: tuple[str, ...], manifest, source_identities
) -> tuple[tuple[str, ...], str, dict]:
    params = {"unit_read_ids": list(ids)}
    canonical = resolved_id_expr("r.requested_id", "rm")
    survivors = survivor_map_subquery("trace_session_id_remap")
    ctes = [
        f"""unit_read_requests AS (
        SELECT toString(r.requested_id) AS raw_id, toString({canonical}) AS unit_id FROM (
            SELECT toUUID(arrayJoin(%(unit_read_ids)s)) AS requested_id
        ) r {remap_left_join("r.requested_id", "trace_session_id_remap", "rm")}
    )""",
        "requested_units AS (SELECT DISTINCT unit_id FROM unit_read_requests)",
        f"""unit_aliases AS (
        SELECT toUUID(unit_id) AS alias_id FROM requested_units
        UNION DISTINCT SELECT any_id AS alias_id FROM ({survivors})
            WHERE toString(survivor_id) IN (SELECT unit_id FROM requested_units)
    )""",
    ]
    nominees = [
        """SELECT trace_id AS trace_key, id AS span_key FROM spans
        PREWHERE project_id = %(project_id)s AND trace_session_id IN (SELECT alias_id FROM unit_aliases)
        WHERE org_id = %(org_id)s OR isNull(org_id)"""
    ]
    if source_identities:
        params["unit_read_source_ids"] = [
            (str(trace), str(span)) for trace, span in source_identities
        ]
        nominees.append(
            "SELECT tupleElement(pair, 1) AS trace_key, tupleElement(pair, 2) AS span_key "
            "FROM (SELECT arrayJoin(%(unit_read_source_ids)s) AS pair)"
        )
    if manifest is not None:
        checkpoint, deltas = _attempts(manifest)
        # Dependencies have no separate checkpoint table. A compacted base is an
        # immutable attempt in this same store. Old committed memberships are a
        # SAFE superset, not an authoritative latest membership read.
        params["unit_read_dependency_attempts"] = tuple(
            dict.fromkeys(([checkpoint] if checkpoint else []) + list(deltas))
        ) or (NIL_ID,)
        nominees.append(f"""SELECT trace_id AS trace_key, span_id AS span_key FROM {SOURCE_DEPENDENCIES.name}
            PREWHERE project_id = %(project_id)s AND org_id = %(org_id)s
            WHERE attempt_id IN %(unit_read_dependency_attempts)s
                AND (canonical_session_id IN (SELECT unit_id FROM requested_units)
                    OR canonical_session_id IN (SELECT raw_id FROM unit_read_requests))""")
    ctes.append("unit_source_keys AS (" + " UNION DISTINCT ".join(nominees) + ")")
    # Read every physical revision of nominated IMMUTABLE (trace,id) identities,
    # then snapshot_select collapses them and filters canonical membership.
    predicate = "(trace_id, id) IN (SELECT trace_key, span_key FROM unit_source_keys)"
    return tuple(ctes), predicate, params


def _authority_ctes(predicate):
    """Snapshot facts, separate from the ceiling-free nomination superset."""
    ctes = []
    ctes.append(f"""unit_live_versions AS (
        SELECT project_id, trace_id, id, trace_session_id, end_user_id, start_time, updated_at, is_deleted
        FROM spans PREWHERE project_id = %(project_id)s AND {predicate}
        WHERE (org_id = %(org_id)s OR isNull(org_id)) AND created_at <= %(as_of)s AND updated_at <= %(as_of)s
        ORDER BY _version DESC LIMIT 1 BY project_id, trace_id, id
    )""")
    canonical_user = f"toString({resolved_id_expr('s.end_user_id', 'au')})"
    has_user = f"notEmpty({canonical_user}) AND {canonical_user} != '{NIL_ID}'"
    ctes.append(f"""conversation_authority AS (
        SELECT toString({resolved_id_expr("s.trace_session_id", "am")}) AS authority_id,
            min(s.start_time) AS anchor, max(toUInt64(toUnixTimestamp64Nano(s.updated_at))) AS arrival,
            count() AS live_members,
            if(uniqExactIf({canonical_user}, {has_user}) = 1, anyIf({canonical_user}, {has_user}), '') AS canonical_user
        FROM unit_live_versions s
        {remap_left_join("s.trace_session_id", "trace_session_id_remap", "am")}
        {remap_left_join("s.end_user_id", "end_user_id_remap", "au")}
        WHERE s.is_deleted = 0 GROUP BY authority_id
    )""")
    return tuple(ctes)


def unit_values_select(
    definition,
    scope: Scope,
    unit_ids,
    *,
    start: datetime,
    end: datetime,
    as_of: datetime,
    filters: list[dict] | None = None,
    source_run_id: str = "",
    settle_minutes: int = 30,
    manifest=None,
    source_identities=(),
) -> tuple[str, dict]:
    """One typed, fully evidenced row per canonical requested conversation.

    Missing/deleted candidates still return tombstones; previous committed rows
    preserve their old anchor for a dirty-bucket replacement. A requested id that
    a remap has retired (it now resolves to another survivor) and that still has a
    live committed row gets its own tombstone, so its old bucket is invalidated.
    A user-level metric stores the SAME conversation rows, not raw user units;
    user_id is canonical, or '' when the conversation names zero or several users.
    Saved definition filters always constrain source membership; optional filters
    may only narrow that membership, never replace the saved predicates.
    Trace storage is off.
    """
    if getattr(definition, "level", "conversation") not in {"conversation", "user"}:
        raise FormulaError("Only conversation-backed metric fields are enabled.")
    from tracer.services.derived_metrics.spec import require_computable

    # Native callers include old recipe DTOs as well as saved versions. Preserve
    # their historical defaults, but never reinterpret a legacy raw-user recipe
    # as a v2 conversation field. Admission precedes even source-plan compilation.
    require_computable(
        SimpleNamespace(
            selection_unit=getattr(definition, "selection_unit", "session"),
            level=getattr(definition, "level", "conversation"),
            kind=getattr(definition, "kind", "custom"),
            user_rule=getattr(definition, "user_rule", ""),
        )
    )
    if type(settle_minutes) is not int or not 0 <= settle_minutes <= 10080:
        raise ValueError("Invalid settle interval.")
    ids = _ids(unit_ids)
    recipe = field_recipe(definition)
    ctes, predicate, source_params = _source_plan(
        scope, ids, manifest, source_identities
    )
    ctes = (*ctes, *_authority_ctes(predicate))
    source, params = snapshot_select(
        recipe.source,
        scope,
        start,
        end,
        as_of,
        # Saved filters are immutable membership, never an optional override.
        # Explicit filters can only narrow it (as in preview). Avoid duplicating
        # saved predicates when a native writer passes the complete saved set.
        list(getattr(definition, "filters", ()))
        + [f for f in (filters or ()) if f not in getattr(definition, "filters", ())],
        source_run_id,
        unit_predicate=predicate,
        source_ctes=ctes,
        source_parameters=source_params,
        include_evidence=True,
    )
    # Only canonical requested units survive the immutable-identity collapse:
    # old memberships may nominate keys but must not resurrect moved spans.
    source = f"SELECT * FROM ({source}) WHERE unit_id IN (SELECT unit_id FROM requested_units)"
    driving = "SELECT unit_id FROM requested_units"
    previous = ""
    old_start = "toDateTime64(0, 6, 'UTC')"
    if manifest is not None:
        metric_id = getattr(definition, "metric_id", None) or definition.metric.id
        committed, committed_params = _committed_rows_sql(
            scope,
            metric_id,
            definition.version,
            manifest,
            "(unit_id IN (SELECT unit_id FROM requested_units) "
            "OR unit_id IN (SELECT raw_id FROM unit_read_requests))",
        )
        params.update(committed_params)
        driving += (
            # A distinct alias: a SELECT alias named unit_id would shadow the
            # canonical column inside this WHERE (ClickHouse alias scoping).
            " UNION ALL SELECT DISTINCT raw_id AS retired_id FROM unit_read_requests"
            " WHERE raw_id != unit_id AND raw_id IN"
            f" (SELECT unit_id FROM ({committed}) WHERE is_deleted = 0)"
        )
        previous = f"LEFT JOIN ({committed}) old ON r.unit_id = old.unit_id"
        old_start = "ifNull(old.unit_start, toDateTime64(0, 6, 'UTC'))"
    params["unit_settle_minutes"] = settle_minutes
    previous += " LEFT JOIN conversation_authority a ON r.unit_id = a.authority_id"
    return _field_projection(
        recipe,
        source,
        params,
        ctes,
        driving,
        previous,
        old_start,
        anchor_expression=f"if(ifNull(a.live_members, 0) > 0, a.anchor, {old_start})",
        arrival_expression="ifNull(a.arrival, 0)",
        user_expression="ifNull(a.canonical_user, '')",
    )


def span_values_select(definition, scope, *, start, end, as_of):
    """Transient deduped-span fields for a WHOLE dirty bucket; never persisted.

    Mutable start/filter predicates run after immutable source collapse. This
    shares the exact typed values, filters, sketches and sampling compiler with
    preview and conversation refresh. A span settles only after it has ended.
    """
    from tracer.services.derived_metrics.spec import require_computable

    require_computable(definition)
    if definition.level != "span":
        raise ValueError("span totals require a span definition")
    recipe = field_recipe(definition)
    # A raw timestamp may nominate an identity, never its winning revision.
    # Select the current in-window identities from their complete history before
    # invoking the shared compiler. unit_predicate deliberately disables that
    # compiler's member window, so this relation MUST apply it after collapse.
    # This also keeps same-span-id sampling ambiguity restricted to the window.
    window_keys = """span_window_keys AS (
        SELECT trace_id, id FROM (
            SELECT trace_id, id, start_time FROM spans
            PREWHERE project_id = %(project_id)s AND (trace_id, id) IN (
                SELECT trace_id, id FROM spans
                PREWHERE project_id = %(project_id)s
                WHERE (org_id = %(org_id)s OR isNull(org_id))
                    AND start_time >= %(start)s AND start_time < %(end)s
            )
            WHERE (org_id = %(org_id)s OR isNull(org_id))
                AND created_at <= %(as_of)s AND updated_at <= %(as_of)s
            ORDER BY _version DESC LIMIT 1 BY project_id, trace_id, id
        ) WHERE start_time >= %(start)s AND start_time < %(end)s
    )"""
    # The shared compiler also derives evaluation candidates from span_versions
    # BEFORE collapsing evaluation IDs. Evaluation targets are mutable, so a
    # window-limited span universe can hide a newer retargeted revision (including
    # a tombstone) and resurrect its old score. Preserve the legacy universe for
    # eval-backed recipes until that compiler nominates immutable evaluation IDs
    # and loads their complete histories independently of span membership.
    has_evaluations = any(item["source"] == "eval" for item in recipe.source.inputs)
    source, params = snapshot_select(
        recipe.source,
        scope,
        start,
        end,
        as_of,
        list(getattr(definition, "filters", ())),
        "",
        unit_predicate=None
        if has_evaluations
        else "(trace_id, id) IN (SELECT trace_id, id FROM span_window_keys)",
        source_ctes=() if has_evaluations else (window_keys,),
        include_evidence=True,
    )
    params["unit_settle_minutes"] = 0
    return _field_projection(
        recipe,
        source,
        params,
        (),
        f"SELECT unit_id FROM ({source})",
        "",
        "toDateTime64(0, 6, 'UTC')",
        level="span",
    )


def _field_projection(
    recipe,
    source,
    params,
    ctes,
    driving,
    previous,
    old_start,
    *,
    level="conversation",
    anchor_expression=None,
    arrival_expression="ifNull(s.last_arrival_ns, 0)",
    user_expression="ifNull(s.user_id, '')",
):
    formula, value_type, alias = recipe.formula, recipe.value_type, recipe.value_alias
    with_clause = f"WITH {', '.join(ctes)}" if ctes else ""
    requested_id = "s.unit_id" if level == "span" else "r.unit_id"
    source_join = (
        f"({source}) s"
        if level == "span"
        else f"({driving}) r LEFT JOIN ({source}) s ON r.unit_id = s.unit_id {previous}"
    )
    due = (
        "if(ifNull(s.span_ended, 0), fromUnixTimestamp64Nano(toInt64(last_arrival_ns), 'UTC'), "
        "toDateTime64(%(as_of)s, 6, 'UTC') + toIntervalSecond(1))"
        if level == "span"
        else "fromUnixTimestamp64Nano(toInt64(last_arrival_ns), 'UTC') + toIntervalMinute(%(unit_settle_minutes)s)"
    )
    evidence = {"number": "inputs", "boolean": "inputs_bool", "text": "inputs_text"}

    def input_value(item: dict) -> str:
        # Legacy descriptors (no value_type) reduce to numbers, as before.
        return f"{evidence[reduced_type(item)]}['{item['alias']}']"

    if formula:
        params.update(formula.parameters)
        unpack = ", ".join(
            f"s.input_values['{name}'] AS v_{name}" for name in formula.required
        )
        n, d = formula.numerator, formula.denominator
        complete = f"({formula.complete_sql}) AND isNotNull(part_n) AND isFinite(part_n) AND isNotNull(part_d) AND isFinite(part_d)"
        missing = " OR ".join(f"isNull(v_{name})" for name in formula.required)
        ratio = formula.kind in {"mean", "ratio"}
        raw_value = "part_n / nullIf(part_d, 0)" if ratio else "part_n"
        if value_type == "boolean":
            raw_value = "toUInt8(part_n != 0)"
        zero = "part_d = 0" if ratio else "0"
    else:
        unpack, n, d, zero = (
            "1 AS unused",
            "CAST(NULL, 'Nullable(Float64)')",
            "CAST(NULL, 'Nullable(Float64)')",
            "0",
        )
        raw_value = f"{evidence[value_type]}['{alias}']"
        complete = f"isNotNull({raw_value})"
        missing = f"isNull({raw_value})"
    # The missing reason names the input that is actually required: an absent
    # span attribute is attribute_absent, an unsampled/absent eval not_evaluated.
    missing_evals = [
        f"isNull({input_value(item)})"
        for item in recipe.required_inputs
        if item["source"] == "eval"
    ]
    missing_reason_sql = (
        f"if({' OR '.join(missing_evals)}, 'not_evaluated', 'attribute_absent')"
        if missing_evals
        else "'attribute_absent'"
    )
    columns = []
    for candidate in ("boolean", "number", "text"):
        cast = {"boolean": "UInt8", "number": "Float64", "text": "String"}[candidate]
        expression = (
            "if(status = 'ok', computed_value, NULL)"
            if candidate == value_type
            else "NULL"
        )
        columns.append(
            f"CAST({expression}, 'Nullable({cast})') AS {value_column(candidate)}"
        )
    finite = "isFinite(computed_value)" if value_type == "number" else "1"
    anchor_expression = (
        anchor_expression
        or f"if(ifNull(s.source_rows, 0) = 0, {old_start}, s.unit_start)"
    )
    query = f"""{with_clause}
        SELECT unit_id, session_id, user_id, '{level}' AS level, unit_start,
            toStartOfFifteenMinutes(unit_start) AS bucket_start, last_arrival_ns,
            if(status = 'open' OR is_deleted, NULL, due_at) AS settled_at,
            status, no_value_reason, {", ".join(columns)},
            if(is_deleted, NULL, part_n) AS part_n, if(is_deleted, NULL, part_d) AS part_d,
            inputs, inputs_bool, inputs_text, sample_probability, sampling_scope,
            sampling_stratum, sampling_design, population_size, invalid_probability,
            cohort AS group_key, is_deleted, source_rows
        FROM (
            SELECT *, {raw_value} AS computed_value,
                multiIf(is_deleted, 'no_value', due_at > toDateTime64(%(as_of)s, 6, 'UTC'), 'open',
                        invalid_probability != 0 OR NOT ({complete}) OR ({zero}) OR NOT ({finite}), 'no_value', 'ok') AS status,
                multiIf(is_deleted OR status = 'open', '', invalid_probability != 0, 'invalid_probability',
                        {missing}, {missing_reason_sql}, {zero}, 'zero_denominator',
                        NOT ({complete}) OR NOT ({finite}), 'non_finite', '') AS no_value_reason
            FROM (
                SELECT {requested_id} AS unit_id, if(notEmpty(ifNull(s.session_id, '')), s.session_id, {requested_id}) AS session_id,
                    {user_expression} AS user_id, {anchor_expression} AS unit_start,
                    {arrival_expression} AS last_arrival_ns,
                    {due} AS due_at,
                    toUInt8(ifNull(s.source_rows, 0) = 0) AS is_deleted, ifNull(s.source_rows, 0) AS source_rows,
                    s.input_values AS inputs, s.inputs_bool AS inputs_bool, s.inputs_text AS inputs_text,
                    s.probability AS sample_probability, ifNull(s.sampling_scope, '') AS sampling_scope,
                    ifNull(s.sampling_stratum, '') AS sampling_stratum, ifNull(s.sampling_design, '') AS sampling_design,
                    s.population_size AS population_size, ifNull(s.invalid_probability, 0) AS invalid_probability,
                    ifNull(s.cohort, '') AS cohort, {n} AS part_n, {d} AS part_d,
                    {unpack}
                FROM {source_join}
            ) s
        )
    """
    return query, params
