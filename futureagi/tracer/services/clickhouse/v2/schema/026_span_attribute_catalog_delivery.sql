-- =============================================================================
-- 026 — durable catalog delivery/source-fence state (additive tables only)
-- =============================================================================
--
-- This migration adds the transport-neutral ledger used by BOTH direct and
-- Kafka catalog ingestion. It deliberately does not ALTER, SELECT from, or
-- attach a materialized view to `spans` (or any other existing table).
--
-- Source files remain single-node MergeTree DDL for local/dev. Production's
-- production schema runner maps these engines to the established Keeper path
-- /clickhouse/tables/ch25/{shard}/<table> and fans DDL across the `cluster`
-- topology. Production is currently one shard / three replicas; keeping the
-- same source/rewrite model prevents three independent local ledgers.
-- =============================================================================

CREATE TABLE IF NOT EXISTS span_attribute_catalog_deliveries
(
    project_id              UUID,
    catalog_epoch           UInt16,
    producer_stream_id      UUID,
    sequence                UInt64,
    envelope_format         String,
    envelope_version        UInt16,
    envelope_id             FixedString(64),
    payload_sha256          FixedString(64),
    previous_payload_sha256 FixedString(64),
    source_batch_digest     FixedString(64),
    outcome                 Enum8(
        'committed' = 1,
        'gap' = 2
    ),
    gap_reasons             Array(String),
    source_min_start        DateTime64(6, 'UTC'),
    source_max_start        DateTime64(6, 'UTC'),
    source_rows             UInt64,
    key_rows                UInt64,
    value_rows              UInt64,
    transport               Enum8(
        'direct' = 1,
        'kafka' = 2,
        'reconcile' = 3
    ),
    kafka_partition         Int32 DEFAULT -1,
    kafka_offset            Int64 DEFAULT -1,
    delivered_at            DateTime64(6, 'UTC'),
    _version                UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(project_id) % 64
ORDER BY (project_id, catalog_epoch, producer_stream_id, sequence)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS span_attribute_catalog_source_streams
(
    project_id              UUID,
    catalog_epoch           UInt16,
    producer_stream_id      UUID,
    envelope_version        UInt16,
    first_sequence          UInt64,
    last_sequence           UInt64,
    frozen_sequence         UInt64,
    terminal_payload_sha256 FixedString(64),
    source_fence_digest     FixedString(64),
    status                  Enum8(
        'open' = 1,
        'frozen' = 2,
        'complete' = 3,
        'gap' = 4,
        'failed' = 5
    ),
    gap_count               UInt64,
    gap_reasons             Array(String),
    started_at              DateTime64(6, 'UTC'),
    updated_at              DateTime64(6, 'UTC'),
    frozen_at               Nullable(DateTime64(6, 'UTC')),
    _version                UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(project_id) % 64
ORDER BY (project_id, catalog_epoch, producer_stream_id)
SETTINGS index_granularity = 8192;
