"""Pure CREATE parsing may be reused; admission evidence must remain fresh."""

import hashlib
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2 import catalog_prod_schema as schema
from tracer.services.clickhouse.v2.property_catalog import write_admission
from tracer.tests.test_property_catalog_write_reattestation import lane

OPTIONS = {
    "target_database": "catalog_test",
    "expected_table": "catalog_rows",
    "cluster": "test_cluster",
}
SQL = (
    "CREATE TABLE catalog_test.catalog_rows ON CLUSTER 'test_cluster' "
    "UUID '11111111-1111-1111-1111-111111111111' "
    "(value UInt64) ENGINE = MergeTree ORDER BY value"
)


@pytest.fixture(autouse=True)
def empty_parser_cache():
    schema._cached_canonical_create_tokens.cache_clear()
    yield
    schema._cached_canonical_create_tokens.cache_clear()


def test_identical_full_inputs_reuse_only_an_immutable_tuple(monkeypatch):
    tokenize = Mock(wraps=schema._create_query_tokens)
    monkeypatch.setattr(schema, "_create_query_tokens", tokenize)
    first = schema._canonical_create_tokens(SQL, **OPTIONS)
    assert schema._canonical_create_tokens(SQL, **OPTIONS) is first
    assert type(first) is tuple and all(type(token) is str for token in first)
    with pytest.raises(TypeError):
        first[0] = "changed"
    assert tokenize.call_count == 1
    assert schema._cached_canonical_create_tokens.cache_info().hits == 1


@pytest.mark.parametrize("field", OPTIONS)
def test_each_validation_input_participates_in_the_full_key(field):
    schema._canonical_create_tokens(SQL, **OPTIONS)
    changed = {**OPTIONS, field: "another_name"}
    for _ in range(2):
        with pytest.raises(schema.CatalogProdSchemaError, match="another"):
            schema._canonical_create_tokens(SQL, **changed)
    info = schema._cached_canonical_create_tokens.cache_info()
    assert (info.hits, info.misses, info.currsize) == (0, 3, 1)


@pytest.mark.parametrize(
    "changed,equivalent",
    [
        (SQL + " ", True),
        (SQL.replace("11111111", "22222222"), True),
        (SQL.replace("value UInt64", "value String"), False),
        (SQL.replace("ORDER BY value", "ORDER BY tuple()"), False),
    ],
)
def test_full_sql_including_header_uuid_is_the_key(changed, equivalent):
    original = schema._canonical_create_tokens(SQL, **OPTIONS)
    actual = schema._canonical_create_tokens(changed, **OPTIONS)
    assert (actual == original) is equivalent
    assert actual == schema._cached_canonical_create_tokens.__wrapped__(
        changed, **OPTIONS
    )
    info = schema._cached_canonical_create_tokens.cache_info()
    assert (info.hits, info.misses, info.currsize) == (0, 2, 2)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "CREATE TABLE",
        SQL + "; SELECT 1",
        SQL + " '",
        SQL + " `",
        SQL.replace("catalog_test.", "wrong_database."),
        SQL.replace("UUID '11111111-1111-1111-1111-111111111111'", "UUID"),
    ],
)
def test_parser_exceptions_are_not_cached(sql, monkeypatch):
    tokenize = Mock(wraps=schema._create_query_tokens)
    monkeypatch.setattr(schema, "_create_query_tokens", tokenize)
    for _ in range(2):
        with pytest.raises(schema.CatalogProdSchemaError):
            schema._canonical_create_tokens(sql, **OPTIONS)
    assert tokenize.call_count == 2
    info = schema._cached_canonical_create_tokens.cache_info()
    assert (info.hits, info.misses, info.currsize) == (0, 2, 0)


