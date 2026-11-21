-- cluster-shard-key: derived_metric_inputs = cityHash64(project_id)
-- cluster-shard-key: derived_metric_results = cityHash64(project_id)
-- cluster-shard-key: derived_metric_commits = cityHash64(project_id)

-- New receipt: installations that already applied 028 must also receive this
-- upgrade. Keep 028 unchanged, and every statement here idempotent.
ALTER TABLE derived_metric_inputs ADD COLUMN IF NOT EXISTS sampling_design LowCardinality(String) DEFAULT '';
ALTER TABLE derived_metric_inputs ADD COLUMN IF NOT EXISTS population_size Nullable(UInt64);
ALTER TABLE derived_metric_results ADD COLUMN IF NOT EXISTS std_error Nullable(Float64);
ALTER TABLE derived_metric_results ADD COLUMN IF NOT EXISTS ci_low Nullable(Float64);
ALTER TABLE derived_metric_results ADD COLUMN IF NOT EXISTS ci_high Nullable(Float64);
ALTER TABLE derived_metric_results ADD COLUMN IF NOT EXISTS effective_n Nullable(Float64);
ALTER TABLE derived_metric_results ADD COLUMN IF NOT EXISTS ci_df Nullable(UInt64);
ALTER TABLE derived_metric_results ADD COLUMN IF NOT EXISTS ci_method LowCardinality(String) DEFAULT '';
ALTER TABLE derived_metric_results ADD COLUMN IF NOT EXISTS ci_status LowCardinality(String) DEFAULT '';
ALTER TABLE derived_metric_results ADD COLUMN IF NOT EXISTS ci_reason LowCardinality(String) DEFAULT '';
ALTER TABLE derived_metric_results ADD COLUMN IF NOT EXISTS ci_interval LowCardinality(String) DEFAULT '';

-- Views resolve SELECT * at creation time, so refresh the projection after ADD
-- COLUMN. Replacing the view preserves committed-only visibility.
CREATE OR REPLACE VIEW derived_metrics AS
SELECT r.*
FROM derived_metric_results AS r
INNER JOIN (SELECT * FROM derived_metric_commits FINAL) AS c
    ON r.org_id = c.org_id AND r.workspace_id = c.workspace_id
    AND r.project_id = c.project_id AND r.metric_id = c.metric_id
    AND r.run_id = c.run_id AND r.result_id = c.result_id;
