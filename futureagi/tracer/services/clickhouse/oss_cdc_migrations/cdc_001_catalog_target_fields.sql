-- Explicit OSS CDC destination upgrade; never part of default v2/schema discovery.
-- Preexisting columns must pass oss_cdc_upgrade inspection before any apply.
ALTER TABLE model_hub_score ADD COLUMN IF NOT EXISTS tracer_project_id Nullable(UUID);
ALTER TABLE model_hub_score ADD COLUMN IF NOT EXISTS value_history String DEFAULT '[]';
ALTER TABLE simulate_agent_definition ADD COLUMN IF NOT EXISTS target_speaks_first Nullable(UInt8);
