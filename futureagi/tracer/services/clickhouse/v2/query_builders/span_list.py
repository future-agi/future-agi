"""
v2 SpanList query builder — targets the CH 25.3 spans schema.

Subclass the v1 builder so all of its logic (pagination, sort, eval +
annotation joins, the 3-phase merge) is inherited unchanged, then let
`V2RewriteMixin` route every inherited `build*` method's compiled SQL through
the v2 rewriter at one boundary — `build()`, `build_content_query()` and
`build_count_query()` need no per-method overrides.

The eval and annotation queries (`build_eval_query`, `build_annotation_query`)
read from the eval-logger and `model_hub_score` tables, which the `spans`
rewrite doesn't apply to — so both are excluded via `_v2_rewrite_exclude`.
`build_eval_query` instead resolves its table + not-deleted predicate through
`eval_logger_source()`, so it follows the CH25 `tracer_eval_logger_v2`
(`is_deleted`) flip on its own. `build_annotation_query` still reads the
CDC'd `model_hub_score` with `_peerdb_is_deleted`.
"""

from __future__ import annotations

from typing import Any

from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
    _append_v2_settings,
    rewrite_v1_sql_to_v2,
)


class SpanListQueryBuilderV2(V2RewriteMixin, SpanListQueryBuilder):
    """Drop-in v2 SpanList builder.

    Callers can swap import lines:
        v1: from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
        v2: from tracer.services.clickhouse.v2.query_builders.span_list  import SpanListQueryBuilderV2

    Or the dispatch layer can route per-query-type via the shadow harness
    (tracer/services/clickhouse/v2/shadow.py) so v1 and v2 run in parallel
    until the operator promotes the query type to v2_primary or v2_only.
    """

    _v2_rewrite_exclude = frozenset(
        {
            "build_eval_query",
            "build_annotation_query",
            # Native typed-JSON aggregation below. The generic v1 rewriter's
            # bare-JSON compatibility transform is intentionally not applied
            # inside argMax expressions.
            "build_content_query",
            # Candidate classification may join point-scoped FINAL relations
            # over mutable state; keep their skip indexes disabled explicitly.
            "build_latest_attribute_candidate_matches",
        }
    )

    # Use the v2 filter compiler so filters read the v2 dimension tables
    # (end_users, etc.) instead of the dropped legacy CDC tables.
    _FILTER_BUILDER_CLS = ClickHouseFilterBuilderV2

    def build_latest_attribute_candidate_matches(
        self,
        candidate_span_ids: list[str],
        *,
        window_start: Any = None,
        window_end: Any = None,
    ) -> tuple[str, dict[str, Any]]:
        """Rewrite a bounded classifier without mutable FINAL skip indexes."""

        query, params = super().build_latest_attribute_candidate_matches(
            candidate_span_ids,
            window_start=window_start,
            window_end=window_end,
        )
        if not query:
            return query, params
        query = _append_v2_settings(rewrite_v1_sql_to_v2(query))
        return (
            query.replace(
                "use_skip_indexes_if_final = 1",
                "use_skip_indexes_if_final = 0",
            ),
            params,
        )

    def build_content_query(self, span_ids: list) -> tuple[str, dict[str, Any]]:
        """Hydrate the bounded page at latest version without table ``FINAL``."""

        if not span_ids:
            return "", {}
        start_date, end_date = self.parse_time_range(self.filters)
        params: dict[str, Any] = {
            **self.params,
            "start_date": start_date,
            "end_date": end_date,
            "content_span_ids": tuple(str(span_id) for span_id in span_ids),
        }
        query = f"""
        SELECT
            grouped_id AS id,
            latest_trace_id AS trace_id,
            latest_name AS name,
            latest_observation_type AS observation_type,
            latest_status AS status,
            latest_start_time AS start_time,
            latest_end_time AS end_time,
            latest_latency_ms AS latency_ms,
            latest_cost AS cost,
            latest_total_tokens AS total_tokens,
            latest_prompt_tokens AS prompt_tokens,
            latest_completion_tokens AS completion_tokens,
            latest_model AS model,
            latest_provider AS provider,
            latest_end_user_id AS end_user_id,
            latest_created_at AS created_at,
            latest_input AS input,
            latest_output AS output,
            latest_attributes_extra AS attributes_extra,
            latest_attrs_string AS attrs_string,
            latest_attrs_number AS attrs_number,
            latest_attrs_bool AS attrs_bool
        FROM (
            SELECT
                id AS grouped_id,
                argMax(trace_id, _version) AS latest_trace_id,
                argMax(name, _version) AS latest_name,
                argMax(observation_type, _version) AS latest_observation_type,
                argMax(status, _version) AS latest_status,
                argMax(tuple(start_time), _version).1 AS latest_start_time,
                argMax(tuple(end_time), _version).1 AS latest_end_time,
                argMax(latency_ms, _version) AS latest_latency_ms,
                argMax(cost, _version) AS latest_cost,
                argMax(total_tokens, _version) AS latest_total_tokens,
                argMax(prompt_tokens, _version) AS latest_prompt_tokens,
                argMax(completion_tokens, _version) AS latest_completion_tokens,
                argMax(model, _version) AS latest_model,
                argMax(provider, _version) AS latest_provider,
                argMax(tuple(end_user_id), _version).1 AS latest_end_user_id,
                argMax(created_at, _version) AS latest_created_at,
                argMax(input, _version) AS latest_input,
                argMax(output, _version) AS latest_output,
                argMax(toJSONString(attributes_extra), _version)
                    AS latest_attributes_extra,
                argMax(attrs_string, _version) AS latest_attrs_string,
                argMax(attrs_number, _version) AS latest_attrs_number,
                argMax(attrs_bool, _version) AS latest_attrs_bool,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND id IN %(content_span_ids)s
              AND start_time >= %(start_date)s - INTERVAL 1 DAY
              AND start_time < %(end_date)s + INTERVAL 1 DAY
            GROUP BY id
        )
        WHERE latest_is_deleted = 0
        """
        return _append_v2_settings(query), params


__all__ = ["SpanListQueryBuilderV2"]
