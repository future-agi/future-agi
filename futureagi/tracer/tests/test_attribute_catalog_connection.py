"""Retained SQL safety boundaries without obsolete snapshot credentials."""

import pytest

from tracer.services.clickhouse.v2.attribute_catalog_connection import (
    _bounded_query_settings,
    _validate_catalog_query,
)

DATABASE = "observed_test"
TABLES = frozenset({"observed_attribute_keys", "observed_attribute_values"})
KEYS = f"{DATABASE}.observed_attribute_keys"
VALUES = f"{DATABASE}.observed_attribute_values"


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT * FROM {KEYS}",
        f"SELECT * FROM `{DATABASE}`.`observed_attribute_keys`",
        f"WITH keys AS (SELECT attribute_key FROM {KEYS}) SELECT * FROM keys",
        f"SELECT * FROM {KEYS} k JOIN {VALUES} v ON k.attribute_key=v.attribute_key",
        f"SELECT * FROM (SELECT * FROM {KEYS}) LIMIT 1",
        f"SELECT 'DROP TABLE spans' AS text FROM {KEYS} /* JOIN spans */",
    ],
)
def test_catalog_selects_allow_only_explicit_index_tables(sql):
    _validate_catalog_query(sql, database=DATABASE, allowed_tables=TABLES)


@pytest.mark.parametrize(
    "sql",
    [
        f"INSERT INTO {KEYS} VALUES (1)",
        f"SELECT * FROM {DATABASE}.spans",
        "SELECT * FROM spans",
        "SELECT 1",
        "SHOW TABLES",
        f"SELECT * FROM {KEYS}, {DATABASE}.spans",
        f"SELECT * FROM {KEYS} JOIN other.observed_attribute_values USING attribute_key",
        f"WITH hidden AS (SELECT * FROM {DATABASE}.spans) SELECT * FROM {KEYS}, hidden",
        f"SELECT * FROM {KEYS} UNION ALL SELECT * FROM remote('host', 'spans')",
        f"SELECT * FROM {KEYS}; DROP TABLE spans",
        f"SELECT * FROM {KEYS} SETTINGS readonly=0",
        f"SELECT * FROM {DATABASE}.observed_attribute_keys()",
    ],
)
def test_fact_access_mutations_and_table_functions_are_rejected(sql):
    with pytest.raises((RuntimeError, ValueError)):
        _validate_catalog_query(sql, database=DATABASE, allowed_tables=TABLES)


@pytest.mark.parametrize(
    "suffix", [" /*", " WHERE key='missing", ' WHERE key="missing']
)
def test_unterminated_sql_is_rejected(suffix):
    with pytest.raises((RuntimeError, ValueError)):
        _validate_catalog_query(
            f"SELECT * FROM {KEYS}{suffix}", database=DATABASE, allowed_tables=TABLES
        )


def test_query_bounds_cannot_disable_readonly_or_silently_truncate():
    settings = _bounded_query_settings(
        {
            "readonly": 0,
            "max_execution_time": 99,
            "read_overflow_mode": "break",
            "max_result_rows": 25,
        },
        timeout_ms=1500,
    )
    assert settings == {
        "readonly": 1,
        "max_execution_time": 1.5,
        "read_overflow_mode": "throw",
        "max_result_rows": 25,
        "result_overflow_mode": "throw",
        "timeout_overflow_mode": "throw",
    }
    with pytest.raises(ValueError, match="unsupported"):
        _bounded_query_settings({"allow_ddl": 1}, timeout_ms=1500)
