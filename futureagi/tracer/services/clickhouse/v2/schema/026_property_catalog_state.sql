-- =============================================================================
-- 026 — unified property-catalog checkpoints and immutable activations
-- =============================================================================
--
-- These are control-plane tables only. They never contain property facts or
-- selectable values. A revision becomes readable only after all adapters in
-- its pinned source manifest are complete, gap-free, and conflict-free.
-- Activation history is retained so signed cursors can continue against the
-- exact epoch/revision they were issued from.
-- =============================================================================

CREATE TABLE IF NOT EXISTS property_catalog_checkpoints
(
    organization_id       UUID,
    workspace_id          UUID,
    catalog_epoch         UInt16,
    catalog_revision      UInt64,
    build_token           UUID,
    projection_version    UInt16,
    source_adapter        Enum8(
        'system_manifest' = 1,
        'span_attribute' = 2,
        'eval_template' = 3,
        'eval_config' = 4,
        'simulation_eval_config' = 5,
        'annotation_label' = 6,
        'dataset_column' = 7
    ),
    producer_stream_id    UUID,
    status                Enum8(
        'pending' = 1,
        'running' = 2,
        'complete' = 3,
        'gap' = 4,
        'failed' = 5
    ),
    terminal              UInt8,
    source_cursor         String,
    watermark             String,
    source_version_fence  UInt64,
    source_fingerprint    FixedString(64),
    source_rows           UInt64,
    processed_rows        UInt64,
    definition_rows       UInt64,
    value_rows            UInt64,
    tombstone_rows        UInt64,
    gap_count             UInt64,
    poison_count          UInt64,
    conflict_count        UInt64,
    gap_reasons           Array(String),
    first_sequence        Nullable(UInt64),
    last_sequence         Nullable(UInt64),
    last_issued_sequence  UInt64,
    fenced_sequence       UInt64,
    terminal_payload_sha256 FixedString(64),
    delivery_count        UInt64,
    source_digest         FixedString(64),
    emitted_digest        FixedString(64),
    previous_payload_sha256 FixedString(64),
    run_id                UUID,
    worker_id             String,
    error                 String,
    started_at            DateTime64(6, 'UTC'),
    updated_at            DateTime64(6, 'UTC'),
    finished_at           Nullable(DateTime64(6, 'UTC')),
    _version              UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(workspace_id) % 64
ORDER BY
(
    organization_id,
    workspace_id,
    catalog_epoch,
    catalog_revision,
    build_token,
    source_adapter,
    producer_stream_id
)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS property_catalog_activations
(
    organization_id       UUID,
    workspace_id          UUID,
    catalog_epoch         UInt16,
    catalog_revision      UInt64,
    build_token           UUID,
    projection_version    UInt16,
    lifecycle_mode        Enum8(
        'initial_backfill' = 1,
        'incremental' = 2,
        'full_repair' = 3
    ),
    lineage_anchor_revision UInt64,
    activation_sequence   UInt64,
    source_manifest_json  String,
    source_manifest_sha256 FixedString(64),
    revision_fence_sha256 FixedString(64),
    activation_sha256     FixedString(64),
    status                Enum8(
        'building' = 1,
        'active' = 2,
        'disabled' = 3
    ),
    live_definition_rows  UInt64,
    tombstone_rows        UInt64,
    value_rows            UInt64,
    qualified_at          Nullable(DateTime64(6, 'UTC')),
    updated_at            DateTime64(6, 'UTC'),
    _version              UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(workspace_id) % 64
ORDER BY
(
    organization_id,
    workspace_id,
    catalog_epoch,
    catalog_revision,
    build_token
)
SETTINGS index_granularity = 8192;
