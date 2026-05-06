"""
Dataset Dashboard Query Builder for ClickHouse.

Translates a widget ``query_config`` (with ``workflow: "dataset"``) into
ClickHouse SQL queries.  Mirrors :class:`DashboardQueryBuilder` but queries
the ``dataset_cells`` view and related CDC tables instead of ``spans``.

Supports four metric types:
- **system_metric** -- row_count, tokens, response_time, cell_error_rate
- **eval_metric** -- aggregates from eval columns (score, pass/fail, choices)
- **annotation_metric** -- aggregates from annotation columns
- **custom_column** -- user-defined numeric/boolean BUILD columns
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from tracer.services.clickhouse.query_builders.dashboard import (
    _get_operator_symbol,
)
from tracer.services.clickhouse.query_builders.dashboard_base import (
    FILTER_OPERATORS,
    GRANULARITY_TO_CH,
    PRESET_RANGES,
    DashboardQueryBuilderBase,
    _coerce_filter_value,
    _generate_time_buckets,
    _parse_dt,
    rescale_rate_to_percent,
)

# Allowed characters for safe string interpolation
_SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9._\-]+$")


def _sanitize_key(key: str) -> str:
    if not key or not _SAFE_KEY_RE.match(key):
        raise ValueError(f"Invalid key: {key!r}")
    return key


# ---------------------------------------------------------------------------
# Metric resolution tables
# ---------------------------------------------------------------------------

DATASET_SYSTEM_METRICS: Dict[str, Tuple[str, str]] = {
    "row_count": ("model_hub_cell", "1"),
    "dataset_count": ("model_hub_cell", "c.dataset_id"),
    "prompt_tokens": ("model_hub_cell", "prompt_tokens"),
    "completion_tokens": ("model_hub_cell", "completion_tokens"),
    "total_tokens": (
        "model_hub_cell",
        "COALESCE(prompt_tokens, 0) + COALESCE(completion_tokens, 0)",
    ),
    "response_time": ("model_hub_cell", "response_time"),
    "cell_error_rate": (
        "model_hub_cell",
        "CASE WHEN status = 'error' THEN 1.0 ELSE 0.0 END",
    ),
}

# Metrics whose column expression emits a 0/1 indicator per row.
_RATE_INDICATOR_METRICS = frozenset({"cell_error_rate"})

DATASET_METRIC_UNITS: Dict[str, str] = {
    "row_count": "",
    "dataset_count": "",
    "prompt_tokens": "tokens",
    "completion_tokens": "tokens",
    "total_tokens": "tokens",
    "response_time": "ms",
    "cell_error_rate": "%",
}

# Extend base aggregations with dataset-specific ones
DATASET_AGGREGATIONS: Dict[str, str] = {
    "avg": "avg({col})",
    "median": "quantile(0.5)({col})",
    "max": "max({col})",
    "min": "min({col})",
    "p25": "quantile(0.25)({col})",
    "p50": "quantile(0.5)({col})",
    "p75": "quantile(0.75)({col})",
    "p90": "quantile(0.9)({col})",
    "p95": "quantile(0.95)({col})",
    "p99": "quantile(0.99)({col})",
    "count": "count()",
    "count_distinct": "uniq({col})",
    "sum": "sum({col})",
    # Dataset-specific aggregations for pass/fail and boolean.
    # Rate aggregations return 0–100 (percentage) so widgets that display
    # them with a ``%`` suffix don't show 0.42% for a 42% pass rate.
    "pass_rate": (
        "countIf(lower({col}) IN ('true', 'pass', 'passed', '1')) * 100.0 "
        "/ nullIf(count(), 0)"
    ),
    "fail_rate": (
        "countIf(lower({col}) IN ('false', 'fail', 'failed', '0')) * 100.0 "
        "/ nullIf(count(), 0)"
    ),
    "pass_count": "countIf(lower({col}) IN ('true', 'pass', 'passed', '1'))",
    "fail_count": "countIf(lower({col}) IN ('false', 'fail', 'failed', '0'))",
    "true_rate": (
        "countIf(lower({col}) IN ('true', '1')) * 100.0 / nullIf(count(), 0)"
    ),
}

# Breakdown dimensions for dataset workflow.
# Note: ``dataset`` is intentionally excluded — each chart already filters to
# one (or a chosen subset of) datasets, so breaking down by dataset is either
# trivial or meaningless. The dataset attribute remains available as a filter
# (see ``DATASET_FILTER_COLUMNS``).
DATASET_BREAKDOWN_COLUMNS: Dict[str, str] = {
    "eval_template": "dictGet('column_dict', 'name', c.column_id)",
    "column_name": "dictGet('column_dict', 'name', c.column_id)",
    "cell_status": "c.status",
}

# Filter dimensions for dataset workflow
DATASET_FILTER_COLUMNS: Dict[str, str] = {
    "dataset": "toString(c.dataset_id)",
    "column_name": "dictGet('column_dict', 'name', c.column_id)",
    "column_source": "dictGet('column_dict', 'source', c.column_id)",
    "cell_status": "c.status",
}


class DatasetQueryBuilder(DashboardQueryBuilderBase):
    """Translates a dataset widget query_config into ClickHouse SQL.

    Queries the denormalized ``dataset_cells`` view (via model_hub_cell +
    dictionaries) instead of ``spans``.
    """

    def __init__(self, query_config: dict) -> None:
        super().__init__(query_config)
        self.workspace_id = query_config.get("workspace_id", "")
        self.dataset_ids = query_config.get("dataset_ids", [])

    # ------------------------------------------------------------------
    # Time range
    # ------------------------------------------------------------------

    def parse_time_range(self) -> Tuple[datetime, datetime]:
        tr = self.config.get("time_range", {})
        preset = tr.get("preset")
        custom_start = tr.get("custom_start")
        custom_end = tr.get("custom_end")
        now = datetime.now(timezone.utc)

        if custom_start and custom_end:
            return _parse_dt(custom_start), _parse_dt(custom_end)

        if preset == "today":
            return now.replace(hour=0, minute=0, second=0, microsecond=0), now
        if preset == "yesterday":
            yesterday = now - timedelta(days=1)
            return (
                yesterday.replace(hour=0, minute=0, second=0, microsecond=0),
                yesterday.replace(hour=23, minute=59, second=59, microsecond=999999),
            )

        delta = PRESET_RANGES.get(preset)
        if delta:
            return now - delta, now
        return now - timedelta(days=30), now

    # ------------------------------------------------------------------
    # Single-metric query
    # ------------------------------------------------------------------

    def build_metric_query(self, metric: dict) -> Tuple[str, dict]:
        metric_type = metric.get("type", "system_metric")
        metric_name = metric.get("id") or metric.get("name", "")
        aggregation = metric.get("aggregation", "avg")
        per_metric_filters = metric.get("filters", [])

        start_date, end_date = self.parse_time_range()
        bucket_fn = GRANULARITY_TO_CH.get(self.granularity, "toStartOfDay")

        params: Dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
        }

        if self.workspace_id:
            params["workspace_id"] = self.workspace_id
        if self.dataset_ids:
            params["dataset_ids"] = self.dataset_ids

        if metric_type == "system_metric":
            return self._build_system_metric_query(
                metric_name, aggregation, bucket_fn, per_metric_filters, params
            )
        elif metric_type == "eval_metric":
            return self._build_eval_metric_query(
                metric, aggregation, bucket_fn, per_metric_filters, params
            )
        elif metric_type == "annotation_metric":
            return self._build_annotation_metric_query(
                metric, aggregation, bucket_fn, per_metric_filters, params
            )
        elif metric_type == "custom_column":
            return self._build_custom_column_query(
                metric, aggregation, bucket_fn, per_metric_filters, params
            )
        else:
            raise ValueError(f"Unknown metric type: {metric_type}")

    # ------------------------------------------------------------------
    # System metric
    # ------------------------------------------------------------------

    def _build_system_metric_query(
        self,
        metric_name: str,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: List[dict],
        params: dict,
    ) -> Tuple[str, dict]:
        if metric_name not in DATASET_SYSTEM_METRICS:
            raise ValueError(f"Unknown dataset system metric: {metric_name}")
        _, col_expr = DATASET_SYSTEM_METRICS[metric_name]

        # row_count should default to count
        if metric_name == "row_count" and aggregation not in ("count", "sum"):
            aggregation = "count"

        # dataset_count is always count(distinct dataset_id) — ignore caller agg
        if metric_name == "dataset_count":
            aggregation = "count_distinct"

        agg_expr = DATASET_AGGREGATIONS.get(aggregation, "avg({col})").format(
            col=col_expr
        )

        if metric_name in _RATE_INDICATOR_METRICS:
            agg_expr = rescale_rate_to_percent(agg_expr, aggregation)

        select_parts = [f"{bucket_fn}(c.created_at) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]

        breakdown_expr = self._breakdown_select()
        if breakdown_expr:
            select_parts.append(f"{breakdown_expr} AS breakdown_value")
            group_parts.append("breakdown_value")
            order_parts.append("breakdown_value")

        select_parts.append(f"{agg_expr} AS value")

        where_clauses = self._build_base_where(params)
        where_clauses = self._apply_filters(
            where_clauses, self.global_filters + per_metric_filters, params
        )

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM model_hub_cell AS c FINAL\n"
            f"WHERE {' AND '.join(where_clauses)}\n"
            f"GROUP BY {', '.join(group_parts)}\n"
            f"ORDER BY {', '.join(order_parts)}"
        )
        return query, params

    # ------------------------------------------------------------------
    # Eval metric
    # ------------------------------------------------------------------

    def _build_eval_metric_query(
        self,
        metric: dict,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: List[dict],
        params: dict,
    ) -> Tuple[str, dict]:
        config_id = metric.get("config_id", "")
        output_type = metric.get("output_type", "SCORE")
        params["eval_config_id"] = config_id

        # For eval metrics, we query cells where the column's source_id
        # matches the eval template UUID
        PASS_FAIL_AGGS = ("pass_rate", "fail_rate", "pass_count", "fail_count")

        if output_type == "PASS_FAIL":
            # For pass/fail, the cell value is text like "true"/"false"/"pass"/"fail"
            col_expr = "c.value"
            if aggregation not in (*PASS_FAIL_AGGS, "count"):
                aggregation = "pass_rate"
        elif output_type == "CHOICE":
            col_expr = "c.value"
            if aggregation not in ("count", "count_distinct"):
                aggregation = "count"
        else:
            # SCORE — numeric float value stored as text
            col_expr = "toFloat64OrNull(c.value)"
            # pass_rate/fail_rate use lower() which is incompatible with Float64
            if aggregation in PASS_FAIL_AGGS:
                aggregation = "avg"

        agg_expr = DATASET_AGGREGATIONS.get(aggregation, "avg({col})").format(
            col=col_expr
        )

        select_parts = [f"{bucket_fn}(c.created_at) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]

        breakdown_expr = self._breakdown_select()
        if breakdown_expr:
            select_parts.append(f"{breakdown_expr} AS breakdown_value")
            group_parts.append("breakdown_value")
            order_parts.append("breakdown_value")

        select_parts.append(f"{agg_expr} AS value")

        where_clauses = self._build_base_where(params)
        # Filter to eval columns matching the config_id (eval template UUID)
        where_clauses.append(
            "dictGet('column_dict', 'source', c.column_id) = 'evaluation'"
        )
        where_clauses.append(
            "dictGet('column_dict', 'source_id', c.column_id) = %(eval_config_id)s"
        )

        where_clauses = self._apply_filters(
            where_clauses, self.global_filters + per_metric_filters, params
        )

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM model_hub_cell AS c FINAL\n"
            f"WHERE {' AND '.join(where_clauses)}\n"
            f"GROUP BY {', '.join(group_parts)}\n"
            f"ORDER BY {', '.join(order_parts)}"
        )
        return query, params

    # ------------------------------------------------------------------
    # Annotation metric
    # ------------------------------------------------------------------

    def _build_annotation_metric_query(
        self,
        metric: dict,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: List[dict],
        params: dict,
    ) -> Tuple[str, dict]:
        label_id = metric.get("label_id", metric.get("config_id", ""))
        output_type = metric.get("output_type", "numeric")
        params["annotation_label_id"] = label_id

        # Annotation data is stored in cells with column source = 'annotation_label'
        TEXT_AGGS = ("pass_rate", "fail_rate", "pass_count", "fail_count", "true_rate")

        if output_type in ("thumbs_up_down",):
            col_expr = "c.value"
            if aggregation not in ("true_rate", "count"):
                aggregation = "true_rate"
        elif output_type in ("categorical", "text"):
            col_expr = "c.value"
            if aggregation not in ("count", "count_distinct"):
                aggregation = "count"
        else:
            # numeric, star — value is a number stored as text
            col_expr = "toFloat64OrNull(c.value)"
            # Text-based aggregations are incompatible with Float64
            if aggregation in TEXT_AGGS:
                aggregation = "avg"

        agg_expr = DATASET_AGGREGATIONS.get(aggregation, "avg({col})").format(
            col=col_expr
        )

        select_parts = [f"{bucket_fn}(c.created_at) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]

        breakdown_expr = self._breakdown_select()
        if breakdown_expr:
            select_parts.append(f"{breakdown_expr} AS breakdown_value")
            group_parts.append("breakdown_value")
            order_parts.append("breakdown_value")

        select_parts.append(f"{agg_expr} AS value")

        where_clauses = self._build_base_where(params)
        where_clauses.append(
            "dictGet('column_dict', 'source', c.column_id) = 'annotation_label'"
        )
        where_clauses.append(
            "dictGet('column_dict', 'source_id', c.column_id) = %(annotation_label_id)s"
        )

        where_clauses = self._apply_filters(
            where_clauses, self.global_filters + per_metric_filters, params
        )

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM model_hub_cell AS c FINAL\n"
            f"WHERE {' AND '.join(where_clauses)}\n"
            f"GROUP BY {', '.join(group_parts)}\n"
            f"ORDER BY {', '.join(order_parts)}"
        )
        return query, params

    # ------------------------------------------------------------------
    # Custom column metric
    # ------------------------------------------------------------------

    def _build_custom_column_query(
        self,
        metric: dict,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: List[dict],
        params: dict,
    ) -> Tuple[str, dict]:
        column_id = metric.get("column_id", metric.get("config_id", ""))
        data_type = metric.get("data_type", "float")
        params["custom_column_id"] = column_id

        TEXT_AGGS = ("pass_rate", "fail_rate", "pass_count", "fail_count", "true_rate")

        if data_type == "boolean":
            col_expr = "c.value"
            if aggregation not in ("true_rate", "count"):
                aggregation = "true_rate"
        else:
            col_expr = "toFloat64OrNull(c.value)"
            if aggregation in TEXT_AGGS:
                aggregation = "avg"

        agg_expr = DATASET_AGGREGATIONS.get(aggregation, "avg({col})").format(
            col=col_expr
        )

        select_parts = [f"{bucket_fn}(c.created_at) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]

        breakdown_expr = self._breakdown_select()
        if breakdown_expr:
            select_parts.append(f"{breakdown_expr} AS breakdown_value")
            group_parts.append("breakdown_value")
            order_parts.append("breakdown_value")

        select_parts.append(f"{agg_expr} AS value")

        where_clauses = self._build_base_where(params)
        where_clauses.append("c.column_id = toUUID(%(custom_column_id)s)")

        where_clauses = self._apply_filters(
            where_clauses, self.global_filters + per_metric_filters, params
        )

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM model_hub_cell AS c FINAL\n"
            f"WHERE {' AND '.join(where_clauses)}\n"
            f"GROUP BY {', '.join(group_parts)}\n"
            f"ORDER BY {', '.join(order_parts)}"
        )
        return query, params

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    def format_results(
        self,
        metric_results: List[Tuple[dict, List[dict]]],
        dataset_name_map: Optional[Dict[str, str]] = None,
    ) -> dict:
        start_date, end_date = self.parse_time_range()
        all_buckets = _generate_time_buckets(start_date, end_date, self.granularity)
        formatted_metrics = []

        for metric_info, rows in metric_results:
            formatted_metrics.append(
                self._format_metric_result(
                    metric_info,
                    rows,
                    all_buckets,
                    DATASET_METRIC_UNITS,
                    name_map=dataset_name_map,
                    name_map_breakdown="dataset",
                )
            )

        return {
            "metrics": formatted_metrics,
            "time_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            "granularity": self.granularity,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _breakdown_select(self) -> Optional[str]:
        if not self.breakdowns:
            return None
        bd = self.breakdowns[0]
        bd_name = bd.get("name", "")
        col = DATASET_BREAKDOWN_COLUMNS.get(bd_name)
        if col:
            return col
        return None

    @staticmethod
    def _dataset_scope_subquery() -> str:
        return (
            "SELECT id FROM model_hub_dataset FINAL "
            "WHERE _peerdb_is_deleted = 0 AND deleted = 0"
        )

    def _build_base_where(self, params: dict) -> List[str]:
        clauses = [
            "c._peerdb_is_deleted = 0",
            "c.created_at >= %(start_date)s",
            "c.created_at < %(end_date)s",
        ]
        if self.workspace_id:
            clauses.append(
                "c.dataset_id IN ("
                f"{self._dataset_scope_subquery()} "
                "AND workspace_id = toUUID(%(workspace_id)s)"
                ")"
            )
        if self.dataset_ids:
            clauses.append("c.dataset_id IN %(dataset_ids)s")
        return clauses

    def _apply_filters(
        self,
        clauses: List[str],
        filters: List[dict],
        params: dict,
    ) -> List[str]:
        idx = 0
        for f in filters:
            f_type = f.get("metric_type", "")
            f_name = f.get("metric_name", "")
            op = f.get("operator", "")
            val = f.get("value")

            if f_type != "system_metric":
                # Dataset filters only support system_metric dimensions for now
                continue

            if f_name == "dataset":
                if op in ("is_set", "is_not_set"):
                    clauses.append(
                        "c.dataset_id IS NOT NULL"
                        if op == "is_set"
                        else "c.dataset_id IS NULL"
                    )
                    continue

                if val is None or val == "" or val == []:
                    continue

                if op in ("between", "not_between"):
                    continue

                op_tpl = FILTER_OPERATORS.get(op)
                if op_tpl:
                    param_key = f"df_{idx}_val"
                    op_sql = op_tpl.format(prefix="df_", idx=idx)
                    clauses.append(
                        "c.dataset_id IN ("
                        f"{self._dataset_scope_subquery()} "
                        f"AND name {op_sql}"
                        ")"
                    )
                    params[param_key] = _coerce_filter_value(val, op)
                    idx += 1
                continue

            col = DATASET_FILTER_COLUMNS.get(f_name)
            if not col:
                continue

            if op in ("is_set", "is_not_set"):
                op_tpl = FILTER_OPERATORS.get(op)
                if op_tpl:
                    clauses.append(f"{col} {op_tpl}")
                continue

            if val is None or val == "" or val == []:
                continue

            if op in ("between", "not_between"):
                if isinstance(val, list) and len(val) == 2:
                    lo_key = f"df_{idx}_lo"
                    hi_key = f"df_{idx}_hi"
                    params[lo_key] = _coerce_filter_value(val[0], "equal_to")
                    params[hi_key] = _coerce_filter_value(val[1], "equal_to")
                    neg = "NOT " if op == "not_between" else ""
                    clauses.append(f"{col} {neg}BETWEEN %({lo_key})s AND %({hi_key})s")
                    idx += 1
                continue

            op_tpl = FILTER_OPERATORS.get(op)
            if op_tpl:
                param_key = f"df_{idx}_val"
                op_sql = op_tpl.format(prefix="df_", idx=idx)
                clauses.append(f"{col} {op_sql}")
                params[param_key] = _coerce_filter_value(val, op)
                idx += 1

        return clauses
