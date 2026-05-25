"""
Analytics Query Service - Dispatch Layer

Routes analytics queries to ClickHouse or PostgreSQL based on per-query-type
feature flags, with automatic fallback and shadow mode support.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import structlog
from django.conf import settings
from django.db import connection as pg_connection

from tracer.services.clickhouse.client import (
    ClickHouseClient,
    get_clickhouse_client,
    is_clickhouse_enabled,
)

logger = structlog.get_logger(__name__)


class QueryType(str, Enum):
    """Supported query types with per-type routing."""

    TIME_SERIES = "TIME_SERIES"
    TRACE_LIST = "TRACE_LIST"
    SESSION_LIST = "SESSION_LIST"
    EVAL_METRICS = "EVAL_METRICS"
    ERROR_ANALYSIS = "ERROR_ANALYSIS"
    SPAN_LIST = "SPAN_LIST"
    TRACE_OF_SESSION_LIST = "TRACE_OF_SESSION_LIST"
    SPAN_GRAPH = "SPAN_GRAPH"
    VOICE_CALL_LIST = "VOICE_CALL_LIST"
    SESSION_ANALYTICS = "SESSION_ANALYTICS"
    ANNOTATION_GRAPH = "ANNOTATION_GRAPH"
    TRACE_DETAIL = "TRACE_DETAIL"
    MONITOR_METRICS = "MONITOR_METRICS"
    ANNOTATION_DETAIL = "ANNOTATION_DETAIL"
    VOICE_CALL_DETAIL = "VOICE_CALL_DETAIL"


class RouteDecision(str, Enum):
    """Possible routing decisions."""

    POSTGRES = "postgres"
    CLICKHOUSE = "clickhouse"
    AUTO = "auto"
    SHADOW = "shadow"


@dataclass
class QueryResult:
    """Container for query results with metadata."""

    data: Any  # Can be list, dict, or any serializable structure
    row_count: int
    backend_used: str  # "clickhouse" or "postgres"
    query_time_ms: float
    columns: Optional[List[str]] = None

    @classmethod
    def from_clickhouse_rows(cls, rows, columns, query_time_ms):
        """Create from ClickHouse result rows."""
        col_names = [c[0] if isinstance(c, tuple) else c for c in columns]
        data = [dict(zip(col_names, row)) for row in rows]
        return cls(
            data=data,
            row_count=len(rows),
            backend_used="clickhouse",
            query_time_ms=query_time_ms,
            columns=col_names,
        )


class AnalyticsQueryService:
    """
    Service for routing analytics queries to the appropriate backend.

    Supports per-query-type routing via CH_ROUTE_* settings:
    - "postgres": Always use PostgreSQL
    - "clickhouse": Always use ClickHouse (fail if unavailable)
    - "auto": Try ClickHouse, fallback to PostgreSQL on failure
    - "shadow": Run both, compare, return PostgreSQL result
    """

    def __init__(self):
        self._ch_client: Optional[ClickHouseClient] = None

    @property
    def ch_client(self) -> ClickHouseClient:
        if self._ch_client is None:
            self._ch_client = get_clickhouse_client()
        return self._ch_client

    def get_route(self, query_type: QueryType) -> RouteDecision:
        """Get the routing decision for a query type."""
        ch_settings = getattr(settings, "CLICKHOUSE", {})
        route_key = f"CH_ROUTE_{query_type.value}"
        route = ch_settings.get(route_key, "postgres")

        # If shadow mode is globally enabled, override to shadow
        if ch_settings.get("CH_SHADOW_MODE", False) and route != "postgres":
            return RouteDecision.SHADOW

        try:
            return RouteDecision(route)
        except ValueError:
            return RouteDecision.POSTGRES

    def should_use_clickhouse(self, query_type: QueryType) -> bool:
        """Check if ClickHouse should be used for this query type."""
        route = self.get_route(query_type)
        if route == RouteDecision.POSTGRES:
            return False
        return is_clickhouse_enabled()

    def execute_ch_query(
        self, query: str, params: dict = None, timeout_ms: int = 10000
    ) -> QueryResult:
        """Execute a query on ClickHouse and return QueryResult."""
        start = time.monotonic()
        rows, columns, qt = self.ch_client.execute_read(
            query, params or {}, timeout_ms=timeout_ms
        )
        elapsed = (time.monotonic() - start) * 1000

        col_names = [c[0] if isinstance(c, tuple) else c for c in columns]
        data = [dict(zip(col_names, row)) for row in rows]

        logger.info(
            "ch_query_executed",
            query_time_ms=round(elapsed, 2),
            rows=len(rows),
            backend="clickhouse",
        )

        return QueryResult(
            data=data,
            row_count=len(rows),
            backend_used="clickhouse",
            query_time_ms=round(elapsed, 2),
            columns=col_names,
        )

    def get_span_attribute_keys_ch(self, project_id: str) -> List[dict]:
        """Get distinct span attribute keys with types from ClickHouse.

        Tries the denormalized ``spans`` table first (typed Maps).
        If Maps are mostly empty (MV didn't populate them), falls back
        to the CDC table ``tracer_observation_span`` and infers types
        from the raw JSON using JSONExtractRaw.
        """
        # --- Try spans Maps first (fast, typed) ---
        # This is a *discovery* query (populate a filter dropdown), not an
        # accounting one, so an approximate sample is semantically fine.
        # Two bounds keep it bounded even on very large projects:
        #   * 7-day window on `created_at` (the sort/partition key) so CH
        #     can skip partitions and granules.
        #   * `LIMIT 10000` inside each per-map subquery before the
        #     ARRAY JOIN — without this, projects with millions of spans
        #     and wide `span_attr_*` maps hit Code: 307 (max_bytes_to_read)
        #     because every row's Map gets exploded.
        # Trade-off: `argMax(type, cnt)` type resolution is now on capped
        # counts, and brand-new attribute keys added in the last hour on a
        # high-volume project may not appear until older rows drop out of
        # the LIMIT window.
        # Use argMax(type, cnt) to pick the type with the highest row count.
        # When a key exists in both span_attr_str and span_attr_num,
        # the map with more rows wins (avoids phone numbers being typed as
        # number when they appear in span_attr_num for only a few rows).
        query = """
            SELECT key, argMax(type, cnt) AS type FROM (
                SELECT key, 'text' AS type, count() AS cnt FROM (
                    SELECT span_attr_str FROM spans
                    WHERE project_id = %(project_id)s
                      AND _peerdb_is_deleted = 0
                      AND created_at >= now() - INTERVAL 7 DAY
                    LIMIT 10000
                ) ARRAY JOIN mapKeys(span_attr_str) AS key
                GROUP BY key
                UNION ALL
                SELECT key, 'number' AS type, count() AS cnt FROM (
                    SELECT span_attr_num FROM spans
                    WHERE project_id = %(project_id)s
                      AND _peerdb_is_deleted = 0
                      AND created_at >= now() - INTERVAL 7 DAY
                    LIMIT 10000
                ) ARRAY JOIN mapKeys(span_attr_num) AS key
                GROUP BY key
                UNION ALL
                SELECT key, 'boolean' AS type, count() AS cnt FROM (
                    SELECT span_attr_bool FROM spans
                    WHERE project_id = %(project_id)s
                      AND _peerdb_is_deleted = 0
                      AND created_at >= now() - INTERVAL 7 DAY
                    LIMIT 10000
                ) ARRAY JOIN mapKeys(span_attr_bool) AS key
                GROUP BY key
            )
            GROUP BY key
            ORDER BY key
            LIMIT 1000
        """
        result = self.execute_ch_query(
            query, {"project_id": project_id}, timeout_ms=10000
        )
        if len(result.data) >= 5:
            return [{"key": row["key"], "type": row["type"]} for row in result.data]

        # --- Fallback: CDC table with JSON type inference ---
        # Step 1: get a sample of span IDs (light query, no heavy columns)
        id_query = """
            SELECT id FROM tracer_observation_span
            WHERE project_id = %(project_id)s
              AND _peerdb_is_deleted = 0
            ORDER BY created_at DESC
            LIMIT 20
        """
        id_result = self.execute_ch_query(
            id_query, {"project_id": project_id}, timeout_ms=5000
        )
        sample_ids = tuple(row["id"] for row in id_result.data)
        if not sample_ids:
            return []

        # Step 2: extract keys + types from those specific rows via PREWHERE
        cdc_query = """
            SELECT key, argMax(type, type) AS type FROM (
                SELECT
                    kv.1 AS key,
                    multiIf(
                        kv.2 IN ('true', 'false'), 'boolean',
                        match(kv.2, '^-?[0-9]+(\\\\.[0-9]+)?$'), 'number',
                        'text'
                    ) AS type
                FROM (
                    SELECT DISTINCT
                        arrayJoin(JSONExtractKeysAndValuesRaw(s.span_attributes)) AS kv
                    FROM tracer_observation_span AS s
                    PREWHERE s.id IN %(sample_ids)s
                    WHERE s._peerdb_is_deleted = 0
                )
                WHERE kv.1 NOT IN ('raw_log', 'call', 'metrics_data')
            )
            GROUP BY key
            ORDER BY key
            LIMIT 1000
        """
        cdc_result = self.execute_ch_query(
            cdc_query,
            {"sample_ids": sample_ids, "project_id": project_id},
            timeout_ms=10000,
        )
        return [{"key": row["key"], "type": row["type"]} for row in cdc_result.data]

    def get_eval_config_ids_with_data_ch(self, project_id: str) -> List[str]:
        """Get distinct eval config IDs that have data for a project in ClickHouse."""
        query = """
            SELECT DISTINCT toString(custom_eval_config_id) AS config_id
            FROM tracer_eval_logger FINAL
            WHERE _peerdb_is_deleted = 0
              AND (deleted = 0 OR deleted IS NULL)
              AND trace_id IN (
                  SELECT DISTINCT trace_id
                  FROM spans
                  WHERE project_id = %(project_id)s
                    AND _peerdb_is_deleted = 0
              )
        """
        result = self.execute_ch_query(
            query, {"project_id": project_id}, timeout_ms=5000
        )
        return [row["config_id"] for row in result.data]

    def get_backend_status(self) -> Dict[str, Any]:
        """Get status of all backends and routing config."""
        ch_settings = getattr(settings, "CLICKHOUSE", {})
        status = {
            "clickhouse": {
                "enabled": is_clickhouse_enabled(),
                "connected": False,
            },
            "routing": {
                k: v for k, v in ch_settings.items() if k.startswith("CH_ROUTE_")
            },
            "shadow_mode": ch_settings.get("CH_SHADOW_MODE", False),
        }

        try:
            if is_clickhouse_enabled():
                status["clickhouse"]["connected"] = self.ch_client.ping()
        except Exception as e:
            status["clickhouse"]["error"] = str(e)

        return status
