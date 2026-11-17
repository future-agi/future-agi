"""Bounded runtime read building blocks for the shared usage/load API.

Uses ST6's authoritative live-row fence, including attribute-less tombstones.
The dependency must be integrated before queries are enabled. No ORM lookups or
user-authored SQL fragments enter this builder; project permissions and version
identities must be resolved by the calling service first.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from tracer.services.clickhouse.read_budget import ReadDeadline, WallCappedAnalytics
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)


class RuntimeDependencyUnavailable(RuntimeError):
    """The shared filter compiler has not acquired its runtime source yet."""


class SkillRuntimeQueryBuilder(V2RewriteMixin):
    def __init__(self, *, project_ids, start: datetime, end: datetime):
        projects = tuple(sorted({str(UUID(str(value))) for value in project_ids}))
        if not projects or len(projects) > 50:
            raise ValueError("Runtime reads require one to fifty authorized projects")
        if start.utcoffset() is None or end.utcoffset() is None or start >= end:
            raise ValueError("Runtime reads require a nonempty timezone-aware window")
        self.project_ids = projects
        self.params = {"project_ids": projects, "start_date": start, "end_date": end}
        self.compiler = ClickHouseFilterBuilderV2(project_ids=list(projects))

    def _live_source(self, *, failed=False):
        method = getattr(self.compiler, "skill_live_rows_sql", None)
        if method is None:
            raise RuntimeDependencyUnavailable("Runtime reads require the shared SKILL filter compiler")
        return method(failed=failed)

    def build_filter_values(self, *, field, verified_identities, context_ids=(),
                            failed=False, skill_name=None, search="", after=None, limit=100):
        """Keyset-paged picker values from live evidence, never raw name identity.

        The caller resolves all catalogue IDs in Postgres before this query.
        Failed names remain unverified strings, not navigable skill identities.
        """
        columns = {
            "skill.name": "skill_name" if failed else "toString(skill_id)",
            "skill.version_id": "toString(skill_version_id)",
            "skill.agent": "agent_name",
            "skill.service": "service_name",
            "skill.requested_label": "requested_label",
            "skill.context_id": "toString(context_id)",
        }
        if field not in columns or (failed and field == "skill.version_id"):
            raise ValueError("Unsupported filter-value field")
        if not 1 <= limit <= 100:
            raise ValueError("Invalid filter-value page size")
        params = {
            **self.params, "verified_identities": tuple(verified_identities),
            "context_ids": tuple(context_ids), "search": search,
            "after": after, "page_limit": limit + 1, "skill_name": skill_name,
        }
        if failed:
            scope = "skill_name = %(skill_name)s" if skill_name is not None else "1 = 1"
        else:
            scope = "(skill_id, skill_version_id, skill_digest) IN %(verified_identities)s" if verified_identities else "0 = 1"
            scope += " AND status != 'ERROR'"
        if field == "skill.context_id":
            scope += " AND toString(context_id) IN %(context_ids)s" if context_ids else " AND 0 = 1"
        # Only the allowlisted column expression is interpolated. All values,
        # including blank agent names, are bound; no SQL is accepted from HTTP.
        expression = columns[field]
        cursor = " AND value > %(after)s" if after is not None else ""
        return f"""
            SELECT DISTINCT {expression} AS value
            FROM ({self._live_source(failed=failed)})
            WHERE {scope} AND positionCaseInsensitiveUTF8(value, %(search)s) > 0 {cursor}
            ORDER BY value LIMIT %(page_limit)s
        """, params

    def build_usage_loads(self, *, identities, skill_names, label=None, context_id=None, agent=None, failed=False, mismatch=False):
        """Bounded facts for exact overlapping cohorts; never return a prefix."""
        params = {**self.params, "identities": tuple(identities), "skill_names": tuple(skill_names)}
        predicate = "skill_name IN %(skill_names)s" if failed else "(skill_id, skill_version_id, skill_digest) IN %(identities)s AND status != 'ERROR'"
        if mismatch:
            params["skill_ids"] = tuple(sorted({identity[0] for identity in identities}))
            predicate = "skill_id IN %(skill_ids)s AND (skill_id, skill_version_id, skill_digest) NOT IN %(identities)s AND status != 'ERROR'"
        if not (skill_names if failed else identities):
            predicate = "0 = 1"
        if label is not None:
            params["label"] = label
            predicate += " AND requested_label = %(label)s"
        if context_id is not None:
            params["context_id"] = str(context_id)
            predicate += " AND context_id = %(context_id)s"
        if agent is not None:
            params.update(agent_project=str(agent[0]), service=agent[1], agent=agent[2])
            predicate += " AND toString(project_id) = %(agent_project)s AND service_name = %(service)s AND agent_name = %(agent)s"
        return f"SELECT * FROM ({self._live_source(failed=failed)}) WHERE {predicate} LIMIT 20001", params

    def build_context_members(self, *, resource_id, identities):
        """Context cohorts include stamped spans even without a leaf skill load.

        The value-index candidate is replayed before testing the final identity,
        so an attribute removal or attribute-less tombstone cannot resurrect it.
        """
        params = {**self.params, "resource_id": str(resource_id), "context_identities": tuple(identities)}
        return """
            SELECT project_id, trace_id, id AS span_id, state.2 AS start_time,
                   state.5 AS service_name,
                   if(state.4['gen_ai.agent.name'] != '', state.4['gen_ai.agent.name'], state.4['fi.context.name']) AS agent_name,
                   state.4['fi.context.id'] AS context_id,
                   state.4['fi.context.version_id'] AS context_version_id,
                   context_version_id AS skill_version_id,
                   state.4['fi.context.requested_label'] AS requested_label,
                   0 AS requested_version, '' AS via_context_version_id,
                   NULL AS cached, state.1 = 'context.load' AS is_load
            FROM (
                SELECT project_id, trace_id, id,
                       argMax(tuple(name, start_time, status, attrs_string, service_name, is_deleted), _version) AS state
                FROM spans
                WHERE project_id IN %(project_ids)s AND (project_id, trace_id, id) IN (
                    SELECT project_id, trace_id, id FROM spans
                    WHERE project_id IN %(project_ids)s
                      AND start_time >= %(start_date)s AND start_time < %(end_date)s
                      AND attrs_string['fi.context.id'] = %(resource_id)s
                )
                GROUP BY project_id, trace_id, id
            )
            WHERE state.6 = 0 AND (state.1 != 'context.load' OR state.3 != 'ERROR')
              AND state.4['fi.context.id'] = %(resource_id)s
              AND (state.4['fi.context.id'], state.4['fi.context.version_id'], state.4['fi.context.digest']) IN %(context_identities)s
              AND state.2 >= %(start_date)s AND state.2 < %(end_date)s
            LIMIT 20001
        """, params

    def build_usage_spans(self, *, members):
        """Replay complete traces, not just spans started in the load window."""
        params = {**self.params, "members": tuple(members)}
        return """
            SELECT project_id, trace_id, id, state.1 AS parent_span_id,
                   state.2 AS status, state.3 AS latency_ms, state.4 AS cost,
                   state.5 AS total_tokens, state.6 AS skill_version_ids,
                   state.7 AS start_time, state.8 AS context_version_id
            FROM (
                SELECT project_id, trace_id, id,
                       argMax(tuple(parent_span_id, status, latency_ms, cost,
                                    total_tokens, skill_version_ids, start_time, attrs_string['fi.context.version_id']), _version) AS state
                FROM spans
                WHERE project_id IN %(project_ids)s
                  AND (toString(project_id), trace_id) IN %(members)s
                GROUP BY project_id, trace_id, id
                HAVING argMax(is_deleted, _version) = 0
            ) LIMIT 20001
        """, params

    def build_usage_evals(self, *, config_ids, members):
        # Config IDs are PG-authorized by project. Trace IDs use the same textual
        # UUID join as EvalMetricsQueryBuilderV2; results on ANY live span count.
        params = {**self.params, "config_ids": tuple(config_ids), "trace_ids": tuple(sorted({trace for _, trace in members}))}
        return """
            SELECT toString(trace_id) AS trace_id, observation_span_id,
                   toString(custom_eval_config_id) AS config_id,
                   output_float, output_bool, output_str
            FROM tracer_eval_logger_v2 FINAL
            WHERE is_deleted = 0 AND error = 0
              AND toString(custom_eval_config_id) IN %(config_ids)s
              AND toString(trace_id) IN %(trace_ids)s
            LIMIT 20001
        """, params

    def build_library_counts(self, *, verified_identities):
        """One CH read for the page. Identities are (skill, version, digest).

        Counts are trace-grain across projects (the project is part of identity).
        The caller's window is always the last seven days for this projection.
        """
        params = {**self.params, "verified_identities": tuple(verified_identities)}
        predicate = "(skill_id, skill_version_id, skill_digest) IN %(verified_identities)s" if verified_identities else "0 = 1"
        return f"""
            SELECT skill_id, uniqExact(tuple(project_id, trace_id)) AS traces
            FROM ({self._live_source()})
            WHERE status != 'ERROR' AND {predicate}
            GROUP BY skill_id ORDER BY skill_id
        """, params

    def build_mismatch_counts(self, *, skill_id, verified_identities):
        params = {**self.params, "skill_id": str(skill_id), "verified_identities": tuple(verified_identities)}
        predicate = "(skill_id, skill_version_id, skill_digest) NOT IN %(verified_identities)s" if verified_identities else "1 = 1"
        return f"""
            SELECT count() AS loads, uniqExact(tuple(project_id, trace_id)) AS traces
            FROM ({self._live_source()})
            WHERE skill_id = %(skill_id)s AND status != 'ERROR' AND {predicate}
        """, params

    def build_trace_loads(self, *, trace_ids, failed=False):
        """Bounded page hydration. One project prevents trace-id collisions.

        The row cap is a rejection sentinel, not a partial success. The caller
        must reject 10001 rows, never publish a misleading prefix as all loads.
        """
        trace_ids = tuple(dict.fromkeys(trace_ids))
        if len(self.project_ids) != 1 or not 1 <= len(trace_ids) <= 100:
            raise ValueError("Load hydration requires one project and one to 100 traces")
        if any(not isinstance(value, str) or not value or len(value) > 256 for value in trace_ids):
            raise ValueError("Invalid trace id")
        fields = (
            "skill_name, error_type, requested_label, requested_version, context_id, context_version_id"
            if failed else
            "skill_id, skill_version_id, skill_digest, requested_label, requested_version, "
            "evidence, cached, context_id, context_version_id, via_context_version_id, binding_id"
        )
        params = {**self.params, "trace_ids": trace_ids}
        return f"""
            SELECT project_id, trace_id, span_id, start_time, service_name, agent_name, {fields}
            FROM ({self._live_source(failed=failed)})
            WHERE trace_id IN %(trace_ids)s
            ORDER BY start_time, span_id LIMIT 10001
        """, params

    def build_latest_load(self, *, resource_id, kind, version_ids):
        """Newest live load, not a fetch, a context link or a stamped child.

        Contexts use spans directly: a context with no skill loads must still
        be detectable. Replay before filtering attributes so a correction or
        attribute-less tombstone cannot resurrect an older load.
        """
        if kind not in {"skill", "agent_context"}:
            raise ValueError("Unknown runtime resource kind")
        params = {
            **self.params, "resource_id": str(UUID(str(resource_id))),
            "version_ids": tuple(str(UUID(str(value))) for value in version_ids),
        }
        if kind == "skill":
            return f"""
                SELECT project_id, skill_version_id AS version_id, start_time,
                       service_name, agent_name
                FROM ({self._live_source()})
                WHERE skill_id = %(resource_id)s AND status != 'ERROR'
                  AND skill_version_id IN %(version_ids)s
                ORDER BY start_time DESC, span_id DESC LIMIT 1
            """, params
        return """
            SELECT project_id, state.4['fi.context.version_id'] AS version_id,
                   state.2 AS start_time, state.5 AS service_name,
                   if(state.4['gen_ai.agent.name'] != '', state.4['gen_ai.agent.name'],
                      state.4['fi.context.name']) AS agent_name
            FROM (
                SELECT project_id, trace_id, id,
                       argMax(tuple(name, start_time, status, attrs_string,
                                    service_name, is_deleted), _version) AS state
                FROM spans
                WHERE project_id IN %(project_ids)s
                  AND (project_id, trace_id, id) IN (
                      SELECT project_id, trace_id, id FROM spans
                      WHERE project_id IN %(project_ids)s
                        AND start_time >= %(start_date)s AND start_time < %(end_date)s
                        AND name = 'context.load'
                        AND attrs_string['fi.context.id'] = %(resource_id)s
                  )
                GROUP BY project_id, trace_id, id
            )
            WHERE state.6 = 0 AND state.1 = 'context.load' AND state.3 != 'ERROR'
              AND state.4['fi.context.id'] = %(resource_id)s
              AND state.4['fi.context.version_id'] IN %(version_ids)s
              AND state.2 >= %(start_date)s AND state.2 < %(end_date)s
            ORDER BY start_time DESC, id DESC LIMIT 1
        """, params

    def build_cohort_metrics(self, *, skill_id, version_id, digest, agent=None):
        """Exact core metrics for ONE verified version, optionally one agent.

        Loads select the cohort in the half-open load window; all live spans of
        those traces contribute cost, including roots started before the load.
        Observe's skill.root_status uses this same canonical-root rule, without
        clipping roots to the load window. Eval aggregation remains a separate
        adapter pending V8's any-span join and per-trace reduction contract.
        """
        params = {**self.params, "skill_id": str(skill_id), "version_id": str(version_id), "digest": digest}
        agent_where = ""
        if agent is not None:
            project_id, service_name, agent_name = agent
            if str(project_id) not in self.project_ids:
                raise ValueError("Agent is outside the authorized project scope")
            params.update(agent_project=str(project_id), service=service_name, agent=agent_name)
            agent_where = " AND toString(project_id) = %(agent_project)s AND service_name = %(service)s AND agent_name = %(agent)s"
        return f"""
            WITH loads AS (
                SELECT * FROM ({self._live_source()})
                WHERE skill_id = %(skill_id)s AND skill_version_id = %(version_id)s
                  AND skill_digest = %(digest)s AND status != 'ERROR' {agent_where}
            ), members AS (
                SELECT DISTINCT project_id, trace_id FROM loads
            ), span_winners AS (
                SELECT project_id, trace_id, id,
                       argMax(tuple(parent_span_id, status, latency_ms, cost,
                                    total_tokens, skill_version_ids, start_time), _version) AS state
                FROM spans
                WHERE project_id IN %(project_ids)s
                  AND (project_id, trace_id) IN (SELECT project_id, trace_id FROM members)
                GROUP BY project_id, trace_id, id
                HAVING argMax(is_deleted, _version) = 0
            ), live_spans AS (
                SELECT project_id, trace_id, id, state.1 AS parent_span_id,
                       state.2 AS status, state.3 AS latency_ms, state.4 AS cost,
                       state.5 AS total_tokens, state.6 AS skill_version_ids,
                       state.7 AS start_time
                FROM span_winners
            ), per_trace AS (
                SELECT project_id, trace_id,
                       countIf(parent_span_id = '') AS roots,
                       argMinIf(tuple(status, latency_ms), tuple(start_time, id), parent_span_id = '') AS root,
                       countIf(status = 'ERROR') > 0 AS any_error,
                       sum(cost) AS trace_cost, sum(total_tokens) AS trace_tokens,
                       sumIf(cost, has(skill_version_ids, %(version_id)s)) AS scope_cost,
                       countIf(has(skill_version_ids, %(version_id)s)) > 0 AS stamped
                FROM live_spans GROUP BY project_id, trace_id
            ), metrics AS (
                SELECT count() AS traces,
                       countIf(roots = 0) AS incomplete,
                       countIf(roots > 0 AND root.1 = 'ERROR') AS failed_count,
                       failed_count / nullIf(countIf(roots > 0), 0) AS failed_rate,
                       countIf(any_error) AS errors_any_span,
                       if(countIf(roots > 0) = 0, NULL, quantileExactIf(0.5)(root.2, roots > 0)) AS latency_p50,
                       if(countIf(roots > 0) = 0, NULL, quantileExactIf(0.95)(root.2, roots > 0)) AS latency_p95,
                       sum(trace_cost) AS cost_total,
                       cost_total / nullIf(count(), 0) AS cost_mean,
                       if(count() = 0, NULL, quantileExact(0.5)(trace_cost)) AS cost_p50,
                       sum(trace_tokens) AS tokens_total,
                       tokens_total / nullIf(count(), 0) AS tokens_mean,
                       sum(scope_cost) AS scope_cost_total,
                       countIf(stamped) / nullIf(count(), 0) AS scope_coverage
                FROM per_trace
            ), load_metrics AS (
                SELECT count() AS loads, avg(cached) AS cached_share,
                       minOrNull(start_time) AS first_seen, maxOrNull(start_time) AS last_seen
                FROM loads
            ), overhead AS (
                SELECT if(count() = 0, NULL, quantileExact(0.5)(latency_ms)) AS load_overhead_ms_p50
                FROM live_spans
                WHERE (project_id, trace_id, id) IN (SELECT project_id, trace_id, span_id FROM loads)
            )
            SELECT * FROM metrics CROSS JOIN load_metrics CROSS JOIN overhead
        """, params


class RuntimeReadSession:
    """One request-owned wall budget for all statements, never a timer per row."""

    def __init__(self, service, *, deadline: ReadDeadline):
        self.deadline = deadline
        self.service = WallCappedAnalytics(service, deadline, floor_ms=25)

    def execute(self, statement):
        sql, params = statement
        return self.service.execute_ch_query(sql, params, timeout_ms=self.deadline.remaining_ms())
