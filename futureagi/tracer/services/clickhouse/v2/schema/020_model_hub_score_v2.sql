-- =============================================================================
-- 020 — model_hub_score on CH 25.3 (CDC-off home for unified annotations)
-- =============================================================================
--
-- Annotation Scores are written to PG ``model_hub_score`` (the unified Score
-- model). The observe filter builder resolves "which spans/traces have a
-- matching annotation" with a CH subquery ``FROM model_hub_score`` (see
-- tracer/services/clickhouse/query_builders/filters.py). That CH table was
-- populated only by the PeerDB CDC chain, dropped by default
-- (``CH25_DROP_LEGACY_CDC_CHAIN``) — so CDC-off every annotation filter
-- (has_annotation / annotator / my_annotations / per-label value) 500s with
-- UNKNOWN_TABLE.
--
-- This is the v2 replacement, mirrored app-side (no CDC) by an ``Score``
-- post-save mirror (tracer/services/clickhouse/v2/score_writer.py), gated by the
-- same ``dual_write_enabled()`` flag as the trace/eval mirrors. The READ side
-- flips to it via ``CH25_SCORE_TABLE=model_hub_score_v2`` (the
-- ``score_source()`` seam). Column names mirror the legacy table's filter-read
-- contract (``deleted`` kept as a plain UInt8 so the existing
-- ``WHERE s.deleted = false`` SQL works unchanged); ``_version`` drives the
-- ReplacingMergeTree dedup.
--
-- Backfill (one-shot, run after apply):
--   the management command / script reads PG model_hub_score and bulk-inserts
--   via score_writer.mirror_scores_to_clickhouse(ids).
-- =============================================================================

CREATE TABLE IF NOT EXISTS model_hub_score_v2
(
    id                   UUID,

    -- Source reference (the filter reads observation_span_id / trace_id)
    source_type          LowCardinality(String) DEFAULT '',
    trace_id             Nullable(UUID),
    observation_span_id  Nullable(String),
    trace_session_id     Nullable(UUID),
    project_id           Nullable(UUID),

    -- What was scored
    label_id             UUID,
    value                String          DEFAULT '{}',

    -- Who scored it
    annotator_id         Nullable(UUID),

    -- Scoping
    organization_id      Nullable(UUID),

    -- Soft-delete (plain column so the existing filter SQL `s.deleted = false`
    -- is reused verbatim through the score_source() seam — no predicate change)
    deleted              UInt8           DEFAULT 0,
    deleted_at           Nullable(DateTime64(3, 'UTC')),

    -- Timestamps
    created_at           DateTime64(3, 'UTC'),
    updated_at           DateTime64(3, 'UTC'),

    -- RMT version — latest write wins
    _version             UInt64          DEFAULT toUnixTimestamp64Nano(now64(9, 'UTC')),

    INDEX idx_trace_id            trace_id             TYPE bloom_filter GRANULARITY 1,
    INDEX idx_observation_span_id observation_span_id  TYPE bloom_filter GRANULARITY 1,
    INDEX idx_label_id            label_id             TYPE bloom_filter GRANULARITY 1,
    INDEX idx_annotator_id        annotator_id         TYPE bloom_filter GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(created_at)
ORDER BY (label_id, created_at, id)
SETTINGS index_granularity = 8192, allow_nullable_key = 1;
