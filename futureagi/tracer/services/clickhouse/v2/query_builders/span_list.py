"""
v2 SpanList query builder — targets the CH 25.3 spans schema.

Subclass the v1 builder so all of its logic (pagination, sort, eval +
annotation joins, the 3-phase merge) is inherited unchanged, then let
`V2RewriteMixin` route every inherited `build*` method's compiled SQL through
the v2 rewriter at one boundary — `build()`, `build_content_query()` and
`build_count_query()` need no per-method overrides.

The eval and annotation queries (`build_eval_query`, `build_annotation_query`)
read from `tracer_eval_logger` and `model_hub_score` respectively — those
tables are NOT part of the CH 25.3 migration (eval results stay in PG;
annotations live in their own CDC'd table) and still carry `_peerdb_is_deleted`.
They are excluded from the rewrite via `_v2_rewrite_exclude`.
"""

from __future__ import annotations

from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin


class SpanListQueryBuilderV2(V2RewriteMixin, SpanListQueryBuilder):
    """Drop-in v2 SpanList builder.

    Callers can swap import lines:
        v1: from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
        v2: from tracer.services.clickhouse.v2.query_builders.span_list  import SpanListQueryBuilderV2

    Or the dispatch layer can route per-query-type via the shadow harness
    (tracer/services/clickhouse/v2/shadow.py) so v1 and v2 run in parallel
    until the operator promotes the query type to v2_primary or v2_only.
    """

    _v2_rewrite_exclude = frozenset({"build_eval_query", "build_annotation_query"})


__all__ = ["SpanListQueryBuilderV2"]
