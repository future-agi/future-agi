"""Independent PG metadata fixtures; no services, Django startup or schema writes."""

import socket
import traceback
from copy import deepcopy
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.oss_cdc_inventory import PeerTarget
from tracer.services.clickhouse.oss_cdc_source import (
    SourceError,
    SourceInventory,
    SourceTable,
    inspect_source,
)

SOURCE = PeerTarget("pg_source", "pg", 5432, "app")
TABLES = ("usage_apicalllog", "simulate_agent_definition", "model_hub_score")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    guards = []
    for name in ("socket", "create_connection", "getaddrinfo"):
        guard = Mock(side_effect=AssertionError("network forbidden"))
        monkeypatch.setattr(socket, name, guard)
        guards.append(guard)
    yield
    for guard in guards:
        guard.assert_not_called()


def column(
    table, name, position, udt, nullable="NO", *, precision=None, scale=None, count=3
):
    # PG SELECT row, not generated from any production mapping/constants.
    return (
        "app",
        "public",
        table,
        name,
        position,
        "pg_catalog",
        udt,
        nullable,
        precision,
        scale,
        None,
        "NEVER",
        count,
    )


def primary_key(table, name="id", position=1, count=1):
    return ("app", "public", table, name, position, count, True)


@pytest.fixture
def api():
    responses = [
        [("app",)],
        [
            column("model_hub_score", "id", 1, "uuid"),
            column("model_hub_score", "value_history", 2, "jsonb"),
            column("model_hub_score", "tracer_project_id", 3, "uuid", "YES"),
            column("simulate_agent_definition", "id", 1, "uuid"),
            column("simulate_agent_definition", "languages", 2, "_varchar", "YES"),
            column(
                "simulate_agent_definition", "target_speaks_first", 3, "bool", "YES"
            ),
            column("usage_apicalllog", "id", 1, "uuid", count=2),
            column("usage_apicalllog", "config", 2, "jsonb", count=2),
        ],
        [primary_key(table) for table in TABLES],
    ]

    def query(sql, parameters):
        sql = " ".join(sql.split())
        assert sql.startswith("SELECT ") and ";" not in sql
        if sql == "SELECT current_database()":
            assert parameters == {}
            return responses[0]
        assert parameters == {"database": "app", "tables": sorted(TABLES)}
        if "FROM information_schema.columns" in sql:
            for field in ("udt_schema", "domain_name", "is_generated", "is_nullable"):
                assert field in sql
            assert "table_schema = 'public'" in sql
            assert "table_catalog = %(database)s" in sql
            assert "table_name = ANY(%(tables)s)" in sql
            assert "count(*) OVER" in sql
            return responses[1]
        assert "FROM pg_catalog.pg_constraint" in sql
        assert "p.contype = 'p'" in sql and "n.nspname = 'public'" in sql
        assert "c.relname = ANY(%(tables)s)" in sql
        assert "WITH ORDINALITY" in sql and "cardinality(p.conkey)" in sql
        assert "a.attnotnull" in sql
        return responses[2]

    return responses, Mock(side_effect=query)


def inspect(api):
    return inspect_source(api[1], source=SOURCE, tables=TABLES)


def replace_field(api, group, row_index, field, value):
    row = list(api[0][group][row_index])
    row[field] = value
    api[0][group][row_index] = tuple(row)


def test_exact_source_owned_shapes_three_selects_no_mutation(api):
    before = deepcopy(api[0])
    result = inspect(api)
    assert result.source == SOURCE
    assert result.tables == {
        "model_hub_score": SourceTable(
            (
                ("id", "UUID"),
                ("value_history", "String"),
                ("tracer_project_id", "Nullable(UUID)"),
            ),
            ("id",),
        ),
        "simulate_agent_definition": SourceTable(
            (
                ("id", "UUID"),
                ("languages", "Array(String)"),
                ("target_speaks_first", "Nullable(Bool)"),
            ),
            ("id",),
        ),
        "usage_apicalllog": SourceTable(
            (("id", "UUID"), ("config", "String")), ("id",)
        ),
    }
    assert api[0] == before
    assert api[1].call_count == 3


