-- Sharded native baseline v1. Append a new migration; never edit after release.
-- Current native contract folded from v2 schemas 002-027; no historical DDL replay.

CREATE TABLE IF NOT EXISTS __database__.traces
(
id                     UUID,
project_id             UUID,
project_version_id     Nullable(UUID),
name                   Nullable(String),
session_id             Nullable(UUID),
external_id            Nullable(String),
tags                   String                  DEFAULT '[]' CODEC(ZSTD(1)),
metadata               String                  DEFAULT '{}' CODEC(ZSTD(1)),
input                  String                  DEFAULT ''   CODEC(ZSTD(3)),
output                 String                  DEFAULT ''   CODEC(ZSTD(3)),
error                  String                  DEFAULT ''   CODEC(ZSTD(3)),
error_analysis_status  LowCardinality(String)  DEFAULT 'PENDING',
created_at             DateTime64(6, 'UTC'),
updated_at             DateTime64(6, 'UTC')    DEFAULT now64(6, 'UTC'),
is_deleted             UInt8                   DEFAULT 0,
_version               UInt64                  DEFAULT toUnixTimestamp64Nano(now64(9, 'UTC')),
INDEX idx_external_id  external_id  TYPE bloom_filter(0.01) GRANULARITY 1,
INDEX auto_minmax_index_created_at created_at TYPE minmax() GRANULARITY 1,
INDEX auto_minmax_index_is_deleted is_deleted TYPE minmax() GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toYYYYMM(created_at)
ORDER BY (project_id, id)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS __database__.end_users
(
project_id       UUID,
end_user_id      UUID,
organization_id  UUID,
user_id          String,
user_id_type     LowCardinality(Nullable(String)),
user_id_hash     String                  DEFAULT '',
metadata         String                  DEFAULT '{}' CODEC(ZSTD(1)),
first_seen       DateTime64(6, 'UTC'),
version          DateTime64(6, 'UTC')    DEFAULT now64(6, 'UTC'),
is_deleted       UInt8                   DEFAULT 0,
INDEX idx_end_user_id end_user_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (project_id, end_user_id)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS __database__.trace_sessions
(
project_id          UUID,
trace_session_id    UUID,
external_session_id String                  DEFAULT '',
first_seen          DateTime64(6, 'UTC'),
version             DateTime64(6, 'UTC')    DEFAULT now64(6, 'UTC'),
is_deleted          UInt8                   DEFAULT 0,
INDEX idx_trace_session_id trace_session_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(version)
ORDER BY (project_id, trace_session_id)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS __database__.end_user_id_remap
(
old_id   UUID,
new_id   UUID,
version  DateTime64(6, 'UTC')  DEFAULT now64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY old_id
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS __database__.trace_session_id_remap
(
old_id   UUID,
new_id   UUID,
version  DateTime64(6, 'UTC')  DEFAULT now64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(version)
ORDER BY old_id
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS __database__.spans_v2_dead_letter
(
pg_id              String,
project_id         Nullable(UUID),
trace_id           Nullable(String),
raw_pg_row         String CODEC(ZSTD(3)),
error_class        LowCardinality(String),
error_message      String,
error_stage        LowCardinality(String),
attempted_at       DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC'),
backfill_run_id    String DEFAULT '',
INDEX idx_pg_id pg_id TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(attempted_at)
ORDER BY (attempted_at, pg_id)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS __database__.backfill_checkpoints
(
project_id         UUID,
hour_bucket        DateTime('UTC'),
status             LowCardinality(String),
rows_in_pg         UInt64               DEFAULT 0,
rows_in_ch         UInt64               DEFAULT 0,
dead_letter_count  UInt64               DEFAULT 0,
started_at         DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC'),
finished_at        Nullable(DateTime64(3, 'UTC')),
worker_id          String               DEFAULT '',
backfill_run_id    String               DEFAULT '',
error_message      String               DEFAULT '',
_version           UInt64               DEFAULT toUnixTimestamp64Nano(now64(9, 'UTC'))
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(hour_bucket)
ORDER BY (project_id, hour_bucket)
SETTINGS index_granularity = 8192;

CREATE DICTIONARY IF NOT EXISTS __database__.trace_dict
(
    id                     UUID,
    project_id             UUID,
    name                   Nullable(String),
    session_id             Nullable(UUID),
    external_id            Nullable(String),
    tags                   String        DEFAULT '[]',
    error_analysis_status  String        DEFAULT 'PENDING',
    is_deleted             UInt8         DEFAULT 0
)
PRIMARY KEY id
SOURCE(CLICKHOUSE(TABLE 'traces'))
LIFETIME(MIN 30 MAX 60)
LAYOUT(COMPLEX_KEY_HASHED(SHARDS 4));

CREATE DICTIONARY IF NOT EXISTS __database__.end_users_dict
(
    end_user_id      UUID,
    project_id       UUID,
    organization_id  UUID,
    user_id          String,
    user_id_type     Nullable(String),
    user_id_hash     String        DEFAULT '',
    metadata         String        DEFAULT '{}',
    is_deleted       UInt8         DEFAULT 0
)
PRIMARY KEY end_user_id
SOURCE(CLICKHOUSE(TABLE 'end_users' WHERE 'is_deleted = 0'))
LIFETIME(MIN 60 MAX 120)
LAYOUT(COMPLEX_KEY_HASHED());

CREATE DICTIONARY IF NOT EXISTS __database__.trace_sessions_dict
(
    trace_session_id    UUID,
    project_id          UUID,
    external_session_id String        DEFAULT '',
    is_deleted          UInt8         DEFAULT 0
)
PRIMARY KEY trace_session_id
SOURCE(CLICKHOUSE(TABLE 'trace_sessions' WHERE 'is_deleted = 0'))
LIFETIME(MIN 60 MAX 120)
LAYOUT(COMPLEX_KEY_HASHED());

CREATE TABLE IF NOT EXISTS __database__.spans
(
project_id          UUID,
observation_type    LowCardinality(String),
service_name        LowCardinality(String) DEFAULT '',
start_time          DateTime64(6, 'UTC'),
trace_id            String,
id                  String,
parent_span_id      String  DEFAULT '',
name                String,
end_time            Nullable(DateTime64(6, 'UTC')),
latency_ms          Int32  DEFAULT 0,
org_id              Nullable(UUID),
project_version_id  Nullable(UUID),
end_user_id         Nullable(UUID),
trace_session_id    Nullable(UUID),
prompt_version_id   Nullable(UUID),
prompt_label_id     Nullable(UUID),
custom_eval_config_id Nullable(UUID),
status              LowCardinality(String)         DEFAULT '',
status_message      String                         DEFAULT '',
model               LowCardinality(String)         DEFAULT '',
provider            LowCardinality(String)         DEFAULT '',
gen_ai_system       LowCardinality(String)         DEFAULT '',
gen_ai_operation    LowCardinality(String)         DEFAULT '',
operation_name      LowCardinality(String)         DEFAULT '',
prompt_tokens       Int32  DEFAULT 0,
completion_tokens   Int32  DEFAULT 0,
total_tokens        Int32  DEFAULT 0,
cost                Float64 DEFAULT 0,
attrs_string        Map(LowCardinality(String), String)  CODEC(ZSTD(1)),
attrs_number        Map(LowCardinality(String), Float64) CODEC(ZSTD(1)),
attrs_bool          Map(LowCardinality(String), UInt8)   CODEC(ZSTD(1)),
resource_attrs      JSON(max_dynamic_paths=512)          CODEC(ZSTD(1)),
metadata            JSON(max_dynamic_paths=256)          CODEC(ZSTD(1)),
input               String  DEFAULT ''   CODEC(ZSTD(3)),
output              String  DEFAULT ''   CODEC(ZSTD(3)),
input_length        UInt32  MATERIALIZED  lengthUTF8(input),
output_length       UInt32  MATERIALIZED  lengthUTF8(output),
input_gcs_url       Nullable(String),
output_gcs_url      Nullable(String),
tags                String  DEFAULT '[]' CODEC(ZSTD(1)),
span_events         String  DEFAULT '[]' CODEC(ZSTD(1)),
eval_status         LowCardinality(String) DEFAULT '',
semconv_source      LowCardinality(String) DEFAULT '',
created_at          DateTime64(6, 'UTC')   DEFAULT now64(6, 'UTC'),
updated_at          DateTime64(6, 'UTC')   DEFAULT now64(6, 'UTC'),
is_deleted          UInt8                  DEFAULT 0,
_version            UInt64                 DEFAULT toUnixTimestamp64Nano(now64(9, 'UTC')),
INDEX idx_id                  id                       TYPE bloom_filter(0.001) GRANULARITY 1,
INDEX idx_trace_id            trace_id                 TYPE bloom_filter(0.001) GRANULARITY 1,
INDEX idx_parent_span_id      parent_span_id           TYPE bloom_filter(0.01)  GRANULARITY 1,
INDEX idx_trace_session_id    trace_session_id         TYPE bloom_filter(0.001) GRANULARITY 1,
INDEX idx_end_user_id         end_user_id              TYPE bloom_filter(0.001) GRANULARITY 1,
INDEX idx_custom_eval_config_id custom_eval_config_id  TYPE bloom_filter(0.01)  GRANULARITY 1,
INDEX idx_model               model                    TYPE bloom_filter(0.01)  GRANULARITY 1,
INDEX idx_provider            provider                 TYPE bloom_filter(0.01)  GRANULARITY 1,
INDEX idx_status              status                   TYPE set(20)             GRANULARITY 1,
INDEX idx_eval_status         eval_status              TYPE set(20)             GRANULARITY 1,
INDEX idx_attrs_str_keys      mapKeys(attrs_string)    TYPE bloom_filter(0.01)  GRANULARITY 1,
INDEX idx_attrs_num_keys      mapKeys(attrs_number)    TYPE bloom_filter(0.01)  GRANULARITY 1,
INDEX idx_attrs_bool_keys     mapKeys(attrs_bool)      TYPE bloom_filter(0.01)  GRANULARITY 1,
INDEX auto_minmax_index_latency_ms        latency_ms        TYPE minmax() GRANULARITY 1,
INDEX auto_minmax_index_prompt_tokens     prompt_tokens     TYPE minmax() GRANULARITY 1,
INDEX auto_minmax_index_completion_tokens completion_tokens TYPE minmax() GRANULARITY 1,
INDEX auto_minmax_index_total_tokens      total_tokens      TYPE minmax() GRANULARITY 1,
INDEX auto_minmax_index_cost              cost              TYPE minmax() GRANULARITY 1,
INDEX auto_minmax_index_input_length      input_length      TYPE minmax() GRANULARITY 1,
INDEX auto_minmax_index_output_length     output_length     TYPE minmax() GRANULARITY 1,
INDEX auto_minmax_index_is_deleted        is_deleted        TYPE minmax() GRANULARITY 1,
INDEX auto_minmax_index__version          _version          TYPE minmax() GRANULARITY 1,
PROJECTION proj_metrics_hourly
    (
        SELECT
            project_id,
            toStartOfHour(start_time)   AS hour,
            observation_type,
            status,
            model,
            provider,
            countState(),
            sumState(cost),
            sumState(total_tokens),
            sumState(prompt_tokens),
            sumState(completion_tokens),
            quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)
        GROUP BY project_id, hour, observation_type, status, model, provider
    ),
PROJECTION proj_root_spans
    (
        SELECT
            project_id,
            start_time,
            trace_id,
            id,
            name,
            observation_type,
            status,
            latency_ms,
            cost,
            total_tokens,
            model,
            provider,
            trace_session_id
        ORDER BY (project_id, is_deleted, parent_span_id, start_time)
    ),
attributes_extra String DEFAULT '{}' CODEC(ZSTD(3)),
trace_name String MATERIALIZED ifNull(dictGetOrDefault('__database__.trace_dict', 'name', toUUID(trace_id), ''), ''),
_peerdb_is_deleted UInt8 ALIAS is_deleted,
llm_request_model LowCardinality(String) MATERIALIZED attrs_string['gen_ai.request.model'],
llm_response_model LowCardinality(String) MATERIALIZED attrs_string['gen_ai.response.model'],
llm_finish_reason LowCardinality(String) MATERIALIZED attrs_string['gen_ai.response.finish_reason'],
embedding_model LowCardinality(String) MATERIALIZED attrs_string['llm.embedding.model'],
streaming Nullable(UInt8) MATERIALIZED if(mapContains(attrs_bool, 'streaming'), attrs_bool['streaming'], NULL),
temperature Nullable(Float64) MATERIALIZED if(mapContains(attrs_number, 'gen_ai.request.temperature'), attrs_number['gen_ai.request.temperature'], NULL),
top_p Nullable(Float64) MATERIALIZED if(mapContains(attrs_number, 'gen_ai.request.top_p'), attrs_number['gen_ai.request.top_p'], NULL),
max_tokens Nullable(Int32) MATERIALIZED if(mapContains(attrs_number, 'gen_ai.request.max_tokens'), toInt32(attrs_number['gen_ai.request.max_tokens']), NULL),
PROJECTION proj_metrics_hourly_by_project
(
    SELECT
        project_id,
        toStartOfHour(start_time) AS hour,
        countState(),
        sumState(cost),
        sumState(total_tokens),
        sumState(prompt_tokens),
        sumState(completion_tokens),
        quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)
    GROUP BY project_id, hour
),
PROJECTION proj_metrics_hourly_by_obs_type
(
    SELECT
        project_id,
        toStartOfHour(start_time) AS hour,
        observation_type,
        countState(),
        sumState(cost),
        sumState(total_tokens),
        quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)
    GROUP BY project_id, hour, observation_type
),
PROJECTION proj_metrics_hourly_by_model
(
    SELECT
        project_id,
        toStartOfHour(start_time) AS hour,
        model,
        countState(),
        sumState(cost),
        sumState(total_tokens),
        sumState(prompt_tokens),
        sumState(completion_tokens),
        quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)
    GROUP BY project_id, hour, model
),
INDEX idx_trace_name trace_name TYPE bloom_filter GRANULARITY 1,
PROJECTION proj_by_session
(
    SELECT
        project_id,
        trace_session_id,
        start_time,
        end_time,
        is_deleted,
        parent_span_id,
        trace_id,
        end_user_id,
        cost,
        total_tokens,
        input,
        attributes_extra,
        attrs_string,
        attrs_number,
        observation_type
    ORDER BY (project_id, trace_session_id, start_time)
),
PROJECTION proj_by_end_user
(
    SELECT
        project_id,
        end_user_id,
        start_time,
        end_time,
        is_deleted,
        trace_session_id,
        observation_type,
        status,
        latency_ms,
        trace_id
    ORDER BY (project_id, end_user_id, start_time)
),
INDEX idx_attrs_str_values arrayMap(x -> lower(x), mapValues(attrs_string))
    TYPE bloom_filter(0.01) GRANULARITY 1,
INDEX idx_attrs_num_values mapValues(attrs_number)
    TYPE bloom_filter(0.01) GRANULARITY 1,
INDEX idx_attrs_str_ngram
    arrayStringConcat(arrayMap(x -> lower(x), mapValues(attrs_string)))
    TYPE ngrambf_v1(4, 32768, 3, 0) GRANULARITY 1,
INDEX auto_minmax_index_created_at created_at
    TYPE minmax() GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toDate(start_time)
PRIMARY KEY (project_id, observation_type, service_name, toStartOfHour(start_time))
ORDER BY    (project_id, observation_type, service_name, toStartOfHour(start_time), trace_id, id)
SETTINGS
    storage_policy                          = 'tiered',
    index_granularity                        = 8192,
    index_granularity_bytes                  = 67108864,    

    merge_max_block_size_bytes               = 67108864,    

    ttl_only_drop_parts                      = 1,
    deduplicate_merge_projection_mode        = 'rebuild',   

    allow_nullable_key                       = 1;

CREATE TABLE IF NOT EXISTS __database__.spans_per_session
(
project_id            UUID,
trace_session_id      UUID,
hour_first_seen       DateTime('UTC'),
span_count            AggregateFunction(count),
total_tokens_sum      AggregateFunction(sum, Int64),
prompt_tokens_sum     AggregateFunction(sum, Int64),
completion_tokens_sum AggregateFunction(sum, Int64),
cost_sum              AggregateFunction(sum, Float64),
latency_q             AggregateFunction(quantilesTDigest(0.5, 0.95, 0.99), Int32),
first_seen            AggregateFunction(min, DateTime64(6, 'UTC')),
last_seen             AggregateFunction(max, Nullable(DateTime64(6, 'UTC'))),
error_count           AggregateFunction(countIf, UInt8),
INDEX idx_trace_session_id trace_session_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour_first_seen)
ORDER BY (project_id, trace_session_id, hour_first_seen)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS __database__.spans_hourly_rollup
(
hour                  DateTime('UTC'),
project_id            UUID,
observation_type      LowCardinality(String),
model                 LowCardinality(String),
provider              LowCardinality(String),
n                     AggregateFunction(count),
error_count           AggregateFunction(countIf, UInt8),
total_tokens_sum      AggregateFunction(sum, Int64),
prompt_tokens_sum     AggregateFunction(sum, Int64),
completion_tokens_sum AggregateFunction(sum, Int64),
cost_sum              AggregateFunction(sum, Float64),
latency_q             AggregateFunction(quantilesTDigest(0.5, 0.95, 0.99), Int32),
INDEX idx_model    model    TYPE bloom_filter(0.01) GRANULARITY 1,
INDEX idx_provider provider TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (project_id, hour, observation_type, model, provider)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS __database__.trace_count_rollup
(
project_id        UUID,
hour              DateTime('UTC'),
uniq_traces_state AggregateFunction(uniqExact, String),
INDEX idx_project_id project_id TYPE minmax GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (project_id, hour)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS __database__.dashboard_attr_rollup
(
project_id   UUID,
hour         DateTime('UTC'),
attr_key     LowCardinality(String),
attr_value   String,
n            AggregateFunction(count),
latency_sum  AggregateFunction(sum, Int64),
INDEX idx_attr_value attr_value TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (project_id, hour, attr_key, attr_value)
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS __database__.spans_per_session_mv
TO __database__.spans_per_session
AS
SELECT
    project_id,
    trace_session_id,
    toStartOfHour(min(start_time))                          AS hour_first_seen,
    countState()                                            AS span_count,
    sumState(toInt64(total_tokens))                         AS total_tokens_sum,
    sumState(toInt64(prompt_tokens))                        AS prompt_tokens_sum,
    sumState(toInt64(completion_tokens))                    AS completion_tokens_sum,
    sumState(cost)                                          AS cost_sum,
    quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)      AS latency_q,
    minState(start_time)                                    AS first_seen,
    maxState(end_time)                                      AS last_seen,
    countIfState(status = 'ERROR')                          AS error_count
FROM __database__.spans
WHERE is_deleted = 0 AND trace_session_id IS NOT NULL
GROUP BY project_id, trace_session_id;

CREATE MATERIALIZED VIEW IF NOT EXISTS __database__.spans_hourly_rollup_mv
TO __database__.spans_hourly_rollup
AS
SELECT
    toStartOfHour(start_time)                            AS hour,
    project_id,
    observation_type,
    model,
    provider,
    countState()                                          AS n,
    countIfState(status = 'ERROR')                        AS error_count,
    sumState(toInt64(total_tokens))                       AS total_tokens_sum,
    sumState(toInt64(prompt_tokens))                      AS prompt_tokens_sum,
    sumState(toInt64(completion_tokens))                  AS completion_tokens_sum,
    sumState(cost)                                        AS cost_sum,
    quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)    AS latency_q
FROM __database__.spans
WHERE is_deleted = 0
GROUP BY toStartOfHour(start_time), project_id, observation_type, model, provider;

CREATE MATERIALIZED VIEW IF NOT EXISTS __database__.trace_count_rollup_mv
TO __database__.trace_count_rollup
AS
SELECT
    project_id,
    toStartOfHour(start_time) AS hour,
    uniqExactState(trace_id)  AS uniq_traces_state    

FROM __database__.spans
WHERE is_deleted = 0
  AND (parent_span_id = '' OR parent_span_id IS NULL)
GROUP BY project_id, hour;

CREATE MATERIALIZED VIEW IF NOT EXISTS __database__.dashboard_attr_rollup_mv
TO __database__.dashboard_attr_rollup
AS
SELECT
    project_id,
    toStartOfHour(start_time)            AS hour,
    attr_key,
    attrs_string[attr_key]               AS attr_value,
    countState()                         AS n,
    sumState(toInt64(latency_ms))        AS latency_sum
FROM __database__.spans
ARRAY JOIN ['final_status', 'country'] AS attr_key
WHERE is_deleted = 0
  AND parent_span_id = ''
GROUP BY project_id, toStartOfHour(start_time), attr_key, attrs_string[attr_key];

CREATE TABLE IF NOT EXISTS eval_per_config
(
hour                     DateTime('UTC'),
custom_eval_config_id    UUID,
n                        AggregateFunction(count),
passes                   AggregateFunction(sumIf, UInt64, UInt8),
fails                    AggregateFunction(sumIf, UInt64, UInt8),
errors                   AggregateFunction(countIf, UInt8),
score_q                  AggregateFunction(quantilesTDigest(0.5, 0.95, 0.99), Float64),
INDEX idx_config_id custom_eval_config_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour)
ORDER BY (custom_eval_config_id, hour)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS tracer_eval_logger_v2
(
id                   UUID,
trace_id             Nullable(UUID),
observation_span_id  Nullable(String),
trace_session_id     Nullable(UUID),
target_type          LowCardinality(String) DEFAULT 'span',
custom_eval_config_id UUID DEFAULT '00000000-0000-0000-0000-000000000000',
eval_type_id         Nullable(String),
output_bool          Nullable(UInt8),
output_float         Nullable(Float64),
output_str           Nullable(String),
output_str_list      String DEFAULT '[]',
error                UInt8           DEFAULT 0,
error_message        Nullable(String),
eval_explanation     Nullable(String),
output_metadata      String          DEFAULT '{}',
results_tags         String          DEFAULT '[]',
results_explanation  String          DEFAULT '{}',
eval_tags            String          DEFAULT '[]',
eval_id              Nullable(String),
eval_task_id         Nullable(String),
created_at           DateTime64(3, 'UTC'),
updated_at           DateTime64(3, 'UTC'),
deleted_at           Nullable(DateTime64(3, 'UTC')),
is_deleted           UInt8           DEFAULT 0,
_version             UInt64          DEFAULT toUnixTimestamp64Nano(now64(9, 'UTC')),
INDEX idx_trace_id              trace_id              TYPE bloom_filter GRANULARITY 1,
INDEX idx_observation_span_id   observation_span_id   TYPE bloom_filter GRANULARITY 1,
INDEX idx_trace_session_id      trace_session_id      TYPE bloom_filter GRANULARITY 1,
INDEX idx_target_type           target_type           TYPE bloom_filter GRANULARITY 1,
INDEX idx_custom_eval_config_id custom_eval_config_id TYPE bloom_filter GRANULARITY 1
)
ENGINE = ReplacingMergeTree(_version, is_deleted)
PARTITION BY toYYYYMM(created_at)
ORDER BY (custom_eval_config_id, created_at, id)
SETTINGS index_granularity = 8192, allow_nullable_key = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS eval_per_config_mv
TO eval_per_config
AS
SELECT
    toStartOfHour(created_at)                              AS hour,
    custom_eval_config_id,
    countState()                                           AS n,
    sumIfState(toUInt64(1), coalesce(output_bool, 255) = 1) AS passes,
    sumIfState(toUInt64(1), coalesce(output_bool, 255) = 0) AS fails,
    countIfState(error = 1)                                AS errors,
    quantilesTDigestStateIf(0.5, 0.95, 0.99)(output_float, output_float IS NOT NULL) AS score_q
FROM tracer_eval_logger_v2
WHERE is_deleted = 0
  AND custom_eval_config_id != '00000000-0000-0000-0000-000000000000'
GROUP BY hour, custom_eval_config_id;

CREATE TABLE IF NOT EXISTS span_user_rollup
(
project_id            UUID,
end_user_id           UUID,
hour_first_seen       DateTime('UTC'),
span_count            AggregateFunction(count),
trace_count           AggregateFunction(uniq, String),
total_tokens_sum      AggregateFunction(sum, Int64),
prompt_tokens_sum     AggregateFunction(sum, Int64),
completion_tokens_sum AggregateFunction(sum, Int64),
cost_sum              AggregateFunction(sum, Float64),
latency_q             AggregateFunction(quantilesTDigest(0.5, 0.95, 0.99), Int32),
first_seen            AggregateFunction(min, DateTime64(6, 'UTC')),
last_seen             AggregateFunction(max, Nullable(DateTime64(6, 'UTC'))),
error_count           AggregateFunction(countIf, UInt8),
INDEX idx_end_user_id end_user_id TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(hour_first_seen)
ORDER BY (project_id, end_user_id, hour_first_seen)
SETTINGS index_granularity = 8192;

CREATE MATERIALIZED VIEW IF NOT EXISTS span_user_rollup_mv
TO span_user_rollup
AS
SELECT
    project_id,
    end_user_id,
    toStartOfHour(min(start_time))                          AS hour_first_seen,
    countState()                                            AS span_count,
    uniqState(toString(trace_id))                           AS trace_count,
    sumState(toInt64(total_tokens))                         AS total_tokens_sum,
    sumState(toInt64(prompt_tokens))                        AS prompt_tokens_sum,
    sumState(toInt64(completion_tokens))                    AS completion_tokens_sum,
    sumState(cost)                                          AS cost_sum,
    quantilesTDigestState(0.5, 0.95, 0.99)(latency_ms)      AS latency_q,
    minState(start_time)                                    AS first_seen,
    maxState(end_time)                                      AS last_seen,
    countIfState(status = 'ERROR')                          AS error_count
FROM spans
WHERE is_deleted = 0 AND end_user_id IS NOT NULL
GROUP BY project_id, end_user_id;

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
