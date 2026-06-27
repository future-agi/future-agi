"""
v2 SpanList query builder — targets the CH 25.3 spans schema.

Mirrors the architecture of v2/query_builders/filters.py:
  - SUBCLASS the v1 builder so all dashboard logic (pagination, sort, eval
    + annotation joins, the 3-phase merge) is inherited unchanged.
  - REWRITE column references in the compiled SQL output via the same
    `rewrite_and_apply_v2_settings()` helper used by the v2 filter compiler. The
    rewriter is whole-SQL: it covers both the dashboard's own column refs
    (e.g. `WHERE _peerdb_is_deleted = 0`) AND the WHERE-clause fragments
    the inner v1 filter compiler emits.

Methods overridden:
  - `build()` — the main query (paginated spans page)
  - `build_content_query()` — the per-span input/output/overflow fetch
    (attributes_extra is the v2 column name for overflow attributes)
  - `build_count_query()` — the COUNT for pagination

The eval and annotation queries (`build_eval_query`, `build_annotation_query`)
read from `tracer_eval_logger` and `model_hub_score` respectively — those
tables are NOT part of the CH 25.3 migration (eval results stay in PG;
annotations live in their own CDC'd table). No rewrite needed there.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_and_apply_v2_settings


class SpanListQueryBuilderV2(SpanListQueryBuilder):
    """Drop-in v2 SpanList builder.

    Callers can swap import lines:
        v1: from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
        v2: from tracer.services.clickhouse.v2.query_builders.span_list  import SpanListQueryBuilderV2

    Or the dispatch layer can route per-query-type via the shadow harness
    (tracer/services/clickhouse/v2/shadow.py) so v1 and v2 run in parallel
    until the operator promotes the query type to v2_primary or v2_only.
    """

    def build(self) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build()
        return rewrite_and_apply_v2_settings(sql), params

    def build_content_query(self, span_ids: list) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_content_query(span_ids)
        return rewrite_and_apply_v2_settings(sql), params

    def build_count_query(self) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_count_query()
        return rewrite_and_apply_v2_settings(sql), params


__all__ = ["SpanListQueryBuilderV2"]
