-- =============================================================================
-- 027 — durable unified property-catalog delivery and source-stream state
-- =============================================================================
--
-- Direct DEV delivery, Kafka delivery, and bounded reconciliation share this
-- ledger. Data rows are committed before the delivery record. A repeated
-- envelope identity is therefore a no-op, while sequence gaps make the target
-- revision ineligible for activation.
-- =============================================================================

CREATE TABLE IF NOT EXISTS property_catalog_deliveries
(
    organization_id         UUID,
    workspace_id            UUID,
    catalog_epoch           UInt16,
    catalog_revision        UInt64,
    build_token             UUID,
    projection_version      UInt16,
    source_adapter          Enum8(
        'system_manifest' = 1,
        'span_attribute' = 2,
        'eval_template' = 3,
        'eval_config' = 4,
        'simulation_eval_config' = 5,
        'annotation_label' = 6,
        'dataset_column' = 7
    ),
    producer_stream_id      UUID,
    sequence                UInt64,
    envelope_format         String,
    envelope_version        UInt16,
    envelope_id             FixedString(64),
    payload_sha256          FixedString(64),
    previous_payload_sha256 FixedString(64),
    source_batch_digest     FixedString(64),
    outcome                 Enum8(
        'committed' = 1,
        'gap' = 2
    ),
    terminal                UInt8,
    gap_reasons             Array(String),
    source_rows             UInt64,
    definition_rows         UInt64,
    value_rows              UInt64,
    tombstone_rows          UInt64,
    transport               Enum8(
        'direct' = 1,
        'kafka' = 2,
        'reconcile' = 3
    ),
    kafka_partition         Int32 DEFAULT -1,
    kafka_offset            Int64 DEFAULT -1,
    delivered_at            DateTime64(6, 'UTC'),
    _version                UInt64
)
ENGINE = MergeTree
PARTITION BY cityHash64(workspace_id) % 64
ORDER BY
(
    organization_id,
    workspace_id,
    catalog_epoch,
    catalog_revision,
    build_token,
    source_adapter,
    producer_stream_id,
    sequence
)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS property_catalog_source_streams
(
    organization_id         UUID,
    workspace_id            UUID,
    catalog_epoch           UInt16,
    catalog_revision        UInt64,
    build_token             UUID,
    projection_version      UInt16,
    source_adapter          Enum8(
        'system_manifest' = 1,
        'span_attribute' = 2,
        'eval_template' = 3,
        'eval_config' = 4,
        'simulation_eval_config' = 5,
        'annotation_label' = 6,
        'dataset_column' = 7
    ),
    producer_stream_id      UUID,
    envelope_version        UInt16,
    first_sequence          UInt64,
    last_sequence           UInt64,
    max_contiguous_sequence UInt64,
    last_issued_sequence    UInt64,
    fenced_sequence         UInt64,
    terminal_payload_sha256 FixedString(64),
    build_plan_json         String,
    build_lease_sha256      FixedString(64),
    status                  Enum8(
        'open' = 1,
        'draining' = 2,
        'fenced' = 3,
        'complete' = 4,
        'gap' = 5,
        'failed' = 6
    ),
    gap_count               UInt64,
    gap_reasons             Array(String),
    kafka_partition         Int32 DEFAULT -1,
    kafka_high_water_offset Int64 DEFAULT -1,
    started_at              DateTime64(6, 'UTC'),
    updated_at              DateTime64(6, 'UTC'),
    drain_deadline          Nullable(DateTime64(6, 'UTC')),
    fenced_at               Nullable(DateTime64(6, 'UTC')),
    _version                UInt64
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
