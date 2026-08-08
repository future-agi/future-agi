"""Strict mode turns the CH25 schema-apply warning path into a hard failure.

Uses the top-level `conftest` import the same way ~10 existing modules do
(root conftest owns the top-level name under prepend import mode).
"""

import pytest
from clickhouse_connect.driver.exceptions import ClickHouseError

from conftest import (
    UnsafeClickHouseTestTarget,
    _apply_ch25_schema_for_tests,
    _require_safe_ch25_test_target,
)


def test_strict_mode_raises_on_unreachable_clickhouse(monkeypatch):
    monkeypatch.setenv("FI_CH25_SCHEMA_APPLY_STRICT", "1")
    monkeypatch.delenv("FI_SKIP_CH25_SCHEMA_APPLY", raising=False)
    # Point at a port nothing listens on so the apply must fail.
    monkeypatch.setenv("CH25_HOST", "127.0.0.1")
    monkeypatch.setenv("CH25_HTTP_PORT", "19999")
    with pytest.raises(ClickHouseError):
        _apply_ch25_schema_for_tests()


def test_default_mode_still_swallows(monkeypatch, capsys):
    monkeypatch.delenv("FI_CH25_SCHEMA_APPLY_STRICT", raising=False)
    monkeypatch.delenv("FI_SKIP_CH25_SCHEMA_APPLY", raising=False)
    monkeypatch.setenv("CH25_HOST", "127.0.0.1")
    monkeypatch.setenv("CH25_HTTP_PORT", "19999")
    _apply_ch25_schema_for_tests()  # must not raise
    assert "CH25 schema apply" in capsys.readouterr().err


@pytest.mark.parametrize(
    "host",
    ("localhost", "LOCALHOST.", "127.0.0.1", "127.42.0.9", "::1", "[::1]"),
)
def test_mutation_guard_accepts_literal_loopback_without_opt_in(monkeypatch, host):
    monkeypatch.delenv("FI_ALLOW_NONLOCAL_CH25_TEST_MUTATIONS", raising=False)

    _require_safe_ch25_test_target(host=host, database="test_tfc")


def test_mutation_guard_rejects_nonlocal_target_without_exact_opt_in(monkeypatch):
    monkeypatch.setenv("FI_ALLOW_NONLOCAL_CH25_TEST_MUTATIONS", "1")

    with pytest.raises(UnsafeClickHouseTestTarget, match="non-loopback"):
        _require_safe_ch25_test_target(host="clickhouse", database="test_tfc")


def test_mutation_guard_rejects_nonlocal_production_database(monkeypatch):
    monkeypatch.setenv("FI_ALLOW_NONLOCAL_CH25_TEST_MUTATIONS", "true")

    with pytest.raises(UnsafeClickHouseTestTarget, match=r"test_\*"):
        _require_safe_ch25_test_target(host="test-clickhouse", database="futureagi")


def test_mutation_guard_accepts_opted_in_test_sidecar(monkeypatch):
    monkeypatch.setenv("FI_ALLOW_NONLOCAL_CH25_TEST_MUTATIONS", "TRUE")

    _require_safe_ch25_test_target(host="test-clickhouse", database="test_tfc")


@pytest.mark.parametrize("host,database", (("", "test_tfc"), ("localhost", "")))
def test_mutation_guard_rejects_blank_target(monkeypatch, host, database):
    monkeypatch.delenv("FI_ALLOW_NONLOCAL_CH25_TEST_MUTATIONS", raising=False)

    with pytest.raises(UnsafeClickHouseTestTarget):
        _require_safe_ch25_test_target(host=host, database=database)


def test_schema_apply_guard_is_not_swallowed_in_non_strict_mode(monkeypatch):
    monkeypatch.delenv("FI_CH25_SCHEMA_APPLY_STRICT", raising=False)
    monkeypatch.delenv("FI_SKIP_CH25_SCHEMA_APPLY", raising=False)
    monkeypatch.delenv("FI_ALLOW_NONLOCAL_CH25_TEST_MUTATIONS", raising=False)
    monkeypatch.setenv("CH25_HOST", "production-clickhouse.example")
    monkeypatch.setenv("CH25_DATABASE", "futureagi")

    with pytest.raises(UnsafeClickHouseTestTarget, match="non-loopback"):
        _apply_ch25_schema_for_tests()
