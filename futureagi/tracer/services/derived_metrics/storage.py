"""Physical contract for the live metric store, independent of Django and clients.

These are immutable attempt deltas, not authoritative latest-state tables.
Readers must restrict to ONE pinned Postgres manifest's exact checkpoint and
committed attempts *before* collapsing the logical key, then apply deletion,
status and time predicates. Neither FINAL nor generation <= head is sufficient.
Publication, read guards, retention and GC belong to publication.py/refresh.py.

A checkpoint uses the same payload and attempt-qualified replacement key as its
source. Compaction writes a new frozen attempt, never overwrites a pinned base.
Retries of a frozen batch must be byte-identical. is_deleted is ordinary payload,
not an engine cleanup parameter, so merges cannot discard needed tombstones.
Dirty keys/preimages live in the append-only PG attempt ledger, written once:
state transitions never copy them (DCM1). There is deliberately no CH key ledger.
"""

from dataclasses import dataclass
from types import MappingProxyType

PROJECT_PARTITION = "cityHash64(project_id) % 32"
PROJECT_SHARD_KEY = "cityHash64(project_id)"
BUCKET_PARTITION = "toYYYYMM(bucket_start)"
# Only field checkpoints hold a complete unit population per pinned generation.
# unit_id is immutable in their logical key; ownership and timestamps are not.
FIELD_CHECKPOINT_BUCKETS = 8
FIELD_CHECKPOINT_PARTITION = (
    f"(cityHash64(project_id) % 32, cityHash64(unit_id) % {FIELD_CHECKPOINT_BUCKETS})"
)

FIELD_VALUE_SCHEMA = (
    ("org_id", "UUID"),
    ("workspace_id", "UUID"),
    ("project_id", "UUID"),
    ("field_id", "UUID"),
    ("field_version", "UInt32"),
    ("level", "LowCardinality(String)"),
    ("unit_id", "String"),
    ("session_id", "String"),
    ("user_id", "String"),
    ("unit_start", "DateTime64(6, 'UTC')"),
    ("bucket_start", "DateTime('UTC')"),
    ("last_arrival_ns", "UInt64"),
    ("settled_at", "Nullable(DateTime64(6, 'UTC'))"),
    ("status", "LowCardinality(String)"),
    ("no_value_reason", "LowCardinality(String)"),
    ("value_bool", "Nullable(UInt8)"),
    ("value_number", "Nullable(Float64)"),
    ("value_text", "Nullable(String)"),
    ("part_n", "Nullable(Float64)"),
    ("part_d", "Nullable(Float64)"),
    ("inputs", "Map(LowCardinality(String), Nullable(Float64))"),
    ("sample_probability", "Nullable(Float64)"),
    ("sampling_scope", "String"),
    ("sampling_stratum", "String"),
    ("sampling_design", "LowCardinality(String)"),
    ("population_size", "Nullable(UInt64)"),
    ("invalid_probability", "UInt8"),
    ("group_key", "String"),
    ("is_deleted", "UInt8"),
    ("_version", "UInt64"),
    ("generation", "UInt64"),
    ("attempt_id", "UUID"),
    ("fencing_epoch", "UInt32"),
    ("row_sequence", "UInt32"),
    ("inputs_bool", "Map(String, Nullable(UInt8))"),
    ("inputs_text", "Map(String, Nullable(String))"),
)
FIELD_VALUE_KEY = (
    "org_id",
    "project_id",
    "field_id",
    "field_version",
    "level",
    "unit_id",
    "generation",
    "attempt_id",
)