@pytest.mark.parametrize("nullable", ["NO", "YES"])
@pytest.mark.parametrize(
    "udt,precision,scale,expected",
    [
        ("varchar", None, None, "String"),
        ("text", None, None, "String"),
        ("jsonb", None, None, "String"),
        ("bool", None, None, "Bool"),
        ("int4", 32, 0, "Int32"),
        ("int8", 64, 0, "Int64"),
        ("float8", 53, None, "Float64"),
        ("uuid", None, None, "UUID"),
        ("timestamptz", None, None, "DateTime64(6)"),
        ("numeric", 5, 2, "Decimal(5,2)"),
        ("numeric", 16, 8, "Decimal(16,8)"),
        ("numeric", 1, 0, "Decimal(1,0)"),
        ("numeric", 38, 38, "Decimal(38,38)"),
        ("numeric", 76, 38, "Decimal(76,38)"),
    ],
)
def test_strict_builtin_and_scalar_nullability_mapping(
    api, udt, precision, scale, expected, nullable
):
    api[0][1][1] = column(
        "model_hub_score",
        "value_history",
        2,
        udt,
        nullable,
        precision=precision,
        scale=scale,
    )
    actual = dict(inspect(api).tables["model_hub_score"].columns)["value_history"]
    assert actual == (f"Nullable({expected})" if nullable == "YES" else expected)


def test_nonnullable_array_is_supported_without_expanding_nullable_exception(api):
    api[0][1][1] = column("model_hub_score", "value_history", 2, "_varchar")
    assert (
        dict(inspect(api).tables["model_hub_score"].columns)["value_history"]
        == "Array(String)"
    )
    replace_field(api, 1, 1, 7, "YES")
    with pytest.raises(SourceError, match="unqualified nullable source array"):
        inspect(api)


@pytest.mark.parametrize(
    "field,value", [(2, "model_hub_score"), (3, "other_languages")]
)
def test_nullable_array_exception_is_exact_table_and_column(api, field, value):
    replace_field(api, 1, 4, field, value)
    with pytest.raises(SourceError):
        inspect(api)


def test_source_and_composite_pk_order_are_independent_of_response_order(api):
    replace_field(api, 1, 2, 7, "NO")
    api[0][2] = [
        primary_key("model_hub_score", "tracer_project_id", 2, 2),
        primary_key("simulate_agent_definition"),
        primary_key("model_hub_score", "id", 1, 2),
        primary_key("usage_apicalllog"),
    ]
    api[0][1].reverse()
    result = inspect(api)
    assert tuple(result.tables) == tuple(sorted(TABLES))
    assert result.tables["model_hub_score"].columns == (
        ("id", "UUID"),
        ("value_history", "String"),
        ("tracer_project_id", "UUID"),
    )
    assert result.tables["model_hub_score"].primary_key == ("id", "tracer_project_id")


@pytest.mark.parametrize(
    "identity", [[], [("wrong-db-secret",)], [("app",), ("app",)], [(None,)], [(True,)]]
)
def test_database_identity_must_match_before_metadata_reads(api, identity):
    api[0][0] = identity
    with pytest.raises(SourceError) as error:
        inspect(api)
    assert "wrong-db-secret" not in str(error.value)
    assert api[1].call_count == 1


@pytest.mark.parametrize("group", [0, 1, 2])
@pytest.mark.parametrize(
    "response",
    [None, {}, "secret-payload", [None], [{}], [("short",)], [["long"] * 14]],
)
def test_malformed_metadata_response_is_rejected_and_redacted(api, group, response):
    api[0][group] = response
    with pytest.raises(SourceError) as error:
        inspect(api)
    assert "secret-payload" not in str(error.value)


@pytest.mark.parametrize("group", [1, 2])
@pytest.mark.parametrize(
    "field,value",
    [
        (0, "other-db"),
        (1, "private"),
        (2, "foreign_table"),
        (2, ["bad"]),
        (3, "bad;secret"),
        (3, "é"),
        (3, None),
    ],
)
def test_metadata_identity_is_exact_and_safe(api, group, field, value):
    replace_field(api, group, 0, field, value)
    with pytest.raises(SourceError):
        inspect(api)


@pytest.mark.parametrize("group", [1, 2])
@pytest.mark.parametrize("table", TABLES)
def test_every_requested_table_needs_columns_and_primary_key(api, group, table):
    api[0][group] = [row for row in api[0][group] if row[2] != table]
    with pytest.raises(SourceError, match="incomplete"):
        inspect(api)


def test_missing_trailing_column_is_not_a_complete_contiguous_prefix(api):
    del api[0][1][2]
    with pytest.raises(SourceError, match="incomplete"):
        inspect(api)


@pytest.mark.parametrize("group,count_field", [(1, 12), (2, 5)])
@pytest.mark.parametrize("value", [None, True, "3", 0, -1, 999])
def test_metadata_cardinality_must_be_exact_integer(api, group, count_field, value):
    replace_field(api, group, 0, count_field, value)
    with pytest.raises(SourceError):
        inspect(api)


@pytest.mark.parametrize("group", [1, 2])
@pytest.mark.parametrize("value", [None, True, "1", 0, -1])
def test_source_and_key_positions_are_exact_positive_integers(api, group, value):
    replace_field(api, group, 0, 4, value)
    with pytest.raises(SourceError):
        inspect(api)


