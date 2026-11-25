"""Trace-scoped evidence and exact whole-request cohort metrics."""

import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.filters import (
    SKILL_VERSION_IDS_SQL,
    SkillEvidenceWindow,
    is_skill_filter,
    validate_skill_filter_groups,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.runtime_cohorts import resolve_cohort
from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    SkillRuntimeQueryBuilder,
)

# Leave room for the v2 wrapper's final SETTINGS under CH's default 256 KiB.
_SUMMARY_SQL_BYTES = 248 * 1024
_BIND = re.compile(r"%\(([a-zA-Z_][a-zA-Z_0-9]*)\)s")
_ARRAY_TYPES = {
    "summary_verified_identities": "Array(Tuple(String, String, String))",
    "summary_trace_ids": "Array(String)",
}


class ServerBoundCohort(dict):
    """Native protocol parameters (requires the driver's server_side_params flag)."""


class CohortStatementTooLarge(ValueError):
    """An exact statement cannot fit on this read lane; never truncate it."""


def _bounded_statement(sql, params):
    # The default native binder expands even a Python list directly into SQL.
    # Measure only referenced binds, not unused datetime instrumentation fields.
    used = {name: params[name] for name in _BIND.findall(sql)}
    context = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))
    escaped = escape_params(used, context)
    sizes = {name: len(str(value).encode()) for name, value in escaped.items()}
    size = len(sql.encode()) + sum(sizes[m[1]] - len(m[0]) for m in _BIND.finditer(sql))
    if size <= _SUMMARY_SQL_BYTES:
        return sql, params

    # Native binding mode is statement-wide. Preserve the compiler's ordinary
    # tuple/list literal shapes, replacing scalar leaves with bind markers, not
    # values. The two large relations each travel as ONE protocol parameter.
    binds = ServerBoundCohort()

    def marker(name, value):
        if name in _ARRAY_TYPES:
            # Send the entire array in one protocol field. Decode a typed data
            # relation on the server instead of relying on nested tuple/string
            # literal quoting (which differs between native driver versions).
            binds[name] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            return "JSONExtract({" + name + ":String}, '" + _ARRAY_TYPES[name] + "')"
        if isinstance(value, (list, tuple)):
            left, right = ("[", "]") if isinstance(value, list) else ("(", ")")
            return (
                left
                + ", ".join(marker(f"{name}_{i}", v) for i, v in enumerate(value))
                + right
            )
        elif isinstance(value, datetime):
            kind = "DateTime64(6, 'UTC')"
            value = (value.astimezone(UTC) if value.tzinfo else value).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            )
        elif isinstance(value, date):
            kind, value = "Date", value.isoformat()
        elif isinstance(value, UUID):
            kind, value = "UUID", str(value)
        elif type(value) is int:
            kind = "UInt64" if value > 2**63 - 1 else "Int64"
        else:
            kind = {str: "String", float: "Float64", bool: "Bool"}.get(type(value))
            if kind is None:
                if value is None:
                    return "NULL"
                raise CohortStatementTooLarge(
                    "Cannot bind this oversized cohort statement"
                )
        binds[name] = value
        return "{" + name + ":" + kind + "}"

    # The template is compiler-owned. User values are sent separately by the
    # driver; never insert the byte measurement's escaped literals in the query.
    refs = {
        name: marker(name if name in _ARRAY_TYPES else f"bind_{i}", value)
        for i, (name, value) in enumerate(used.items())
    }
    typed_sql = sql % refs
    if len(typed_sql.encode()) > _SUMMARY_SQL_BYTES:
        raise CohortStatementTooLarge("Cohort query exceeds the statement limit")
    return typed_sql, binds


_TRACE_SELECTORS = {"skill.trace_cohort", "skill.root_status"}
_IDENTITY_FIELDS = {"skill.id": 0, "skill.version_id": 1, "skill.digest": 2}


def _selects_failed_load(group):
    return any(
        item["column_id"] == "skill.load_result"
        and item["filter_config"]["filter_value"] == "failed"
        for item in group
    )