# Untransformed per-(group, scope, stratum) sufficient statistics. Closed-stratum
# corrections and support decisions must be recomputed AFTER merging buckets.
# Missing/invalid rows contribute scope evidence too, not just successful units.
BUCKET_STAT_SCHEMA = (
    ("unit_count", "UInt64"),
    ("valid_count", "UInt64"),
    ("missing_count", "UInt64"),
    ("invalid_count", "UInt64"),
    ("population_missing", "UInt64"),
    ("non_binary", "UInt64"),
    ("sampled", "UInt64"),
    ("known_sampled", "UInt64"),
    ("census_disqualifiers", "UInt64"),
    ("support_invalid", "UInt64"),
    ("support_m", "UInt64"),
    ("support_q", "UInt64"),
    ("support_success", "UInt64"),
    ("support_failure", "UInt64"),
    ("nonempty_scope_count", "UInt64"),
    ("weighted_n", "Float64"),
    ("weighted_d", "Float64"),
    ("sum_w", "Float64"),
    ("sum_w2", "Float64"),
    ("p_min", "Float64"),
    ("p_max", "Float64"),
    ("population_min", "Float64"),
    ("population_max", "Float64"),
    ("support_p_min", "Float64"),
    ("support_p_max", "Float64"),
    ("b_yy", "Float64"),
    ("b_xy", "Float64"),
    ("b_xx", "Float64"),
    ("c_yy", "Float64"),
    ("c_xy", "Float64"),
    ("c_xx", "Float64"),
    ("support_n_min", "Float64"),
    ("support_n_max", "Float64"),
    ("support_d_min", "Float64"),
    ("support_d_max", "Float64"),
    ("support_n", "Float64"),
    ("support_d", "Float64"),
    ("support_nn", "Float64"),
    ("support_nd", "Float64"),
    ("support_abs_nd", "Float64"),
    ("support_dd", "Float64"),
    ("design_min", "String"),
    ("design_max", "String"),
    ("raw_design_min", "String"),
    ("raw_design_max", "String"),
    ("scope_min", "String"),
    ("scope_max", "String"),
    ("uniq_state", "AggregateFunction(uniqCombined64, String)"),
    ("quantile_state", "AggregateFunction(quantileTDigest, Float64)"),
)
BUCKET_TOTAL_SCHEMA = (
    ("org_id", "UUID"),
    ("workspace_id", "UUID"),
    ("project_id", "UUID"),
    ("metric_id", "UUID"),
    ("metric_version", "UInt32"),
    ("bucket_start", "DateTime('UTC')"),
    ("group_key", "String"),
    ("scope_key", "String"),
    ("stratum_key", "String"),
    *BUCKET_STAT_SCHEMA,
    ("is_deleted", "UInt8"),
    ("version", "UInt64"),
    ("generation", "UInt64"),
    ("attempt_id", "UUID"),
    ("fencing_epoch", "UInt32"),
    ("row_sequence", "UInt32"),
)
BUCKET_TOTAL_KEY = (
    "org_id",
    "project_id",
    "metric_id",
    "metric_version",
    "bucket_start",
    "group_key",
    "scope_key",
    "stratum_key",
    "generation",
    "attempt_id",
)

UNIT_STATE_SCHEMA = (
    ("org_id", "UUID"),
    ("project_id", "UUID"),
    ("level", "LowCardinality(String)"),
    ("unit_id", "String"),
    ("last_arrival_ns", "UInt64"),
    ("first_seen", "DateTime64(6, 'UTC')"),
    ("reopened", "UInt8"),
    ("is_deleted", "UInt8"),
    ("_version", "UInt64"),
    ("due_at", "DateTime64(6, 'UTC')"),
    ("generation", "UInt64"),
    ("attempt_id", "UUID"),
    ("fencing_epoch", "UInt32"),
    ("row_sequence", "UInt32"),
)
UNIT_STATE_KEY = (
    "org_id",
    "project_id",
    "level",
    "unit_id",
    "generation",
    "attempt_id",
)

SOURCE_DEPENDENCY_SCHEMA = (
    ("org_id", "UUID"),
    ("project_id", "UUID"),
    ("trace_id", "String"),
    ("span_id", "String"),
    ("raw_session_id", "String"),
    ("canonical_session_id", "String"),
    ("raw_user_id", "String"),
    ("canonical_user_id", "String"),
    ("unit_start", "DateTime64(6, 'UTC')"),
    ("is_deleted", "UInt8"),
    ("generation", "UInt64"),
    ("attempt_id", "UUID"),
    ("_version", "UInt64"),
)
SOURCE_DEPENDENCY_KEY = (
    "org_id",
    "project_id",
    "trace_id",
    "span_id",
    "generation",
    "attempt_id",
)

# Anonymous pre-fold group statistics, never unit evidence or a user group key.
RETIRED_PART_SCHEMA = BUCKET_TOTAL_SCHEMA
RETIRED_PART_KEY = BUCKET_TOTAL_KEY

FIELD_VALUE_COLUMNS = tuple(name for name, _ in FIELD_VALUE_SCHEMA)
BUCKET_TOTAL_COLUMNS = tuple(name for name, _ in BUCKET_TOTAL_SCHEMA)
UNIT_STATE_COLUMNS = tuple(name for name, _ in UNIT_STATE_SCHEMA)
SOURCE_DEPENDENCY_COLUMNS = tuple(name for name, _ in SOURCE_DEPENDENCY_SCHEMA)
RETIRED_PART_COLUMNS = tuple(name for name, _ in RETIRED_PART_SCHEMA)


@dataclass(frozen=True)
class MetricTable:
    """Identifiers/types for explicit INSERT lists and receipt verification."""

    name: str
    schema_file: str
    schema: tuple[tuple[str, str], ...]
    version_column: str
    partition_by: str
    order_by: tuple[str, ...]

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.schema)

    @property
    def logical_key(self) -> tuple[str, ...]:
        return self.order_by[:-2]