def test_entry_bound_and_least_recently_used_eviction():
    cache = schema._cached_canonical_create_tokens
    assert cache.cache_info().maxsize == 64
    variants = [SQL + f" SETTINGS index_granularity = {i}" for i in range(65)]
    for sql in variants[:64]:
        schema._canonical_create_tokens(sql, **OPTIONS)
    schema._canonical_create_tokens(variants[0], **OPTIONS)  # Keep the oldest key.
    schema._canonical_create_tokens(variants[64], **OPTIONS)
    schema._canonical_create_tokens(variants[0], **OPTIONS)
    info = cache.cache_info()
    assert (info.hits, info.misses, info.currsize) == (2, 65, 64)
    schema._canonical_create_tokens(variants[1], **OPTIONS)  # Evicted, reparse.
    assert cache.cache_info().misses == 66
    assert cache.cache_info().currsize == 64


def test_size_boundary_bypasses_cache_without_changing_acceptance(monkeypatch):
    expected = schema._canonical_create_tokens(SQL, **OPTIONS)
    schema._cached_canonical_create_tokens.cache_clear()
    tokenize = Mock(wraps=schema._create_query_tokens)
    monkeypatch.setattr(schema, "_create_query_tokens", tokenize)
    padding = (
        schema._CREATE_TOKEN_CACHE_MAX_CHARS
        - len(SQL)
        - sum(map(len, OPTIONS.values()))
    )
    bounded = SQL + " " * padding
    for _ in range(2):
        assert schema._canonical_create_tokens(bounded, **OPTIONS) == expected
    assert tokenize.call_count == 1
    before = schema._cached_canonical_create_tokens.cache_info()
    for _ in range(2):
        assert schema._canonical_create_tokens(bounded + " ", **OPTIONS) == expected
    assert tokenize.call_count == 3
    assert schema._cached_canonical_create_tokens.cache_info() == before


@pytest.mark.parametrize("count", [1, 3])
def test_warm_parser_still_performs_all_fresh_inventory_brackets(tmp_path, count):
    probe, admission, check = lane(tmp_path, count)
    check()
    before = schema._cached_canonical_create_tokens.cache_info()
    probe.calls.clear()
    check()
    assert schema._cached_canonical_create_tokens.cache_info().hits > before.hits
    inventory_sql = (
        write_admission._STANDALONE_INVENTORY_SQL
        if admission.family == "standalone"
        else write_admission._INVENTORY_SQL
    )
    for member in admission.members:
        assert [
            kind
            for kind, name, sql, _ in probe.calls
            if name == member.name and sql == inventory_sql
        ] == (
            ["native", "http", "native"]
            if count == 1
            else ["native", "http", "native", "http"]
        )


@pytest.mark.parametrize("count", [1, 3])
@pytest.mark.parametrize("mutation", ["ddl", "uuid"])
def test_changed_live_ddl_or_uuid_still_rejects_after_cache_hit(
    tmp_path, count, mutation
):
    probe, _, check = lane(tmp_path, count)
    check()
    assert schema._cached_canonical_create_tokens.cache_info().hits > 0
    row = probe.rows["replica1"][0]
    if mutation == "uuid":
        row["uuid"] = str(UUID(int=999))
    else:
        original = row["create_table_query"]
        row["create_table_query"] = original.replace(
            "index_granularity = 8192", "index_granularity = 4096"
        )
        assert row["create_table_query"] != original
        row["create_sha256"] = hashlib.sha256(
            row["create_table_query"].encode()
        ).hexdigest()
    with pytest.raises(ValueError):
        check()


@pytest.mark.parametrize("count", [1, 3])
def test_pinned_source_file_is_read_and_hashed_again_with_warm_cache(
    tmp_path, monkeypatch, count
):
    _, _, check = lane(tmp_path, count)
    check()
    assert schema._cached_canonical_create_tokens.cache_info().hits > 0
    source = schema._SCHEMA_DIR / schema._PINNED_MIGRATIONS[0].filename
    read_bytes = Path.read_bytes
    reads = []

    def drift(path):
        raw = read_bytes(path)
        if path == source:
            reads.append(path)
            return raw + b"\n-- changed after successful admission\n"
        return raw

    monkeypatch.setattr(Path, "read_bytes", drift)
    for _ in range(2):
        with pytest.raises(schema.CatalogProdSchemaError, match="migration drift"):
            check()
    assert reads == [source, source]
