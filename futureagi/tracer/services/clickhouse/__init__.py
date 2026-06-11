"""
ClickHouse Analytics Backend

PostgreSQL + ClickHouse unified analytics stack.
PeerDB CDC replicates data from PostgreSQL to ClickHouse.
All analytics, dashboarding, and filtering reads are served by ClickHouse.
"""

from tracer.services.clickhouse.client import (
    ClickHouseClient,
    get_clickhouse_client,
    get_v2_clickhouse_client,
    is_clickhouse_enabled,
)
from tracer.services.clickhouse.consistency import ConsistencyChecker, HealthStatus
from tracer.services.clickhouse.query_service import (
    AnalyticsQueryService,
    QueryResult,
    QueryType,
)

__all__ = [
    "ClickHouseClient",
    "get_clickhouse_client",
    "get_v2_clickhouse_client",
    "is_clickhouse_enabled",
    "AnalyticsQueryService",
    "QueryType",
    "QueryResult",
    "ConsistencyChecker",
    "HealthStatus",
]
