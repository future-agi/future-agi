-- Additive live-ingestion coverage for common dashboard string breakdowns.
--
-- Keep 021 immutable: production schema application records each file hash.
-- Use a separate table so deployment/backfill never alters, truncates, or
-- risks double-counting the existing dashboard_attr_rollup data.

CREATE TABLE IF NOT EXISTS dashboard_attr_rollup_common_keys
(
    project_id   UUID,
    hour         DateTime('UTC'),
    attr_key     LowCardinality(String),
    attr_value   String,
    n            AggregateFunction(count),
    latency_sum  AggregateFunction(sum, Int64),

    INDEX idx_attr_value attr_value TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (project_id, hour, attr_key, attr_value)
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS dashboard_attr_rollup_common_keys_mv
TO dashboard_attr_rollup_common_keys
AS
SELECT
    project_id,
    toStartOfHour(start_time)            AS hour,
    attr_key,
    attrs_string[attr_key]               AS attr_value,
    countState()                         AS n,
    sumState(toInt64(latency_ms))        AS latency_sum
FROM spans
ARRAY JOIN ['llm.model_name', 'user.country'] AS attr_key
WHERE is_deleted = 0
  AND parent_span_id = ''
  AND mapContains(attrs_string, attr_key)
GROUP BY project_id, toStartOfHour(start_time), attr_key, attrs_string[attr_key];
