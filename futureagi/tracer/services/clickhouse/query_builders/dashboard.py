"""
Dashboard Query Builder for ClickHouse.

Translates a widget ``query_config`` dict into one or more ClickHouse SQL
queries.  Unlike the other builders this class does NOT extend
:class:`BaseQueryBuilder` because it operates on multiple project IDs and
produces multiple queries (one per metric).

Supports four metric types:
- **system_metric** -- columns on the ``spans`` table (latency, tokens, cost, etc.)
- **eval_metric** -- aggregates from ``tracer_eval_logger FINAL``
- **annotation_metric** -- aggregates from ``model_hub_score FINAL``
- **custom_attribute** -- ``span_attr_num`` / ``span_attr_str`` map columns on ``spans``
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Allowed characters for ClickHouse map keys: alphanumeric, dots, underscores, hyphens
_SAFE_ATTR_KEY_RE = re.compile(r"^[a-zA-Z0-9._\-]+$")


def _sanitize_attr_key(key: str) -> str:
    """Validate an attribute key is safe for use in ClickHouse map access expressions."""
    if not key or not _SAFE_ATTR_KEY_RE.match(key):
        raise ValueError(f"Invalid attribute key: {key!r}")
    return key


# ---------------------------------------------------------------------------
# Metric resolution tables
# ---------------------------------------------------------------------------

SYSTEM_METRICS: Dict[str, Tuple[str, str]] = {
    "project": ("spans", "project_id"),
    "latency": ("spans", "latency_ms"),
    "error_rate": ("spans", "CASE WHEN status='ERROR' THEN 1.0 ELSE 0.0 END"),
    "tokens": ("spans", "total_tokens"),
    "input_tokens": ("spans", "prompt_tokens"),
    "output_tokens": ("spans", "completion_tokens"),
    "time_to_first_token": (
        "spans",
        "span_attr_num['gen_ai.server.time_to_first_token']",
    ),
    "cost": ("spans", "cost"),
    # Count metrics
    "session_count": ("spans", "trace_session_id"),
    "user_count": ("spans", "end_user_id"),
    "trace_count": ("spans", "trace_id"),
    "span_count": ("spans", "id"),
    # String dimensions (for breakdown/filter)
    "model": ("spans", "model"),
    "status": ("spans", "status"),
    "service_name": ("spans", "trace_name"),
    "span_kind": ("spans", "observation_type"),
    "provider": ("spans", "provider"),
    "session": ("spans", "trace_session_id"),
    "user": ("spans", "end_user_id"),
    "user_id_type": ("spans", "end_user_id"),  # resolved via dict in column map
    # Prompt dimensions
    "prompt_name": ("spans", "prompt_version_id"),
    "prompt_version": ("spans", "prompt_version_id"),
    "prompt_label": ("spans", "prompt_label_id"),
    # Tags
    "tag": ("spans", "tags"),
}

METRIC_UNITS: Dict[str, str] = {
    "latency": "ms",
    "error_rate": "%",
    "tokens": "tokens",
    "input_tokens": "tokens",
    "output_tokens": "tokens",
    "time_to_first_token": "ms",
    "cost": "$",
    "session_count": "",
    "user_count": "",
    "trace_count": "",
    "span_count": "",
    "model": "",
    "status": "",
    "service_name": "",
    "span_kind": "",
    "provider": "",
    "session": "",
    "user": "",
    "user_id_type": "",
    "prompt_name": "",
    "prompt_version": "",
    "prompt_label": "",
    "tag": "",
}

# Metrics that are non-numeric identifiers — force count_distinct aggregation
_COUNT_DISTINCT_METRICS = frozenset(
    {
        "project",
        "session",
        "user",
        "user_id_type",
        "session_count",
        "user_count",
        "trace_count",
        "span_count",
        "model",
        "status",
        "service_name",
        "span_kind",
        "provider",
        "prompt_name",
        "prompt_version",
        "prompt_label",
        "tag",
    }
)

AGGREGATIONS: Dict[str, str] = {
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
}

FILTER_OPERATORS: Dict[str, str] = {
    "less_than": "< %({prefix}{idx}_val)s",
    "greater_than": "> %({prefix}{idx}_val)s",
    "equal_to": "= %({prefix}{idx}_val)s",
    "not_equal_to": "!= %({prefix}{idx}_val)s",
    "greater_than_or_equal": ">= %({prefix}{idx}_val)s",
    "less_than_or_equal": "<= %({prefix}{idx}_val)s",
    "contains": "IN %({prefix}{idx}_val)s",
    "not_contains": "NOT IN %({prefix}{idx}_val)s",
    "str_contains": "LIKE %({prefix}{idx}_val)s",
    "str_not_contains": "NOT LIKE %({prefix}{idx}_val)s",
    "is_set": "!= ''",
    "is_not_set": "= ''",
    "is_numeric": "!= 0",
    "is_not_numeric": "= 0",
}

PRESET_RANGES: Dict[str, Optional[timedelta]] = {
    "30m": timedelta(minutes=30),
    "6h": timedelta(hours=6),
    "today": None,
    "yesterday": None,
    "7D": timedelta(days=7),
    "30D": timedelta(days=30),
    "3M": timedelta(days=90),
    "6M": timedelta(days=180),
    "12M": timedelta(days=365),
}

GRANULARITY_TO_CH: Dict[str, str] = {
    "minute": "toStartOfMinute",
    "hour": "toStartOfHour",
    "day": "toStartOfDay",
    "week": "toMonday",
    "month": "toStartOfMonth",
    "year": "toStartOfYear",
}


def _prefix_spans_columns(clause: str) -> str:
    """Add 's.' prefix to spans table columns in a WHERE clause for JOINed queries.
    Only prefixes known column names, avoids touching parameter references like %(project_ids)s.
    """
    import re

    # Prefix bare column names (not already prefixed and not inside %(...))
    for col in ("project_id", "_peerdb_is_deleted", "start_time"):
        # Match bare column name not preceded by . or inside %(...)
        clause = re.sub(
            rf"(?<!\.)(?<!%\()(?<!\w){col}(?!\w)(?!s\))",
            f"s.{col}",
            clause,
        )
    return clause


class DashboardQueryBuilder:
    """Translates a widget query_config into ClickHouse SQL.

    Does NOT extend BaseQueryBuilder because it operates on multiple
    project_ids and builds multiple queries (one per metric).
    """

    def __init__(self, query_config: dict) -> None:
        self.config = query_config
        self.project_ids = query_config.get("project_ids", [])
        self.organization_id = query_config.get("organization_id", "")
        self.workspace_id = query_config.get("workspace_id", "")
        self.granularity = query_config.get("granularity", "day")
        self.metrics = query_config.get("metrics", [])
        self.global_filters = query_config.get("filters", [])
        self.breakdowns = query_config.get("breakdowns", [])

    # ------------------------------------------------------------------
    # Time range
    # ------------------------------------------------------------------

    def parse_time_range(self) -> Tuple[datetime, datetime]:
        """Parse time range from preset or custom start/end."""
        tr = self.config.get("time_range") or self.config.get("timeRange") or {}
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

        # Default: last 30 days
        return now - timedelta(days=30), now

    # ------------------------------------------------------------------
    # Single-metric query
    # ------------------------------------------------------------------

    def build_metric_query(self, metric: dict) -> Tuple[str, dict]:
        """Build ClickHouse SQL for a single metric.

        Returns:
            (sql_string, params_dict)
        """
        metric_type = metric.get("type", "system_metric")
        metric_name = metric.get("id") or metric.get("name", "")
        aggregation = metric.get("aggregation", "avg")
        per_metric_filters = metric.get("filters", [])

        start_date, end_date = self.parse_time_range()
        bucket_fn = GRANULARITY_TO_CH.get(self.granularity, "toStartOfDay")

        params: Dict[str, Any] = {
            "project_ids": self.project_ids,
            "start_date": start_date,
            "end_date": end_date,
        }

        if metric_type == "system_metric":
            # Normalize to lowercase for case-insensitive lookup
            metric_name_lower = metric_name.lower() if metric_name else metric_name
            if metric_name_lower in SYSTEM_METRICS:
                metric_name = metric_name_lower
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
        elif metric_type == "custom_attribute":
            return self._build_custom_attr_query(
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
        # Normalize: saved widgets may have capitalized names (e.g. "Latency")
        metric_name = metric_name.lower() if metric_name else metric_name
        if metric_name not in SYSTEM_METRICS:
            # Fallback: treat unknown system metrics as custom span attributes
            # (handles widgets saved with wrong type, e.g. span attribute saved as system_metric)
            logger.warning(
                "Unknown system metric '%s', treating as custom attribute",
                metric_name,
            )
            return self._build_custom_attr_query(
                {"attribute_key": metric_name, "attribute_type": "number"},
                aggregation,
                bucket_fn,
                per_metric_filters,
                params,
            )
        _, col_expr = SYSTEM_METRICS[metric_name]
        # Force count-based aggregation for non-numeric / identity metrics
        if metric_name in _COUNT_DISTINCT_METRICS and aggregation not in (
            "count",
            "count_distinct",
        ):
            aggregation = "count_distinct"
        agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        select_parts = [f"{bucket_fn}(start_time) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]

        select_parts.append(f"{agg_expr} AS value")

        where_clauses, params = self._build_where_clauses(
            "spans", "start_time", per_metric_filters, params
        )

        # Subquery filters from global + per-metric for non-system metrics
        subquery_clauses = self._build_subquery_filters(
            self.global_filters + per_metric_filters, params, "s_"
        )
        params.update(subquery_clauses[1])

        all_where = where_clauses
        if subquery_clauses[0]:
            all_where += subquery_clauses[0]

        # Resolve all breakdowns (supports multiple, including annotation JOINs)
        bd_infos = self._resolve_all_breakdowns(params)
        has_annotation_bd = any(b["type"] == "annotation" for b in bd_infos)

        if bd_infos:
            # Build composite breakdown_value from all breakdowns
            bd_exprs = []
            join_clauses = []
            for b in bd_infos:
                bd_exprs.append(b["expr"])
                if b["join"]:
                    join_clauses.append(b["join"])

            if len(bd_exprs) == 1:
                bd_select = f"{bd_exprs[0]} AS breakdown_value"
            else:
                # Concatenate multiple breakdowns with " / " separator
                parts = ", ' / ', ".join(f"toString({e})" for e in bd_exprs)
                bd_select = f"concat({parts}) AS breakdown_value"

            # Use table alias 's' when JOINs are present
            if has_annotation_bd:
                agg_with_alias = (
                    agg_expr.replace("(", "(s.") if "(" in agg_expr else agg_expr
                )
                where_str = " AND ".join(_prefix_spans_columns(c) for c in all_where)
                join_str = "\n".join(join_clauses)
                query = (
                    f"SELECT {bucket_fn}(s.start_time) AS time_bucket,\n"
                    f"       {bd_select},\n"
                    f"       {agg_with_alias} AS value\n"
                    f"FROM spans AS s\n"
                    f"{join_str}\n"
                    f"WHERE {where_str}\n"
                    f"GROUP BY time_bucket, breakdown_value\n"
                    f"ORDER BY time_bucket, breakdown_value"
                )
            else:
                select_parts_with_bd = [
                    f"{bucket_fn}(start_time) AS time_bucket",
                    bd_select,
                    f"{agg_expr} AS value",
                ]
                query = (
                    f"SELECT {', '.join(select_parts_with_bd)}\n"
                    f"FROM spans\n"
                    f"WHERE {' AND '.join(all_where)}\n"
                    f"GROUP BY time_bucket, breakdown_value\n"
                    f"ORDER BY time_bucket, breakdown_value"
                )
        else:
            query = (
                f"SELECT {', '.join(select_parts)}\n"
                f"FROM spans\n"
                f"WHERE {' AND '.join(all_where)}\n"
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
        """Build eval metric query against usage_apicalllog (central eval table).

        All eval executions (tracer, dataset, simulation, SDK, playground) write
        to APICallLog with source_id = eval_template_id. The score is stored in
        the JSON ``config`` field as ``config.output.output``.

        The eval table acts as a **hub** — breakdowns and filters from any
        connected source (traces, datasets, simulations, other evals) are
        resolved by dynamically JOINing the relevant tables via keys in the
        config JSON (trace_id, dataset_id, etc.).
        """
        # Accept both config_id (legacy) and name (new) as the template identifier
        eval_template_id = metric.get("config_id") or metric.get("name", "")
        output_type = (metric.get("output_type") or "SCORE").upper()

        from tracer.utils.eval_helpers import resolve_eval_template_id

        eval_template_id = resolve_eval_template_id(
            eval_template_id, organization_id=self.organization_id
        )

        params["eval_template_id"] = eval_template_id
        params["organization_id"] = self.organization_id
        params["workspace_id"] = self.workspace_id

        # --- Score extraction & aggregation ---
        # eval_score (Float64 MATERIALIZED): JSONExtractFloat on output.output.
        #   SCORE evals:     0.0 – 1.0 (reliable)
        #   PASS_FAIL evals: 1.0 when output is numeric, BUT 0.0 when output
        #                    is the string "Passed"/"Failed" (JSONExtractFloat
        #                    can't parse strings → returns 0).
        #
        # eval_output_str (String MATERIALIZED): raw string from output.output.
        #   SCORE:     "0.4", "1", "0.8"
        #   PASS_FAIL: "1", "Passed", "Failed", "0"  (both formats exist)
        #
        # Unified pass detection must check BOTH: numeric score >= 1.0 OR
        # string matches "Passed"/"Pass"/"True"/"true".
        _is_pass = (
            "(e.eval_score >= 1.0 OR e.eval_output_str IN "
            "('Passed', 'Pass', 'True', 'true'))"
        )
        _is_fail = (
            "(e.eval_score < 1.0 AND e.eval_output_str NOT IN "
            "('Passed', 'Pass', 'True', 'true'))"
        )
        # Unified score: 1.0 for pass (string or numeric), else eval_score
        _unified_score = f"if({_is_pass}, 1.0, e.eval_score)"

        _EVAL_AGGREGATIONS: Dict[str, str] = {
            "pass_rate": f"countIf({_is_pass}) / nullIf(count(), 0)",
            "fail_rate": f"countIf({_is_fail}) / nullIf(count(), 0)",
            "pass_count": f"countIf({_is_pass})",
            "fail_count": f"countIf({_is_fail})",
            "true_rate": f"countIf({_is_pass}) / nullIf(count(), 0)",
        }

        if aggregation in _EVAL_AGGREGATIONS:
            agg_expr = _EVAL_AGGREGATIONS[aggregation]
        else:
            # Standard numeric aggregations.
            if output_type in ("CHOICE", "CHOICES"):
                col_expr = "1.0"
            elif output_type == "PASS_FAIL":
                # Use unified score so string "Passed" → 1.0
                col_expr = _unified_score
            else:
                col_expr = "e.eval_score"
            agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        select_parts = [f"{bucket_fn}(e.created_at) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]
        select_parts.append(f"{agg_expr} AS value")

        # Scope to workspace when available, otherwise org
        if self.workspace_id:
            _scope_filter = "e.workspace_id = toUUID(%(workspace_id)s)"
        else:
            _scope_filter = "e.organization_id = toUUID(%(organization_id)s)"

        where_parts = [
            _scope_filter,
            "e._peerdb_is_deleted = 0",
            "e.status = 'success'",
            "e.source_id = %(eval_template_id)s",
            "e.created_at >= %(start_date)s",
            "e.created_at < %(end_date)s",
        ]

        # --- Dynamic JOINs ---
        # The eval hub can connect to spans (traces), datasets, simulations,
        # and other evals. We track which JOINs are needed and add them once.
        joins = []
        need_spans_join = False  # JOIN spans via trace_id
        need_eval_join = {}  # alias -> eval_template_id for cross-eval JOINs

        # Helper: use materialized column for trace_id
        _trace_id_expr = "e.eval_trace_id"

        # --- Resolve breakdowns ---
        if self.breakdowns:
            bd = self.breakdowns[0]
            bd_name = (bd.get("name") or bd.get("id") or "").lower()
            bd_type = bd.get("type", "system_metric")

            # Eval-native breakdowns (no JOIN needed)
            if bd_name in ("source", "eval_source"):
                bd_expr = "if(e.source = '', '(not set)', e.source)"

            elif bd_name == "dataset":
                # Use materialized column eval_dataset_id (pre-extracted at
                # insert time — avoids runtime JSON parsing on every row).
                # Name resolution happens in the view layer via PG lookup,
                # so we just return the UUID here.
                bd_expr = (
                    "if(e.eval_dataset_id = '', '(no dataset)', " "e.eval_dataset_id)"
                )

            # Trace dimension breakdowns → JOIN spans
            elif bd_name == "project":
                # Resolve trace_id → project_id via trace_dict.
                # Non-trace evals show "(no project)".
                _proj_uuid = (
                    f"dictGet('trace_dict', 'project_id', "
                    f"toUUIDOrZero({_trace_id_expr}))"
                )
                bd_expr = (
                    f"if({_trace_id_expr} != '' "
                    f"AND {_proj_uuid} != toUUID('00000000-0000-0000-0000-000000000000'), "
                    f"toString({_proj_uuid}), '(no project)')"
                )
            elif bd_name in SYSTEM_METRICS:
                # Known span column — JOIN spans to get the dimension
                need_spans_join = True
                _, span_col = SYSTEM_METRICS[bd_name]
                bd_expr = f"if(s.trace_id = '', '(not set)', toString(s.{span_col}))"

            elif bd_name in (
                "model",
                "status",
                "service_name",
                "span_kind",
                "provider",
                "session",
                "user",
                "tag",
                "prompt_name",
                "prompt_version",
                "prompt_label",
            ):
                need_spans_join = True
                # Map common names to spans columns
                _span_col_map = {
                    "model": "model",
                    "status": "status",
                    "service_name": "name",
                    "span_kind": "observation_type",
                    "provider": "provider",
                    "session": "trace_session_id",
                    "user": "end_user_id",
                    "tag": "tags",
                    "prompt_name": "prompt_name",
                    "prompt_version": "prompt_version",
                    "prompt_label": "prompt_label",
                }
                scol = _span_col_map.get(bd_name, bd_name)
                bd_expr = f"if(s.trace_id = '', '(not set)', toString(s.{scol}))"

            # Breakdown by eval metric
            elif bd_type == "eval_metric":
                ev_tid = bd.get("config_id") or bd.get("label_id") or bd_name
                ev_tid = resolve_eval_template_id(
                    ev_tid, organization_id=self.organization_id
                )
                bd_output_type = (
                    bd.get("output_type") or bd.get("outputType") or ""
                ).upper()

                # Same eval as the metric → use main table columns directly
                if ev_tid == eval_template_id:
                    if bd_output_type in ("PASS_FAIL", "CHOICE", "CHOICES"):
                        bd_expr = (
                            "if(e.eval_output_str = '', '(not set)', "
                            "e.eval_output_str)"
                        )
                    else:
                        bd_expr = (
                            "if(e.eval_score = 0, '(not set)', "
                            "toString(round(e.eval_score * 100)))"
                        )
                else:
                    # Different eval → cross-eval JOIN
                    ev_alias = "ev_bd"
                    params["_ev_bd_tid"] = ev_tid
                    need_eval_join[ev_alias] = "_ev_bd_tid"
                    if bd_output_type in ("PASS_FAIL", "CHOICE", "CHOICES"):
                        bd_expr = (
                            f"if({ev_alias}.id IS NULL OR "
                            f"{ev_alias}.eval_output_str = '', '(not set)', "
                            f"{ev_alias}.eval_output_str)"
                        )
                    else:
                        bd_expr = (
                            f"if({ev_alias}.id IS NULL, '(not set)', "
                            f"toString(round({ev_alias}.eval_score * 100)))"
                        )

            # Custom attribute
            elif bd_type == "custom_attribute":
                need_spans_join = True
                attr_key = _sanitize_attr_key(bd_name)
                bd_expr = f"if(s.span_attr_str['{attr_key}'] != '', s.span_attr_str['{attr_key}'], '(not set)')"

            else:
                bd_expr = "'(not set)'"

            select_parts.append(f"{bd_expr} AS breakdown_value")
            group_parts.append("breakdown_value")
            order_parts.append("breakdown_value")

        # --- Resolve filters (from any source) ---
        for i, f in enumerate(per_metric_filters + self.global_filters):
            f_type = f.get("metric_type") or f.get("type", "")
            f_name = f.get("metric_name") or f.get("name") or f.get("id", "")
            op = f.get("operator", "")
            val = f.get("value")
            op_symbol = _get_operator_symbol(op)
            if not op_symbol:
                continue

            val_key = f"_evf_{i}_val"

            if f_type == "system_metric" and f_name.lower() in SYSTEM_METRICS:
                # Trace dimension filter → JOIN spans
                need_spans_join = True
                _, span_col = SYSTEM_METRICS[f_name.lower()]
                where_parts.append(f"s.{span_col} {op_symbol} %({val_key})s")
                params[val_key] = _coerce_filter_value(val, op)

            elif f_type == "eval_metric":
                ev_tid = f_name
                ev_tid = resolve_eval_template_id(
                    ev_tid, organization_id=self.organization_id
                )
                f_out_type = (f.get("output_type") or "SCORE").upper()

                # Same eval as the metric → filter on main table directly
                if ev_tid == eval_template_id:
                    if f_out_type in ("PASS_FAIL", "CHOICE", "CHOICES"):
                        where_parts.append(
                            f"e.eval_output_str {op_symbol} %({val_key})s"
                        )
                        params[val_key] = val
                    else:
                        where_parts.append(f"e.eval_score {op_symbol} %({val_key})s")
                        params[val_key] = _coerce_filter_value(val, op)
                else:
                    # Different eval → cross-eval JOIN
                    ev_alias = f"ev_f{i}"
                    fkey = f"_evf_{i}_tid"
                    params[fkey] = ev_tid
                    need_eval_join[ev_alias] = fkey
                    if f_out_type in ("PASS_FAIL", "CHOICE", "CHOICES"):
                        ev_col = f"{ev_alias}.eval_output_str"
                        where_parts.append(f"{ev_col} {op_symbol} %({val_key})s")
                        params[val_key] = val
                    else:
                        ev_col = f"{ev_alias}.eval_score"
                        where_parts.append(f"{ev_col} {op_symbol} %({val_key})s")
                        params[val_key] = _coerce_filter_value(val, op)

            elif f_type == "custom_attribute":
                need_spans_join = True
                attr_key = _sanitize_attr_key(f_name)
                where_parts.append(
                    f"s.span_attr_str['{attr_key}'] {op_symbol} %({val_key})s"
                )
                params[val_key] = _coerce_filter_value(val, op)

        # --- Build JOINs ---
        if need_spans_join:
            joins.append(
                f"LEFT JOIN spans AS s ON s.trace_id = {_trace_id_expr} "
                f"AND s.parent_span_id = '' "
                f"AND s._peerdb_is_deleted = 0"
            )

        _join_scope = (
            f"AND {'{alias}'}.workspace_id = toUUID(%(workspace_id)s)"
            if self.workspace_id
            else f"AND {'{alias}'}.organization_id = toUUID(%(organization_id)s)"
        )
        for ev_alias, param_key in need_eval_join.items():
            # Cross-eval JOIN: match on same trace_id via materialized columns
            joins.append(
                f"LEFT JOIN usage_apicalllog AS {ev_alias} FINAL "
                f"ON {ev_alias}.eval_trace_id = {_trace_id_expr} "
                f"AND {ev_alias}.source_id = %({param_key})s "
                f"{_join_scope.format(alias=ev_alias)} "
                f"AND {ev_alias}.status = 'success' "
                f"AND {ev_alias}._peerdb_is_deleted = 0"
            )

        join_str = "\n".join(joins)

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM usage_apicalllog AS e FINAL\n"
            f"{join_str}\n"
            f"WHERE {' AND '.join(where_parts)}\n"
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
        # The metric "name" is the annotation label UUID
        label_id = metric.get("label_id") or metric.get("name", "")
        params["annotation_label_id"] = label_id

        # model_hub_score stores the value as a JSON string.
        # The extraction depends on annotation type:
        output_type = (
            metric.get("output_type") or metric.get("outputType") or ""
        ).lower()
        # If output_type missing, look it up from PG
        if not output_type and label_id:
            try:
                from model_hub.models.develop_annotations import AnnotationsLabels

                lbl = (
                    AnnotationsLabels.objects.filter(id=label_id)
                    .values_list("type", flat=True)
                    .first()
                )
                if lbl:
                    output_type = lbl.lower()
            except Exception:
                pass
        if output_type in ("categorical", "choice"):
            # Categorical: count rows (each row = one annotation)
            col_expr = "1"
            agg_expr = "count()"
        elif output_type in ("thumbs_up_down", "pass_fail"):
            # Boolean: percentage of "up" / "Passed"
            col_expr = "JSONExtractString(a.value, 'value')"
            agg_expr = f"countIf({col_expr} = 'up') * 100.0 / greatest(count(), 1)"
        elif output_type == "text":
            # Text: just count annotations
            col_expr = "1"
            agg_expr = "count()"
        else:
            # Numeric/star: average of the float value
            col_expr = "JSONExtractFloat(a.value, 'value')"
            agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        select_parts = [f"{bucket_fn}(a.created_at) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]
        select_parts.append(f"{agg_expr} AS value")

        # model_hub_score has no project_id — resolve via trace_dict
        where_parts = [
            "dictGet('trace_dict', 'project_id', a.trace_id) IN %(project_ids)s",
            "a._peerdb_is_deleted = 0",
            "a.created_at >= %(start_date)s",
            "a.created_at < %(end_date)s",
            "a.label_id = toUUID(%(annotation_label_id)s)",
        ]

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM model_hub_score AS a FINAL\n"
            f"WHERE {' AND '.join(where_parts)}\n"
            f"GROUP BY {', '.join(group_parts)}\n"
            f"ORDER BY {', '.join(order_parts)}"
        )
        return query, params

    # ------------------------------------------------------------------
    # Custom attribute metric
    # ------------------------------------------------------------------

    def _build_custom_attr_query(
        self,
        metric: dict,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: List[dict],
        params: dict,
    ) -> Tuple[str, dict]:
        attr_key = _sanitize_attr_key(metric.get("attribute_key", ""))
        attr_type = metric.get("attribute_type", "number")

        if attr_type == "number":
            col_expr = f"span_attr_num['{attr_key}']"
        else:
            col_expr = f"span_attr_str['{attr_key}']"

        agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        select_parts = [f"{bucket_fn}(start_time) AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]

        breakdown_expr = self._breakdown_select()
        if breakdown_expr:
            select_parts.append(f"{breakdown_expr} AS breakdown_value")
            group_parts.append("breakdown_value")
            order_parts.append("breakdown_value")

        select_parts.append(f"{agg_expr} AS value")

        where_clauses, params = self._build_where_clauses(
            "spans", "start_time", per_metric_filters, params
        )

        subquery_clauses = self._build_subquery_filters(
            self.global_filters + per_metric_filters, params, "ca_"
        )
        params.update(subquery_clauses[1])

        all_where = where_clauses
        if subquery_clauses[0]:
            all_where += subquery_clauses[0]

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM spans\n"
            f"WHERE {' AND '.join(all_where)}\n"
            f"GROUP BY {', '.join(group_parts)}\n"
            f"ORDER BY {', '.join(order_parts)}"
        )
        return query, params

    # ------------------------------------------------------------------
    # Build all queries
    # ------------------------------------------------------------------

    def build_all_queries(self) -> List[Tuple[str, dict, dict]]:
        """Build queries for all metrics.

        Returns:
            List of (sql, params, metric_info) tuples.
        """
        results = []
        for metric in self.metrics:
            sql, params = self.build_metric_query(metric)
            metric_info = {
                "id": metric.get("id", ""),
                "name": metric.get("display_name")
                or metric.get("displayName")
                or metric.get("name", ""),
                "type": metric.get("type", "system_metric"),
                "aggregation": metric.get("aggregation", "avg"),
            }
            results.append((sql, params, metric_info))
        return results

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    def format_results(
        self,
        metric_results: List[Tuple[dict, List[dict]]],
        project_name_map: Optional[Dict[str, str]] = None,
    ) -> dict:
        """Format raw ClickHouse results into the response format.

        Args:
            metric_results: List of (metric_info, rows) tuples where rows
                are dicts with ``time_bucket``, ``value``, and optionally
                ``breakdown_value`` keys.
            project_name_map: Optional mapping of project UUID strings to
                human-readable project names.

        Returns:
            Response dict with ``metrics``, ``time_range``, and ``granularity``.
        """
        start_date, end_date = self.parse_time_range()
        all_buckets = _generate_time_buckets(start_date, end_date, self.granularity)
        formatted_metrics = []

        # Check if any breakdown is by project (needs UUID→name resolution)
        has_project_breakdown = any(
            bd.get("name") == "project" for bd in self.breakdowns
        )

        for metric_info, rows in metric_results:
            metric_name = metric_info.get("name", "")
            unit = METRIC_UNITS.get(metric_name, "")

            # Group rows by breakdown value if present
            # Use a dict of {iso_timestamp: value} for easy merging
            series_data: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                breakdown_key = str(row.get("breakdown_value", "total"))
                # Resolve project UUID to name if breaking down by project
                if has_project_breakdown and project_name_map:
                    if " / " in breakdown_key:
                        # Multi-breakdown: resolve each segment independently
                        parts = breakdown_key.split(" / ")
                        parts = [project_name_map.get(p, p) for p in parts]
                        breakdown_key = " / ".join(parts)
                    else:
                        breakdown_key = project_name_map.get(
                            breakdown_key, breakdown_key
                        )
                if breakdown_key not in series_data:
                    series_data[breakdown_key] = {}
                ts = row.get("time_bucket", "")
                if hasattr(ts, "isoformat"):
                    # CH may return date or naive datetime; convert to
                    # timezone-aware datetime so keys match _generate_time_buckets
                    if isinstance(ts, date) and not isinstance(ts, datetime):
                        ts = datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)
                    elif hasattr(ts, "tzinfo") and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    ts = ts.isoformat()
                val = row.get("value")
                if isinstance(val, float):
                    val = round(val, 6)
                series_data[breakdown_key][ts] = val

            if not series_data:
                series_data["total"] = {}

            # Limit breakdown series (frontend shows top 10 in chart,
            # rest available via legend toggle)
            MAX_SERIES = 100
            if len(series_data) > MAX_SERIES and "total" not in series_data:
                ranked = sorted(
                    series_data.items(),
                    key=lambda kv: sum(v for v in kv[1].values() if v is not None),
                    reverse=True,
                )[:MAX_SERIES]
                series_data = dict(ranked)

            # Fill in missing time buckets with null so consumers can
            # distinguish missing values from real zeros.
            series = []
            for name in sorted(series_data.keys()):
                data_map = series_data[name]
                filled = []
                for bucket_ts in all_buckets:
                    filled.append(
                        {
                            "timestamp": bucket_ts,
                            "value": data_map[bucket_ts]
                            if bucket_ts in data_map
                            else None,
                        }
                    )
                series.append({"name": name, "data": filled})

            formatted_metrics.append(
                {
                    "id": metric_info.get("id", ""),
                    "name": metric_name,
                    "aggregation": metric_info.get("aggregation", "avg"),
                    "unit": unit,
                    "series": series,
                }
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

    # System metric breakdown column map
    _BREAKDOWN_COL_MAP = {
        "project": "toString(project_id)",
        "model": "model",
        "status": "status",
        "service_name": "trace_name",
        "span_kind": "observation_type",
        "provider": "provider",
        "session": "toString(trace_session_id)",
        "user": "dictGetOrDefault('enduser_dict', 'user_id', end_user_id, toString(end_user_id))",
        "user_id_type": "dictGetOrDefault('enduser_dict', 'user_id_type', end_user_id, '')",
        "prompt_name": "dictGetOrDefault('prompt_dict', 'prompt_name', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), '')",
        "prompt_version": "concat(dictGetOrDefault('prompt_dict', 'prompt_name', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), ''), ' v', dictGetOrDefault('prompt_dict', 'template_version', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), ''))",
        "prompt_label": "dictGetOrDefault('prompt_label_dict', 'name', ifNull(prompt_label_id, toUUID('00000000-0000-0000-0000-000000000000')), '')",
        "tag": "arrayJoin(JSONExtract(tags, 'Array(String)'))",
    }

    def _resolve_all_breakdowns(self, params: dict):
        """Resolve all breakdowns into a list of {type, expr, join_clause} dicts.

        For system/custom_attribute breakdowns: expr is a column expression on spans.
        For annotation breakdowns: expr is a value extraction + join_clause for LEFT JOIN.

        Returns:
            List of breakdown info dicts. Empty list if no breakdowns.
        """
        result = []
        ann_idx = 0
        for bd in self.breakdowns:
            bd_type = bd.get("type", "system_metric")
            bd_name = bd.get("name", "")
            bd_source = bd.get("source", "traces")

            if bd_source not in ("traces", "both", ""):
                continue

            if bd_type == "system_metric":
                if bd_name in self._BREAKDOWN_COL_MAP:
                    result.append(
                        {
                            "type": "column",
                            "expr": self._BREAKDOWN_COL_MAP[bd_name],
                            "join": None,
                        }
                    )
                elif bd_name in SYSTEM_METRICS:
                    _, col_expr = SYSTEM_METRICS[bd_name]
                    result.append({"type": "column", "expr": col_expr, "join": None})

            elif bd_type == "custom_attribute":
                safe_name = _sanitize_attr_key(bd_name)
                attr_type = bd.get("attribute_type", "string")
                expr = (
                    f"span_attr_num['{safe_name}']"
                    if attr_type == "number"
                    else f"span_attr_str['{safe_name}']"
                )
                result.append({"type": "column", "expr": expr, "join": None})

            elif bd_type == "annotation_metric":
                label_id = bd.get("label_id") or bd_name
                output_type = (
                    bd.get("output_type") or bd.get("outputType") or ""
                ).lower()
                if not output_type and label_id:
                    try:
                        from model_hub.models.develop_annotations import (
                            AnnotationsLabels,
                        )

                        lbl = (
                            AnnotationsLabels.objects.filter(id=label_id)
                            .values_list("type", flat=True)
                            .first()
                        )
                        if lbl:
                            output_type = lbl.lower()
                    except Exception:
                        pass

                alias = f"ann{ann_idx}"
                param_key = f"_ann_bd_label_{ann_idx}"
                params[param_key] = label_id
                ann_idx += 1

                # Value extraction based on type
                if output_type in ("categorical", "choice"):
                    val_expr = f"arrayJoin(if({alias}.trace_id IS NULL, ['(not set)'], JSONExtract({alias}.value, 'selected', 'Array(String)')))"
                elif output_type in ("thumbs_up_down", "pass_fail"):
                    val_expr = f"if({alias}.trace_id IS NULL, '(not set)', JSONExtractString({alias}.value, 'value'))"
                elif output_type == "text":
                    val_expr = f"if({alias}.trace_id IS NULL, '(not set)', JSONExtractString({alias}.value, 'text'))"
                elif output_type in ("numeric", "star"):
                    val_expr = f"if({alias}.trace_id IS NULL, '(not set)', toString(round(JSONExtractFloat({alias}.value, 'value'), 1)))"
                else:
                    val_expr = f"if({alias}.trace_id IS NULL, '(not set)', toString(JSONExtractFloat({alias}.value, 'value')))"

                join_clause = (
                    f"LEFT JOIN model_hub_score AS {alias} "
                    f"ON toString({alias}.trace_id) = s.trace_id "
                    f"AND {alias}.label_id = toUUID(%({param_key})s) "
                    f"AND {alias}._peerdb_is_deleted = 0"
                )
                result.append(
                    {"type": "annotation", "expr": val_expr, "join": join_clause}
                )

            elif bd_type == "eval_metric":
                eval_template_id = bd.get("config_id") or bd.get("label_id") or bd_name
                output_type = (
                    bd.get("output_type") or bd.get("outputType") or ""
                ).upper()
                # Auto-detect output type from PG if missing
                if not output_type and eval_template_id:
                    try:
                        from model_hub.models.evals_metric import EvalTemplate

                        et = EvalTemplate.objects.filter(id=eval_template_id).first()
                        if et:
                            output_type = (
                                (et.config or {})
                                .get("output", "SCORE")
                                .upper()
                                .replace("/", "_")
                            )
                    except Exception:
                        pass

                alias = f"ev{ann_idx}"
                param_key = f"_ev_bd_cfg_{ann_idx}"
                params[param_key] = eval_template_id
                ann_idx += 1

                # Use materialized columns for fast extraction
                if output_type == "PASS_FAIL":
                    val_expr = (
                        f"if({alias}.id IS NULL, '(not set)', "
                        f"if({alias}.eval_output_str = 'Passed', 'Pass', 'Fail'))"
                    )
                elif output_type in ("CHOICE", "CHOICES"):
                    val_expr = (
                        f"if({alias}.id IS NULL, '(not set)', "
                        f"{alias}.eval_output_str)"
                    )
                else:
                    # SCORE: show as percentage
                    val_expr = (
                        f"if({alias}.id IS NULL, '(not set)', "
                        f"toString(round({alias}.eval_score * 100)))"
                    )

                join_clause = (
                    f"LEFT JOIN usage_apicalllog AS {alias} FINAL "
                    f"ON {alias}.eval_trace_id = s.trace_id "
                    f"AND {alias}.source_id = %({param_key})s "
                    f"AND {alias}.status = 'success' "
                    f"AND {alias}._peerdb_is_deleted = 0"
                )
                result.append(
                    {"type": "annotation", "expr": val_expr, "join": join_clause}
                )

        return result

    def _breakdown_select(self) -> Optional[str]:
        """Return the SQL expression for the first breakdown, or None.
        Kept for backward compat — delegates to _resolve_all_breakdowns for single breakdown.
        """
        if not self.breakdowns:
            return None
        # For single-breakdown compat, just check first
        breakdowns = self._resolve_all_breakdowns({})
        if not breakdowns:
            return None
        bd = breakdowns[0]
        if bd["type"] == "annotation":
            return "__ANNOTATION_BREAKDOWN__"
        return bd["expr"]

    def _build_where_clauses(
        self,
        table: str,
        time_col: str,
        per_metric_filters: List[dict],
        params: dict,
    ) -> Tuple[List[str], dict]:
        """Build base WHERE clauses for spans-based queries."""
        clauses = [
            "project_id IN %(project_ids)s",
            "_peerdb_is_deleted = 0",
            f"{time_col} >= %(start_date)s",
            f"{time_col} < %(end_date)s",
        ]

        # Apply global + per-metric system_metric filters directly
        # For string-comparable system metrics, use toString() to avoid UUID parse errors
        _STRING_FILTER_COL = {
            "project": "toString(project_id)",
            "status": "status",
            "model": "model",
            "service_name": "trace_name",
            "span_kind": "observation_type",
            "provider": "provider",
            "session": "toString(trace_session_id)",
            "user": "dictGetOrDefault('enduser_dict', 'user_id', end_user_id, toString(end_user_id))",
            "user_id_type": "dictGetOrDefault('enduser_dict', 'user_id_type', end_user_id, '')",
            "prompt_name": "dictGetOrDefault('prompt_dict', 'prompt_name', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), '')",
            "prompt_version": "concat(dictGetOrDefault('prompt_dict', 'prompt_name', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), ''), ' v', dictGetOrDefault('prompt_dict', 'template_version', ifNull(prompt_version_id, toUUID('00000000-0000-0000-0000-000000000000')), ''))",
            "prompt_label": "dictGetOrDefault('prompt_label_dict', 'name', ifNull(prompt_label_id, toUUID('00000000-0000-0000-0000-000000000000')), '')",
            "tag": "arrayJoin(JSONExtract(tags, 'Array(String)'))",
        }
        all_filters = self.global_filters + per_metric_filters
        # Skip filters that belong to other sources (e.g. simulation filters in trace queries)
        all_filters = [
            f for f in all_filters if f.get("source", "traces") in ("traces", "")
        ]
        idx = 0
        for f in all_filters:
            f_type = f.get("metric_type", "")
            if f_type == "system_metric":
                f_name = (f.get("metric_name", "") or "").lower()
                op = f.get("operator", "")
                val = f.get("value")
                # Use string-safe column for non-numeric metrics
                if f_name in _STRING_FILTER_COL:
                    col = _STRING_FILTER_COL[f_name]
                elif f_name in SYSTEM_METRICS:
                    _, col = SYSTEM_METRICS[f_name]
                else:
                    # Unknown filter metric — skip to prevent SQL injection
                    logger.warning("Skipping unknown filter metric: %s", f_name)
                    continue

                # No-value operators
                if op in ("is_set", "is_not_set", "is_numeric", "is_not_numeric"):
                    op_tpl = FILTER_OPERATORS.get(op)
                    if op_tpl:
                        clauses.append(f"{col} {op_tpl}")
                    continue

                # Skip filters with empty values
                if val is None or val == "" or val == []:
                    continue

                # Between operators need two params
                if op in ("between", "not_between"):
                    if isinstance(val, list) and len(val) == 2:
                        lo_key = f"f_{idx}_lo"
                        hi_key = f"f_{idx}_hi"
                        params[lo_key] = _coerce_filter_value(val[0], "equal_to")
                        params[hi_key] = _coerce_filter_value(val[1], "equal_to")
                        neg = "NOT " if op == "not_between" else ""
                        clauses.append(
                            f"{col} {neg}BETWEEN %({lo_key})s AND %({hi_key})s"
                        )
                        idx += 1
                    continue

                op_tpl = FILTER_OPERATORS.get(op)
                if op_tpl:
                    param_key = f"f_{idx}_val"
                    clause = f"{col} {op_tpl.format(prefix='f_', idx=idx)}"
                    params[param_key] = _coerce_filter_value(val, op)
                    clauses.append(clause)
                    idx += 1
            elif f_type == "custom_attribute":
                f_name = _sanitize_attr_key(f.get("metric_name", ""))
                op = f.get("operator", "")
                val = f.get("value")
                attr_type = f.get("attribute_type", "string")
                if attr_type == "number":
                    col = f"span_attr_num['{f_name}']"
                else:
                    col = f"span_attr_str['{f_name}']"

                if op in ("is_set", "is_not_set", "is_numeric", "is_not_numeric"):
                    op_tpl = FILTER_OPERATORS.get(op)
                    if op_tpl:
                        clauses.append(f"{col} {op_tpl}")
                    continue

                if val is None or val == "" or val == []:
                    continue

                if op in ("between", "not_between"):
                    if isinstance(val, list) and len(val) == 2:
                        lo_key = f"f_{idx}_lo"
                        hi_key = f"f_{idx}_hi"
                        params[lo_key] = _coerce_filter_value(val[0], "equal_to")
                        params[hi_key] = _coerce_filter_value(val[1], "equal_to")
                        neg = "NOT " if op == "not_between" else ""
                        clauses.append(
                            f"{col} {neg}BETWEEN %({lo_key})s AND %({hi_key})s"
                        )
                        idx += 1
                    continue

                op_tpl = FILTER_OPERATORS.get(op)
                if op_tpl:
                    param_key = f"f_{idx}_val"
                    clause = f"{col} {op_tpl.format(prefix='f_', idx=idx)}"
                    params[param_key] = _coerce_filter_value(val, op)
                    clauses.append(clause)
                    idx += 1

        return clauses, params

    def _build_subquery_filters(
        self,
        filters: List[dict],
        params: dict,
        prefix: str,
    ) -> Tuple[List[str], dict]:
        """Build IN-subquery clauses for eval/annotation metric filters on spans."""
        clauses: List[str] = []
        extra_params: Dict[str, Any] = {}
        idx = 0

        for f in filters:
            f_type = f.get("metric_type", "")
            op = f.get("operator", "")
            val = f.get("value")
            op_symbol = _get_operator_symbol(op)
            if not op_symbol:
                continue

            val_key = f"{prefix}{idx}_val"

            if f_type == "eval_metric":
                eval_id_key = f"{prefix}eval_id_{idx}"
                eval_template_id = f.get("metric_name", "")

                # Resolve name to UUID if needed
                from tracer.utils.eval_helpers import resolve_eval_template_id

                eval_template_id = resolve_eval_template_id(
                    eval_template_id, organization_id=self.organization_id
                )

                output_type = (f.get("output_type") or "SCORE").upper()
                # config is double-encoded
                if output_type == "PASS_FAIL":
                    eval_col = "if(eval_output_str = 'Passed', 1.0, 0.0)"
                else:
                    eval_col = "eval_score"

                scope_key = f"{prefix}scope_id_{idx}"
                if self.workspace_id:
                    _sub_scope = f"AND workspace_id = toUUID(%({scope_key})s)"
                    _sub_scope_val = self.workspace_id
                else:
                    _sub_scope = f"AND organization_id = toUUID(%({scope_key})s)"
                    _sub_scope_val = self.organization_id
                subquery = (
                    f"trace_id IN ("
                    f"SELECT eval_trace_id "
                    f"FROM usage_apicalllog FINAL "
                    f"WHERE source_id = %({eval_id_key})s "
                    f"{_sub_scope} "
                    f"AND status = 'success' "
                    f"AND {eval_col} {op_symbol} %({val_key})s "
                    f"AND _peerdb_is_deleted = 0"
                    f")"
                )
                clauses.append(subquery)
                extra_params[eval_id_key] = eval_template_id
                extra_params[scope_key] = _sub_scope_val
                extra_params[val_key] = _coerce_filter_value(val, op)
                idx += 1

            elif f_type == "annotation_metric":
                label_id_key = f"{prefix}label_id_{idx}"
                label_id = f.get("metric_name", "")
                ann_org_key = f"{prefix}ann_org_id_{idx}"

                subquery = (
                    f"trace_id IN ("
                    f"SELECT toString(trace_id) FROM model_hub_score FINAL "
                    f"WHERE label_id = toUUID(%({label_id_key})s) "
                    f"AND organization_id = toUUID(%({ann_org_key})s) "
                    f"AND JSONExtractFloat(value, 'value') {op_symbol} %({val_key})s "
                    f"AND _peerdb_is_deleted = 0"
                    f")"
                )
                clauses.append(subquery)
                extra_params[label_id_key] = label_id
                extra_params[ann_org_key] = self.organization_id
                extra_params[val_key] = _coerce_filter_value(val, op)
                idx += 1

        return clauses, extra_params


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_OPERATOR_SYMBOLS: Dict[str, str] = {
    "less_than": "<",
    "greater_than": ">",
    "equal_to": "=",
    "not_equal_to": "!=",
    "greater_than_or_equal": ">=",
    "less_than_or_equal": "<=",
    "contains": "IN",
    "not_contains": "NOT IN",
    "str_contains": "LIKE",
    "str_not_contains": "NOT LIKE",
}


def _generate_time_buckets(
    start: datetime, end: datetime, granularity: str
) -> List[str]:
    """Generate all time bucket ISO strings between *start* and *end*.

    Mirrors the ClickHouse ``toStartOf*`` bucketing so that the response
    includes every expected bucket — even those with no data (filled with null).
    """
    buckets: List[str] = []
    if granularity == "minute":
        cur = start.replace(second=0, microsecond=0)
        delta = timedelta(minutes=1)
    elif granularity == "hour":
        cur = start.replace(minute=0, second=0, microsecond=0)
        delta = timedelta(hours=1)
    elif granularity == "week":
        # toMonday — align to Monday
        cur = start - timedelta(days=start.weekday())
        cur = cur.replace(hour=0, minute=0, second=0, microsecond=0)
        delta = timedelta(weeks=1)
    elif granularity == "month":
        cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        delta = None  # handled specially
    elif granularity == "year":
        cur = start.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        delta = None  # handled specially
    else:
        # Default: day
        cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
        delta = timedelta(days=1)

    if granularity == "month":
        while cur <= end:
            buckets.append(cur.isoformat())
            # Advance to next month
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1)
            else:
                cur = cur.replace(month=cur.month + 1)
    elif granularity == "year":
        while cur <= end:
            buckets.append(cur.isoformat())
            cur = cur.replace(year=cur.year + 1)
    else:
        while cur <= end:
            buckets.append(cur.isoformat())
            cur += delta

    return buckets


def _get_operator_symbol(op: str) -> Optional[str]:
    """Return the SQL operator symbol for a filter operator name."""
    return _OPERATOR_SYMBOLS.get(op)


def _parse_dt(val: Any) -> datetime:
    """Parse a datetime from string or return as-is with UTC timezone.

    Always returns a timezone-aware (UTC) datetime so that callers
    produce consistent isoformat strings (with ``+00:00`` suffix).
    """
    dt: Optional[datetime] = None
    if isinstance(val, datetime):
        dt = val
    elif isinstance(val, str):
        cleaned = val.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(cleaned)
        except (ValueError, AttributeError):
            pass
        if dt is None:
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    dt = datetime.strptime(val, fmt)
                    break
                except ValueError:
                    continue
    if dt is None:
        return datetime.now(timezone.utc)
    # Ensure timezone-aware (UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _coerce_filter_value(val: Any, operator: str) -> Any:
    """Coerce a filter value to the appropriate Python type for ClickHouse params."""
    if operator in ("contains", "not_contains"):
        if isinstance(val, list):
            return val
        return [val]
    if operator in ("str_contains", "str_not_contains"):
        s = str(val) if val else ""
        s = s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{s}%"
    if isinstance(val, str):
        try:
            return float(val)
        except ValueError:
            return val
    return val
