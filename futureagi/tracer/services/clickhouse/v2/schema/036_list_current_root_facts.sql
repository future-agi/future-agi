-- Current root/member facts use the existing immutable attempt publication.
-- No TTL or materialized-view latest-state authority; privacy uses the ledger.
-- cluster-shard-key: list_current_root_facts = cityHash64(project_id)
CREATE TABLE IF NOT EXISTS list_current_root_facts
(
    org_id UUID,
    project_id UUID,
    observation_type LowCardinality(String),
    service_name LowCardinality(String),
    start_hour DateTime('UTC'),
    trace_id String,
    span_id String,
    unit_start DateTime64(6, 'UTC'),
    end_time Nullable(DateTime64(6, 'UTC')),
    parent_span_id Nullable(String),
    raw_session_id String,
    canonical_session_id String,
    raw_user_id String,
    canonical_user_id String,
    name String,
    input String,
    cost Float64,
    total_tokens Int32,
    prompt_tokens Int32,
    completion_tokens Int32,
    attributes_extra String,
    attrs_string Map(String, String),
    attrs_number Map(String, Float64),
    attrs_bool Map(String, UInt8),
    source_version UInt64,
    is_deleted UInt8,
    generation UInt64,
    attempt_id UUID,
    _version UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(project_id) % 32
ORDER BY (org_id, project_id, observation_type, service_name, start_hour, trace_id, span_id, generation, attempt_id);

-- cluster-shard-key: list_current_members = cityHash64(project_id)
CREATE TABLE IF NOT EXISTS list_current_members
(
    org_id UUID,
    project_id UUID,
    observation_type LowCardinality(String),
    service_name LowCardinality(String),
    start_hour DateTime('UTC'),
    trace_id String,
    span_id String,
    unit_start DateTime64(6, 'UTC'),
    end_time Nullable(DateTime64(6, 'UTC')),
    parent_span_id Nullable(String),
    raw_session_id String,
    canonical_session_id String,
    raw_user_id String,
    canonical_user_id String,
    name String,
    input String,
    cost Float64,
    total_tokens Int32,
    prompt_tokens Int32,
    completion_tokens Int32,
    attributes_extra String,
    attrs_string Map(String, String),
    attrs_number Map(String, Float64),
    attrs_bool Map(String, UInt8),
    source_version UInt64,
    is_deleted UInt8,
    generation UInt64,
    attempt_id UUID,
    _version UInt64
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY cityHash64(project_id) % 32
ORDER BY (org_id, project_id, observation_type, service_name, start_hour, trace_id, span_id, generation, attempt_id);
