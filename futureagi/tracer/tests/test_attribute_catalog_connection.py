"""Fail-closed tests for the dedicated public catalog read connection."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tracer.services.clickhouse.v2 import attribute_catalog_connection as connection
from tracer.services.clickhouse.v2 import attribute_catalog_cutover as cutover
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    AttributeCatalogReader,
)

CATALOG_DATABASE = "property_catalog_dev"
CATALOG_PASSWORD = "catalog-password-must-stay-secret"
CATALOG_QUERY = f"""
SELECT attribute_key
FROM {CATALOG_DATABASE}.span_attribute_key_catalog
LIMIT 1
"""


def _settings(**overrides: Any) -> SimpleNamespace:
    values = {
        "SPAN_ATTRIBUTE_CATALOG_CH_HOST": "catalog-ch.internal",
        "SPAN_ATTRIBUTE_CATALOG_CH_PORT": 9440,
        "SPAN_ATTRIBUTE_CATALOG_CH_DATABASE": CATALOG_DATABASE,
        "SPAN_ATTRIBUTE_CATALOG_CH_USER": "catalog_reader",
        "SPAN_ATTRIBUTE_CATALOG_CH_PASSWORD": CATALOG_PASSWORD,
        "SPAN_ATTRIBUTE_CATALOG_DATABASE": CATALOG_DATABASE,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def execute_read_with_progress(self, query, params, *, timeout_ms, settings):
        self.calls.append(
            {
                "query": query,
                "params": params,
                "timeout_ms": timeout_ms,
                "settings": settings,
            }
        )
        return (
            [("catalog.key",)],
            [("attribute_key", "String")],
            0.25,
            1,
            32,
        )

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_dedicated_client():
    connection.reset_attribute_catalog_read_client()
    yield
    connection.reset_attribute_catalog_read_client()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("setting_name", "missing_value"),
    [
        ("SPAN_ATTRIBUTE_CATALOG_CH_HOST", ""),
        ("SPAN_ATTRIBUTE_CATALOG_CH_PORT", 0),
        ("SPAN_ATTRIBUTE_CATALOG_CH_DATABASE", ""),
        ("SPAN_ATTRIBUTE_CATALOG_CH_USER", ""),
        ("SPAN_ATTRIBUTE_CATALOG_CH_PASSWORD", ""),
    ],
)
def test_dedicated_config_rejects_every_missing_field(setting_name, missing_value):
    with pytest.raises(ValueError):
        connection.AttributeCatalogConnectionConfig.from_settings(
            _settings(**{setting_name: missing_value})
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "database",
    ["default", "system", "information_schema", "futureagi", "catalog_prod", "x-y"],
)
def test_dedicated_config_requires_an_isolated_dev_database(database):
    with pytest.raises(ValueError, match="isolated development"):
        connection.AttributeCatalogConnectionConfig.from_settings(
            _settings(
                SPAN_ATTRIBUTE_CATALOG_CH_DATABASE=database,
                SPAN_ATTRIBUTE_CATALOG_DATABASE=database,
            )
        )


@pytest.mark.unit
def test_dedicated_config_requires_qualifier_and_connection_database_match():
    with pytest.raises(ValueError, match="databases must match"):
        connection.AttributeCatalogConnectionConfig.from_settings(
            _settings(SPAN_ATTRIBUTE_CATALOG_DATABASE="different_catalog_dev")
        )


@pytest.mark.unit
def test_dedicated_config_rejects_the_source_application_identity():
    source = _settings()
    source.CLICKHOUSE_V2 = {"CH25_USER": "catalog_reader"}
    source.CLICKHOUSE = {"CH_USERNAME": "legacy_app"}

    with pytest.raises(ValueError, match="dedicated identity"):
        connection.AttributeCatalogConnectionConfig.from_settings(source)


@pytest.mark.unit
def test_password_is_redacted_and_never_logged(caplog):
    config = connection.AttributeCatalogConnectionConfig.from_settings(_settings())

    assert CATALOG_PASSWORD not in repr(config)
    assert "password=" not in repr(config)

    class ExplodingClient(_FakeClient):
        def execute_read_with_progress(self, *args, **kwargs):
            raise RuntimeError("sanitized connection failure")

    executor = connection.AttributeCatalogReadExecutor(
        config=config,
        client_factory=lambda _config: ExplodingClient(),
    )
    with pytest.raises(RuntimeError, match="sanitized connection failure"):
        executor.execute(CATALOG_QUERY, {}, timeout_ms=2_000, settings={})

    assert CATALOG_PASSWORD not in caplog.text


@pytest.mark.unit
def test_dedicated_client_uses_no_source_config_and_is_server_readonly(
    monkeypatch, settings
):
    source_secret = "source-password-must-not-be-used"
    settings.CLICKHOUSE = {
        "CH_HOST": "source-v1.internal",
        "CH_PORT": 9000,
        "CH_USERNAME": "source-v1",
        "CH_PASSWORD": source_secret,
        "CH_DATABASE": "futureagi",
    }
    settings.CLICKHOUSE_V2 = {
        "CH25_HOST": "source-v2.internal",
        "CH25_TCP_PORT": 9001,
        "CH25_USER": "source-v2",
        "CH25_PASSWORD": source_secret,
        "CH25_DATABASE": "futureagi",
    }
    captured: dict[str, Any] = {}

    class ConstructedClient(_FakeClient):
        def __init__(self, **kwargs):
            super().__init__()
            captured.update(kwargs)

    monkeypatch.setattr(connection, "ClickHouseClient", ConstructedClient)
    config = connection.AttributeCatalogConnectionConfig.from_settings(_settings())

    client = connection.get_attribute_catalog_read_client(config)

    assert isinstance(client, ConstructedClient)
    assert captured == {
        "host": "catalog-ch.internal",
        "port": 9440,
        "user": "catalog_reader",
        "password": CATALOG_PASSWORD,
        "database": CATALOG_DATABASE,
        "server_enforced_readonly": True,
        "connect_timeout": 2.0,
        "send_timeout": 2.0,
        "receive_timeout": 2.0,
        "pool_size": connection.CATALOG_READ_POOL_SIZE,
    }
    assert source_secret not in repr(captured)


@pytest.mark.unit
def test_global_dedicated_client_reuses_resets_and_rotates_by_exact_config(
    monkeypatch,
):
    constructed: list[_FakeClient] = []

    class ConstructedClient(_FakeClient):
        def __init__(self, **_kwargs):
            super().__init__()
            constructed.append(self)

    monkeypatch.setattr(connection, "ClickHouseClient", ConstructedClient)
    first_config = connection.AttributeCatalogConnectionConfig.from_settings(
        _settings()
    )
    second_config = connection.AttributeCatalogConnectionConfig.from_settings(
        _settings(SPAN_ATTRIBUTE_CATALOG_CH_HOST="rotated-catalog-ch.internal")
    )

    first = connection.get_attribute_catalog_read_client(first_config)
    assert connection.get_attribute_catalog_read_client(first_config) is first
    second = connection.get_attribute_catalog_read_client(second_config)

    assert second is not first
    assert first.closed is True
    assert len(constructed) == 2

    connection.reset_attribute_catalog_read_client()

    assert second.closed is True
    assert connection._client is None
    assert connection._client_config is None


@pytest.mark.unit
def test_executor_does_not_construct_source_v2_client(monkeypatch):
    from tracer.services.clickhouse.v2 import query_service

    monkeypatch.setattr(
        query_service,
        "get_v2_query_client",
        lambda: pytest.fail("source CH25 client must never serve catalog reads"),
    )
    fake = _FakeClient()
    config = connection.AttributeCatalogConnectionConfig.from_settings(_settings())
    executor = connection.AttributeCatalogReadExecutor(
        config=config, client_factory=lambda _config: fake
    )

    page = executor.execute(CATALOG_QUERY, {}, timeout_ms=2_000, settings={})

    assert page.data == [{"attribute_key": "catalog.key"}]
    assert len(fake.calls) == 1


@pytest.mark.unit
def test_cutover_constructs_only_the_dedicated_executor(settings):
    dedicated = _settings()
    for name, value in vars(dedicated).items():
        setattr(settings, name, value)
    settings.CLICKHOUSE_V2 = {"CH25_USER": "source_v2"}
    settings.CLICKHOUSE = {"CH_USERNAME": "source_v1"}

    executor = cutover._new_executor()

    assert isinstance(executor, connection.AttributeCatalogReadExecutor)
    assert executor._client is None


@pytest.mark.unit
def test_executor_and_cutover_client_are_lazy(monkeypatch, settings):
    config = connection.AttributeCatalogConnectionConfig.from_settings(_settings())
    constructed = False

    def construct(_config):
        nonlocal constructed
        constructed = True
        return _FakeClient()

    connection.AttributeCatalogReadExecutor(config=config, client_factory=construct)
    assert constructed is False

    settings.SPAN_ATTRIBUTE_CATALOG_READ_MODE = "off"
    monkeypatch.setattr(
        cutover,
        "_new_executor",
        lambda: pytest.fail("off mode must not construct any catalog executor"),
    )
    attempt = cutover.try_catalog_key_page(
        project_ids=("c4de3065-12b5-488c-a814-aa1c8e3f856f",),
        window_start=SimpleNamespace(),
        window_end=SimpleNamespace(),
        page_size=10,
        search=None,
        after=None,
        request_deadline=None,
    )
    assert attempt.attempted is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "query",
    [
        f"INSERT INTO {CATALOG_DATABASE}.span_attribute_key_catalog VALUES (1)",
        f"SELECT * FROM {CATALOG_DATABASE}.spans",
        "SELECT * FROM spans",
        "SHOW TABLES",
        (
            f"WITH source_rows AS (SELECT * FROM {CATALOG_DATABASE}.spans) "
            "SELECT * FROM source_rows"
        ),
        (
            f"SELECT * FROM {CATALOG_DATABASE}.span_attribute_key_catalog, "
            f"{CATALOG_DATABASE}.spans"
        ),
    ],
)
def test_executor_allows_only_select_with_queries_over_catalog_tables(query):
    config = connection.AttributeCatalogConnectionConfig.from_settings(_settings())
    fake = _FakeClient()
    executor = connection.AttributeCatalogReadExecutor(
        config=config, client_factory=lambda _config: fake
    )

    with pytest.raises(RuntimeError):
        executor.execute(query, {}, timeout_ms=2_000, settings={})

    assert fake.calls == []


@pytest.mark.unit
def test_all_reader_queries_pass_the_dedicated_table_allowlist():
    reader = AttributeCatalogReader(
        SimpleNamespace(),
        project_ids=("c4de3065-12b5-488c-a814-aa1c8e3f856f",),
        catalog_epoch=7,
        window_start=__import__("datetime").datetime(
            2026, 8, 1, tzinfo=__import__("datetime").UTC
        ),
        window_end=__import__("datetime").datetime(
            2026, 8, 2, tzinfo=__import__("datetime").UTC
        ),
        catalog_database=CATALOG_DATABASE,
    )

    for query in (
        reader._activation_sql,
        reader._source_stream_sql,
        reader._checkpoint_sql,
        reader._key_page_sql,
        reader._value_page_sql,
    ):
        connection._validate_catalog_query(query, database=CATALOG_DATABASE)


@pytest.mark.unit
def test_executor_shares_one_two_second_wall_and_forces_readonly_bounds():
    now = [100.0]
    fake = _FakeClient()
    config = connection.AttributeCatalogConnectionConfig.from_settings(_settings())
    executor = connection.AttributeCatalogReadExecutor(
        config=config,
        client_factory=lambda _config: fake,
        clock=lambda: now[0],
    )

    executor.execute(CATALOG_QUERY, {}, timeout_ms=99_000, settings={})
    now[0] += 1.5
    executor.execute(
        CATALOG_QUERY,
        {},
        timeout_ms=99_000,
        settings={"readonly": 0, "max_execution_time": 99},
    )
    now[0] += 0.501
    with pytest.raises(TimeoutError, match="deadline exhausted"):
        executor.execute(CATALOG_QUERY, {}, timeout_ms=99_000, settings={})

    assert len(fake.calls) == 2
    assert 1 <= fake.calls[0]["timeout_ms"] <= 2_000
    assert 1 <= fake.calls[1]["timeout_ms"] <= 500
    for call in fake.calls:
        assert call["settings"]["readonly"] == 1
        assert call["settings"]["max_execution_time"] == pytest.approx(
            call["timeout_ms"] / 1_000
        )
        assert call["settings"]["read_overflow_mode"] == "throw"
        assert call["settings"]["result_overflow_mode"] == "throw"


@pytest.mark.unit
def test_read_mode_settings_import_fails_when_dedicated_config_is_missing():
    env, futureagi_root = _complete_read_mode_import_env()
    env.pop("SPAN_ATTRIBUTE_CATALOG_CH_PASSWORD")

    completed = _import_settings(env, futureagi_root)

    assert completed.returncode != 0
    assert "complete dedicated ClickHouse connection settings" in completed.stderr
    assert CATALOG_PASSWORD not in completed.stdout + completed.stderr


def _complete_read_mode_import_env():
    futureagi_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    for name in tuple(env):
        if name.startswith("SPAN_ATTRIBUTE_CATALOG_"):
            env.pop(name)
    env.update(
        {
            "PYTHONPATH": str(futureagi_root),
            "ENV_TYPE": "dev",
            "SECRET_KEY": "non-default-test-secret",
            "SPAN_ATTRIBUTE_CATALOG_READ_MODE": "read",
            "SPAN_ATTRIBUTE_CATALOG_DEV_READ_ACK": (
                "I_ACKNOWLEDGE_DEV_ONLY_ATTRIBUTE_CATALOG_READS"
            ),
            "SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED": "true",
            "SPAN_ATTRIBUTE_CATALOG_HANDOFF_START": "2026-08-01T00:00:00Z",
            "SPAN_ATTRIBUTE_CATALOG_HANDOFF_END": "2026-08-02T00:00:00Z",
            "SPAN_ATTRIBUTE_CATALOG_EPOCH": "7",
            "SPAN_ATTRIBUTE_CATALOG_DATABASE": CATALOG_DATABASE,
            "SPAN_ATTRIBUTE_CATALOG_CH_HOST": "catalog-ch.internal",
            "SPAN_ATTRIBUTE_CATALOG_CH_PORT": "9440",
            "SPAN_ATTRIBUTE_CATALOG_CH_DATABASE": CATALOG_DATABASE,
            "SPAN_ATTRIBUTE_CATALOG_CH_USER": "catalog_reader",
            "SPAN_ATTRIBUTE_CATALOG_CH_PASSWORD": CATALOG_PASSWORD,
        }
    )
    return env, futureagi_root


def _import_settings(env, futureagi_root):
    return subprocess.run(
        [sys.executable, "-c", "import tfc.settings.settings"],
        cwd=futureagi_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "error_text"),
    [
        (
            {"SPAN_ATTRIBUTE_CATALOG_EPOCH": "0"},
            "require an epoch from 1 to 65535",
        ),
        (
            {"SPAN_ATTRIBUTE_CATALOG_DEV_SNAPSHOT_ENABLED": "false"},
            "require the pinned DEV snapshot",
        ),
        (
            {"SPAN_ATTRIBUTE_CATALOG_HANDOFF_START": "2026-08-01T00:30:00Z"},
            "require aligned increasing UTC handoff bounds",
        ),
        (
            {"SPAN_ATTRIBUTE_CATALOG_HANDOFF_END": "2026-07-31T00:00:00Z"},
            "require aligned increasing UTC handoff bounds",
        ),
        (
            {"SPAN_ATTRIBUTE_CATALOG_CH_DATABASE": "different_catalog_dev"},
            "require the same isolated development catalog database",
        ),
        (
            {
                "SPAN_ATTRIBUTE_CATALOG_DATABASE": "futureagi",
                "SPAN_ATTRIBUTE_CATALOG_CH_DATABASE": "futureagi",
            },
            "require the same isolated development catalog database",
        ),
        (
            {"CH25_USER": "catalog_reader"},
            "require a dedicated ClickHouse read identity",
        ),
    ],
)
def test_read_mode_settings_import_fails_closed(overrides, error_text):
    env, futureagi_root = _complete_read_mode_import_env()
    env.update(overrides)

    completed = _import_settings(env, futureagi_root)

    assert completed.returncode != 0
    assert error_text in completed.stderr
    assert CATALOG_PASSWORD not in completed.stdout + completed.stderr


@pytest.mark.unit
def test_complete_read_mode_settings_import_succeeds():
    env, futureagi_root = _complete_read_mode_import_env()

    completed = _import_settings(env, futureagi_root)

    assert completed.returncode == 0, completed.stderr
    assert CATALOG_PASSWORD not in completed.stdout + completed.stderr


@pytest.mark.unit
def test_staging_dev_cloud_read_mode_settings_import_succeeds():
    env, futureagi_root = _complete_read_mode_import_env()
    env.update({"ENV_TYPE": "staging", "CLOUD_DEPLOYMENT": "DEV"})

    completed = _import_settings(env, futureagi_root)

    assert completed.returncode == 0, completed.stderr
    assert CATALOG_PASSWORD not in completed.stdout + completed.stderr


@pytest.mark.unit
@pytest.mark.parametrize(
    ("environment", "cloud_deployment"),
    (("staging", ""), ("staging", "US"), ("prod", "DEV")),
)
def test_read_mode_settings_reject_non_dev_deployment_combinations(
    environment,
    cloud_deployment,
):
    env, futureagi_root = _complete_read_mode_import_env()
    env.update({"ENV_TYPE": environment, "CLOUD_DEPLOYMENT": cloud_deployment})

    completed = _import_settings(env, futureagi_root)

    assert completed.returncode != 0
    assert "requires acknowledged DEV read mode" in completed.stderr
    assert CATALOG_PASSWORD not in completed.stdout + completed.stderr
