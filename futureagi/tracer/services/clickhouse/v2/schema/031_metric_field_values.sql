-- cluster-shard-key: metric_field_values = cityHash64(project_id)
-- cluster-shard-key: metric_field_values_checkpoint = cityHash64(project_id)

-- Attempt-qualified immutable deltas. A late earlier span may move unit_start
-- across a month, so partitioning MUST NOT depend on time or mutable membership.
-- is_deleted is logical payload: allowlist attempts, collapse, THEN filter it.
-- Time indexes only nominate candidates, never prune a logical latest collapse.
-- Complete inputs are retained for passing as well as failing units.
-- user_id is canonical, or empty for conversations with zero/several users.
-- No TTL: B07 publishes anonymous retirement parts before explicit evidence purge.
CREATE TABLE IF NOT EXISTS metric_field_values
(
    org_id UUID,
    workspace_id UUID,
    project_id UUID,
    field_id UUID,
    field_version UInt32,
    level LowCardinality(String),
    unit_id String,
    session_id String,
    user_id String,
    unit_start DateTime64(6, 'UTC'),
    bucket_start DateTime('UTC'),
    last_arrival_ns UInt64,
    settled_at Nullable(DateTime64(6, 'UTC')),
    status LowCardinality(String),
    no_value_reason LowCardinality(String),
    value_bool Nullable(UInt8),
    value_number Nullable(Float64),
    value_text Nullable(String),
    part_n Nullable(Float64),
    part_d Nullable(Float64),
    inputs Map(LowCardinality(String), Nullable(Float64)),
    sample_probability Nullable(Float64),
    sampling_scope String,
    sampling_stratum String,
    sampling_design LowCardinality(String),
    population_size Nullable(UInt64),
    invalid_probability UInt8,
    group_key String,
    is_deleted UInt8,
    _version UInt64,
    generation UInt64,
    attempt_id UUID,
    fencing_epoch UInt32,
    row_sequence UInt32,
    inputs_bool Map(String, Nullable(UInt8)),
    inputs_text Map(String, Nullable(String)),
    INDEX ix_start unit_start TYPE minmax GRANULARITY 1,
    INDEX ix_settle settled_at TYPE minmax GRANULARITY 1,
    INDEX ix_user user_id TYPE bloom_filter GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(project_id) % 32
ORDER BY (org_id, project_id, field_id, field_version, level, unit_id, generation, attempt_id);

-- DCM7: compaction writes a NEW frozen checkpoint attempt with the same complete
-- payload. Pinned checkpoints coexist until publication/GC proves them unreachable.
-- No user-ordered projection (DCM6). There are no user-level stored rows.
-- Existing tables need a reviewed copy/cutover; IF NOT EXISTS is not a migration.
-- Never drop a nonempty predecessor or any reachable/pinned checkpoint.
CREATE TABLE IF NOT EXISTS metric_field_values_checkpoint
(
    org_id UUID,
    workspace_id UUID,
    project_id UUID,
    field_id UUID,
    field_version UInt32,
    level LowCardinality(String),
    unit_id String,
    session_id String,
    user_id String,
    unit_start DateTime64(6, 'UTC'),
    bucket_start DateTime('UTC'),
    last_arrival_ns UInt64,
    settled_at Nullable(DateTime64(6, 'UTC')),
    status LowCardinality(String),
    no_value_reason LowCardinality(String),
    value_bool Nullable(UInt8),
    value_number Nullable(Float64),
    value_text Nullable(String),
    part_n Nullable(Float64),
    part_d Nullable(Float64),
    inputs Map(LowCardinality(String), Nullable(Float64)),
    sample_probability Nullable(Float64),
    sampling_scope String,
    sampling_stratum String,
    sampling_design LowCardinality(String),
    population_size Nullable(UInt64),
    invalid_probability UInt8,
    group_key String,
    is_deleted UInt8,
    _version UInt64,
    generation UInt64,
    attempt_id UUID,
    fencing_epoch UInt32,
    row_sequence UInt32,
    inputs_bool Map(String, Nullable(UInt8)),
    inputs_text Map(String, Nullable(String)),
    INDEX ix_start unit_start TYPE minmax GRANULARITY 1,
    INDEX ix_settle settled_at TYPE minmax GRANULARITY 1,
    INDEX ix_user user_id TYPE bloom_filter GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY (cityHash64(project_id) % 32, cityHash64(unit_id) % 8)
ORDER BY (org_id, project_id, field_id, field_version, level, unit_id, generation, attempt_id);
