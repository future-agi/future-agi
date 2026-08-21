-- =============================================================================
-- 025 — ingestion-fed span-attribute catalog (storage contract only)
-- =============================================================================
--
-- This migration creates the independent lookup storage used by the later
-- PostHog-style attribute-discovery path. It is deliberately SCHEMA ONLY:
-- no ALTER of `spans`, no insert-time materialized view, no backfill, and no
-- reader/writer activation. A later, feature-gated change will dual-write
-- bounded catalog batches from ingestion and populate history explicitly.
--
-- SCALE / SAFETY CONTRACT
--   * Four independent tables keep key discovery, selectable values,
--     checkpoint progress, and reader activation independently operable.
--   * Project-hash partitioning has a fixed 64-bucket ceiling across every
--     epoch. `catalog_epoch` remains in the three data/progress sorting keys
--     for logical generation isolation, but it is deliberately not a partition
--     dimension: successive rebuilds cannot create unbounded partition fan-out.
--   * The activation table keeps one replaceable pointer per project. Rollback
--     writes a newer activation state; it does not read an epoch history from
--     that table's sorting key.
--   * There are intentionally NO occurrence counters. Replayed ingestion can
--     update first/last-seen bounds without inflating a user-visible count.
--   * Search skip indexes are deferred until the folded-storage contract makes
--     the full Unicode-safe predicate indexable. The current conservative OR
--     branch prevents an ngram bloom filter from excluding any granule.
--   * Value fingerprints are lowercase SHA-256 hex (`FixedString(64)`), i.e.
--     a semantic 256-bit fingerprint transported safely over JSONEachRow.
--     The shared Python/Go codec and golden fixtures pin the byte contract.
--
-- VALUE SHAPE CONTRACT
--   * string / number / boolean attributes may emit one selectable value.
--   * arrays may emit their scalar members independently; nested arrays,
--     objects, and null members emit no value row.
--   * map / json attributes are key-only and emit no value row.
-- These rules prevent row-expanding work inside ClickHouse. Any expansion is
-- bounded in the application/collector batch before insertion.
-- =============================================================================

CREATE TABLE IF NOT EXISTS span_attribute_key_catalog
(
    project_id      UUID,
    attribute_key   String,
    key_folded      String,
    attribute_type  Enum8(
        'string' = 1,
        'number' = 2,
        'boolean' = 3,
        'array' = 4,
        'map' = 5,
        'json' = 6
    ),
    first_seen      SimpleAggregateFunction(min, DateTime64(6, 'UTC')),
    last_seen       SimpleAggregateFunction(max, DateTime64(6, 'UTC')),
    catalog_epoch   UInt16
)
ENGINE = AggregatingMergeTree
PARTITION BY cityHash64(project_id) % 64
ORDER BY (project_id, catalog_epoch, key_folded, attribute_key, attribute_type)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS span_attribute_value_catalog
(
    project_id        UUID,
    attribute_key     String,
    attribute_type    Enum8(
        'string' = 1,
        'number' = 2,
        'boolean' = 3,
        'array' = 4,
        'map' = 5,
        'json' = 6
    ),
    value_fingerprint FixedString(64),
    value_json        SimpleAggregateFunction(anyLast, String),
    value_search_text SimpleAggregateFunction(anyLast, String),
    first_seen        SimpleAggregateFunction(min, DateTime64(6, 'UTC')),
    last_seen         SimpleAggregateFunction(max, DateTime64(6, 'UTC')),
    catalog_epoch     UInt16
)
ENGINE = AggregatingMergeTree
PARTITION BY cityHash64(project_id) % 64
ORDER BY
(
    project_id,
    catalog_epoch,
    attribute_key,
    attribute_type,
    value_fingerprint
)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS span_attribute_catalog_checkpoints
(
    project_id                 UUID,
    catalog_epoch              UInt16,
    window_start               DateTime64(6, 'UTC'),
    window_end                 DateTime64(6, 'UTC'),
    source_version_fence       UInt64,
    cursor_observation_type    String,
    cursor_service_name        String,
    cursor_trace_id            String,
    cursor_span_id             String,
    status                     Enum8(
        'pending' = 1,
        'running' = 2,
        'complete' = 3,
        'gap' = 4,
        'failed' = 5
    ),
    source_rows                UInt64,
    processed_rows             UInt64,
    key_rows                   UInt64,
    value_rows                 UInt64,
    gap_count                  UInt64,
    gap_reasons                Array(String),
    run_id                     UUID,
    worker_id                  String,
    error                      String,
    started_at                 DateTime64(6, 'UTC'),
    updated_at                 DateTime64(6, 'UTC'),
    finished_at                Nullable(DateTime64(6, 'UTC')),
    _version                   UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(project_id) % 64
ORDER BY (project_id, catalog_epoch, window_start, window_end)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS span_attribute_catalog_activations
(
    project_id        UUID,
    catalog_epoch     UInt16,
    handoff_start     DateTime64(6, 'UTC'),
    handoff_end       DateTime64(6, 'UTC'),
    writer_watermark  DateTime64(6, 'UTC'),
    status            Enum8(
        'shadow' = 1,
        'active' = 2,
        'disabled' = 3
    ),
    qualified_at      DateTime64(6, 'UTC'),
    updated_at        DateTime64(6, 'UTC'),
    _version          UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(project_id) % 64
ORDER BY (project_id)
SETTINGS index_granularity = 8192;
