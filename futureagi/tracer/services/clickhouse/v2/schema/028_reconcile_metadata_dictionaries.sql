-- Re-issue the native metadata dictionaries for already-installed clusters.
-- Before dictionary DDL was included in the replicated-mode rewrite, 015/017/018
-- could be recorded as applied while creating dictionaries on only one node.
-- Source tables replicate, dictionaries do not: span inserts and metadata reads
-- on another replica then fail. Keep earlier hash-tracked files unchanged;
-- this forward-only file repairs their definitions through the fixed rewriter.
-- Definitions match 015/017/018. Replacing dictionaries does not replay spans,
-- alter source tables, or recover previously acknowledged dead-letter batches.

CREATE OR REPLACE DICTIONARY trace_dict
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

CREATE OR REPLACE DICTIONARY end_users_dict
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

CREATE OR REPLACE DICTIONARY trace_sessions_dict
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
