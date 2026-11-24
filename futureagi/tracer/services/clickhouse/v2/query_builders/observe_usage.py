"""Observe-only usage predicates over the same totals as page hydration.

Acquisition still walks canonical roots in its bounded order. Usage is a
residual predicate, evaluated only for that finite candidate batch, so a zero
root cannot exclude a trace whose generation carries the matching usage.
Voice, prototype, tasks and raw SPAN_ATTRIBUTE filters retain their contracts.
"""

from tracer.services.clickhouse.query_builders.base import _unix_microseconds
from tracer.services.clickhouse.query_builders.trace_usage import (
    USAGE_COLUMNS,
    usage_aggregate_sql,
    usage_coverage_sql,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)


def usage_column(item):
    config = item.get("filter_config") or item.get("filterConfig") or {}
    if (config.get("col_type") or config.get("colType") or "NORMAL") not in (
        "NORMAL",
        "SYSTEM_METRIC",
    ):
        return None
    key = item.get("column_id") or item.get("columnId")
    column = ClickHouseFilterBuilderV2.SYSTEM_METRIC_MAP.get(key, key)
    return column if column in USAGE_COLUMNS else None


class ObserveUsageFilterBuilderV2(ClickHouseFilterBuilderV2):
    def _build_system_metric_condition(
        self, col_id, filter_type, filter_op, filter_value
    ):
        column = self.SYSTEM_METRIC_MAP.get(col_id, col_id)
        if column not in USAGE_COLUMNS or self.query_mode != self.QUERY_MODE_TRACE:
            return super()._build_system_metric_condition(
                col_id, filter_type, filter_op, filter_value
            )
        # This compiler is private to the bounded Observe classifier. Refuse a
        # tenant-wide usage aggregate if a future caller forgets the batch.
        if self.candidate_ids_param is None or not self.project_ids:
            raise ValueError("Observe usage filtering requires scoped candidate traces")
        predicate = super()._build_column_condition(
            f"usage_{column}", filter_type, filter_op, filter_value
        )
        # The single-project identity-only classifier deliberately omits the
        # constant project column. Org-scoped classifiers carry it and require
        # tuple membership so identical trace IDs cannot pool or cross-match.
        identity = "project_id, trace_id" if self._org_scoped else "trace_id"
        lhs = f"({identity})" if self._org_scoped else identity
        span_key = (
            "project_id, observation_type, service_name, "
            "toStartOfHour(start_time), trace_id, id"
        )
        return f"""{lhs} IN (
            SELECT {identity} FROM (
                SELECT
                    project_id,
                    trace_id,
                    usage_metrics.2 AS usage_cost,
                    usage_metrics.3 AS usage_total_tokens,
                    usage_metrics.4 AS usage_prompt_tokens,
                    usage_metrics.5 AS usage_completion_tokens
                FROM (
                    SELECT project_id, trace_id, {usage_aggregate_sql(coverage="usage_coverage", materialized=True)}
                    FROM (
                        SELECT
                            project_id,
                            trace_id,
                            id,
                            observation_type,
                            latest_span.1 AS parent_span_id,
                            latest_span.2 AS name,
                            latest_span.3 AS cost,
                            latest_span.4 AS total_tokens,
                            latest_span.5 AS prompt_tokens,
                            latest_span.6 AS completion_tokens,
                            latest_span.7 AS usage_coverage,
                            latest_span.8 AS latest_is_deleted
                        FROM (
                            SELECT
                                project_id,
                                trace_id,
                                id,
                                observation_type,
                                service_name,
                                toStartOfHour(start_time) AS start_hour,
                                argMax(
                                    tuple(
                                        parent_span_id, name, cost, total_tokens,
                                        prompt_tokens, completion_tokens,
                                        {usage_coverage_sql(attrs="attrs_string", include_summary=True)},
                                        is_deleted
                                    ),
                                    _version
                                ) AS latest_span
                            FROM spans
                            PREWHERE {self._project_scope_predicate()}
                                {self._candidate_filter("trace_id")}
                            GROUP BY {span_key}
                        )
                        WHERE latest_is_deleted = 0
                    ) AS live_usage_spans
                    GROUP BY project_id, trace_id
                ) AS trace_usage_raw
                ARRAY JOIN trace_usage_metrics AS usage_metrics
            ) AS trace_usage
            WHERE {predicate}
        )"""


