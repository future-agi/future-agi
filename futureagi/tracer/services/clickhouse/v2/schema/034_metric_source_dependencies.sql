-- cluster-shard-key: metric_source_dependencies = cityHash64(project_id)

-- Last committed source membership remains available even after source merges
-- remove its preimage. Both old and new owners are invalidated on moves/remaps.
-- Publish with fields and queue state, not through an independent materialized
-- view. No TTL: explicit retention purge removes ALL expired payload revisions.
CREATE TABLE IF NOT EXISTS metric_source_dependencies
(
    org_id UUID,
    project_id UUID,
    trace_id String,
    span_id String,
    raw_session_id String,
    canonical_session_id String,
    raw_user_id String,
    canonical_user_id String,
    unit_start DateTime64(6, 'UTC'),
    is_deleted UInt8,
    generation UInt64,
    attempt_id UUID,
    _version UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(project_id) % 32
ORDER BY (org_id, project_id, trace_id, span_id, generation, attempt_id);
