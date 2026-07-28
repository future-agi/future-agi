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
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tracer.services.clickhouse.query_builders.expressions import (
    annotation_numeric_value_expr,
)
from tracer.services.clickhouse.v2.id_remap_sql import (
    remap_left_join,
    resolved_id_expr,
)

logger = logging.getLogger(__name__)

# Allowed characters for ClickHouse map keys: alphanumeric, dots, underscores, hyphens
_SAFE_ATTR_KEY_RE = re.compile(r"^[a-zA-Z0-9._\-]+$")


def _sanitize_attr_key(key: str) -> str:
    """Validate an attribute key is safe for use in ClickHouse map access expressions."""
    if not key or not _SAFE_ATTR_KEY_RE.match(key):
        raise ValueError(f"Invalid attribute key: {key!r}")
    return key


def _snap_to_hour(dt: datetime) -> datetime:
    """Truncate a datetime to the hour (ClickHouse ``toStartOfHour``)."""
    return dt.replace(minute=0, second=0, microsecond=0)


def _bucket_expr(bucket_fn: str, column: str) -> str:
    return f"{bucket_fn}({column}, %(timezone)s)"


# ---------------------------------------------------------------------------
# Metric resolution tables
# ---------------------------------------------------------------------------

SYSTEM_METRICS: dict[str, tuple[str, str]] = {
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
    "session_count": (
        "spans",
        "nullIf(trace_session_id, toUUID('00000000-0000-0000-0000-000000000000'))",
    ),
    "user_count": (
        "spans",
        # dictGetOrDefault cannot take NULL keys; keep both branches as String.
        "if(end_user_id IS NULL "
        "OR end_user_id = toUUID('00000000-0000-0000-0000-000000000000'), "
        "NULL, "
        "dictGetOrDefault("
        "'end_users_dict', 'user_id', "
        "assumeNotNull(end_user_id), "
        "toString(assumeNotNull(end_user_id))))",
    ),
    "trace_count": ("spans", "trace_id"),
    "span_count": ("spans", "id"),
    # String dimensions (for breakdown/filter)
    "model": ("spans", "model"),
    "status": ("spans", "status"),
    "service_name": ("spans", "service_name"),
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

METRIC_UNITS: dict[str, str] = {
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

# Metrics whose column expression emits a 0/1 indicator per row. The
# averaging aggregations get rescaled to a percentage at query time via
# ``rescale_rate_to_percent`` so the result matches the ``%`` unit.
_RATE_INDICATOR_METRICS = frozenset({"error_rate"})

# Covered by dashboard_attr_rollup. Adding one: extend the MV's ARRAY JOIN list too.
_ROLLUP_COVERED_ATTRS = frozenset({"final_status", "country"})

# Rollup is hour-resolution; sub-hour granularities keep the spans scan.
_ROLLUP_GRANULARITIES = frozenset({"hour", "day", "week", "month", "year"})

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

# Aggregations that produce an "average-like" result (mean, median, any
# percentile). Rate metrics that store a 0/1 indicator per row need their
# averaging result multiplied by 100 so the value matches the declared
# ``%`` unit. sum/count/min/max are intentionally excluded — for a 0/1
# indicator they keep their natural meaning (count of matching rows for
# sum/count; bounded 0/1 for min/max).
AVERAGING_AGGREGATIONS = frozenset(
    {"avg", "median", "p25", "p50", "p75", "p90", "p95", "p99"}
)


def _eval_source_bucket_expr(exclude: str) -> str:
    """Map eval source values to fallback labels for project/dataset breakdowns."""
    buckets: list[tuple[str, str]] = [
        ("tracer", "(trace)"),
        ("feedback", "(feedback)"),
        ("tracer_composite", "(composite)"),
        ("dataset_evaluation", "(dataset)"),
        ("experiment", "(experiment)"),
        ("prompt_template", "(prompt)"),
        ("eval_playground", "(playground)"),
        ("eval_playground_test", "(playground)"),
        ("standalone_v2", "(sdk)"),
        ("simulate", "(simulation)"),
        ("simulate_tool_evaluation", "(simulation)"),
        ("voice_call", "(simulation)"),
        ("text_call", "(simulation)"),
        ("fix_your_agent", "(fix-your-agent)"),
        ("trace_error_analysis", "(error-analysis)"),
        ("error_localizer", "(error-analysis)"),
        ("run_prompt_improve", "(prompt-improve)"),
        ("composite_eval", "(composite)"),
        ("composite_eval_adhoc", "(composite)"),
        ("composite_eval_dataset", "(composite)"),
    ]
    excluded_self = {
        "project": {"tracer"},
        "dataset": {"dataset_evaluation"},
    }.get(exclude, set())
    parts = ["multiIf("]
    for source, label in buckets:
        if source in excluded_self:
            continue
        parts.append(f"e.source = '{source}', '{label}', ")
    parts.append("e.source = '', '(unknown)', ")
    parts.append(f"'(no {exclude})')")
    return "".join(parts)


def _filter_predicate(
    expression: str,
    operator: str,
    value: Any,
    param_prefix: str,
    params: dict[str, Any],
) -> str | None:
    if operator == "is_set":
        return f"({expression} IS NOT NULL AND toString({expression}) != '')"
    if operator == "is_not_set":
        return f"({expression} IS NULL OR toString({expression}) = '')"
    if operator in ("between", "not_between"):
        if not isinstance(value, list) or len(value) != 2:
            return None
        params[f"{param_prefix}_lo"] = _coerce_filter_value(value[0], "equal_to")
        params[f"{param_prefix}_hi"] = _coerce_filter_value(value[1], "equal_to")
        negation = "NOT " if operator == "not_between" else ""
        return (
            f"{expression} {negation}BETWEEN %({param_prefix}_lo)s "
            f"AND %({param_prefix}_hi)s"
        )
    symbol = _get_operator_symbol(operator)
    if not symbol or value in (None, "", []):
        return None
    params[f"{param_prefix}_val"] = _coerce_filter_value(value, operator)
    return f"{expression} {symbol} %({param_prefix}_val)s"


def _annotation_entity_key(alias: str) -> str:
    return (
        "multiIf("
        f"{alias}.observation_span_id IS NOT NULL AND {alias}.observation_span_id != '', "
        f"concat('s:', {alias}.observation_span_id), "
        f"{alias}.trace_id IS NOT NULL, concat('t:', toString({alias}.trace_id)), "
        f"{alias}.trace_session_id IS NOT NULL, "
        f"concat('ss:', toString({alias}.trace_session_id)), "
        f"{alias}.project_id IS NOT NULL, concat('p:', toString({alias}.project_id)), "
        f"concat('id:', toString({alias}.id)))"
    )


def _annotation_value_expr(alias: str, output_type: str) -> str:
    normalized = output_type.lower()
    if normalized in ("categorical", "choice", "str_list"):
        return f"arrayJoin(JSONExtract({alias}.value, 'selected', 'Array(String)'))"
    if normalized in ("thumbs_up_down", "boolean", "bool"):
        return f"JSONExtractString({alias}.value, 'value')"
    if normalized in ("text", "string"):
        return (
            f"coalesce(nullIf(JSONExtractString({alias}.value, 'text'), ''), "
            f"JSONExtractString({alias}.value, 'value'))"
        )
    return annotation_numeric_value_expr(alias=alias, nullable=True)


def rescale_rate_to_percent(agg_expr: str, aggregation: str) -> str:
    """Wrap *agg_expr* in ``(... ) * 100`` for averaging aggregations.

    Used by metrics whose column expression emits a 0/1 indicator per
    row (``error_rate``, ``cell_error_rate``, ``success_rate``,
    ``failure_rate``) so widgets that render them with a ``%`` suffix
    show ``42%`` rather than ``0.42%``. Non-averaging aggregations are
    returned unchanged so e.g. ``sum(failure_rate)`` still reports a
    failure count.
    """
    if aggregation in AVERAGING_AGGREGATIONS:
        return f"({agg_expr}) * 100"
    return agg_expr


AGGREGATIONS: dict[str, str] = {
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

# Aggregations that require a numeric operand. ClickHouse raises "Illegal type
# String of argument for aggregate function ..." when these are applied to a
# text column (e.g. a string custom attribute). ``count`` / ``count_distinct``
# work on any type, so they are NOT listed here.
_NUMERIC_ONLY_AGGREGATIONS = frozenset(
    {"avg", "sum", "median", "p25", "p50", "p75", "p90", "p95", "p99"}
)


class InvalidMetricCombinationError(ValueError):
    """A metric's aggregation cannot be applied to its value type.

    e.g. averaging a text custom attribute. The message is user-facing — callers
    surface it per-widget so one nonsensical metric does not fail the whole
    dashboard query.
    """


FILTER_OPERATORS: dict[str, str] = {
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

PRESET_RANGES: dict[str, timedelta | None] = {
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

GRANULARITY_TO_CH: dict[str, str] = {
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
    for col in ("project_id", "_peerdb_is_deleted", "start_time", "parent_span_id"):
        # Match bare column name not preceded by . or inside %(...)
        clause = re.sub(
            rf"(?<!\.)(?<!%\()(?<!\w){col}(?!\w)(?!s\))",
            f"s.{col}",
            clause,
        )
    return clause


_ID_RESOLVED_NAMES = frozenset(
    {
        "user_count",
        "session_count",
        "user",
        "session",
        "user_id_type",
    }
)


# ClickHouse omits materialized columns from sp.*. The current dashboard
# dimensions only use stored columns, so no re-projection is needed.
_MATERIALIZED_DASHBOARD_COLS: tuple[str, ...] = ()


def _resolved_spans_source(alias: str | None = None) -> str:
    """Return a spans source with user/session ids resolved through id_remap."""
    out_alias = alias or "spans"
    eu_join = remap_left_join("sp.end_user_id", "end_user_id_remap", "eu_remap")
    ts_join = remap_left_join(
        "sp.trace_session_id", "trace_session_id_remap", "ts_remap"
    )
    resolved_eu = resolved_id_expr("sp.end_user_id", "eu_remap")
    resolved_ts = resolved_id_expr("sp.trace_session_id", "ts_remap")
    materialized = "".join(f"sp.{c} AS {c}, " for c in _MATERIALIZED_DASHBOARD_COLS)
    return (
        "(SELECT sp.* EXCEPT (end_user_id, trace_session_id), "
        f"{materialized}"
        f"{resolved_eu} AS end_user_id, "
        f"{resolved_ts} AS trace_session_id "
        f"FROM spans AS sp {eu_join} {ts_join}) AS {out_alias}"
    )


class DashboardQueryBuilder:
    """Translates a widget query_config into ClickHouse SQL.

    Does NOT extend BaseQueryBuilder because it operates on multiple
    project_ids and builds multiple queries (one per metric).
    """

    # dashboard_attr_rollup lives only in the v2 schema; the v2 subclass flips
    # this True. Base/v1 never routes to the rollup (fail-closed: missing table).
    _attr_rollup_available: bool = False

    def __init__(self, query_config: dict) -> None:
        self.config = query_config
        self.project_ids = query_config.get("project_ids", [])
        self.organization_id = query_config.get("organization_id", "")
        self.workspace_id = query_config.get("workspace_id", "")
        self.all_workspace_projects = query_config.get("all_workspace_projects", False)
        self.granularity = query_config.get("granularity", "day")
        timezone_name = query_config.get("timezone") or "UTC"
        try:
            self.timezone = ZoneInfo(timezone_name)
            self.timezone_name = timezone_name
        except (ValueError, ZoneInfoNotFoundError):
            self.timezone = UTC
            self.timezone_name = "UTC"
        self.metrics = query_config.get("metrics", [])
        self.global_filters = query_config.get("filters", [])
        self.breakdowns = query_config.get("breakdowns", [])

    # ------------------------------------------------------------------
    # Time range
    # ------------------------------------------------------------------

    def parse_time_range(self) -> tuple[datetime, datetime]:
        """Parse time range from preset or custom start/end."""
        tr = self.config.get("time_range") or self.config.get("timeRange") or {}
        preset = tr.get("preset")
        custom_start = tr.get("custom_start")
        custom_end = tr.get("custom_end")

        now = datetime.now(self.timezone)

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

    def build_metric_query(self, metric: dict) -> tuple[str, dict]:
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

        params: dict[str, Any] = {
            "project_ids": self.project_ids,
            "start_date": start_date,
            "end_date": end_date,
            "timezone": self.timezone_name,
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

    def _names_reference_id(self, *names: str | None) -> bool:
        return any(
            (n or "").lower() in _ID_RESOLVED_NAMES for n in names if n is not None
        )

    def _query_references_id(
        self, metric_name: str | None, per_metric_filters: list[dict]
    ) -> bool:
        if self._names_reference_id(metric_name):
            return True
        for bd in self.breakdowns:
            if bd.get("type", "system_metric") != "system_metric":
                continue
            if self._names_reference_id(bd.get("name"), bd.get("id")):
                return True
        for f in self.global_filters + (per_metric_filters or []):
            f_type = f.get("metric_type") or f.get("type", "")
            if f_type and f_type != "system_metric":
                continue
            if self._names_reference_id(
                f.get("metric_name"), f.get("name"), f.get("id")
            ):
                return True
        return False

    def _spans_source(
        self, metric_name: str | None, per_metric_filters: list[dict], alias: str
    ) -> str:
        """Return the spans FROM/JOIN source for the given alias — the id-remap
        resolved derived table when the query references an id, else the bare
        table (so id-free metrics stay byte-identical with zero added joins).

        ``alias`` is ``"spans"`` for the flat ``FROM spans`` shapes (the derived
        table is aliased back to ``spans``) or ``"s"`` for the JOINed shapes.
        """
        if self._query_references_id(metric_name, per_metric_filters):
            return _resolved_spans_source(None if alias == "spans" else alias)
        return "spans" if alias == "spans" else f"spans AS {alias}"

    def _trace_spans_source(
        self, metric_name: str | None, per_metric_filters: list[dict], alias: str
    ) -> str:
        source = self._spans_source(metric_name, per_metric_filters, "trace_spans")
        predicates = ["(parent_span_id IS NULL OR parent_span_id = '')"]
        if metric_name == "latency":
            predicates.extend(
                [
                    "project_id IN %(project_ids)s",
                    "start_time >= %(start_date)s",
                    "start_time < %(end_date)s",
                    "created_at >= %(start_date)s - INTERVAL 1 DAY",
                ]
            )
        return (
            f"(SELECT * FROM {source} WHERE {' AND '.join(predicates)} "
            "ORDER BY _peerdb_version DESC LIMIT 1 BY trace_id) "
            f"AS {alias}"
        )

    # ------------------------------------------------------------------
    # System metric
    # ------------------------------------------------------------------

    def _attr_rollup_window_covered(self, start_date: datetime) -> bool:
        """True only when the rollup flag is on AND the requested window starts
        within the backfilled-and-covered range — fail-closed on a fresh deploy
        (off until ops backfills the rollup and sets the coverage date)."""
        from django.conf import settings

        if not getattr(settings, "DASHBOARD_ATTR_ROLLUP_ENABLED", False):
            return False
        covered_since = getattr(settings, "DASHBOARD_ATTR_ROLLUP_COVERED_SINCE", None)
        if covered_since is None:
            return False
        if covered_since.tzinfo is None:
            covered_since = covered_since.replace(tzinfo=UTC)
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=UTC)
        return start_date >= covered_since

    def _should_use_rollup(
        self,
        metric_name: str,
        aggregation: str,
        single_bd: dict | None,
        per_metric_filters: list[dict],
        start_date: datetime,
    ) -> bool:
        """True only for the covered latency-breakdown shape on a v2 build with the
        rollup enabled and the window inside coverage — fail-closed everywhere else."""
        return (
            self._attr_rollup_available
            and metric_name == "latency"
            and aggregation == "avg"
            and self.granularity in _ROLLUP_GRANULARITIES
            and single_bd is not None
            and single_bd.get("type") == "custom_attribute"
            and single_bd.get("name") in _ROLLUP_COVERED_ATTRS
            and not per_metric_filters
            and not self.global_filters
            and self._attr_rollup_window_covered(start_date)
        )

    def _build_system_metric_query(
        self,
        metric_name: str,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[str, dict]:
        # Normalize: saved widgets may have capitalized names (e.g. "Latency")
        metric_name = metric_name.lower() if metric_name else metric_name

        # Covered latency-breakdown shape → the pre-aggregated rollup; anything
        # else falls through to the spans scan (fail-closed, see _should_use_rollup).
        single_bd = self.breakdowns[0] if len(self.breakdowns) == 1 else None
        if self._should_use_rollup(
            metric_name,
            aggregation,
            single_bd,
            per_metric_filters,
            params["start_date"],
        ):
            params = dict(params)
            params["attr_key"] = _sanitize_attr_key(single_bd["name"])
            # Rollup is hourly — snap the window to whole hours so no partial bucket.
            params["start_date"] = _snap_to_hour(params["start_date"])
            params["end_date"] = _snap_to_hour(params["end_date"])
            rollup_query = (
                f"SELECT {_bucket_expr(bucket_fn, 'hour')} AS time_bucket,\n"
                "       attr_value AS breakdown_value,\n"
                "       sumMerge(latency_sum) / countMerge(n) AS value\n"
                "FROM dashboard_attr_rollup\n"
                "WHERE project_id IN %(project_ids)s\n"
                "  AND attr_key = %(attr_key)s\n"
                "  AND hour >= %(start_date)s\n"
                "  AND hour < %(end_date)s\n"
                "GROUP BY time_bucket, breakdown_value\n"
                "ORDER BY time_bucket, breakdown_value"
            )
            return rollup_query, params

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
        # Identifier metrics should count unique identities, not raw span rows.
        if metric_name in _COUNT_DISTINCT_METRICS and aggregation != "count_distinct":
            aggregation = "count_distinct"
        agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        # 0/1 indicator metrics → rescale averaging aggs to percent.
        if metric_name in _RATE_INDICATOR_METRICS:
            agg_expr = rescale_rate_to_percent(agg_expr, aggregation)

        select_parts = [f"{_bucket_expr(bucket_fn, 'start_time')} AS time_bucket"]
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

        if metric_name == "latency":
            spans_flat = self._trace_spans_source(
                metric_name, per_metric_filters, "spans"
            )
            spans_joined = self._trace_spans_source(
                metric_name, per_metric_filters, "s"
            )
        else:
            spans_flat = self._spans_source(metric_name, per_metric_filters, "spans")
            spans_joined = self._spans_source(metric_name, per_metric_filters, "s")

        bd_infos = self._resolve_all_breakdowns(params)
        has_annotation_bd = any(b["type"] == "annotation" for b in bd_infos)

        if bd_infos:
            bd_exprs = []
            join_clauses = []
            for b in bd_infos:
                bd_exprs.append(b["expr"])
                if b["join"]:
                    join_clauses.append(b["join"])

            if len(bd_exprs) == 1:
                bd_select = f"{bd_exprs[0]} AS breakdown_value"
            else:
                parts = ", ' / ', ".join(f"toString({e})" for e in bd_exprs)
                bd_select = f"concat({parts}) AS breakdown_value"

            if has_annotation_bd:
                agg_with_alias = (
                    agg_expr.replace("(", "(s.") if "(" in agg_expr else agg_expr
                )
                where_str = " AND ".join(_prefix_spans_columns(c) for c in all_where)
                join_str = "\n".join(join_clauses)
                query = (
                    f"SELECT {_bucket_expr(bucket_fn, 's.start_time')} AS time_bucket,\n"
                    f"       {bd_select},\n"
                    f"       {agg_with_alias} AS value\n"
                    f"FROM {spans_joined}\n"
                    f"{join_str}\n"
                    f"WHERE {where_str}\n"
                    f"GROUP BY time_bucket, breakdown_value\n"
                    f"ORDER BY time_bucket, breakdown_value"
                )
            else:
                select_parts_with_bd = [
                    f"{_bucket_expr(bucket_fn, 'start_time')} AS time_bucket",
                    bd_select,
                    f"{agg_expr} AS value",
                ]
                query = (
                    f"SELECT {', '.join(select_parts_with_bd)}\n"
                    f"FROM {spans_flat}\n"
                    f"WHERE {' AND '.join(all_where)}\n"
                    f"GROUP BY time_bucket, breakdown_value\n"
                    f"ORDER BY time_bucket, breakdown_value"
                )
        else:
            query = (
                f"SELECT {', '.join(select_parts)}\n"
                f"FROM {spans_flat}\n"
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
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[str, dict]:
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

        _output_str_lower = "lower(e.eval_output_str)"
        _is_pass = (
            f"(e.eval_score >= 1.0 OR {_output_str_lower} IN "
            "('passed', 'pass', 'true', '1'))"
        )
        _is_fail = (
            f"(e.eval_score < 1.0 AND {_output_str_lower} NOT IN "
            "('passed', 'pass', 'true', '1'))"
        )
        _unified_score = f"if({_is_pass}, 1.0, e.eval_score)"

        _EVAL_AGGREGATIONS: dict[str, str] = {
            "pass_rate": f"countIf({_is_pass}) / nullIf(count(), 0)",
            "fail_rate": f"countIf({_is_fail}) / nullIf(count(), 0)",
            "pass_count": f"countIf({_is_pass})",
            "fail_count": f"countIf({_is_fail})",
            "true_rate": f"countIf({_is_pass}) / nullIf(count(), 0)",
        }

        if aggregation in _EVAL_AGGREGATIONS:
            agg_expr = _EVAL_AGGREGATIONS[aggregation]
        elif output_type in ("CHOICE", "CHOICES"):
            agg_expr = "count()"
        else:
            if output_type == "PASS_FAIL":
                col_expr = _unified_score
            else:
                # Some templates with missing output_type still emit pass/fail strings.
                col_expr = (
                    "if(e.eval_output_str = '', NULL, "
                    f"if({_output_str_lower} IN ('passed', 'pass', 'true', '1'), 1.0, "
                    f"if({_output_str_lower} IN ('failed', 'fail', 'false', '0'), 0.0, "
                    "if(match(e.eval_output_str, '^-?[0-9]+\\.?[0-9]*$'), "
                    "e.eval_score, NULL))))"
                )
            agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        eval_time_expr = "if(e.eval_trace_id = '', e.created_at, s.start_time)"
        select_parts = [
            f"{_bucket_expr(bucket_fn, eval_time_expr)} AS time_bucket"
        ]
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
            f"{eval_time_expr} >= %(start_date)s",
            f"{eval_time_expr} < %(end_date)s",
        ]

        # Keep one latest trace-eval attempt; dataset/playground rows pass through.
        _dedup_scope_d = (
            "d.workspace_id = toUUID(%(workspace_id)s)"
            if self.workspace_id
            else "d.organization_id = toUUID(%(organization_id)s)"
        )
        # `d._peerdb_is_deleted = 0` already filters CDC tombstones; the outer
        # `e … FINAL` still collapses duplicate parts for the join. argMax(id)
        # picks the latest attempt deterministically even across unmerged parts,
        # so we can skip the extra FINAL scan on this subquery.
        where_parts.append(
            "(e.eval_trace_id = '' OR (e.eval_trace_id, e.id) IN ("
            "SELECT d.eval_trace_id, argMax(d.id, tuple(d.created_at, d.id)) "
            "FROM usage_apicalllog AS d "
            f"WHERE {_dedup_scope_d} "
            "AND d._peerdb_is_deleted = 0 AND d.status = 'success' "
            "AND d.source_id = %(eval_template_id)s "
            "AND d.eval_trace_id != '' "
            "GROUP BY d.eval_trace_id))"
        )

        joins = []
        need_spans_join = True
        need_eval_join = {}

        _trace_id_expr = "e.eval_trace_id"

        bd_exprs = []
        for bd_idx, bd in enumerate(self.breakdowns):
            bd_name = (bd.get("name") or bd.get("id") or "").lower()
            bd_type = bd.get("type", "system_metric")

            if bd_name in ("source", "eval_source"):
                bd_expr = "if(e.source = '', '(not set)', e.source)"

            elif bd_name == "dataset":
                bd_expr = (
                    "if(e.eval_dataset_id != '', e.eval_dataset_id, "
                    + _eval_source_bucket_expr(exclude="dataset")
                    + ")"
                )

            elif bd_name == "project":
                _proj_uuid = (
                    f"dictGet('trace_dict', 'project_id', "
                    f"toUUIDOrZero({_trace_id_expr}))"
                )
                bd_expr = (
                    f"if({_trace_id_expr} != '' "
                    f"AND {_proj_uuid} != toUUID('00000000-0000-0000-0000-000000000000'), "
                    f"toString({_proj_uuid}), "
                    + _eval_source_bucket_expr(exclude="project")
                    + ")"
                )
            elif bd_name in SYSTEM_METRICS:
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
                    "service_name": "service_name",
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

            elif bd_type == "eval_metric":
                ev_tid = bd.get("config_id") or bd.get("label_id") or bd_name
                ev_tid = resolve_eval_template_id(
                    ev_tid, organization_id=self.organization_id
                )
                bd_output_type = (
                    bd.get("output_type") or bd.get("outputType") or ""
                ).upper()

                if ev_tid == eval_template_id:
                    if bd_output_type in ("PASS_FAIL", "CHOICE", "CHOICES"):
                        bd_expr = (
                            "if(e.eval_output_str = '', '(not set)', e.eval_output_str)"
                        )
                    else:
                        bd_expr = (
                            "if(e.eval_score = 0, '(not set)', "
                            "toString(round(e.eval_score * 100)))"
                        )
                else:
                    ev_alias = f"ev_bd{bd_idx}"
                    param_key = f"_ev_bd{bd_idx}_tid"
                    params[param_key] = ev_tid
                    need_eval_join[ev_alias] = param_key
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

            elif bd_type == "custom_attribute":
                need_spans_join = True
                attr_key = _sanitize_attr_key(bd_name)
                bd_expr = f"if(s.span_attr_str['{attr_key}'] != '', s.span_attr_str['{attr_key}'], '(not set)')"

            else:
                bd_expr = "'(not set)'"

            bd_exprs.append(bd_expr)

        if bd_exprs:
            if len(bd_exprs) == 1:
                bd_select = f"{bd_exprs[0]} AS breakdown_value"
            else:
                parts = ", ' / ', ".join(f"toString({expr})" for expr in bd_exprs)
                bd_select = f"concat({parts}) AS breakdown_value"
            select_parts.append(bd_select)
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

        if need_spans_join:
            spans_joined = self._trace_spans_source(None, per_metric_filters, "s")
            joins.append(
                f"LEFT JOIN {spans_joined} ON s.trace_id = {_trace_id_expr} "
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
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[str, dict]:
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
            agg_expr = "count()"
        elif output_type in ("thumbs_up_down", "boolean", "bool"):
            col_expr = "JSONExtract(a.value, 'value', 'Nullable(String)')"
            agg_expr = (
                f"countIf(lower({col_expr}) IN ('up', 'thumbs_up', 'true', '1')) "
                f"* 100.0 / greatest(countIf({col_expr} IS NOT NULL), 1)"
            )
        elif output_type == "text":
            # Text: just count annotations
            agg_expr = "count()"
        else:
            # Numeric/star: aggregate the float value, skipping NULLs so
            # missing/non-numeric payloads don't pull averages toward 0.
            col_expr = annotation_numeric_value_expr(alias="a", nullable=True)
            agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        select_parts = [f"{_bucket_expr(bucket_fn, 'a.created_at')} AS time_bucket"]
        group_parts = ["time_bucket"]
        order_parts = ["time_bucket"]
        joins: list[str] = []

        trace_breakdowns = [
            breakdown
            for breakdown in self.breakdowns
            if breakdown.get("source", "traces") in ("traces", "both", "all", "")
        ]
        if trace_breakdowns:
            breakdown = trace_breakdowns[0]
            if breakdown.get("type") == "annotation_metric":
                breakdown_label_id = breakdown.get("label_id") or breakdown.get(
                    "name", ""
                )
                breakdown_output_type = (
                    breakdown.get("output_type")
                    or breakdown.get("outputType")
                    or output_type
                )
                if str(breakdown_label_id) == str(label_id):
                    breakdown_expr = _annotation_value_expr("a", breakdown_output_type)
                else:
                    params["annotation_breakdown_label_id"] = breakdown_label_id
                    annotation_key = _annotation_entity_key("a")
                    breakdown_key = _annotation_entity_key("ab")
                    joins.append(
                        "LEFT JOIN model_hub_score AS ab FINAL "
                        f"ON {breakdown_key} = {annotation_key} "
                        "AND ab.label_id = toUUID(%(annotation_breakdown_label_id)s) "
                        "AND ab._peerdb_is_deleted = 0 AND ab.deleted = 0"
                    )
                    breakdown_expr = _annotation_value_expr(
                        "ab", breakdown_output_type
                    )
                select_parts.append(f"{breakdown_expr} AS breakdown_value")
                group_parts.append("breakdown_value")
                order_parts.append("breakdown_value")

        select_parts.append(f"{agg_expr} AS value")

        # model_hub_score has no project_id of its own that maps to
        # tracer.Project, so resolve via trace_dict for trace-attached
        # scores and via the spans table for span-attached scores.
        # Other source types (call_execution, dataset_row, …) are out of
        # scope for trace dashboards.
        workspace_scope = ""
        if self.all_workspace_projects and self.workspace_id:
            params["annotation_workspace_id"] = self.workspace_id
            workspace_scope = (
                "(a.workspace_id = toUUID(%(annotation_workspace_id)s)) OR "
            )

        where_parts = [
            (
                "("
                + workspace_scope
                + "(a.project_id IS NOT NULL AND a.project_id IN %(project_ids)s)"
                " OR "
                "(a.trace_id IS NOT NULL "
                "AND dictGet('trace_dict', 'project_id', a.trace_id) "
                "IN %(project_ids)s)"
                " OR "
                "(a.trace_session_id IS NOT NULL "
                "AND dictGet('trace_session_dict', 'project_id', a.trace_session_id) "
                "IN %(project_ids)s)"
                " OR "
                "(a.observation_span_id IS NOT NULL "
                "AND a.observation_span_id != '' "
                "AND a.observation_span_id IN ("
                "SELECT id FROM spans "
                "WHERE project_id IN %(project_ids)s "
                "AND _peerdb_is_deleted = 0))"
                ")"
            ),
            "a._peerdb_is_deleted = 0",
            "a.deleted = 0",
            "a.created_at >= %(start_date)s",
            "a.created_at < %(end_date)s",
            "a.label_id = toUUID(%(annotation_label_id)s)",
        ]

        annotation_filter_idx = 0
        for filter_item in self.global_filters + per_metric_filters:
            if (
                filter_item.get("metric_type") != "annotation_metric"
                or filter_item.get("source", "traces")
                not in ("traces", "both", "all", "")
            ):
                continue
            operator = filter_item.get("operator", "")
            value = filter_item.get("value")
            filter_label_id = filter_item.get("label_id") or filter_item.get(
                "metric_name", ""
            )
            filter_output_type = (
                filter_item.get("output_type")
                or filter_item.get("outputType")
                or output_type
            )
            param_prefix = f"annotation_filter_{annotation_filter_idx}"
            label_key = f"{param_prefix}_label"
            filter_expr = _annotation_value_expr("a", filter_output_type)
            direct_predicate = _filter_predicate(
                filter_expr, operator, value, param_prefix, params
            )
            if not direct_predicate:
                continue
            if str(filter_label_id) == str(label_id):
                where_parts.append(direct_predicate)
            else:
                params[label_key] = filter_label_id
                outer_key = _annotation_entity_key("a")
                nested_expr = _annotation_value_expr("af", filter_output_type)
                nested_key = _annotation_entity_key("af")
                nested_predicate = direct_predicate.replace(filter_expr, nested_expr)
                where_parts.append(
                    f"{outer_key} IN (SELECT {nested_key} FROM model_hub_score AS af FINAL "
                    f"WHERE af.label_id = toUUID(%({label_key})s) "
                    "AND af._peerdb_is_deleted = 0 AND af.deleted = 0 "
                    f"AND {nested_predicate})"
                )
            annotation_filter_idx += 1

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM model_hub_score AS a FINAL\n"
            f"{' '.join(joins)}\n"
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
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[str, dict]:
        attr_key = _sanitize_attr_key(metric.get("attribute_key", ""))
        attr_type = metric.get("attribute_type", "number")

        if attr_type == "number":
            col_expr = f"span_attr_num['{attr_key}']"
        else:
            if aggregation in _NUMERIC_ONLY_AGGREGATIONS:
                raise InvalidMetricCombinationError(
                    f"'{aggregation}' can't be applied to the text attribute "
                    f"'{attr_key}'. Use count or count distinct, or pick a "
                    f"numeric attribute."
                )
            col_expr = f"span_attr_str['{attr_key}']"

        agg_expr = AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

        select_parts = [f"{_bucket_expr(bucket_fn, 'start_time')} AS time_bucket"]
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

        spans_flat = self._spans_source(None, per_metric_filters, "spans")

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM {spans_flat}\n"
            f"WHERE {' AND '.join(all_where)}\n"
            f"GROUP BY {', '.join(group_parts)}\n"
            f"ORDER BY {', '.join(order_parts)}"
        )
        return query, params

    # ------------------------------------------------------------------
    # Build all queries
    # ------------------------------------------------------------------

    def build_all_queries(self) -> list[tuple[str, dict, dict]]:
        """Build queries for all metrics.

        Returns:
            List of (sql, params, metric_info) tuples.
        """
        results = []
        for metric in self.metrics:
            sql, params = self.build_metric_query(metric)
            results.append((sql, params, self.metric_info(metric)))
        return results

    def metric_info(self, metric: dict) -> dict:
        """Build the response metadata for a single metric.

        Exposed so callers can construct the metric's ``metric_info`` without
        building its SQL — e.g. to attach a per-metric error when the build or
        execution fails, keeping the rest of the dashboard's widgets intact.
        """
        return {
            "id": metric.get("id", ""),
            "name": metric.get("display_name")
            or metric.get("displayName")
            or metric.get("name", ""),
            "type": metric.get("type", "system_metric"),
            "aggregation": metric.get("aggregation", "avg"),
        }

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    def format_results(
        self,
        metric_results: list[tuple[dict, list[dict]]],
        project_name_map: dict[str, str] | None = None,
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
        all_buckets = _generate_time_buckets(
            start_date, end_date, self.granularity, self.timezone
        )
        formatted_metrics = []

        # Check if any breakdown is by project (needs UUID→name resolution)
        has_project_breakdown = any(
            bd.get("name") == "project" for bd in self.breakdowns
        )

        for metric_info, rows in metric_results:
            metric_name = metric_info.get("name", "")
            metric_id = metric_info.get("id", "")
            unit = METRIC_UNITS.get(metric_name) or METRIC_UNITS.get(metric_id, "")

            # Group rows by breakdown value if present
            # Use a dict of {iso_timestamp: value} for easy merging
            series_data: dict[str, dict[str, Any]] = {}
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
                        ts = datetime(ts.year, ts.month, ts.day, tzinfo=self.timezone)
                    elif hasattr(ts, "tzinfo") and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=self.timezone)
                    else:
                        ts = ts.astimezone(self.timezone)
                    ts = ts.isoformat()
                val = row.get("value")
                if isinstance(val, float):
                    val = round(val, 6)
                series_data[breakdown_key][ts] = val

            if not series_data:
                series_data["total"] = {}

            # Keep the highest-volume series first; the frontend still limits
            # the initially visible chart series.
            MAX_SERIES = 100
            if "total" not in series_data:
                ranked = sorted(
                    series_data.items(),
                    key=lambda kv: sum(v for v in kv[1].values() if v is not None),
                    reverse=True,
                )
                if len(ranked) > MAX_SERIES:
                    ranked = ranked[:MAX_SERIES]
                series_data = dict(ranked)

            # Preserve volume order from ``series_data``.
            series = []
            for name, data_map in series_data.items():
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

            formatted_metric = {
                "id": metric_info.get("id", ""),
                "name": metric_name,
                "aggregation": metric_info.get("aggregation", "avg"),
                "unit": unit,
                "series": series,
            }
            if metric_info.get("error"):
                formatted_metric["error"] = metric_info["error"]
            formatted_metrics.append(formatted_metric)

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
        "service_name": "service_name",
        "span_kind": "observation_type",
        "provider": "provider",
        "session": "toString(trace_session_id)",
        "user": "dictGetOrDefault('end_users_dict', 'user_id', end_user_id, toString(end_user_id))",
        "user_id_type": "dictGetOrDefault('end_users_dict', 'user_id_type', end_user_id, '')",
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

            # source="all" can still be a trace-side dimension.
            if bd_source in ("datasets", "simulation"):
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

                # ``id IS NULL`` distinguishes "no annotation row matched
                # the LEFT JOIN" from "row exists but value JSON is
                # missing the key" (which would otherwise extract as 0
                # / empty and silently bucket alongside real values).
                missing_check = f"{alias}.id IS NULL"
                if output_type in ("categorical", "choice"):
                    val_expr = (
                        f"arrayJoin(if({missing_check}, ['(not set)'], "
                        f"JSONExtract({alias}.value, 'selected', 'Array(String)')))"
                    )
                elif output_type == "thumbs_up_down":
                    val_expr = (
                        f"if({missing_check}, '(not set)', "
                        f"JSONExtractString({alias}.value, 'value'))"
                    )
                elif output_type == "text":
                    val_expr = (
                        f"if({missing_check}, '(not set)', "
                        f"JSONExtractString({alias}.value, 'text'))"
                    )
                else:
                    nullable_num = annotation_numeric_value_expr(
                        alias=alias, nullable=True
                    )
                    rounding = ", 1" if output_type in ("numeric", "star") else ""
                    val_expr = (
                        f"if({missing_check} OR {nullable_num} IS NULL, "
                        f"'(not set)', toString(round({nullable_num}{rounding})))"
                    )

                join_clause = (
                    f"LEFT JOIN model_hub_score AS {alias} "
                    f"ON toString({alias}.trace_id) = s.trace_id "
                    f"AND {alias}.label_id = toUUID(%({param_key})s) "
                    f"AND {alias}._peerdb_is_deleted = 0 "
                    f"AND {alias}.deleted = 0"
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
                        f"if({alias}.id IS NULL, '(not set)', {alias}.eval_output_str)"
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

    def _breakdown_select(self) -> str | None:
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
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[list[str], dict]:
        """Build base WHERE clauses for spans-based queries."""
        clauses = [
            "project_id IN %(project_ids)s",
            "_peerdb_is_deleted = 0",
            f"{time_col} >= %(start_date)s",
            f"{time_col} < %(end_date)s",
        ]

        # spans is partitioned by toYYYYMM(created_at) but the window is
        # filtered on start_time, so bound created_at too — otherwise no
        # partitions prune and the scan covers all history. Lower bound only
        # (created_at >= start_time always holds), so no in-window row drops.
        if time_col != "created_at":
            clauses.append("created_at >= %(start_date)s - INTERVAL 1 DAY")

        # Apply global + per-metric system_metric filters directly
        # For string-comparable system metrics, use toString() to avoid UUID parse errors
        _STRING_FILTER_COL = {
            "project": "toString(project_id)",
            "status": "status",
            "model": "model",
            "service_name": "service_name",
            "span_kind": "observation_type",
            "provider": "provider",
            "session": "toString(trace_session_id)",
            "user": "dictGetOrDefault('end_users_dict', 'user_id', end_user_id, toString(end_user_id))",
            "user_id_type": "dictGetOrDefault('end_users_dict', 'user_id_type', end_user_id, '')",
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
        filters: list[dict],
        params: dict,
        prefix: str,
    ) -> tuple[list[str], dict]:
        """Build IN-subquery clauses for eval/annotation metric filters on spans."""
        clauses: list[str] = []
        extra_params: dict[str, Any] = {}
        idx = 0

        for f in filters:
            if f.get("source", "traces") not in ("traces", "both", "all", ""):
                continue
            f_type = f.get("metric_type", "")
            op = f.get("operator", "")
            val = f.get("value")
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

                filter_params: dict[str, Any] = {}
                eval_predicate = _filter_predicate(
                    eval_col, op, val, f"{prefix}{idx}", filter_params
                )
                if not eval_predicate:
                    continue

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
                    f"AND {eval_predicate} "
                    f"AND _peerdb_is_deleted = 0"
                    f")"
                )
                clauses.append(subquery)
                extra_params[eval_id_key] = eval_template_id
                extra_params[scope_key] = _sub_scope_val
                extra_params.update(filter_params)
                idx += 1

            elif f_type == "annotation_metric":
                label_id_key = f"{prefix}label_id_{idx}"
                label_id = f.get("label_id") or f.get("metric_name", "")
                ann_org_key = f"{prefix}ann_org_id_{idx}"

                output_type = (
                    f.get("output_type") or f.get("outputType") or "numeric"
                )
                annotation_expr = _annotation_value_expr("mhs", output_type)
                filter_params = {}
                value_predicate = _filter_predicate(
                    annotation_expr, op, val, f"{prefix}{idx}", filter_params
                )
                if not value_predicate:
                    continue
                annotation_predicate = (
                    f"mhs.label_id = toUUID(%({label_id_key})s) "
                    f"AND mhs.organization_id = toUUID(%({ann_org_key})s) "
                    f"AND {value_predicate} "
                    "AND mhs._peerdb_is_deleted = 0 AND mhs.deleted = 0"
                )
                subquery = (
                    "(trace_id IN ("
                    "SELECT toString(mhs.trace_id) FROM model_hub_score AS mhs FINAL "
                    f"WHERE mhs.trace_id IS NOT NULL AND {annotation_predicate}"
                    ") OR id IN ("
                    "SELECT mhs.observation_span_id FROM model_hub_score AS mhs FINAL "
                    f"WHERE mhs.observation_span_id != '' AND {annotation_predicate}"
                    "))"
                )
                clauses.append(subquery)
                extra_params[label_id_key] = label_id
                extra_params[ann_org_key] = self.organization_id
                extra_params.update(filter_params)
                idx += 1

        return clauses, extra_params


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

_OPERATOR_SYMBOLS: dict[str, str] = {
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
    start: datetime,
    end: datetime,
    granularity: str,
    timezone_info: tzinfo = UTC,
) -> list[str]:
    """Generate all time bucket ISO strings between *start* and *end*.

    Mirrors the ClickHouse ``toStartOf*`` bucketing so that the response
    includes every expected bucket — even those with no data (filled with null).
    """
    buckets: list[str] = []
    start = start.astimezone(timezone_info)
    end = end.astimezone(timezone_info)
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


def _get_operator_symbol(op: str) -> str | None:
    """Return the SQL operator symbol for a filter operator name."""
    return _OPERATOR_SYMBOLS.get(op)


def _parse_dt(val: Any) -> datetime:
    """Parse a datetime from string or return as-is with UTC timezone.

    Always returns a timezone-aware (UTC) datetime so that callers
    produce consistent isoformat strings (with ``+00:00`` suffix).
    """
    dt: datetime | None = None
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
        return datetime.now(UTC)
    # Ensure timezone-aware (UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
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
