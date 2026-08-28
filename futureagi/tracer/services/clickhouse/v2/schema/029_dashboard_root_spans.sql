-- Exact trace-level dashboard facts.
--
-- Long-window trace dashboards currently read ``spans FINAL``. Attribute
-- filters and breakdowns prevent ClickHouse from using the narrow root-span
-- projection, so a 12-month query still scans every child span in the tenant.
-- This additive table keeps the same queryable trace fields and typed
-- attribute maps, but stores only root spans in project/time order.
--
-- Correctness and rollout:
--   * ReplacingMergeTree keeps the source version/tombstone contract.
--   * The MV includes tombstones; ``FINAL`` reads therefore cannot resurrect a
--     deleted trace.
--   * Existing history is populated separately. Application routing remains
--     fail-closed until DASHBOARD_ROOT_SPANS_COVERED_SINCE and either the
--     project allowlist or explicit all-project proof establish coverage.
--   * No existing table, partition, or row is mutated by this schema file.
--
-- This is intentionally a narrow exception to 002's ban on the old
-- JSON-shredding insert-time MV. The trigger below performs only a root-row
-- predicate plus a column-for-column copy: no JSON parsing, ARRAY JOIN, JOIN,
-- aggregation, or Python work. Keeping it synchronous is what makes a claimed
-- coverage timestamp exact; an asynchronous best-effort collector mirror could
-- silently miss rows. Qualify collector throughput before enabling the read
-- flag in each environment.

CREATE TABLE IF NOT EXISTS dashboard_root_spans
(
    project_id          UUID,
    observation_type    LowCardinality(String),
    service_name        LowCardinality(String) DEFAULT '',
    start_time          DateTime64(6, 'UTC'),
    trace_id            String,
    id                  String,
    parent_span_id      String DEFAULT '',
    name                String DEFAULT '',

    latency_ms          Int32 DEFAULT 0,
    end_user_id         Nullable(UUID),
    trace_session_id    Nullable(UUID),
    prompt_version_id   Nullable(UUID),
    prompt_label_id     Nullable(UUID),

    status              LowCardinality(String) DEFAULT '',
    model               LowCardinality(String) DEFAULT '',
    provider            LowCardinality(String) DEFAULT '',
    prompt_tokens       Int32 DEFAULT 0,
    completion_tokens   Int32 DEFAULT 0,
    total_tokens        Int32 DEFAULT 0,
    cost                Float64 DEFAULT 0,

    attrs_string        Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    attrs_number        Map(LowCardinality(String), Float64) CODEC(ZSTD(1)),
    attrs_bool          Map(LowCardinality(String), UInt8) CODEC(ZSTD(1)),
    attributes_extra    String DEFAULT '{}' CODEC(ZSTD(3)),
    tags                String DEFAULT '[]' CODEC(ZSTD(1)),

    is_deleted          UInt8 DEFAULT 0,
    _version            UInt64,

    INDEX idx_trace_id trace_id TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_attrs_str_keys mapKeys(attrs_string)
        TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attrs_num_keys mapKeys(attrs_number)
        TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attrs_bool_keys mapKeys(attrs_bool)
        TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_status status TYPE set(20) GRANULARITY 1,
    INDEX idx_latency_ms latency_ms TYPE minmax() GRANULARITY 1,
    INDEX idx_total_tokens total_tokens TYPE minmax() GRANULARITY 1,
    INDEX idx_cost cost TYPE minmax() GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toYYYYMM(start_time)
ORDER BY (project_id, toStartOfHour(start_time), trace_id, id)
SETTINGS
    index_granularity = 8192,
    index_granularity_bytes = 67108864,
    merge_max_block_size_bytes = 67108864,
    deduplicate_merge_projection_mode = 'rebuild',
    allow_nullable_key = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS dashboard_root_spans_mv
TO dashboard_root_spans
AS
SELECT
    project_id,
    observation_type,
    service_name,
    start_time,
    trace_id,
    id,
    parent_span_id,
    name,
    latency_ms,
    end_user_id,
    trace_session_id,
    prompt_version_id,
    prompt_label_id,
    status,
    model,
    provider,
    prompt_tokens,
    completion_tokens,
    total_tokens,
    cost,
    attrs_string,
    attrs_number,
    attrs_bool,
    attributes_extra,
    tags,
    is_deleted,
    _version
FROM spans
WHERE parent_span_id = '';
