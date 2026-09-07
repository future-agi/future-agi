"""Pure DDL qualification; no ClickHouse transport or source mutation."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
import sqlparse

from tracer.services.clickhouse.v2.property_catalog import (
    source_capture_schema as subject,
)
from tracer.tests.test_property_catalog_source_capture import specification

SOURCE_UUID = "5a207528-aeb8-4c15-93b7-61554560da40"
TARGET_UUID = "a0a809e5-cd23-4b77-bc30-2c92b1806541"
SCHEMA = Path(__file__).parents[1] / "services/clickhouse/v2/schema"
COLUMNS = """(`id` UInt64, `time` DateTime64(6, 'UTC'),
    `message` String DEFAULT 'TTL, SETTINGS, COMMENT; \\' quoted )' CODEC(ZSTD(3)) COMMENT 'keep me',
    `attrs` Map(String, String), `nested` Tuple(a String, b Array(UInt64)),
    `derived` String MATERIALIZED if(attrs['ttl'] = '', message, attrs['ttl']),
    `_version` UInt64 DEFAULT toUnixTimestamp64Nano(now64(9, 'UTC')),
    `is_deleted` UInt8 DEFAULT 0, `compat` UInt8 ALIAS is_deleted,
    INDEX idx message TYPE bloom_filter(0.01) GRANULARITY 1,
    PROJECTION proj (SELECT id, time, sum(id) AS count GROUP BY id, time),
    CONSTRAINT c CHECK length(message) >= 0)"""
KEYS = (
    "PARTITION BY toDate(time) PRIMARY KEY (id, time) ORDER BY (id, time) SAMPLE BY id"
)
TTL = "TTL time + toIntervalDay(7) TO VOLUME 'cold, SETTINGS', time + toIntervalDay(90) DELETE WHERE message != 'COMMENT'"
SETTINGS = "SETTINGS storage_policy = 'tiered', index_granularity = 8192, index_granularity_bytes = 67108864, max_bytes_to_merge_at_max_space_in_pool = 999, min_age_to_force_merge_seconds = 42"
INPUTS = (
    "id",
    "time",
    "message",
    "attrs",
    "nested",
    "derived",
    "_version",
    "is_deleted",
)


def source(engine="ReplacingMergeTree(_version, is_deleted)"):
    # The server's metadata is commonly one line, with no newline before TTL.
    return " ".join(
        (
            f"CREATE TABLE `src`.`spans` UUID '{SOURCE_UUID}'",
            COLUMNS.replace("\n", " "),
            "ENGINE = " + engine,
            KEYS,
            TTL,
            SETTINGS,
            "COMMENT 'normal table comment'",
        )
    )


def qualify(create=None, **changes):
    arguments = {
        "source_database": "src",
        "source_table": "spans",
        "target_database": "capture",
        "target_table": "owned_spans",
        "target_uuid": TARGET_UUID,
        "required_columns": INPUTS,
    }
    return subject.qualified_capture_schema(
        source() if create is None else create, **(arguments | changes)
    )


@pytest.mark.parametrize(
    "engine",
    [
        "ReplacingMergeTree(_version, is_deleted)",
        "ReplicatedReplacingMergeTree('/custom/path(TTL),{shard}', '{replica}', _version, is_deleted)",
    ],
)
def test_exact_one_line_schema_preserved_with_capture_owned_changes(engine):
    original = source(engine)
    assert "\n" not in original
    result = qualify(original)
    assert COLUMNS.replace("\n", " ") in result.create_sql
    assert KEYS in result.create_sql.replace("\n", " ")
    assert TTL not in result.create_sql
    assert "normal table comment" not in result.create_sql
    assert "ENGINE = MergeTree\n" in result.create_sql
    assert "ReplacingMergeTree" not in result.create_sql
    assert "storage_policy = 'tiered'" in result.create_sql
    assert "index_granularity_bytes = 67108864" in result.create_sql
    assert "max_bytes_to_merge_at_max_space_in_pool = 0" in result.create_sql
    assert "min_age_to_force_merge_seconds = 0" in result.create_sql
    assert result.create_sql.endswith(
        "COMMENT '" + subject.SOURCE_CAPTURE_COMMENT + "'"
    )
    assert result.source_sha256 == subject.normalized_schema_sha256(original)
    assert result.capture_sha256 == subject.normalized_schema_sha256(result.create_sql)
    assert result.required_stored_columns == tuple(sorted(INPUTS))
    result.require_stored_columns(result.required_stored_columns)
    with pytest.raises(FrozenInstanceError):
        result.create_sql = "changed"


def test_capture_metadata_round_trip_omitted_uuid_if_not_exists_quotes_and_engine_parens():
    result = qualify()
    observed = result.create_sql.replace(f" UUID '{TARGET_UUID}'", "")
    observed = observed.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS")
    observed = observed.replace("`capture`.`owned_spans`", "capture.owned_spans")
    observed = observed.replace("ENGINE = MergeTree", "ENGINE = MergeTree()")
    observed = observed.replace("PRIMARY KEY", "PRIMARY\n /* whitespace */ KEY") + ";"
    result.verify_capture(observed)
    assert result.capture_sha256 == subject.normalized_schema_sha256(observed)


def test_source_schema_hash_ignores_header_only_and_spec_derivation_does_not_cycle():
    initial = specification()
    create = source().replace("`src`.`spans`", "`default`.`spans`")
    result = subject.qualified_capture_schema(
        create,
        source_database=initial.source_database,
        source_table="spans",
        target_database=initial.capture_database,
        target_table=initial.capture_table,
        target_uuid=initial.capture_table_uuid,
        required_columns=INPUTS,
    )
    bound = replace(
        initial,
        source_schema_sha256=result.source_sha256,
        capture_schema_sha256=result.capture_sha256,
    )
    assert bound.capture_database == initial.capture_database
    assert bound.capture_table_uuid == initial.capture_table_uuid
    changed_header = create.replace("`default`.`spans`", "`another`.`source`").replace(
        f" UUID '{SOURCE_UUID}'", ""
    )
    assert subject.normalized_schema_sha256(changed_header) == result.source_sha256
    assert (
        qualify(source().replace("90)", "91)")).source_sha256 != qualify().source_sha256
    )
    result.verify_capture(result.create_sql)


def test_source_verification_requires_original_header_and_exact_normalized_metadata():
    original = source()
    result = qualify(original)
    result.verify_source(original.replace(f" UUID '{SOURCE_UUID}'", ""))
    result.verify_source(original.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))
    for old, new in (
        ("`src`", "`other`"),
        ("`spans`", "`other`"),
        (SOURCE_UUID, TARGET_UUID),
        ("90)", "91)"),
        ("'tiered'", "'default'"),
        ("now64(9", "now64(6"),
    ):
        with pytest.raises(subject.SourceCaptureSchemaError, match="differs"):
            result.verify_source(original.replace(old, new))


def test_empty_engine_parentheses_normalization_does_not_change_expressions():
    first = "CREATE TABLE a (id UInt64) ENGINE = MergeTree ORDER BY MergeTree()"
    second = "CREATE TABLE a (id UInt64) ENGINE = MergeTree ORDER BY MergeTree"
    assert subject.normalized_schema_sha256(first) != subject.normalized_schema_sha256(
        second
    )


def test_empty_array_spacing_normalizes_without_hiding_nested_punctuation():
    first = "CREATE TABLE a (id UInt64, a Array(UInt64) DEFAULT []) ENGINE = MergeTree ORDER BY id"
    second = first.replace("[]", "[ ]")
    assert subject.normalized_schema_sha256(first) == subject.normalized_schema_sha256(
        second
    )
    with pytest.raises(subject.SourceCaptureSchemaError, match="one statement"):
        subject.normalized_schema_sha256(first.replace("[]", "[1;]"))


def test_default_required_columns_are_exact_existing_native_catalog_inputs():
    from tracer.services.clickhouse.v2.property_catalog.source_capture_native import (
        REQUIRED_CAPTURE_COLUMNS,
    )

    assert set(subject.CATALOG_CAPTURE_INPUTS) == REQUIRED_CAPTURE_COLUMNS


def test_unread_alias_expression_is_preserved_without_expanding_physical_inputs():
    original = source().replace(
        "`id` UInt64,",
        "`id` UInt64, unused String ALIAS dictGetOrDefault('trace_dict', 'name', id, ''),",
    )
    result = qualify(original)
    assert "ALIAS dictGetOrDefault('trace_dict'" in result.create_sql
    assert "unused" not in result.required_stored_columns
    with pytest.raises(subject.SourceCaptureSchemaError, match="unstored ALIAS"):
        qualify(original, required_columns=INPUTS + ("unused",))


@pytest.mark.parametrize(
    "old,new",
    [
        ("`capture`", "`wrong_database`"),
        ("`owned_spans`", "`wrong_table`"),
        (TARGET_UUID, SOURCE_UUID),
        ("ENGINE = MergeTree", "ENGINE = ReplacingMergeTree(_version)"),
        ("ORDER BY (id, time)", "ORDER BY (time, id)"),
        ("PRIMARY KEY (id, time)", "PRIMARY KEY (id)"),
        ("PARTITION BY toDate(time)", "PARTITION BY toYYYYMM(time)"),
        ("CODEC(ZSTD(3))", "CODEC(ZSTD(1))"),
        ("bloom_filter(0.01)", "bloom_filter(0.1)"),
        ("sum(id) AS count", "max(id) AS count"),
        ("'tiered'", "'default'"),
        ("granularity = 8192", "granularity = 4096"),
        ("_pool = 0", "_pool = 1"),
        ("seconds = 0", "seconds = 86400"),
        (
            "SETTINGS storage_policy",
            "TTL time + toIntervalDay(1) SETTINGS storage_policy",
        ),
        (subject.SOURCE_CAPTURE_COMMENT, "not owned"),
        ("attrs['ttl']", "attrs['other']"),
    ],
)
def test_verify_rejects_exact_metadata_drift(old, new):
    result = qualify()
    assert old in result.create_sql
    with pytest.raises(subject.SourceCaptureSchemaError, match="differs"):
        result.verify_capture(result.create_sql.replace(old, new))


def test_nested_comments_strings_arrays_and_storage_expressions_do_not_define_clauses():
    create = source().replace(
        "`id` UInt64", "`id` UInt64 /* outer ' ( /* nested */ still ' ) */"
    )
    create = create.replace("'tiered'", "'tiered, TTL /*literal*/'")
    create = create.replace(
        "index_granularity = 8192",
        "disk = disk(type = 'local', path = '/data/SETTINGS, )'), index_granularity = 8192",
    )
    create = create.replace(
        "`attrs` Map",
        "`array` Array(Array(String)) DEFAULT [['a,b', 'TTL'], ['COMMENT']], `attrs` Map",
    )
    result = qualify(create)
    assert "DEFAULT [['a,b', 'TTL'], ['COMMENT']]" in result.create_sql
    assert "/* outer ' ( /* nested */ still ' ) */" in result.create_sql
    assert "disk(type = 'local', path = '/data/SETTINGS, )')" in result.create_sql
    assert "TTL time" not in result.create_sql
    result.verify_capture(result.create_sql)


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        " TTL time + toIntervalDay(3)",
        " COMMENT 'source'",
        " TTL time + toIntervalDay(3) COMMENT 'source'",
        " SETTINGS index_granularity = 8192",
    ],
)
def test_optional_table_ttl_settings_comment_are_handled_without_line_assumptions(
    suffix,
):
    result = qualify(
        "CREATE TABLE src.spans (id UInt64, time DateTime) ENGINE = ReplacingMergeTree(id) ORDER BY id"
        + suffix,
        required_columns=("id", "time"),
    )
    assert "TTL" not in result.create_sql
    assert result.create_sql.count("max_bytes_to_merge_at_max_space_in_pool") == 1
    assert result.create_sql.count("min_age_to_force_merge_seconds") == 1
    result.verify_capture(result.create_sql)


@pytest.mark.parametrize(
    "definition,error",
    [
        ("unsafe String TTL time + toIntervalDay(1)", "column TTL: unsafe"),
        (
            "unsafe String ALIAS dictGetOrDefault('dict', 'name', id, '')",
            "unstored ALIAS",
        ),
        ("unsafe UInt64 ALIAS rand()", "unstored ALIAS"),
        ("unsafe DateTime ALIAS now()", "unstored ALIAS"),
        ("unsafe String ALIAS someUserFunction(id)", "unstored ALIAS"),
        ("unsafe String EPHEMERAL", "EPHEMERAL"),
        ("unsafe UInt64 ALIAS missing", "not bound to a stored"),
        ("unsafe UInt64 ALIAS unsafe", "not bound to a stored"),
    ],
)
def test_unsafe_unstored_inputs_and_column_ttl_fail_precisely(definition, error):
    with pytest.raises(subject.SourceCaptureSchemaError, match=error):
        qualify(
            source().replace("`id` UInt64,", "`id` UInt64, " + definition + ","),
            required_columns=INPUTS + ("unsafe",),
        )


def test_direct_alias_chains_are_metadata_not_required_physical_columns():
    result = qualify(
        source().replace(
            "`compat` UInt8 ALIAS is_deleted",
            "`compat` UInt8 ALIAS compat2, compat2 UInt8 ALIAS is_deleted",
        ),
        required_columns=INPUTS + ("compat",),
    )
    assert "compat" not in result.required_stored_columns
    assert "compat2" not in result.required_stored_columns
    with pytest.raises(subject.SourceCaptureSchemaError, match="not bound"):
        qualify(
            source().replace(
                "`compat` UInt8 ALIAS is_deleted",
                "`compat` UInt8 ALIAS compat2, compat2 UInt8 ALIAS compat",
            ),
            required_columns=INPUTS + ("compat",),
        )


@pytest.mark.parametrize(
    "missing", ["_version", "is_deleted", "derived", "message", "id"]
)
def test_per_part_physical_presence_is_mandatory_even_for_default_or_materialized(
    missing,
):
    result = qualify()
    with pytest.raises(
        subject.SourceCaptureSchemaError, match="physically stored columns: " + missing
    ):
        result.require_stored_columns(set(result.required_stored_columns) - {missing})


@pytest.mark.parametrize("replicated", [False, True])
def test_checked_in_canonical_002_metadata_is_copied_not_reconstructed(replicated):
    ddl = sqlparse.split((SCHEMA / "002_spans_v2.sql").read_text())[0]
    ddl = sqlparse.format(ddl, strip_comments=True, strip_whitespace=True).replace(
        "\n", " "
    )
    ddl = ddl.replace("CREATE TABLE IF NOT EXISTS spans", "CREATE TABLE src.spans")
    if replicated:
        ddl = ddl.replace(
            "ReplacingMergeTree(_version",
            "ReplicatedReplacingMergeTree('/arbitrary/{shard}/table', '{replica}', _version",
        )
    result = qualify(ddl, required_columns=subject.CATALOG_CAPTURE_INPUTS)
    for preserved in (
        "proj_root_spans",
        "proj_metrics_hourly",
        "auto_minmax_index__version",
        "deduplicate_merge_projection_mode",
        "index_granularity_bytes",
        "storage_policy",
        "max_dynamic_paths",
    ):
        assert preserved in result.create_sql
    assert set(result.required_stored_columns) == set(subject.CATALOG_CAPTURE_INPUTS)
    assert "TO VOLUME" not in result.create_sql
    result.verify_capture(result.create_sql)


def test_migration_015_trace_name_dictionary_is_not_silently_rewritten_or_unstored():
    migration = (SCHEMA / "015_traces_and_trace_dict.sql").read_text()
    expression = (
        migration.rsplit("MODIFY COLUMN trace_name String", 1)[1]
        .strip()
        .removesuffix(";")
    )
    assert "dictGetOrDefault('trace_dict'" in expression
    ddl = source().replace(
        "`id` UInt64,", "`id` UInt64, trace_name String " + expression + ","
    )
    result = qualify(ddl)
    assert (
        expression in result.create_sql
    )  # No source-database rebinding invented here.
    assert "trace_name" not in result.required_stored_columns
    result.require_stored_columns(result.required_stored_columns)
    # A future caller that reads it needs the stored value, not a dictionary
    # recomputation for an old part lacking this MATERIALIZED column.
    needed = qualify(ddl, required_columns=INPUTS + ("trace_name",))
    assert "trace_name" in needed.required_stored_columns
    with pytest.raises(
        subject.SourceCaptureSchemaError, match="physically stored columns: trace_name"
    ):
        needed.require_stored_columns(
            set(needed.required_stored_columns) - {"trace_name"}
        )
    needed.require_stored_columns(needed.required_stored_columns)


@pytest.mark.parametrize(
    "old,new",
    [
        ("CREATE TABLE", "CREATE OR REPLACE TABLE"),
        ("CREATE TABLE", "ATTACH TABLE"),
        ("`src`.`spans`", "`elsewhere`.`spans`"),
        ("`src`.`spans`", "`src`.`other`"),
        (f" UUID '{SOURCE_UUID}'", " ON CLUSTER 'cluster'"),
        ("ReplacingMergeTree(_version, is_deleted)", "MergeTree()"),
        ("ReplacingMergeTree(_version, is_deleted)", "AggregatingMergeTree()"),
        ("COMMENT 'normal table comment'", "AS SELECT * FROM src.spans"),
        ("COMMENT 'normal table comment'", "COMMENT 'unterminated"),
        ("COMMENT 'normal table comment'", "COMMENT 'x'; DROP TABLE src.spans"),
        ("COMMENT 'normal table comment'", "/* unterminated"),
        ("COMMENT 'normal table comment'", "/* outer /* inner */ unfinished"),
        ("ORDER BY (id, time)", "ORDER BY (id, time]"),
        (
            "storage_policy = 'tiered'",
            "storage_policy = 'tiered', storage_policy = 'other'",
        ),
        ("COMMENT 'normal table comment'", "TTL time + toIntervalDay(2)"),
    ],
)
def test_invalid_or_ambiguous_source_metadata_fails_closed(old, new):
    with pytest.raises(subject.SourceCaptureSchemaError):
        qualify(source().replace(old, new))


@pytest.mark.parametrize(
    "arguments",
    [
        {"target_uuid": "00000000-0000-0000-0000-000000000000"},
        {"target_uuid": SOURCE_UUID},
        {"target_uuid": TARGET_UUID.upper()},
        {"target_uuid": "invalid"},
        {"target_uuid": None},
        {"target_database": "src"},
        {"target_database": "unsafe;drop"},
        {"target_table": "`quoted`"},
        {"source_database": "other"},
        {"source_table": "other"},
    ],
)
def test_exact_source_and_target_identity_arguments(arguments):
    with pytest.raises(subject.SourceCaptureSchemaError):
        qualify(**arguments)


def test_parser_size_and_nesting_are_bounded():
    with pytest.raises(subject.SourceCaptureSchemaError, match="byte bound"):
        qualify(source() + " " * subject.MAX_SOURCE_CREATE_BYTES)
    with pytest.raises(subject.SourceCaptureSchemaError, match="nesting"):
        qualify(source().replace("DEFAULT 0", "DEFAULT " + "(" * 129 + "0" + ")" * 129))
    with pytest.raises(subject.SourceCaptureSchemaError, match="comment nesting"):
        qualify(source() + "/*" * 129 + "*/" * 129)
