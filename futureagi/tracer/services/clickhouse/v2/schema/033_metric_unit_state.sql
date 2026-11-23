-- cluster-shard-key: metric_unit_state = cityHash64(project_id)

-- Durable queue: nominate raw overdue/open rows using these indexes (DCM2),
-- then verify the latest committed state by immutable unit_id. No sliding lower
-- time bound, and no deletion/time predicate before the committed collapse.
-- Settled tombstones survive merges. No TTL or automatic tombstone cleanup.
CREATE TABLE IF NOT EXISTS metric_unit_state
(
    org_id UUID,
    project_id UUID,
    level LowCardinality(String),
    unit_id String,
    last_arrival_ns UInt64,
    first_seen DateTime64(6, 'UTC'),
    reopened UInt8,
    is_deleted UInt8,
    _version UInt64,
    due_at DateTime64(6, 'UTC'),
    generation UInt64,
    attempt_id UUID,
    fencing_epoch UInt32,
    row_sequence UInt32,
    INDEX ix_due due_at TYPE minmax GRANULARITY 1,
    INDEX ix_deleted is_deleted TYPE minmax GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(project_id) % 32
ORDER BY (org_id, project_id, level, unit_id, generation, attempt_id);
