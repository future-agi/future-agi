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
from tracer.services.clickhouse.v2.query_builders.filters import ClickHouseFilterBuilderV2


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

    def build_cohort_metrics(self, *, skill_id, version_id, digest, agent=None):
        """Exact core metrics for ONE verified version, optionally one agent.

        Loads select the cohort in the half-open load window; all live spans of
        those traces contribute cost, including roots started before the load.
        Do not wire Observe links until ST6's root-window landing discrepancy
        is resolved. Eval aggregation intentionally remains a separate adapter
        pending V8's any-span join and per-trace reduction contract.
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
