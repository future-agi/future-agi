-- =============================================================================
-- schema_versions — tracks which schema files have been applied.
-- Maintained by scripts/apply_schema.py.
-- =============================================================================
--
-- Read it manually with:
--   SELECT filename, sha256, applied_at FROM schema_versions ORDER BY applied_at;
--
-- Drift detection (run after every apply):
--   SELECT filename, sha256 FROM schema_versions WHERE filename = '002_spans_v2.sql';
--   -- then compare with `sha256sum schema/002_spans_v2.sql` on disk
--
-- The apply_schema script auto-creates this table on first run if missing.
-- This file exists so anyone reading the schema directory can see the contract.
-- =============================================================================

CREATE TABLE IF NOT EXISTS schema_versions
(
    filename      String,
    sha256        FixedString(64),
    applied_at    DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC'),
    applied_by    String DEFAULT '',          -- env-var FI_MIGRATION_USER if set
    notes         String DEFAULT ''
)
ENGINE = MergeTree
ORDER BY (filename, applied_at);
