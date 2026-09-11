"""
v2 EvalMetrics query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. EvalMetrics powers the eval scoreboard panels
(pass-rate by config, by span type, etc.). It JOINs spans to
tracer_eval_logger. `V2RewriteMixin` routes the inherited `build()` SQL through
the v2 rewriter at one boundary.
"""

from __future__ import annotations

from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.query_builders.eval_metrics import (
    EvalMetricsQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin


class EvalMetricsQueryBuilderV2(V2RewriteMixin, EvalMetricsQueryBuilder):
    """Direct-write eval metrics builder for the CH25 topology.

    The legacy ``eval_metrics_hourly`` rollup is fed from the PeerDB eval
    logger and is therefore not authoritative after direct-write cutover.
    Keep the graph on the config/time-pruned authoritative raw table selected
    by ``CH25_EVAL_LOGGER_TABLE`` until a schema-compatible rollup can preserve
    the same average-score contract. The surrounding spans read and connection
    still use the CH25/V2 path. This is a read-routing change only; it requires
    no DDL.
    """

    _EVAL_LOGGER_SOURCE = staticmethod(eval_logger_source)
    # Eval logger trace IDs are UUID-typed, while the direct spans table stores
    # dashed UUIDs as String. Compare their textual forms so filtered eval
    # graphs do not fail with NO_COMMON_TYPE (Code 386).
    _EVAL_TRACE_ID_EXPR = "toString(raw_eval_logger.trace_id)"

    def __init__(
        self,
        *args,
        observe_type="trace",
        session_trace_membership_sql: str | None = None,
        session_trace_membership_params: dict | None = None,
        user_trace_membership_sql: str | None = None,
        user_trace_membership_params: dict | None = None,
        annotation_label_ids: list[str] | tuple[str, ...] | None = None,
        **kwargs,
    ):
        if session_trace_membership_sql and user_trace_membership_sql:
            raise ValueError("only one aggregate trace membership may be supplied")
        self.session_trace_membership_sql = session_trace_membership_sql
        self.session_trace_membership_params = dict(
            session_trace_membership_params or {}
        )
        self.user_trace_membership_sql = user_trace_membership_sql
        self.user_trace_membership_params = dict(user_trace_membership_params or {})
        self.annotation_label_ids = (
            None if annotation_label_ids is None else tuple(annotation_label_ids)
        )
        super().__init__(*args, **kwargs)
        self.use_preaggregated = False
        self.observe_type = str(observe_type or "trace").strip().lower()
        if self.observe_type not in {"trace", "span"}:
            raise ValueError("observe_type must be trace or span")

    def _filter_fragment(self) -> str:
        """Build exact trace/span membership on direct-write latest rows."""
        aggregate_membership_sql = (
            self.session_trace_membership_sql or self.user_trace_membership_sql
        )
        if aggregate_membership_sql:
            if self.observe_type != "trace":
                raise ValueError("aggregate graph membership requires trace mode")
            self.params.update(
                self.session_trace_membership_params
                or self.user_trace_membership_params
            )
            return (
                "AND toString(raw_eval_logger.trace_id) IN ("
                f"{aggregate_membership_sql})"
            )
        if not self.filters:
            return ""
        from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
            compile_exact_graph_filter_predicates,
        )

        extra_where, extra_params = compile_exact_graph_filter_predicates(
            self.filters,
            project_id=str(self.project_id),
            observe_type=self.observe_type,
            annotation_label_ids=self.annotation_label_ids,
        )
        self.params.update(extra_params)
        if not extra_where:
            return ""
        # The outer eval window may be split on output-bucket boundaries by
        # the exact worker. Membership is a full-request-window decision: a
        # trace/span matching anywhere in the frozen window must not disappear
        # merely because its eval row belongs to a different output partition.
        self.params["snapshot_start_date"] = self.start_date
        self.params["snapshot_end_date"] = self.end_date
        if self.observe_type == "span":
            return (
                "AND (toString(raw_eval_logger.trace_id), "
                "raw_eval_logger.observation_span_id) IN ("
                "SELECT trace_id, id FROM spans FINAL "
                "WHERE project_id = toUUID(%(project_id)s) AND is_deleted = 0 "
                "AND start_time >= %(snapshot_start_date)s "
                "AND start_time < %(snapshot_end_date)s "
                f"AND {extra_where})"
            )
        return (
            "AND toString(raw_eval_logger.trace_id) IN ("
            "SELECT DISTINCT trace_id FROM spans FINAL "
            "WHERE project_id = toUUID(%(project_id)s) AND is_deleted = 0 "
            "AND start_time >= %(snapshot_start_date)s "
            "AND start_time < %(snapshot_end_date)s "
            f"AND {extra_where})"
        )


__all__ = ["EvalMetricsQueryBuilderV2"]
