"""Trace-scoped evidence and exact whole-request cohort metrics."""

from datetime import UTC

from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.query_builders.filters import is_skill_filter
from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    SkillRuntimeQueryBuilder,
)


class TraceSkillsQueryBuilder(SkillRuntimeQueryBuilder):
    def _live_source(self, *, failed=False):
        # Identity reads may span all retained history, but never scan the whole
        # project's history. Push the requested trace ids into both the narrow
        # projection and the authoritative winner fence (including tombstones).
        source = super()._live_source(failed=failed)
        if "trace_ids" in self.params:
            source = source.replace("WHERE ", "WHERE trace_id IN %(trace_ids)s AND ")
        return source

    def build_spans(self):
        return (
            """
            SELECT id, state.1 AS start_time, state.2 AS name,
                   state.3 AS status, state.4 AS attrs, state.5 AS version_ids,
                   state.6 AS attributes
            FROM (
                SELECT id, argMax(tuple(start_time, name, status, attrs_string,
                    skill_version_ids, attributes_extra, is_deleted), _version) AS state
                FROM spans WHERE project_id IN %(project_ids)s AND trace_id IN %(trace_ids)s
                GROUP BY id
            ) WHERE state.7 = 0 ORDER BY start_time, id LIMIT 10001
        """,
            self.params,
        )

    def build_summary(self, *, filters, verified_identities):
        """Select by load window, replay entire traces, then apply every filter.

        A failed-load-only cohort is still a trace population. It must not be
        restricted to verified successful versions. Date filters define the
        load window; roots and costs are never clipped to that window.
        """
        project_id = self.project_ids[0]
        window_filter = {
            "column_id": "start_time",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    self.params["start_date"].isoformat(),
                    self.params["end_date"].isoformat(),
                ],
            },
        }
        window = BaseQueryBuilder.analyze_bounded_datetime_filters(
            [*filters, window_filter]
        )
        # The shared Observe parser normalizes bounds to naive UTC; runtime
        # serializers supply aware dates. Compare in UTC without losing offsets.
        self.params.update(
            start_date=max(self.params["start_date"], window.start.replace(tzinfo=UTC)),
            end_date=min(self.params["end_date"], window.end.replace(tzinfo=UTC)),
        )
        plan = compile_exact_graph_row_predicates(
            filters, project_id=project_id, observe_type="trace"
        )
        params = {
            **self.params,
            "project_id": project_id,
            **plan.params,
            "summary_verified_identities": tuple(verified_identities),
        }
        verified = (
            "(skill_id, skill_version_id, skill_digest) IN %(summary_verified_identities)s"
            if verified_identities
            else "0 = 1"
        )
        exclusion, exclusion_params = BaseQueryBuilder.bounded_datetime_exclusion_sql(
            filters,
            column="start_time",
            param_prefix="cohort_exclusion",
        )
        params.update(exclusion_params)
        in_window = (
            "0 = 1"
            if window.empty
            else "start_time >= %(start_date)s AND start_time < %(end_date)s"
        )
        if exclusion:
            in_window += f" AND ({exclusion})"
        predicates = []
        for predicate, window_only in zip(
            plan.predicates, plan.output_window_only, strict=True
        ):
            predicate = predicate.replace(
                "FROM skill_trace_links FINAL WHERE ",
                f"FROM skill_trace_links FINAL WHERE ({verified}) AND ",
            )
            if exclusion:
                # Keep the shared SKILL subqueries on the same excluded load
                # windows as candidate membership, without clipping metrics.
                predicate = predicate.replace(
                    "start_time < %(end_date)s",
                    f"start_time < %(end_date)s AND ({exclusion})",
                )
            predicates.append(
                f"({predicate}) AND ({in_window})" if window_only else predicate
            )
        values = [
            "parent_span_id",
            "status",
            "latency_ms",
            "cost",
            "start_time",
            "is_deleted",
        ]
        values += [f"toUInt8(ifNull(({predicate}), 0))" for predicate in predicates]
        positive_skill = any(
            is_skill_filter(item)
            and item["column_id"] != "skill.root_status"
            and item["filter_config"]["filter_op"] not in {"not_equals", "not_in"}
            for item in filters
        )
        if positive_skill:
            members = f"SELECT DISTINCT project_id, trace_id FROM ({self._live_source()}) WHERE ({verified}) AND {in_window} UNION DISTINCT SELECT DISTINCT project_id, trace_id FROM ({self._live_source(failed=True)}) WHERE {in_window}"
        else:
            # Negative skill filters include traces with no load evidence.
            members = f"SELECT DISTINCT project_id, trace_id FROM spans WHERE project_id IN %(project_ids)s AND {in_window}"
        groups = plan.match_condition_groups or tuple(
            ((i, match),) for i, match in enumerate(plan.required_matches)
        )
        having = (
            " AND ".join(
                "("
                + " OR ".join(
                    f"max(state.{i + 7}) = {int(match)}" for i, match in group
                )
                + ")"
                for group in groups
            )
            or "1 = 1"
        )
        if not positive_skill:
            values.append(f"toUInt8({in_window})")
            having += f" AND max(state.{len(values)}) = 1"
        return (
            f"""
            WITH members AS (
                {members}
            ), winners AS (
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
