-- The direct-write v2 eval store predates work-item/version columns. Evolve it
-- additively; legacy CDC stores already carry these columns in schema.py.
ALTER TABLE tracer_eval_logger_v2 ADD COLUMN IF NOT EXISTS status LowCardinality(String) DEFAULT 'completed';
ALTER TABLE tracer_eval_logger_v2 ADD COLUMN IF NOT EXISTS config_hash Nullable(String);

-- Additive, immutable per-attempt inputs. Only a committed manifest publishes results.
-- No TTL: a completed run must remain reproducible after raw telemetry retention.
CREATE TABLE IF NOT EXISTS derived_metric_inputs
(
    org_id UUID,
    workspace_id UUID,
    project_id UUID,
    snapshot_id UUID,
    unit_id String,
    session_id String,
    user_id String,
    cohort String,
    day Date,
    input_values Map(String, Nullable(Float64)),
    probability Nullable(Float64),
    invalid_probability UInt8,
    source_rows UInt64,
    sampling_scope String,
    sampling_stratum String
)
ENGINE = MergeTree
PARTITION BY cityHash64(toString(project_id)) % 32
ORDER BY (org_id, workspace_id, project_id, snapshot_id, unit_id);

CREATE TABLE IF NOT EXISTS derived_metric_results
(
    org_id UUID,
    workspace_id UUID,
    project_id UUID,
    metric_id UUID,
    metric_version UInt32,
    run_id UUID,
    result_id UUID,
    rollup LowCardinality(String),
    group_key String,
    value Nullable(Float64),
    numerator Nullable(Float64),
    denominator Nullable(Float64),
    status LowCardinality(String),
    unit_count UInt64,
    valid_count UInt64,
    missing_count UInt64,
    invalid_probability_count UInt64,
    window_start DateTime64(6, 'UTC'),
    window_end DateTime64(6, 'UTC'),
    as_of DateTime64(6, 'UTC'),
    source_run_id String,
    definition_hash String,
    sampling_scope String
)
ENGINE = MergeTree
PARTITION BY cityHash64(toString(project_id)) % 32
ORDER BY (org_id, workspace_id, project_id, metric_id, run_id, result_id, rollup, group_key);

CREATE TABLE IF NOT EXISTS derived_metric_commits
(
    org_id UUID,
    workspace_id UUID,
    project_id UUID,
    metric_id UUID,
    run_id UUID,
    result_id UUID,
    committed_at DateTime64(6, 'UTC'),
    version UInt64
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (org_id, workspace_id, project_id, metric_id, run_id);

CREATE VIEW IF NOT EXISTS derived_metrics AS
SELECT r.*
FROM derived_metric_results AS r
INNER JOIN (SELECT * FROM derived_metric_commits FINAL) AS c
    ON r.org_id = c.org_id AND r.workspace_id = c.workspace_id
    AND r.project_id = c.project_id AND r.metric_id = c.metric_id
    AND r.run_id = c.run_id AND r.result_id = c.result_id;
