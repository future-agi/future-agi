"""
Simulation Dashboard Query Builder for ClickHouse.

Translates a widget ``query_config`` (with ``source: "simulation"``) into
ClickHouse SQL queries.  Mirrors :class:`DatasetQueryBuilder` but queries
the ``simulate_calls`` view (via simulate_call_execution + dictionaries).

Supports two metric types:
- **system_metric** -- call_count, duration, cost, score, latency,
  interruptions, WPM, talk_ratio, etc.
- **eval_metric** -- aggregates from eval_outputs JSONB stored on each call

Breakdowns:
- scenario, agent_definition, agent_version, call_type (voice/text), status
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

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

_SAFE_KEY_RE = re.compile(r"^[a-zA-Z0-9._\-]+$")

# Metrics whose column expression emits a 0/1 indicator per row.
_RATE_INDICATOR_METRICS = frozenset({"success_rate", "failure_rate"})
_STRING_DIMENSION_METRICS = frozenset(
    {
        "scenario",
        "agent_definition",
        "agent_version",
        "persona",
        "call_type",
        "status",
        "ended_reason",
        "scenario_type",
        "run_test",
        "test_execution",
        "persona_gender",
        "persona_age_group",
        "persona_location",
        "persona_profession",
        "persona_personality",
        "persona_communication_style",
        "persona_accent",
        "persona_language",
        "persona_conversation_speed",
    }
)


def _sanitize_key(key: str) -> str:
    if not key or not _SAFE_KEY_RE.match(key):
        raise ValueError(f"Invalid key: {key!r}")
    return key


# ---------------------------------------------------------------------------
# Metric resolution tables
# ---------------------------------------------------------------------------

# Persona extraction from call_metadata JSONB
# Persona data is stored in call_metadata.row_data.persona as a Python dict string
# (single-quoted keys/values). We convert single quotes to double quotes for JSON parsing.
# Note: values containing apostrophes (e.g. O'Brien) would break this — but PeerDB
# stores persona data from Python dicts which don't contain unescaped apostrophes
# in practice. If this becomes an issue, use a more targeted regex replacement.
_PERSONA_JSON_EXPR = "replaceAll(JSONExtractString(c.call_metadata, 'row_data', 'persona'), char(39), char(34))"

_PERSONA_NAME_EXPR = (
    f"if(JSONExtractString({_PERSONA_JSON_EXPR}, 'name') != '', "
    f"JSONExtractString({_PERSONA_JSON_EXPR}, 'name'), "
    "JSONExtractString(c.call_metadata, 'row_data', 'persona'))"
)

_PERSONA_FIELD = lambda field: (f"JSONExtractString({_PERSONA_JSON_EXPR}, '{field}')")
_CUSTOMER_LATENCY_FIELD = lambda field: (
    f"JSONExtractFloat(c.customer_latency_metrics, 'systemMetrics', '{field}')"
)
# Nullable variant — returns NULL when the JSON key is absent so avg()
# skips the row instead of folding a 0 into the average.
_CUSTOMER_LATENCY_FIELD_NULLABLE = lambda field: (
    "if(JSONHas(c.customer_latency_metrics, 'systemMetrics', "
    f"'{field}'), JSONExtractFloat(c.customer_latency_metrics, "
    f"'systemMetrics', '{field}'), CAST(NULL, 'Nullable(Float64)'))"
)

SIMULATION_SYSTEM_METRICS: Dict[str, Tuple[str, str]] = {
    # Call counts & entity counts
    "call_count": ("simulate_call_execution", "1"),
    # Rate metrics emit a 0/1 indicator per row. ``avg`` is rescaled to a
    # percentage in ``_build_system_metric_query`` so users see 42% rather
    # than 0.42; ``sum`` / ``count`` keep their natural meaning (count of
    # successful or failed calls).
    "success_rate": (
        "simulate_call_execution",
        "CASE WHEN status = 'completed' THEN 1.0 ELSE 0.0 END",
    ),
    "failure_rate": (
        "simulate_call_execution",
        "CASE WHEN status IN ('failed', 'cancelled') THEN 1.0 ELSE 0.0 END",
    ),
    # Duration & timing
    "duration": ("simulate_call_execution", "duration_seconds"),
    "response_time": ("simulate_call_execution", "response_time_ms"),
    "agent_latency": ("simulate_call_execution", "avg_agent_latency_ms"),
    # Component latencies (STT/TTS/LLM). Use Nullable(Float64) so calls
    # whose customer_latency_metrics JSON is empty / missing the key are
    # skipped by avg() instead of being counted as 0 ms (which silently
    # collapses real values toward zero and looks like "no data").
    "stt_latency": (
        "simulate_call_execution",
        _CUSTOMER_LATENCY_FIELD_NULLABLE("transcriber"),
    ),
    "tts_latency": (
        "simulate_call_execution",
        _CUSTOMER_LATENCY_FIELD_NULLABLE("voice"),
    ),
    "llm_latency": (
        "simulate_call_execution",
        _CUSTOMER_LATENCY_FIELD_NULLABLE("model"),
    ),
    # Cost breakdown
    "total_cost": ("simulate_call_execution", "cost_cents"),
    "stt_cost": ("simulate_call_execution", "stt_cost_cents"),
    "llm_cost": ("simulate_call_execution", "llm_cost_cents"),
    "tts_cost": ("simulate_call_execution", "tts_cost_cents"),
    "customer_cost": ("simulate_call_execution", "customer_cost_cents"),
    # Score
    "overall_score": ("simulate_call_execution", "overall_score"),
    # Conversation metrics
    "message_count": ("simulate_call_execution", "message_count"),
    "user_interruptions": ("simulate_call_execution", "user_interruption_count"),
    "user_interruption_rate": ("simulate_call_execution", "user_interruption_rate"),
    "ai_interruptions": ("simulate_call_execution", "ai_interruption_count"),
    "ai_interruption_rate": ("simulate_call_execution", "ai_interruption_rate"),
    "user_wpm": ("simulate_call_execution", "user_wpm"),
    "bot_wpm": ("simulate_call_execution", "bot_wpm"),
    # ``talk_ratio`` is stored as ``agent_talk_time / customer_talk_time``
    # — an unbounded ratio that can exceed 1.0 (and therefore exceeded
    # 100% when the frontend rendered it as a percentage). Convert it to
    # the agent's share of total speaking time so the value is always
    # within [0, 100]. Mirrors test_execution.agent_talk_percentage.
    "talk_ratio": (
        "simulate_call_execution",
        "if(talk_ratio IS NULL OR talk_ratio <= 0, "
        "CAST(NULL, 'Nullable(Float64)'), "
        "(talk_ratio / (talk_ratio + 1)) * 100)",
    ),
    "stop_time_after_interruption": (
        "simulate_call_execution",
        "avg_stop_time_after_interruption_ms",
    ),
    # --- String dimensions (used as metrics with count_distinct) ---
    "scenario": (
        "simulate_call_execution",
        "dictGetOrDefault('simulate_scenario_dict', 'name', c.scenario_id, '')",
    ),
    "agent_definition": (
        "simulate_call_execution",
        "dictGetOrDefault('simulate_agent_dict', 'agent_name', "
        "dictGetOrDefault('simulate_version_dict', 'agent_definition_id', "
        "c.agent_version_id, toUUID('00000000-0000-0000-0000-000000000000')), '')",
    ),
    "agent_version": (
        "simulate_call_execution",
        "concat("
        "dictGetOrDefault('simulate_agent_dict', 'agent_name', "
        "dictGetOrDefault('simulate_version_dict', 'agent_definition_id', "
        "c.agent_version_id, toUUID('00000000-0000-0000-0000-000000000000')), ''), "
        "' v', toString(dictGetOrDefault('simulate_version_dict', 'version_number', "
        "c.agent_version_id, toUInt32(0))))",
    ),
    "persona": ("simulate_call_execution", _PERSONA_NAME_EXPR),
    "call_type": ("simulate_call_execution", "c.simulation_call_type"),
    "status": ("simulate_call_execution", "c.status"),
    "ended_reason": ("simulate_call_execution", "c.ended_reason"),
    "scenario_type": (
        "simulate_call_execution",
        "dictGetOrDefault('simulate_scenario_dict', 'scenario_type', c.scenario_id, '')",
    ),
    "run_test": (
        "simulate_call_execution",
        "dictGetOrDefault('simulate_run_test_dict', 'name', "
        "dictGetOrDefault('simulate_test_execution_dict', 'run_test_id', "
        "c.test_execution_id, toUUID('00000000-0000-0000-0000-000000000000')), '')",
    ),
    "test_execution": ("simulate_call_execution", "toString(c.test_execution_id)"),
    # Persona attributes
    "persona_gender": ("simulate_call_execution", _PERSONA_FIELD("gender")),
    "persona_age_group": ("simulate_call_execution", _PERSONA_FIELD("age_group")),
    "persona_location": ("simulate_call_execution", _PERSONA_FIELD("location")),
    "persona_profession": ("simulate_call_execution", _PERSONA_FIELD("profession")),
    "persona_personality": ("simulate_call_execution", _PERSONA_FIELD("personality")),
    "persona_communication_style": (
        "simulate_call_execution",
        _PERSONA_FIELD("communication_style"),
    ),
    "persona_accent": ("simulate_call_execution", _PERSONA_FIELD("accent")),
    "persona_language": ("simulate_call_execution", _PERSONA_FIELD("language")),
    "persona_conversation_speed": (
        "simulate_call_execution",
        _PERSONA_FIELD("conversation_speed"),
    ),
}

SIMULATION_METRIC_UNITS: Dict[str, str] = {
    "call_count": "",
    "success_rate": "%",
    "failure_rate": "%",
    "duration": "s",
    "response_time": "ms",
    "agent_latency": "ms",
    "stt_latency": "ms",
    "tts_latency": "ms",
    "llm_latency": "ms",
    "total_cost": "cents",
    "stt_cost": "cents",
    "llm_cost": "cents",
    "tts_cost": "cents",
    "customer_cost": "cents",
    "overall_score": "",
    "message_count": "",
    "user_interruptions": "",
    "user_interruption_rate": "/min",
    "ai_interruptions": "",
    "ai_interruption_rate": "/min",
    "user_wpm": "wpm",
    "bot_wpm": "wpm",
    "talk_ratio": "%",
    "stop_time_after_interruption": "ms",
    # String dimension metrics (used with count/count_distinct)
    "scenario": "",
    "agent_definition": "",
    "agent_version": "",
    "persona": "",
    "call_type": "",
    "status": "",
    "ended_reason": "",
    "scenario_type": "",
    "run_test": "",
    "test_execution": "",
    "persona_gender": "",
    "persona_age_group": "",
    "persona_location": "",
    "persona_profession": "",
    "persona_personality": "",
    "persona_communication_style": "",
    "persona_accent": "",
    "persona_language": "",
    "persona_conversation_speed": "",
}

SIMULATION_AGGREGATIONS: Dict[str, str] = {
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

# Breakdown dimensions for simulation
SIMULATION_BREAKDOWN_COLUMNS: Dict[str, str] = {
    "scenario": "dictGetOrDefault('simulate_scenario_dict', 'name', c.scenario_id, '')",
    "agent_definition": (
        "dictGetOrDefault('simulate_agent_dict', 'agent_name', "
        "dictGetOrDefault('simulate_version_dict', 'agent_definition_id', "
        "c.agent_version_id, toUUID('00000000-0000-0000-0000-000000000000')), '')"
    ),
    "agent_version": (
        "concat("
        "dictGetOrDefault('simulate_agent_dict', 'agent_name', "
        "dictGetOrDefault('simulate_version_dict', 'agent_definition_id', "
        "c.agent_version_id, toUUID('00000000-0000-0000-0000-000000000000')), ''), "
        "' v', toString(dictGetOrDefault('simulate_version_dict', 'version_number', "
        "c.agent_version_id, toUInt32(0))))"
    ),
    "persona": _PERSONA_NAME_EXPR,
    "call_type": "c.simulation_call_type",
    "status": "c.status",
    "ended_reason": "c.ended_reason",
    "scenario_type": "dictGetOrDefault('simulate_scenario_dict', 'scenario_type', c.scenario_id, '')",
    "persona_gender": _PERSONA_FIELD("gender"),
    "persona_age_group": _PERSONA_FIELD("age_group"),
    "persona_location": _PERSONA_FIELD("location"),
    "persona_profession": _PERSONA_FIELD("profession"),
    "persona_personality": _PERSONA_FIELD("personality"),
    "persona_communication_style": _PERSONA_FIELD("communication_style"),
    "persona_accent": _PERSONA_FIELD("accent"),
    "persona_language": _PERSONA_FIELD("language"),
    "persona_conversation_speed": _PERSONA_FIELD("conversation_speed"),
    # Test & Run dimensions (chain: call -> test_execution -> run_test)
    "run_test": (
        "dictGetOrDefault('simulate_run_test_dict', 'name', "
        "dictGetOrDefault('simulate_test_execution_dict', 'run_test_id', "
        "c.test_execution_id, toUUID('00000000-0000-0000-0000-000000000000')), '')"
    ),
    "test_execution": "toString(c.test_execution_id)",
}

# Filter dimensions for simulation
SIMULATION_FILTER_COLUMNS: Dict[str, str] = {
    "scenario": "dictGetOrDefault('simulate_scenario_dict', 'name', c.scenario_id, '')",
    "agent_definition": (
        "dictGetOrDefault('simulate_agent_dict', 'agent_name', "
        "dictGetOrDefault('simulate_version_dict', 'agent_definition_id', "
        "c.agent_version_id, toUUID('00000000-0000-0000-0000-000000000000')), '')"
    ),
    "agent_version": (
        "concat("
        "dictGetOrDefault('simulate_agent_dict', 'agent_name', "
        "dictGetOrDefault('simulate_version_dict', 'agent_definition_id', "
        "c.agent_version_id, toUUID('00000000-0000-0000-0000-000000000000')), ''), "
        "' v', toString(dictGetOrDefault('simulate_version_dict', 'version_number', "
        "c.agent_version_id, toUInt32(0))))"
    ),
    "persona": _PERSONA_NAME_EXPR,
    "call_type": "c.simulation_call_type",
    "status": "c.status",
    "ended_reason": "c.ended_reason",
    "scenario_type": "dictGetOrDefault('simulate_scenario_dict', 'scenario_type', c.scenario_id, '')",
    "persona_gender": _PERSONA_FIELD("gender"),
    "persona_age_group": _PERSONA_FIELD("age_group"),
    "persona_location": _PERSONA_FIELD("location"),
    "persona_profession": _PERSONA_FIELD("profession"),
    "persona_personality": _PERSONA_FIELD("personality"),
    "persona_communication_style": _PERSONA_FIELD("communication_style"),
    "persona_accent": _PERSONA_FIELD("accent"),
    "persona_language": _PERSONA_FIELD("language"),
    "persona_conversation_speed": _PERSONA_FIELD("conversation_speed"),
    # Test & Run dimensions
    "run_test": (
        "dictGetOrDefault('simulate_run_test_dict', 'name', "
        "dictGetOrDefault('simulate_test_execution_dict', 'run_test_id', "
        "c.test_execution_id, toUUID('00000000-0000-0000-0000-000000000000')), '')"
    ),
    "test_execution": "toString(c.test_execution_id)",
    # Numeric columns — needed so range operators (>, <, between, …) on
    # call.duration / latencies / costs actually filter instead of being
    # silently dropped. JSON-derived latencies use the nullable extractor
    # so missing keys don't compare against 0.
    "duration": "c.duration_seconds",
    "response_time": "c.response_time_ms",
    "agent_latency": "c.avg_agent_latency_ms",
    "stt_latency": _CUSTOMER_LATENCY_FIELD_NULLABLE("transcriber"),
    "tts_latency": _CUSTOMER_LATENCY_FIELD_NULLABLE("voice"),
    "llm_latency": _CUSTOMER_LATENCY_FIELD_NULLABLE("model"),
    "total_cost": "c.cost_cents",
    "stt_cost": "c.stt_cost_cents",
    "llm_cost": "c.llm_cost_cents",
    "tts_cost": "c.tts_cost_cents",
    "customer_cost": "c.customer_cost_cents",
    "overall_score": "c.overall_score",
    "message_count": "c.message_count",
    "user_interruptions": "c.user_interruption_count",
    "user_interruption_rate": "c.user_interruption_rate",
    "ai_interruptions": "c.ai_interruption_count",
    "ai_interruption_rate": "c.ai_interruption_rate",
    "user_wpm": "c.user_wpm",
    "bot_wpm": "c.bot_wpm",
    # Filter on the raw stored ratio (unbounded) — the SYSTEM_METRICS
    # entry only converts to a percentage for display/aggregation.
    "talk_ratio": "c.talk_ratio",
    "stop_time_after_interruption": "c.avg_stop_time_after_interruption_ms",
}


class SimulationQueryBuilder(DashboardQueryBuilderBase):
    """Translates a simulation widget query_config into ClickHouse SQL.

    Queries the ``simulate_call_execution`` table with dictionary lookups
    for denormalized scenario/agent/version fields.
    """

    def __init__(self, query_config: dict) -> None:
        super().__init__(query_config)
        self.workspace_id = query_config.get("workspace_id", "")
        self.agent_definition_ids = query_config.get("agent_definition_ids", [])

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
        aggregation = self._effective_aggregation(metric)
        per_metric_filters = metric.get("filters", [])

        start_date, end_date = self.parse_time_range()
        bucket_fn = GRANULARITY_TO_CH.get(self.granularity, "toStartOfDay")

        params: Dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
        }

        if self.workspace_id:
            params["workspace_id"] = self.workspace_id
        if self.agent_definition_ids:
            params["agent_definition_ids"] = self.agent_definition_ids

        if metric_type == "system_metric":
            return self._build_system_metric_query(
                metric_name, aggregation, bucket_fn, per_metric_filters, params
            )
        elif metric_type == "eval_metric":
            return self._build_eval_metric_query(
                metric, aggregation, bucket_fn, per_metric_filters, params
            )
        else:
            raise ValueError(f"Unknown simulation metric type: {metric_type}")

    def build_all_queries(self) -> List[Tuple[str, dict, dict]]:
        results = []
        for metric in self.metrics:
            sql, params = self.build_metric_query(metric)
            metric_info = {
                "id": metric.get("id", ""),
                "name": metric.get("displayName")
                or metric.get("display_name")
                or metric.get("name", ""),
                "type": metric.get("type", "system_metric"),
                "aggregation": self._effective_aggregation(metric),
            }
            results.append((sql, params, metric_info))
        return results

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
        if metric_name not in SIMULATION_SYSTEM_METRICS:
            raise ValueError(f"Unknown simulation system metric: {metric_name}")
        _, col_expr = SIMULATION_SYSTEM_METRICS[metric_name]

        if metric_name in _STRING_DIMENSION_METRICS:
            is_present = f"{col_expr} IS NOT NULL AND {col_expr} != ''"
            if aggregation != "count":
                agg_expr = f"uniqIf({col_expr}, {is_present})"
            else:
                agg_expr = f"countIf({is_present})"
        else:
            agg_expr = SIMULATION_AGGREGATIONS.get(aggregation, "avg({col})").format(
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
            f"FROM simulate_call_execution AS c FINAL\n"
            f"WHERE {' AND '.join(where_clauses)}\n"
            f"GROUP BY {', '.join(group_parts)}\n"
            f"ORDER BY {', '.join(order_parts)}"
        )
        return query, params

    # ------------------------------------------------------------------
    # Eval metric (from eval_outputs JSONB)
    # ------------------------------------------------------------------

    def _build_eval_metric_query(
        self,
        metric: dict,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: List[dict],
        params: dict,
    ) -> Tuple[str, dict]:
        """Query eval results stored in eval_outputs JSONB on call_execution.

        eval_outputs format: {"eval-key": {"score": ..., "result": ...}, ...}
        The eval_key (e.g. "eval-empathy") is the actual JSON key in eval_outputs.
        Falls back to config_id (template UUID) for backward compatibility.
        """
        eval_key = metric.get("eval_key", "") or ""
        if not eval_key:
            # Fall back to config_id — may be a UUID template ID, resolve to key
            config_id = (
                metric.get("config_id", "")
                or metric.get("id")
                or metric.get("name", "")
            )
            try:
                import uuid as _uuid

                _uuid.UUID(config_id)
                # It's a UUID — try to resolve from SimulateEvalConfig.mapping
                try:
                    from simulate.models.eval_config import SimulateEvalConfig

                    sec = (
                        SimulateEvalConfig.objects.filter(eval_template_id=config_id)
                        .values("mapping")
                        .first()
                    )
                    if sec and isinstance(sec.get("mapping"), dict):
                        eval_key = sec["mapping"].get("key", config_id)
                    else:
                        eval_key = config_id
                except Exception:
                    eval_key = config_id
            except (ValueError, AttributeError):
                eval_key = config_id
        eval_key = _sanitize_key(eval_key)
        output_type = metric.get("output_type", "SCORE")
        params["eval_config_id"] = eval_key

        # Extract value from eval_outputs JSON
        if output_type == "PASS_FAIL":
            col_expr = f"JSONExtractString(c.eval_outputs, '{eval_key}', 'result')"
            PASS_FAIL_AGGS = ("pass_rate", "fail_rate", "pass_count", "fail_count")
            if aggregation not in (*PASS_FAIL_AGGS, "count"):
                aggregation = "pass_rate"
            # Add pass/fail aggregations
            if aggregation not in SIMULATION_AGGREGATIONS:
                # Extend with pass/fail support inline
                pass
        elif output_type == "CHOICE":
            col_expr = f"JSONExtractString(c.eval_outputs, '{eval_key}', 'result')"
            if aggregation not in ("count", "count_distinct"):
                aggregation = "count"
        else:
            # SCORE — numeric
            col_expr = (
                f"JSONExtract(c.eval_outputs, '{eval_key}', 'score', 'Nullable(Float64)')"
            )

        # Build aggregation with pass/fail support. Rate aggregations
        # are scaled to 0–100 so widgets that render them with a ``%``
        # suffix don't show 0.42% for a 42% pass rate.
        EVAL_AGGREGATIONS = {
            **SIMULATION_AGGREGATIONS,
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
        }

        agg_expr = EVAL_AGGREGATIONS.get(aggregation, "avg({col})").format(col=col_expr)

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
        # Only include rows that have this eval in eval_outputs
        where_clauses.append(f"JSONHas(c.eval_outputs, '{eval_key}') = 1")
        where_clauses = self._apply_filters(
            where_clauses, self.global_filters + per_metric_filters, params
        )

        query = (
            f"SELECT {', '.join(select_parts)}\n"
            f"FROM simulate_call_execution AS c FINAL\n"
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
    ) -> dict:
        start_date, end_date = self.parse_time_range()
        all_buckets = _generate_time_buckets(start_date, end_date, self.granularity)
        formatted_metrics = []

        for metric_info, rows in metric_results:
            formatted_metrics.append(
                self._format_metric_result(
                    metric_info, rows, all_buckets, SIMULATION_METRIC_UNITS
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
        col = SIMULATION_BREAKDOWN_COLUMNS.get(bd_name)
        if col:
            return col
        return None

    def _effective_aggregation(self, metric: dict) -> str:
        metric_type = metric.get("type", "system_metric")
        metric_name = metric.get("id") or metric.get("name", "")
        aggregation = metric.get("aggregation", "avg")

        if metric_type == "system_metric":
            if metric_name == "call_count" and aggregation not in ("count", "sum"):
                return "count"
            if metric_name in _STRING_DIMENSION_METRICS:
                return "count" if aggregation == "count" else "count_distinct"
            return aggregation

        if metric_type == "eval_metric":
            output_type = metric.get("output_type", "SCORE")
            if output_type == "PASS_FAIL":
                pass_fail_aggs = {"pass_rate", "fail_rate", "pass_count", "fail_count"}
                return aggregation if aggregation in (*pass_fail_aggs, "count") else "pass_rate"
            if output_type == "CHOICE":
                return aggregation if aggregation in ("count", "count_distinct") else "count"

        return aggregation

    def _build_base_where(self, params: dict) -> List[str]:
        clauses = [
            "c._peerdb_is_deleted = 0",
            "c.deleted = 0",
            "c.created_at >= %(start_date)s",
            "c.created_at < %(end_date)s",
        ]
        if self.workspace_id:
            # Filter via scenario's workspace
            clauses.append(
                "dictGetOrDefault('simulate_scenario_dict', 'workspace_id', "
                "c.scenario_id, NULL) = toUUID(%(workspace_id)s)"
            )
        if self.agent_definition_ids:
            clauses.append(
                "dictGetOrDefault('simulate_version_dict', 'agent_definition_id', "
                "c.agent_version_id, toUUID('00000000-0000-0000-0000-000000000000')) "
                "IN %(agent_definition_ids)s"
            )
        return clauses

    def _apply_filters(
        self,
        clauses: List[str],
        filters: List[dict],
        params: dict,
    ) -> List[str]:
        idx = 0
        for f in filters:
            # Skip filters scoped to other sources (trace / dataset). The
            # frontend can mix filters from multiple sources on a single
            # widget config; without this guard, a trace-scoped filter
            # like ``model = 'gpt-4o'`` would be silently dropped here
            # (no matching column) — but more importantly, simulation
            # filters with an explicit ``source`` are picked up cleanly.
            f_source = f.get("source")
            if f_source and f_source != "simulation":
                continue

            f_type = f.get("metric_type", "")
            f_name = f.get("metric_name", "")
            op = f.get("operator", "")
            val = f.get("value")

            if f_type != "system_metric":
                continue

            col = SIMULATION_FILTER_COLUMNS.get(f_name)
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
                    lo_key = f"sf_{idx}_lo"
                    hi_key = f"sf_{idx}_hi"
                    params[lo_key] = _coerce_filter_value(val[0], "equal_to")
                    params[hi_key] = _coerce_filter_value(val[1], "equal_to")
                    neg = "NOT " if op == "not_between" else ""
                    clauses.append(f"{col} {neg}BETWEEN %({lo_key})s AND %({hi_key})s")
                    idx += 1
                continue

            op_tpl = FILTER_OPERATORS.get(op)
            if op_tpl:
                param_key = f"sf_{idx}_val"
                op_sql = op_tpl.format(prefix="sf_", idx=idx)
                clauses.append(f"{col} {op_sql}")
                params[param_key] = _coerce_filter_value(val, op)
                idx += 1

        return clauses
