-- =============================================================================
-- 025 — unified property-definition catalog and native attribute values
-- =============================================================================
--
-- This is the clean pre-release base schema. It deliberately contains one
-- property-DEFINITION catalog for system properties, observed attributes,
-- eval definitions, annotation labels, dataset columns, and simulation evals.
-- It does not copy trace/eval/dataset facts. Selectable observed values remain
-- in their native, independently paginated attribute-value table.
--
-- SAFETY / SNAPSHOT CONTRACT
--   * Definitions are append-only within an epoch. A reader resolves the
--     latest source_version for each binding at or below one activated
--     catalog_revision, so an issued cursor observes an immutable snapshot.
--   * Plain MergeTree intentionally retains same-version disagreements for
--     activation-time conflict detection; no merge may choose one silently.
--   * binding_id, fingerprints, and state hashes are lowercase SHA-256 hex in
--     FixedString(64). Folded strings are produced with Python str.casefold()
--     without NFKC normalization and are pinned by cross-language fixtures.
--   * visibility_id is the all-zero UUID when the scope has no concrete ID.
--   * Epochs stay in sorting keys, not partitions. Fixed workspace hashing
--     prevents unbounded partition growth across rebuilds.
--   * No source table, materialized view, trigger, or backfill is attached.
-- =============================================================================

CREATE TABLE IF NOT EXISTS property_definition_catalog
(
    organization_id       UUID,
    workspace_id          UUID,
    catalog_epoch         UInt16,
    catalog_revision      UInt64,
    build_token           UUID,
    projection_version    UInt16,
    binding_id            FixedString(64),
    visibility_scope      Enum8(
        'always' = 1,
        'workspace_default' = 2,
        'project' = 3,
        'agent_definition' = 4,
        'dataset' = 5
    ),
    visibility_id         UUID,
    source_adapter        Enum8(
        'system_manifest' = 1,
        'span_attribute' = 2,
        'eval_template' = 3,
        'eval_config' = 4,
        'simulation_eval_config' = 5,
        'annotation_label' = 6,
        'dataset_column' = 7
    ),
    source_entity_id      String,
    source_version        UInt64,
    source_fingerprint    FixedString(64),
    producer_stream_id    UUID,
    producer_sequence     UInt64,
    property_id           String,
    property_kind         Enum8(
        'system_attribute' = 1,
        'custom_attribute' = 2,
        'eval_template' = 3,
        'eval_config' = 4,
        'annotation' = 5,
        'dataset_column' = 6
    ),
    category              Enum8(
        'system_metric' = 1,
        'eval_metric' = 2,
        'annotation_metric' = 3,
        'custom_attribute' = 4,
        'custom_column' = 5
    ),
    category_rank         UInt8,
    source_rank           UInt16,
    definition_source     String,
    primary_source        LowCardinality(String),
    primary_source_folded String,
    source_tokens         Array(String),
    value_adapter         LowCardinality(String),
    name                  String,
    display_name          String,
    sort_name_folded      String,
    search_text_folded    String,
    role                  Enum8(
        'metric' = 1,
        'dimension' = 2
    ),
    definition_json       String,
    definition_sha256     FixedString(64),
    first_seen            Nullable(DateTime64(6, 'UTC')),
    last_seen             Nullable(DateTime64(6, 'UTC')),
    is_deleted            UInt8,
    deleted_at            Nullable(DateTime64(6, 'UTC')),
    state_sha256          FixedString(64),
    emitted_at            DateTime64(6, 'UTC'),

    INDEX idx_property_search search_text_folded
        TYPE ngrambf_v1(3, 65536, 4, 0) GRANULARITY 1,
    INDEX idx_property_id property_id
        TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_property_kind property_kind
        TYPE set(16) GRANULARITY 1,
    INDEX idx_property_category category
        TYPE set(16) GRANULARITY 1,
    INDEX idx_property_source primary_source
        TYPE set(64) GRANULARITY 1,
    INDEX idx_property_visibility visibility_scope
        TYPE set(8) GRANULARITY 1,
    INDEX idx_property_revision catalog_revision
        TYPE minmax GRANULARITY 1
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
    binding_id,
    source_version,
    state_sha256
)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS span_attribute_value_catalog
(
    organization_id   UUID,
    workspace_id      UUID,
    project_id        UUID,
    catalog_epoch     UInt16,
    catalog_revision  UInt64,
    build_token       UUID,
    source_kind       Enum8(
        'custom_attribute' = 1,
        'system_attribute' = 2
    ),
    attribute_key     String,
    attribute_type    Enum8(
        'string' = 1,
        'number' = 2,
        'boolean' = 3,
        'array' = 4,
        'map' = 5,
        'json' = 6
    ),
    value_fingerprint FixedString(64),
    value_json        SimpleAggregateFunction(anyLast, String),
    value_search_text_folded SimpleAggregateFunction(anyLast, String),
    first_seen        SimpleAggregateFunction(min, DateTime64(6, 'UTC')),
    last_seen         SimpleAggregateFunction(max, DateTime64(6, 'UTC')),

    INDEX idx_catalog_value_ngram value_search_text_folded
        TYPE ngrambf_v1(3, 32768, 3, 0) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY cityHash64(workspace_id) % 64
ORDER BY
(
    organization_id,
    workspace_id,
    project_id,
    catalog_epoch,
    catalog_revision,
    build_token,
    source_kind,
    attribute_key,
    attribute_type,
    value_fingerprint
)
SETTINGS index_granularity = 8192;