def test_dropped_source_column_gaps_preserve_order_and_full_count(api):
    replace_field(api, 1, 1, 4, 7)
    replace_field(api, 1, 2, 4, 99)
    api[0][1].reverse()
    assert inspect(api).tables["model_hub_score"].columns == (
        ("id", "UUID"),
        ("value_history", "String"),
        ("tracer_project_id", "Nullable(UUID)"),
    )


def test_pk_ordinality_still_cannot_have_gaps(api):
    replace_field(api, 2, 0, 4, 99)
    with pytest.raises(SourceError, match="source positions"):
        inspect(api)


@pytest.mark.parametrize("mode", ["same-position", "same-name", "extra-row"])
def test_duplicate_columns_cannot_become_valid_inventory(api, mode):
    if mode == "extra-row":
        api[0][1].append(api[0][1][0])
    else:
        replace_field(
            api,
            1,
            1,
            4 if mode == "same-position" else 3,
            1 if mode == "same-position" else "id",
        )
    with pytest.raises(SourceError):
        inspect(api)


@pytest.mark.parametrize(
    "udt",
    [
        "int2",
        "float4",
        "json",
        "timestamp",
        "date",
        "bytea",
        "_text",
        "_uuid",
        "unknown-secret",
        None,
        [],
    ],
)
def test_unknown_builtin_type_never_falls_back_to_string(api, udt):
    replace_field(api, 1, 1, 6, udt)
    with pytest.raises(SourceError) as error:
        inspect(api)
    assert "unknown-secret" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        (5, "public"),
        (5, None),
        (10, "domain-secret"),
        (10, ""),
        (11, "ALWAYS"),
        (11, None),
        (11, ""),
        (7, None),
        (7, "yes"),
        (7, True),
    ],
)
def test_domain_generation_and_nullability_metadata_are_strict(api, field, value):
    replace_field(api, 1, 1, field, value)
    with pytest.raises(SourceError) as error:
        inspect(api)
    assert "domain-secret" not in str(error.value)


@pytest.mark.parametrize(
    "precision,scale",
    [
        (None, None),
        (0, 0),
        (77, 1),
        (76, 39),
        (76, 76),
        (3, 4),
        (4, -1),
        (True, 0),
        (4, True),
        ("5", 2),
        (5, "2"),
        (5, None),
        (5.0, 2),
    ],
)
def test_unfaithful_numeric_fallbacks_are_rejected(api, precision, scale):
    api[0][1][1] = column(
        "model_hub_score",
        "value_history",
        2,
        "numeric",
        precision=precision,
        scale=scale,
    )
    with pytest.raises(SourceError, match="decimal"):
        inspect(api)


@pytest.mark.parametrize(
    "name", ["_peerdb_version", "_peerdb_is_deleted", "_PEERDB_synced_at"]
)
def test_transport_columns_cannot_be_source_columns(api, name):
    replace_field(api, 1, 1, 3, name)
    with pytest.raises(SourceError, match="reserved"):
        inspect(api)


@pytest.mark.parametrize(
    "name", ["eval_score", "eval_output_str", "eval_trace_id", "eval_dataset_id"]
)
def test_usage_derived_columns_are_reserved_only_on_usage_table(api, name):
    replace_field(api, 1, 1, 3, name)
    assert name in dict(inspect(api).tables["model_hub_score"].columns)
    replace_field(api, 1, 7, 3, name)
    with pytest.raises(SourceError, match="derived usage"):
        inspect(api)


@pytest.mark.parametrize(
    "field,value", [(3, "missing_column"), (3, "config"), (6, False), (6, 1), (6, None)]
)
def test_pk_metadata_requires_existing_pg_nonnullable_columns(api, field, value):
    replace_field(api, 2, 0, field, value)
    if value == "config":
        replace_field(api, 1, 7, 7, "YES")
    with pytest.raises(SourceError):
        inspect(api)


def test_array_mapping_must_not_hide_nullable_pk(api):
    api[0][2][1] = primary_key("simulate_agent_definition", "languages")
    with pytest.raises(SourceError, match="nullable source primary key"):
        inspect(api)


@pytest.mark.parametrize(
    "mode", ["duplicate-name", "duplicate-position", "missing-tail"]
)
def test_composite_pk_completeness_and_uniqueness(api, mode):
    score_keys = [primary_key("model_hub_score", "id", 1, 2)]
    if mode != "missing-tail":
        score_keys.append(
            primary_key(
                "model_hub_score",
                "id" if mode == "duplicate-name" else "value_history",
                2 if mode == "duplicate-name" else 1,
                2,
            )
        )
    api[0][2] = [row for row in api[0][2] if row[2] != "model_hub_score"] + score_keys
    with pytest.raises(SourceError):
        inspect(api)