class ObserveTraceListQueryBuilderV2(TraceListQueryBuilderV2):
    _FILTER_BUILDER_CLS = ObserveUsageFilterBuilderV2

    def build_usage_sorted_query(self, sort_params, *, count_only=False):
        """Exact global usage order, never a page-local/client-side sort.

        Only project-scoped usage/time predicates are supported. The root time
        window selects trace identities, not child timestamps; FINAL and the
        existing request memory/deadline settings remain authoritative. Unlike
        the newest-first scanner this path cannot publish a partial prefix.
        """
        if not self.project_id or self.project_ids is not None or not sort_params:
            raise ValueError("Usage sorting requires one project and an explicit sort")
        if any(item.get("column_id") not in USAGE_COLUMNS for item in sort_params):
            raise ValueError("Only cost and token sorting is supported")
        if any(
            not self.is_datetime_filter(item) and not usage_column(item)
            for item in self.filters
        ):
            raise ValueError("Usage sorting supports only date and usage filters")
        # Reuse strict list-window semantics, including half-open exclusions and
        # microsecond equality. The general span compiler uses inclusive BETWEEN
        # and must not reinterpret these time leaves. Freeze defaults across the
        # page and count queries on this builder.
        if not hasattr(self, "_usage_window"):
            self._usage_window = self.analyze_bounded_datetime_filters(self.filters)
        window = self._usage_window
        params = {
            **self.params,
            "usage_start_us": _unix_microseconds(window.start),
            "usage_end_us": _unix_microseconds(window.end),
        }
        compiler = ClickHouseFilterBuilderV2(
            table=self.TABLE, query_mode=ClickHouseFilterBuilderV2.QUERY_MODE_SPAN
        )
        predicate, filter_params = compiler.translate(
            [item for item in self.filters if usage_column(item)]
        )
        params.update(filter_params)
        exclusion, exclusion_params = self.bounded_datetime_exclusion_sql(self.filters)
        params.update(exclusion_params)
        time_condition = """start_time >= fromUnixTimestamp64Micro(%(usage_start_us)s)
            AND start_time < fromUnixTimestamp64Micro(%(usage_end_us)s)"""
        root_condition = f"""(parent_span_id IS NULL OR parent_span_id = '')
            AND {time_condition} AND ({exclusion or "1 = 1"})"""
        fields = [
            "id",
            "start_time",
            "observation_type",
            "service_name",
            "_version",
            "trace_name",
            "name",
            "status",
            "end_time",
            "latency_ms",
            "model",
            "provider",
            "trace_session_id",
        ]
        root_fields = ", ".join(f"live.{field}" for field in fields)
        # Resolve latest span state by six-key argMax instead of spans FINAL so
        # ClickHouse prunes partitions via start_time and granules via idx_trace_id
        # even when use_skip_indexes_if_final = 0 is active on the connection.
        # Keep immutable key columns outside argMax and mutable columns inside
        # one tuple so a newer NULL replaces an older value.
        mutable_fields = [
            "start_time",
            "_version",
            "trace_name",
            "name",
            "status",
            "end_time",
            "latency_ms",
            "model",
            "provider",
            "trace_session_id",
            "parent_span_id",
            "is_deleted",
            *USAGE_COLUMNS,
        ]
        tuple_exprs = [
            *mutable_fields,
            usage_coverage_sql(attrs="attrs_string", include_summary=True),
        ]
        latest_projections = [
            f"latest_span.{idx} AS {col}"
            for idx, col in enumerate(mutable_fields, start=1)
        ] + [f"latest_span.{len(tuple_exprs)} AS usage_coverage"]
        span_key = (
            "project_id, observation_type, service_name, "
            "toStartOfHour(start_time), trace_id, id"
        )
        source = f"""
            SELECT project_id, trace_id,
                root.1 AS root_span_id, root.2 AS start_time,
                root.3 AS observation_type, root.3 AS _root_observation_type,
                root.4 AS _root_service_name, toStartOfHour(root.2) AS _root_start_hour,
                root.5 AS _root_version, root.6 AS trace_name, root.7 AS span_name,
                root.8 AS status, root.9 AS end_time, root.10 AS latency_ms,
                root.11 AS model, root.12 AS provider, root.13 AS trace_session_id,
                usage_metrics.2 AS cost, usage_metrics.3 AS total_tokens,
                usage_metrics.4 AS prompt_tokens, usage_metrics.5 AS completion_tokens
            FROM (
                SELECT project_id, trace_id,
                    argMaxIf(tuple({root_fields}),
                        tuple(live.start_time, live.id, live.observation_type, live.service_name),
                        {root_condition}) AS root,
                    {usage_aggregate_sql(prefix="live.", coverage="live.usage_coverage", materialized=True)}
                FROM (
                    SELECT
                        project_id,
                        trace_id,
                        id,
                        observation_type,
                        service_name,
                        {", ".join(latest_projections)}
                    FROM (
                        SELECT
                            project_id,
                            trace_id,
                            id,
                            observation_type,
                            service_name,
                            toStartOfHour(start_time) AS start_hour,
                            argMax(tuple({", ".join(tuple_exprs)}), _version) AS latest_span
                        FROM spans
                        PREWHERE {self.project_filter_sql()}
                            AND start_time >= fromUnixTimestamp64Micro(%(usage_start_us)s) - INTERVAL 7 DAY
                            AND trace_id IN (
                                SELECT trace_id FROM spans
                                PREWHERE {self.project_filter_sql()}
                                    AND {time_condition}
                                WHERE parent_span_id IS NULL OR parent_span_id = ''
                            )
                        GROUP BY {span_key}
                    )
                    WHERE is_deleted = 0
                ) AS live
                GROUP BY project_id, trace_id
                HAVING countIf({root_condition}) > 0
            ) AS usage_roots
            ARRAY JOIN trace_usage_metrics AS usage_metrics
            WHERE {predicate or "1 = 1"}
        """
        if count_only:
            return f"SELECT count() AS total FROM ({source})", params
        order = compiler.translate_sort(
            sort_params, field_map={column: column for column in USAGE_COLUMNS}
        )
        params["limit"] = self.page_size
        params["offset"] = self.page_number * self.page_size
        return (
            f"""SELECT * FROM ({source})
            {order}, start_time DESC, trace_id DESC, project_id DESC
            LIMIT %(limit)s OFFSET %(offset)s""",
            params,
        )

    def _partition_trace_filter_plans(self, filters):
        usage_filters = [item for item in filters if usage_column(item)]
        other_filters = [item for item in filters if not usage_column(item)]
        plans, residual = super()._partition_trace_filter_plans(other_filters)
        # Validate operators/types with the existing compiler before converting
        # these leaves into residuals. Their raw-root seed must never run.
        super()._partition_trace_filter_plans(usage_filters)
        return plans, [*residual, *usage_filters]
