"""DDL for the legacy ``default``-database ClickHouse tables that the model-hub
app boots: ``events`` (ML-monitoring) and ``llm_logs`` (LLM usage telemetry).

Both were plain ``MergeTree`` created inline in ``apps.py``, so on a multi-replica
cluster each row lived on whichever replica took the write and a single-replica
read saw only its slice. They become ``ReplicatedMergeTree`` here — plain, not
``Replacing``: both are append-only logs whose ORDER BY keys are not unique
(``llm_logs`` orders by ``(LLMModelName, EventDateTime)``), so a ``Replacing``
engine would silently collapse distinct rows.

Centralised so the engine choice has one home and the default-to-replicated
migration command can recreate the same schema in a target database.
"""
from __future__ import annotations

from agentic_eval.core.database.ch_vector import (
    ClickHouseVectorDB,
    build_replicated_engine,
)

EVENTS_TABLE = "events"
LLM_LOGS_TABLE = "llm_logs"


def _qualified(table: str, database: str | None) -> str:
    return f"{database}.{table}" if database else table


def ensure_events_table(client, *, database: str | None = None, cluster: str | None = None) -> None:
    """Create the ML-monitoring ``events`` table if absent (idempotent).

    Also back-fills ``original_uuid`` on pre-existing tables that predate the
    column: several downstream readers (``SELECT DISTINCT original_uuid FROM
    events``) fail with "Unknown identifier" without it, and a bare
    ``CREATE TABLE IF NOT EXISTS`` is a no-op against an existing table.
    """
    clustered = ClickHouseVectorDB.is_clustered(client)
    engine, on_cluster = build_replicated_engine(
        "MergeTree()",
        EVENTS_TABLE,
        clustered=clustered,
        database=database,
        cluster=cluster,
    )
    qualified = _qualified(EVENTS_TABLE, database)
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {qualified}{on_cluster} (
            UUID UUID,
            original_uuid UUID DEFAULT UUID,
            EventDate Date,
            EventDateTime DateTime,
            EventName String,
            EventType String,
            AIModel String DEFAULT '',
            OrgID String,
            PredictionID String DEFAULT '',
            ModelVersion String DEFAULT '',
            BatchID String DEFAULT '',
            Environment UInt8 DEFAULT 0,
            Properties Nested(
                Key String,
                Value String,
                DataType String
            ),
            Features Nested(
                Key String,
                Value String,
                DataType String
            ),
            ActualLabel Nested(
                Key String,
                Value String,
                DataType String
            ),
            PredictionLabel Nested(
                Key String,
                Value String,
                DataType String
            ),
            EvalResults Nested(
                Key String,
                Value String,
                DataType String
            ),
            ShapValues Nested(
                Key String,
                Value String,
                DataType String
            ),
            Tags Nested(
                Key String,
                Value String,
                DataType String
            ),
            Embedding Array(Float32) DEFAULT [],
            deleted UInt8 DEFAULT 0
        ) ENGINE = {engine}
        PARTITION BY toYYYYMM(EventDate)
        ORDER BY (EventDate, EventName, OrgID, UUID)
        """
    )
    _backfill_events_original_uuid(client, qualified, on_cluster)


def _backfill_events_original_uuid(client, qualified: str, on_cluster: str) -> None:
    """Add ``original_uuid`` to an existing events table if absent.

    ``ADD COLUMN IF NOT EXISTS`` is itself idempotent, but querying
    ``system.columns`` first avoids a DDL replication round-trip on every
    boot in the common case.
    """
    columns = client.execute(f"DESCRIBE TABLE {qualified}")
    if any(row[0] == "original_uuid" for row in columns):
        return
    client.execute(
        f"ALTER TABLE {qualified}{on_cluster} "
        f"ADD COLUMN IF NOT EXISTS original_uuid UUID DEFAULT UUID"
    )


def ensure_llm_logs_table(client, *, database: str | None = None, cluster: str | None = None) -> None:
    """Create the ``llm_logs`` usage-telemetry table if absent (idempotent)."""
    engine, on_cluster = build_replicated_engine(
        "MergeTree",
        LLM_LOGS_TABLE,
        clustered=ClickHouseVectorDB.is_clustered(client),
        database=database,
        cluster=cluster,
    )
    client.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_qualified(LLM_LOGS_TABLE, database)}{on_cluster}
        (
            `EventDateTime` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
            `EventDate` Date,
            `TraceId` String CODEC(ZSTD(1)),
            `SpanId` String CODEC(ZSTD(1)),
            `SeverityText` LowCardinality(String) CODEC(ZSTD(1)),
            `SeverityNumber` Int32 CODEC(ZSTD(1)),
            `ServiceName` LowCardinality(String) CODEC(ZSTD(1)),
            `LLMModelName` LowCardinality(String) CODEC(ZSTD(1)),
            `UserId` String CODEC(ZSTD(1)),
            `SessionId` String CODEC(ZSTD(1)),
            `RequestBody` String CODEC(ZSTD(1)),
            `ResponseBody` String CODEC(ZSTD(1)),
            `RequestTokens` Int32 CODEC(ZSTD(1)),
            `ResponseTokens` Int32 CODEC(ZSTD(1)),
            `ResponseTime` Float32 CODEC(ZSTD(1)),
            `LogAttributes` Map(LowCardinality(String), String) CODEC(ZSTD(1)),
            `ResourceAttributes` Map(LowCardinality(String), String) CODEC(ZSTD(1)),
            INDEX idx_trace_id TraceId TYPE bloom_filter(0.001) GRANULARITY 1,
            INDEX idx_llm_model_name LLMModelName TYPE bloom_filter(0.001) GRANULARITY 1,
            INDEX idx_user_id UserId TYPE bloom_filter(0.001) GRANULARITY 1,
            INDEX idx_session_id SessionId TYPE bloom_filter(0.001) GRANULARITY 1,
            INDEX idx_request_body RequestBody TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 1,
            INDEX idx_response_body ResponseBody TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 1
        )
        ENGINE = {engine}
        PARTITION BY EventDate
        ORDER BY (LLMModelName, EventDateTime)
        SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1
        """
    )
