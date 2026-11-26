-- Add derived eval fields only. Do not rewrite/materialize existing data or
-- replay the broad legacy startup ALTER list. Existing expressions are checked
-- before this file is applied; IF NOT EXISTS is not a compatibility check.
ALTER TABLE usage_apicalllog ADD COLUMN IF NOT EXISTS eval_score Float64 MATERIALIZED if((JSONType(JSONExtractString(config), 'output', 'output', 'score') IN ('Double', 'Int64', 'UInt64')), JSONExtractFloat(JSONExtractString(config), 'output', 'output', 'score'), JSONExtractFloat(JSONExtractString(config), 'output', 'output'));
ALTER TABLE usage_apicalllog ADD COLUMN IF NOT EXISTS eval_output_str String MATERIALIZED JSONExtractString(JSONExtractString(config), 'output', 'output');
ALTER TABLE usage_apicalllog ADD COLUMN IF NOT EXISTS eval_trace_id String MATERIALIZED JSONExtractString(JSONExtractString(config), 'trace_id');
ALTER TABLE usage_apicalllog ADD COLUMN IF NOT EXISTS eval_dataset_id String MATERIALIZED JSONExtractString(JSONExtractString(config), 'dataset_id');
