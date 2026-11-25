-- Frozen snapshots retain typed unit contributions for explicit erasure, not raw
-- telemetry. No TTL: retention must not silently change a completed capture.
-- Existing numeric captures keep their original columns and semantics.
ALTER TABLE derived_metric_inputs
    ADD COLUMN IF NOT EXISTS unit_start DateTime64(6, 'UTC') DEFAULT toDateTime(day, 'UTC'),
    ADD COLUMN IF NOT EXISTS bucket_start DateTime('UTC') DEFAULT toStartOfFifteenMinutes(toDateTime(unit_start, 'UTC')),
    ADD COLUMN IF NOT EXISTS status LowCardinality(String) DEFAULT 'ok',
    ADD COLUMN IF NOT EXISTS value_bool Nullable(UInt8),
    ADD COLUMN IF NOT EXISTS value_number Nullable(Float64),
    ADD COLUMN IF NOT EXISTS value_text Nullable(String);

-- Bucket-indexed removable contributions are written ONLY for sketch captures.
-- Do not add a wide projection to the legacy numeric input table: that doubles
-- its bulk insert working set and breaks the existing 512 MiB erasure lane.
-- cluster-shard-key: derived_metric_snapshot_units = cityHash64(project_id)
CREATE TABLE IF NOT EXISTS derived_metric_snapshot_units
(
    org_id UUID,
    workspace_id UUID,
    project_id UUID,
    snapshot_id UUID,
    unit_id String,
    session_id String,
    user_id String,
    cohort String,
    unit_start DateTime64(6, 'UTC'),
    bucket_start DateTime('UTC'),
    status LowCardinality(String),
    value_bool Nullable(UInt8),
    value_number Nullable(Float64),
    value_text Nullable(String),
    -- Completeness evidence is independent of the status-masked display value.
    input_number Nullable(Float64),
    probability Nullable(Float64),
    invalid_probability UInt8,
    sampling_scope String,
    sampling_stratum String,
    sampling_design LowCardinality(String),
    population_size Nullable(UInt64)
)
ENGINE = MergeTree
PARTITION BY cityHash64(project_id) % 32
ORDER BY (org_id, workspace_id, project_id, snapshot_id, bucket_start, unit_id);

-- Copy committed states, never finalize then rebuild a sketch from its scalar.
-- Snapshot cleanup owns this namespace independently of the live manifest GC.
-- cluster-shard-key: derived_metric_snapshot_buckets = cityHash64(project_id)
CREATE TABLE IF NOT EXISTS derived_metric_snapshot_buckets
(
    org_id UUID,
    workspace_id UUID,
    project_id UUID,
    snapshot_id UUID,
    bucket_start DateTime('UTC'),
    group_key String,
    scope_key String,
    stratum_key String,
    unit_count UInt64,
    valid_count UInt64,
    missing_count UInt64,
    invalid_count UInt64,
    known_sampled UInt64,
    nonempty_scope_count UInt64,
    scope_min String,
    scope_max String,
    uniq_state AggregateFunction(uniqCombined64, String),
    quantile_state AggregateFunction(quantileTDigest, Float64)
)
ENGINE = MergeTree
PARTITION BY cityHash64(project_id) % 32
ORDER BY (org_id, project_id, snapshot_id, bucket_start, group_key, scope_key, stratum_key);
