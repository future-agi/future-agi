"""Explicit CH25 query service for v2 query-builder reads."""

from __future__ import annotations

import threading

from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_service import (
    AnalyticsQueryService,
    QueryExecutor,
)
from tracer.services.clickhouse.v2 import get_v2_config

_client: ClickHouseClient | None = None
_client_lock = threading.Lock()


def get_v2_query_client() -> ClickHouseClient:
    """Return the process-wide pooled native client for direct-write CH25."""

    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                config = get_v2_config()
                _client = ClickHouseClient(
                    host=config["host"],
                    port=config["tcp_port"],
                    user=config["user"],
                    password=config["password"],
                    database=config["database"],
                    server_enforced_readonly=config["server_enforced_readonly"],
                )
    return _client


def reset_v2_query_client() -> None:
    """Close and clear the singleton; intended for test/config reloads."""

    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
        _client = None


class V2AnalyticsQueryService(AnalyticsQueryService):
    """Run generic read SQL against the configured direct-write CH25 cluster."""

    def __init__(self) -> None:
        self._ch_client = get_v2_query_client()

    @staticmethod
    def _configured_eval_logger_table() -> str:
        """Return the authoritative eval table on the CH25 connection.

        The span storage generation and the eval table name are independent
        rollout decisions. Production currently writes fresh eval rows to the
        legacy-named table in the CH25 database, so the connection stays V2
        while the physical eval source follows the explicit setting.
        """

        table, _ = eval_logger_source()
        return table

    def get_eval_config_ids_with_data_ch(
        self,
        project_id: str,
        timeout_ms: int = 5000,
        window_days: int | None = 30,
        candidate_config_ids: list[str] | None = None,
    ) -> list[str]:
        return super().get_eval_config_ids_with_data_ch(
            project_id,
            timeout_ms=timeout_ms,
            window_days=window_days,
            candidate_config_ids=candidate_config_ids,
            eval_logger_table=self._configured_eval_logger_table(),
        )

    def get_eval_config_ids_for_traces_ch(
        self,
        trace_ids: list[str],
        candidate_config_ids: list[str],
        timeout_ms: int = 3000,
    ) -> list[str]:
        return super().get_eval_config_ids_for_traces_ch(
            trace_ids,
            candidate_config_ids,
            timeout_ms=timeout_ms,
            eval_logger_table=self._configured_eval_logger_table(),
        )

    def get_eval_config_ids_for_candidates_ch(
        self,
        candidate_config_ids: list[str],
        timeout_ms: int = 5000,
        window_days: int | None = 30,
    ) -> list[str]:
        return super().get_eval_config_ids_for_candidates_ch(
            candidate_config_ids,
            timeout_ms=timeout_ms,
            window_days=window_days,
            eval_logger_table=self._configured_eval_logger_table(),
        )

    def get_children_eval_metrics_ch(
        self,
        span_ids: list[str],
        timeout_ms: int = 5000,
    ) -> list[dict]:
        return super().get_children_eval_metrics_ch(
            span_ids,
            timeout_ms=timeout_ms,
            eval_logger_table=self._configured_eval_logger_table(),
        )

    def get_eval_detail_ch(
        self,
        span_id: str,
        config_id: str,
        *,
        project_id: str,
        timeout_ms: int = 5000,
    ) -> dict | None:
        """Read eval details from the authoritative table on direct CH25."""

        return super().get_eval_detail_ch(
            span_id,
            config_id,
            project_id=project_id,
            timeout_ms=timeout_ms,
            eval_logger_table=self._configured_eval_logger_table(),
        )

    def get_trace_eval_scores_ch(
        self,
        trace_ids: list[str],
        config_ids: list[str],
        timeout_ms: int = 5000,
    ) -> list[dict]:
        return super().get_trace_eval_scores_ch(
            trace_ids,
            config_ids,
            timeout_ms=timeout_ms,
            eval_logger_table=self._configured_eval_logger_table(),
        )


def query_service_for_builder(
    query_type: str,
    builder_class: type[BaseQueryBuilder],
    fallback: QueryExecutor,
) -> QueryExecutor:
    """Use the service paired with this query type's dispatched builder.

    Builder inheritance alone is not a safe routing key: several list builders
    share base classes, and a future multiple-inheritance change could make a
    class look like the v2 implementation for a different query type. Pair the
    class with the same explicit query type passed to the dispatch factory so a
    v2 SQL builder can only execute on its matching direct-write CH25 service.
    Explicit test executors remain untouched.
    """

    if not isinstance(fallback, AnalyticsQueryService):
        return fallback

    from tracer.services.clickhouse.v2.dispatch import get_v2_class

    normalized_query_type = (
        query_type.upper() if isinstance(query_type, str) else str(query_type).upper()
    )
    v2_class = get_v2_class(normalized_query_type)
    if v2_class is not None and issubclass(builder_class, v2_class):
        return V2AnalyticsQueryService()
    return fallback


__all__ = [
    "V2AnalyticsQueryService",
    "get_v2_query_client",
    "query_service_for_builder",
    "reset_v2_query_client",
]
