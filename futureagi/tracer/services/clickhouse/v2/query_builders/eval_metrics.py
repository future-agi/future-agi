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


class EvalMetricsQueryBuilderV2(V2RewriteMixin, EvalMetricsQueryBuilder):
    """Drop-in v2 EvalMetrics builder."""


__all__ = ["EvalMetricsQueryBuilderV2"]
