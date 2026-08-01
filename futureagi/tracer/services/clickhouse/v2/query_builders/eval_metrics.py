"""
v2 EvalMetrics query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. EvalMetrics powers the eval scoreboard panels
(pass-rate by config, by span type, etc.). It JOINs spans to
tracer_eval_logger. `V2RewriteMixin` routes the inherited `build()` SQL through
the v2 rewriter at one boundary.
"""

from __future__ import annotations

from tracer.services.clickhouse.query_builders.eval_metrics import (
    EvalMetricsQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)


class EvalMetricsQueryBuilderV2(V2RewriteMixin, EvalMetricsQueryBuilder):
    """Drop-in v2 EvalMetrics builder."""

    _FILTER_BUILDER_CLS = ClickHouseFilterBuilderV2
    _SPANS_TIME_COLUMN = "start_time"
    _SPANS_NOT_DELETED = "is_deleted = 0"
    # The v2 SQL rewriter would rename the legacy CDC tombstone column to an
    # identifier that does not exist on the legacy eval logger.
    _INCLUDE_CDC_TOMBSTONE_GUARD = False

    def __init__(self, *args, **kwargs):
        # The legacy eval_metrics_hourly MV is fed only by the PeerDB logger
        # and is absent entirely on a pure CH25 schema. Until eval_per_config
        # has equivalent project-scoped aggregates, the configured raw logger
        # is the only source that is both present and fresh in every topology.
        kwargs["use_preaggregated"] = False
        super().__init__(*args, **kwargs)


__all__ = ["EvalMetricsQueryBuilderV2"]
