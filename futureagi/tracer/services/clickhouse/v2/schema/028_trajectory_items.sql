-- =============================================================================
-- 028 — Content-addressed trajectory item store + span item-pointer columns
-- =============================================================================
--
-- Multi-turn AI agents resend their accumulated message history, system prompt,
-- and tool definitions on every LLM call, causing O(k^2) storage amplification
-- inside `spans.input` / `spans.output`.
--
-- This migration introduces:
--   1. `trajectory_items` — deduplicated, immutable message & tool-definition
--      items addressed by (project_id, scope_id, item_hash).
--        • Static system prompts & tool schemas use scope_id = '__project__'.
--        • Conversation turns use scope_id = trace_session_id (or trace_id).
--        • item_hash is the 32-char lowercase hex encoding of the first 16
--          bytes of SHA-256 over key-sorted canonical JSON (pure ASCII for
--          HTTP JSONEachRow & clickhouse-connect).
--   2. Additive pointer columns on `spans` with safe defaults so historical
--      rows (`payload_encoding = 'inline'`) and legacy collectors continue
--      working with zero downtime and zero part rewrites.
--
-- No TTL is declared here: retention stays owned by ee/usage/tasks/retention.py
-- (per 020_remove_ttls.sql); cold storage tiering is deferred to a later phase.
-- =============================================================================

CREATE TABLE IF NOT EXISTS trajectory_items
(
    project_id      UUID,
    scope_id        String,
    item_hash       FixedString(32),
    item_type       LowCardinality(String) DEFAULT 'raw',
    role            LowCardinality(String) DEFAULT '',
    content         String                 CODEC(ZSTD(3)),
    content_bytes   UInt32                 MATERIALIZED length(content),
    first_seen_at   DateTime64(6, 'UTC')   DEFAULT now64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(first_seen_at)
PARTITION BY toYYYYMM(first_seen_at)
PRIMARY KEY (project_id, scope_id)
ORDER BY    (project_id, scope_id, item_hash)
SETTINGS
    index_granularity       = 8192,
    index_granularity_bytes = 67108864;

ALTER TABLE spans
    ADD COLUMN IF NOT EXISTS input_item_hashes   Array(String)          DEFAULT []       CODEC(ZSTD(1)),
    ADD COLUMN IF NOT EXISTS output_item_hashes  Array(String)          DEFAULT []       CODEC(ZSTD(1)),
    ADD COLUMN IF NOT EXISTS tool_def_hashes     Array(String)          DEFAULT []       CODEC(ZSTD(1)),
    ADD COLUMN IF NOT EXISTS item_scope_id       String                 DEFAULT ''       CODEC(ZSTD(1)),
    ADD COLUMN IF NOT EXISTS payload_encoding    LowCardinality(String) DEFAULT 'inline' CODEC(ZSTD(1)),
    ADD COLUMN IF NOT EXISTS payload_envelope    String                 DEFAULT ''       CODEC(ZSTD(1));