@pytest.mark.parametrize("call_index", [1, 2, 3])
@pytest.mark.parametrize("exception", [TimeoutError, PermissionError, RuntimeError])
def test_failed_pg_reads_are_sanitized_no_partial_result(api, call_index, exception):
    delegate = api[1].side_effect

    def query(sql, params):
        if api[1].call_count == call_index:
            raise exception("password=secret-token SELECT private payload")
        return delegate(sql, params)

    api[1].side_effect = query
    with pytest.raises(SourceError, match="inspection failed") as error:
        inspect(api)
    assert error.value.__suppress_context__ and error.value.__cause__ is None
    assert "secret-token" not in "".join(traceback.format_exception(error.value))
    assert api[1].call_count == call_index


@pytest.mark.parametrize(
    "tables",
    [
        None,
        [],
        (),
        "model_hub_score",
        ("a", "a"),
        ("bad;SELECT",),
        ("é",),
        ("1bad",),
        ("a" * 64,),
        (None,),
    ],
)
def test_bad_requested_identifiers_fail_before_query(api, tables):
    with pytest.raises(SourceError):
        inspect_source(api[1], source=SOURCE, tables=tables)
    api[1].assert_not_called()


@pytest.mark.parametrize("source", [None, {}, "pg"])
def test_invalid_source_peer_fails_before_query(api, source):
    with pytest.raises(SourceError):
        inspect_source(api[1], source=source, tables=TABLES)
    api[1].assert_not_called()


def test_missing_transport_is_rejected():
    with pytest.raises(SourceError):
        inspect_source(None, source=SOURCE, tables=TABLES)


@pytest.mark.parametrize(
    "columns",
    [
        None,
        [],
        (),
        (("id",),),
        (("id", "UUID", "DEFAULT"),),
        (["id", "UUID"],),
        (("bad;secret", "UUID"),),
        (("é", "UUID"),),
        ((None, "UUID"),),
        (("_peerdb_version", "UUID"),),
        (("id", "UUID"), ("id", "String")),
    ],
)
def test_direct_source_table_validates_column_shape_names_and_duplicates(columns):
    with pytest.raises(SourceError):
        SourceTable(columns, ("id",))


@pytest.mark.parametrize(
    "kind",
    [
        None,
        [],
        "UInt32",
        "Nullable(Array(String))",
        "Nullable(Nullable(UUID))",
        "String DEFAULT 'x'",
        "Decimal(77,0)",
        "Decimal(76,39)",
        "Nullable(Decimal(76,39))",
        "Decimal(0,0)",
        "Decimal(3,4)",
        "Decimal(05,2)",
        "Decimal(5,02)",
        "DateTime64(9)",
        "UUID\n",
    ],
)
def test_direct_source_table_types_are_only_canonical_qualified_shapes(kind):
    with pytest.raises(SourceError):
        SourceTable((("id", "UUID"), ("value", kind)), ("id",))


@pytest.mark.parametrize(
    "pk",
    [
        None,
        [],
        (),
        "id",
        ("missing",),
        ("id", "id"),
        ("bad;secret",),
        ([],),
        (None,),
        ("nullable",),
    ],
)
def test_direct_source_table_validates_complete_nonnullable_pk(pk):
    with pytest.raises(SourceError):
        SourceTable((("id", "UUID"), ("nullable", "Nullable(String)")), pk)


@pytest.mark.parametrize(
    "kind",
    [
        "String",
        "Bool",
        "Int32",
        "Int64",
        "Float64",
        "UUID",
        "DateTime64(6)",
        "Array(String)",
        "Decimal(76,38)",
        "Nullable(Decimal(16,8))",
    ],
)
def test_direct_source_table_accepts_only_supported_type_shapes(kind):
    table = SourceTable((("id", "UUID"), ("value", kind)), ("id",))
    assert table.columns[1][1] == kind


@pytest.mark.parametrize("tables", [None, [], {}, {"bad;secret": None}, {"safe": None}])
def test_direct_inventory_validates_tables(tables):
    with pytest.raises(SourceError):
        SourceInventory(SOURCE, tables)


def test_direct_inventory_validates_source_and_table_specific_reserved_fields():
    table = SourceTable((("id", "UUID"), ("eval_score", "Float64")), ("id",))
    with pytest.raises(SourceError):
        SourceInventory(None, {"model_hub_score": table})
    with pytest.raises(SourceError, match="derived usage"):
        SourceInventory(SOURCE, {"usage_apicalllog": table})
    supplied = {"model_hub_score": table}
    inventory = SourceInventory(SOURCE, supplied)
    supplied.clear()
    assert inventory.tables == {"model_hub_score": table}