def _catalog_for_groups(identities, groups):
    """Keep the union of possible successful-load witnesses, not their intersection."""
    alternatives = []
    for group in groups.values():
        loads = [item for item in group if item["column_id"] not in _TRACE_SELECTORS]
        if not loads or _selects_failed_load(group):
            continue  # Failed rows have no verified identity.
        constraints = []
        for item in loads:
            field = _IDENTITY_FIELDS.get(item["column_id"])
            config = item["filter_config"]
            if field is not None and config["filter_op"] in {"equals", "in"}:
                value = config["filter_value"]
                constraints.append(
                    (field, set(value if isinstance(value, list) else [value]))
                )
        if not constraints:
            return list(identities)  # Includes unrestricted anti-membership witnesses.
        alternatives.append(constraints)
    if not alternatives:
        return []
    return [
        identity
        for identity in identities
        if any(all(identity[i] in values for i, values in arm) for arm in alternatives)
    ]


class TraceSkillsQueryBuilder(SkillRuntimeQueryBuilder):
    def build_spans(self, *, trace_ids):
        return (
            f"""
            SELECT id, state.1 AS start_time, state.2 AS name,
                   state.3 AS status, state.4 AS attrs, state.5 AS version_ids,
                   state.6 AS attributes
            FROM (
                SELECT id, argMax(tuple(start_time, name, status, attrs_string,
                    {SKILL_VERSION_IDS_SQL}, attributes_extra, is_deleted), _version) AS state
                FROM spans WHERE project_id IN %(project_ids)s AND trace_id IN %(trace_ids)s
                GROUP BY id
            ) WHERE state.7 = 0 ORDER BY start_time, id LIMIT 10001
        """,
            {**self.params, "trace_ids": tuple(trace_ids)},
        )

    def build_summary(self, *, filters, verified_identities):
        """Select by load window, replay entire traces, then apply every filter.

        A failed-load-only cohort is still a trace population. It must not be
        restricted to verified successful versions. Date filters define the
        load window; roots and costs are never clipped to that window.
        """
        project_id = self.project_ids[0]
        try:
            groups = validate_skill_filter_groups(filters)
            stored_ids = None
            for group in groups.values():
                for item in group:
                    if item["column_id"] == "skill.trace_cohort":
                        _, ids = resolve_cohort(
                            item["filter_config"]["filter_value"], [project_id]
                        )
                        stored_ids = (
                            set(ids)
                            if stored_ids is None
                            else stored_ids.intersection(ids)
                        )
        except ValueError as exc:
            raise UnsupportedFilterShapeError(str(exc)) from exc

        scope = SkillEvidenceWindow.from_bounds(self.start, self.end, filters)
        in_window = scope.condition
        ctes = []
        params = {**self.params, "project_id": project_id, **scope.params}
        if stored_ids is not None:
            # Resolve once against the same authorized project as the span read.
            params["summary_trace_ids"] = sorted(stored_ids)
            ctes.append(
                "stored_members AS (SELECT toUUID(%(project_id)s) AS project_id, "
                "arrayJoin(CAST(%(summary_trace_ids)s AS Array(String))) AS trace_id)"
            )
            scope = replace(
                scope,
                bounds=scope.bounds
                + " AND trace_id IN (SELECT trace_id FROM stored_members)",
            )

        verified = _catalog_for_groups(verified_identities, groups)
        if verified:
            # Keep the catalogue in one relation, even when several groups use it.
            params["summary_verified_identities"] = verified
            ctes.append(
                "verified_catalog AS (SELECT identity.1, identity.2, identity.3 FROM "
                "(SELECT arrayJoin(%(summary_verified_identities)s) AS identity))"
            )
        # Count only keys that could supply a requested witness. A historical
        # match nominates its key; replay still reads *every* revision of that
        # key and rechecks the winner, including corrections and tombstones.
        candidate_outcomes = []
        if verified:
            candidate_outcomes.append(
                "(attrs_string['fi.skill.id'], attrs_string['fi.skill.version_id'], "
                "attrs_string['fi.skill.digest']) IN (SELECT * FROM verified_catalog)"
            )
        if any(_selects_failed_load(group) for group in groups.values()):
            # Failed loads have no catalogue identity. A separate failed group
            # can be witnessed by a sibling of a verified load in the same trace.
            candidate_outcomes.append(
                "(status = 'ERROR' AND attrs_string['fi.skill.version_id'] = '')"
            )
        scope = replace(
            scope,
            bounds=f"{scope.bounds} AND ({' OR '.join(candidate_outcomes) or '0 = 1'})",
            loaded_identity_condition=(
                "(skill_id, skill_version_id, skill_digest) IN (SELECT * FROM verified_catalog)"
                if verified
                else "0 = 1"
            ),
        )
        # Handle membership is the seed; the canonical root below already has
        # the status. Only actual load selectors belong in the load compiler.
        residuals = [
            item
            for item in filters
            if not (is_skill_filter(item) and item["column_id"] in _TRACE_SELECTORS)
        ]
        plan = compile_exact_graph_row_predicates(
            residuals,
            project_id=project_id,
            observe_type="trace",
            skill_window=scope,
        )
        params.update(plan.params)
        # Non-skill eval/score witnesses can also read the enclosing dates.
        # Preserve their intersection with Observe's filters, not just the
        # outer picker range, and avoid native datetime rounding there too.
        row_predicates = (
            predicate.replace(
                "%(start_date)s", "fromUnixTimestamp64Micro(%(skill_window_start_us)s)"
            ).replace(
                "%(end_date)s", "fromUnixTimestamp64Micro(%(skill_window_end_us)s)"
            )
            for predicate in plan.predicates
        )
        predicates = [
            f"({predicate}) AND ({in_window})" if window_only else predicate
            for predicate, window_only in zip(
                row_predicates, plan.output_window_only, strict=True
            )
        ]
        values = [
            "parent_span_id",
            "status",
            "latency_ms",
            "cost",
            "start_time",
            "is_deleted",
        ]
        values += [f"toUInt8(ifNull(({predicate}), 0))" for predicate in predicates]
        # At most one load group is needed to seed the population. The shared
        # compiler still enforces ALL groups (including absence) against winners.
        witness_group = next(
            (
                group
                for group in groups.values()
                if any(
                    item["column_id"] not in _TRACE_SELECTORS
                    and item["filter_config"]["filter_op"]
                    not in {"not_equals", "not_in"}
                    for item in group
                )
            ),
            None,
        )
        if stored_ids is not None:
            members = "SELECT project_id, trace_id FROM stored_members"
        elif witness_group:
            source = self._live_source(
                failed=_selects_failed_load(witness_group), skill_window=scope
            )
            members = f"SELECT DISTINCT project_id, trace_id FROM ({source})"
        else:
            # Negative-only cohorts can include traces with no skill.load at all.
            members = f"SELECT DISTINCT project_id, trace_id FROM spans WHERE project_id = toUUID(%(project_id)s) AND {in_window}"
        root_statuses = {
            item["filter_config"]["filter_value"]
            for group in groups.values()
            for item in group
            if item["column_id"] == "skill.root_status"
        }
        match_groups = plan.match_condition_groups or tuple(
            ((i, match),) for i, match in enumerate(plan.required_matches)
        )
        having = (
            " AND ".join(
                "("
                + " OR ".join(
                    f"max(state.{i + 7}) = {int(match)}" for i, match in group
                )
                + ")"
                for group in match_groups
            )
            or "1 = 1"
        )
        if root_statuses:
            if len(root_statuses) > 1:
                having += " AND 0 = 1"
            else:
                operator = "=" if "error" in root_statuses else "!="
                having += f" AND roots > 0 AND root.1 {operator} 'ERROR'"
        if witness_group is None:
            values.append(f"toUInt8({in_window})")
            having += f" AND max(state.{len(values)}) = 1"
        ctes.append(f"members AS ({members})")
        return _bounded_statement(
            f"""
            WITH {", ".join(ctes)}, winners AS (
                SELECT project_id, trace_id, id, argMax(tuple({", ".join(values)}), _version) AS state
                FROM spans WHERE project_id IN %(project_ids)s
                  AND (project_id, trace_id) IN (SELECT project_id, trace_id FROM members)
                GROUP BY project_id, trace_id, id
            ), per_trace AS (
                SELECT project_id, trace_id, countIf(state.1 = '') AS roots,
                    argMinIf(tuple(state.2, state.3), tuple(state.5, id), state.1 = '') AS root,
                    sum(state.4) AS trace_cost
                FROM winners WHERE state.6 = 0 GROUP BY project_id, trace_id HAVING {having}
            ) SELECT count() AS traces, countIf(roots = 0) AS incomplete,
                countIf(roots > 0 AND root.1 = 'ERROR') AS failed_count,
                failed_count / nullIf(countIf(roots > 0), 0) AS failed_rate,
                if(countIf(roots > 0) = 0, NULL, quantileExactIf(0.95)(root.2, roots > 0)) AS latency_p95,
                sum(trace_cost) / nullIf(count(), 0) AS cost_mean
            FROM per_trace
        """,
            params,
        )