FIELD_VALUES = MetricTable(
    "metric_field_values",
    "031_metric_field_values.sql",
    FIELD_VALUE_SCHEMA,
    "_version",
    PROJECT_PARTITION,
    FIELD_VALUE_KEY,
)
FIELD_CHECKPOINT = MetricTable(
    "metric_field_values_checkpoint",
    FIELD_VALUES.schema_file,
    FIELD_VALUE_SCHEMA,
    "_version",
    FIELD_CHECKPOINT_PARTITION,
    FIELD_VALUE_KEY,
)
BUCKET_TOTALS = MetricTable(
    "metric_bucket_totals",
    "032_metric_bucket_totals.sql",
    BUCKET_TOTAL_SCHEMA,
    "version",
    BUCKET_PARTITION,
    BUCKET_TOTAL_KEY,
)
BUCKET_CHECKPOINT = MetricTable(
    "metric_bucket_totals_checkpoint",
    BUCKET_TOTALS.schema_file,
    BUCKET_TOTAL_SCHEMA,
    "version",
    BUCKET_PARTITION,
    BUCKET_TOTAL_KEY,
)
UNIT_STATE = MetricTable(
    "metric_unit_state",
    "033_metric_unit_state.sql",
    UNIT_STATE_SCHEMA,
    "_version",
    PROJECT_PARTITION,
    UNIT_STATE_KEY,
)
SOURCE_DEPENDENCIES = MetricTable(
    "metric_source_dependencies",
    "034_metric_source_dependencies.sql",
    SOURCE_DEPENDENCY_SCHEMA,
    "_version",
    PROJECT_PARTITION,
    SOURCE_DEPENDENCY_KEY,
)
RETIRED_PARTS = MetricTable(
    "metric_bucket_retired_parts",
    "035_metric_bucket_retired_parts.sql",
    RETIRED_PART_SCHEMA,
    "version",
    BUCKET_PARTITION,
    RETIRED_PART_KEY,
)
# Current facts are keyed by the native PHYSICAL replacement identity, not a
# mutable session/user/time predicate. Root retractions remain ordinary rows.
# A checkpoint occupies its own immutable attempt namespace, like unit state.
LIST_CURRENT_SCHEMA = (
    ("org_id", "UUID"),
    ("project_id", "UUID"),
    ("observation_type", "LowCardinality(String)"),
    ("service_name", "LowCardinality(String)"),
    ("start_hour", "DateTime('UTC')"),
    ("trace_id", "String"),
    ("span_id", "String"),
    ("unit_start", "DateTime64(6, 'UTC')"),
    ("end_time", "Nullable(DateTime64(6, 'UTC'))"),
    ("parent_span_id", "Nullable(String)"),
    ("raw_session_id", "String"),
    ("canonical_session_id", "String"),
    ("raw_user_id", "String"),
    ("canonical_user_id", "String"),
    ("name", "String"),
    ("input", "String"),
    ("cost", "Float64"),
    ("total_tokens", "Int32"),
    ("prompt_tokens", "Int32"),
    ("completion_tokens", "Int32"),
    ("attributes_extra", "String"),
    ("attrs_string", "Map(String, String)"),
    ("attrs_number", "Map(String, Float64)"),
    ("attrs_bool", "Map(String, UInt8)"),
    ("source_version", "UInt64"),
    ("is_deleted", "UInt8"),
    ("generation", "UInt64"),
    ("attempt_id", "UUID"),
    ("_version", "UInt64"),
)
LIST_CURRENT_KEY = (
    "org_id",
    "project_id",
    "observation_type",
    "service_name",
    "start_hour",
    "trace_id",
    "span_id",
    "generation",
    "attempt_id",
)
LIST_CURRENT_ROOTS = MetricTable(
    "list_current_root_facts",
    "036_list_current_root_facts.sql",
    LIST_CURRENT_SCHEMA,
    "_version",
    PROJECT_PARTITION,
    LIST_CURRENT_KEY,
)
LIST_CURRENT_MEMBERS = MetricTable(
    "list_current_members",
    "036_list_current_root_facts.sql",
    LIST_CURRENT_SCHEMA,
    "_version",
    PROJECT_PARTITION,
    LIST_CURRENT_KEY,
)
# A user reassignment retracts the old user-key and inserts the new key in the
# same publication. Time, deletion and usage stay payload, never pruning keys.
LIST_USER_ACTIVITY_SCHEMA = tuple(
    column
    for column in LIST_CURRENT_SCHEMA
    if column[0]
    not in {
        "parent_span_id",
        "name",
        "input",
        "attributes_extra",
        "attrs_string",
        "attrs_number",
        "attrs_bool",
    }
)
LIST_USER_ACTIVITY = MetricTable(
    "list_current_user_activity",
    "037_list_current_user_activity.sql",
    LIST_USER_ACTIVITY_SCHEMA,
    "_version",
    PROJECT_PARTITION,
    (*LIST_CURRENT_KEY[:2], "canonical_user_id", *LIST_CURRENT_KEY[2:]),
)
LIST_CURRENT_TABLES = (LIST_CURRENT_ROOTS, LIST_CURRENT_MEMBERS, LIST_USER_ACTIVITY)

TABLES = MappingProxyType(
    {
        table.name: table
        for table in (
            FIELD_VALUES,
            FIELD_CHECKPOINT,
            BUCKET_TOTALS,
            BUCKET_CHECKPOINT,
            UNIT_STATE,
            SOURCE_DEPENDENCIES,
            RETIRED_PARTS,
            *LIST_CURRENT_TABLES,
        )
    }
)
SCHEMA_FILES = tuple(dict.fromkeys(table.schema_file for table in TABLES.values()))
