CREATE TABLE IF NOT EXISTS property_catalog_activation_control_events
(
    organization_id          UUID,
    workspace_id             UUID,
    catalog_epoch            UInt16,
    projection_version       UInt16,
    control_sequence         UInt64,
    request_id               UUID,
    action                   Enum8('activate' = 1, 'disable' = 2, 'rollback' = 3, 'follow' = 4),
    target_catalog_revision  UInt64,
    target_build_token       UUID,
    target_activation_sha256 FixedString(64),
    previous_control_sha256  FixedString(64),
    control_sha256           FixedString(64),
    controlled_at            DateTime64(6, 'UTC')
)
ENGINE = MergeTree
ORDER BY
(
    organization_id,
    workspace_id,
    control_sequence,
    request_id
)
SETTINGS index_granularity = 8192;
