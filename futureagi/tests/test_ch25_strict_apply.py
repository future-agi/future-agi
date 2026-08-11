"""Strict mode turns the CH25 schema-apply warning path into a hard failure.

Uses the top-level `conftest` import the same way ~10 existing modules do
(root conftest owns the top-level name under prepend import mode).
"""

import pytest
from clickhouse_connect.driver.exceptions import ClickHouseError

from conftest import _apply_ch25_schema_for_tests


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
