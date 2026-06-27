"""
v2 EvalMetrics query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. EvalMetrics powers the eval scoreboard panels
(pass-rate by config, by span type, etc.). It JOINs spans to
tracer_eval_logger; only the spans-side column refs need rewriting.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from tracer.services.clickhouse.query_builders.eval_metrics import (
    EvalMetricsQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_and_apply_v2_settings


class EvalMetricsQueryBuilderV2(EvalMetricsQueryBuilder):
    """Drop-in v2 EvalMetrics builder."""

    def build(self) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build()
        return rewrite_and_apply_v2_settings(sql), params


__all__ = ["EvalMetricsQueryBuilderV2"]
