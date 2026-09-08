-- Apply only to the isolated catalog database, never the source-span database.
-- Live observations, backfill and retries share these identities. Readers must
-- GROUP BY identity with min/max timestamps: parts need not have merged yet.
-- No activation, source revision or exact occurrence count is stored here.

CREATE TABLE IF NOT EXISTS observed_attribute_keys
(
    organization_id String,
    workspace_id String,
    project_id String,
    source_kind LowCardinality(String),
    attribute_key String,
    attribute_type LowCardinality(String),
    key_folded String,
    first_seen SimpleAggregateFunction(min, DateTime64(6, 'UTC')),
    last_seen SimpleAggregateFunction(max, DateTime64(6, 'UTC')),
    CONSTRAINT valid_source CHECK source_kind IN ('custom_attribute', 'system_attribute'),
    CONSTRAINT valid_type CHECK attribute_type IN ('string', 'number', 'boolean', 'array', 'map', 'json'),
    CONSTRAINT valid_interval CHECK first_seen <= last_seen,
    INDEX key_search key_folded TYPE ngrambf_v1(3, 32768, 3, 0) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY cityHash64(workspace_id) % 64
ORDER BY (organization_id, workspace_id, project_id, source_kind, attribute_key, attribute_type);

CREATE TABLE IF NOT EXISTS observed_attribute_values
(
    organization_id String,
    workspace_id String,
    project_id String,
    source_kind LowCardinality(String),
    attribute_key String,
    attribute_type LowCardinality(String),
    value_fingerprint FixedString(64),
    value_json String,
    value_search_text_folded String,
    first_seen SimpleAggregateFunction(min, DateTime64(6, 'UTC')),
    last_seen SimpleAggregateFunction(max, DateTime64(6, 'UTC')),
    CONSTRAINT valid_source CHECK source_kind IN ('custom_attribute', 'system_attribute'),
    CONSTRAINT valid_type CHECK attribute_type IN ('string', 'number', 'boolean', 'array'),
    CONSTRAINT valid_interval CHECK first_seen <= last_seen,
    INDEX value_search value_search_text_folded TYPE ngrambf_v1(3, 32768, 3, 0) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY cityHash64(workspace_id) % 64
-- Canonical bytes are part of identity, not just their hash. A collision must
-- never merge two suggestions. The fingerprint remains useful for keyset reads.
ORDER BY (organization_id, workspace_id, project_id, source_kind, attribute_key, attribute_type, value_fingerprint, value_json);
