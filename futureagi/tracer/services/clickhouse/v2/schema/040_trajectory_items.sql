-- =============================================================================
-- 040 — Content-addressed trajectory item store + span item-pointer columns
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
-- No TTL and no time partitioning: items are content-addressed and shared by
-- every span that references them, so their age says nothing about whether a
-- span inside retention still needs them. Retention and erasure must be
-- reference-aware and are not part of this file (the store ships disabled).
-- Partitions are a stable identity bucket (as in 036/038) so a re-sent item
-- always lands in the same partition and merges collapse it.
--
-- `persisted_at` is the ReplacingMergeTree version: the copy persisted last
-- wins among rows with the same key (canonical-equal content).
--
-- Topology: like `spans`, this is created local, or Keeper-coordinated when
-- apply_schema runs with --replicated (it adds ON CLUSTER at apply time). No v2
-- table has a Distributed facade yet; a multi-shard layout must add local
-- storage plus a Distributed table for trajectory_items and alter spans on
-- every shard, together with the sharding of spans itself.
--
-- cluster-shard-key: trajectory_items = cityHash64(project_id)
-- =============================================================================

CREATE TABLE IF NOT EXISTS trajectory_items
(
    project_id      UUID,
    scope_id        String,
    item_hash       FixedString(32),
    item_type       LowCardinality(String) DEFAULT 'raw',
    role            LowCardinality(String) DEFAULT '',
    content         String                 CODEC(ZSTD(3)),
    persisted_at    DateTime64(6, 'UTC')   DEFAULT now64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(persisted_at)
PARTITION BY cityHash64(project_id) % 32
PRIMARY KEY (project_id, scope_id, item_hash)
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
