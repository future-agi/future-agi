"""
ClickHouse Analytics Backend

PostgreSQL + ClickHouse unified analytics stack.
PeerDB CDC replicates data from PostgreSQL to ClickHouse.
All analytics, dashboarding, and filtering reads are served by ClickHouse.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tracer.services.clickhouse.client import (
        ClickHouseClient,
        get_clickhouse_client,
        is_clickhouse_enabled,
    )
    from tracer.services.clickhouse.consistency import ConsistencyChecker, HealthStatus
    from tracer.services.clickhouse.query_service import (
        AnalyticsQueryService,
        QueryResult,
        QueryType,
    )

_LAZY_EXPORTS = {
    "ClickHouseClient": (".client", "ClickHouseClient"),
    "get_clickhouse_client": (".client", "get_clickhouse_client"),
    "is_clickhouse_enabled": (".client", "is_clickhouse_enabled"),
    "ConsistencyChecker": (".consistency", "ConsistencyChecker"),
    "HealthStatus": (".consistency", "HealthStatus"),
    "AnalyticsQueryService": (".query_service", "AnalyticsQueryService"),
    "QueryResult": (".query_service", "QueryResult"),
    "QueryType": (".query_service", "QueryType"),
}

__all__ = [
    "ClickHouseClient",
    "get_clickhouse_client",
    "is_clickhouse_enabled",
    "AnalyticsQueryService",
    "QueryType",
    "QueryResult",
    "ConsistencyChecker",
    "HealthStatus",
]


def __getattr__(name: str) -> Any:
    """Load Django-backed conveniences only when callers request them."""

    try:
        module_name, attribute_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
