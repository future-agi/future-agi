from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tfc.settings.settings import (
    PROPERTY_CATALOG_DEV_READ_ACKNOWLEDGEMENT,
    PROPERTY_CATALOG_PROD_READ_ACKNOWLEDGEMENT,
    property_catalog_read_workspace_allowlist,
    property_catalog_reads_all_production_workspaces,
)
from tracer.services.clickhouse.v2.attribute_catalog_connection import (
    _validate_catalog_query,
)
from tracer.services.clickhouse.v2.property_catalog.connection import (
    PROPERTY_CATALOG_TABLES,
    PropertyCatalogConnectionConfig,
    PropertyCatalogReadExecutor,
    get_property_catalog_read_client,
    reset_property_catalog_read_client,
    validate_property_catalog_database,
    validate_property_catalog_read_admission,
)
from tracer.services.clickhouse.v2.property_catalog.database import (
    configured_production_property_catalog_database,
)
from tracer.services.clickhouse.v2.property_catalog.publisher import (
    PropertyCatalogPublishError,
    require_dev_catalog_database,
    require_prod_catalog_database,
)
from tracer.services.clickhouse.v2.property_catalog.reader import PropertyCatalogReader

CONFIG = PropertyCatalogConnectionConfig(
    host="catalog.internal",
    port=9440,
    database="property_catalog_dev_clean",
    user="property_catalog_reader",
    password="not-logged",
)


def _read_settings(**overrides):
    values = {
        "PROPERTY_CATALOG_READ_MODE": "read",
        "ENV_TYPE": "development",
        "CLOUD_DEPLOYMENT": "DEV",
        "PROPERTY_CATALOG_DEV_READ_ACK": (PROPERTY_CATALOG_DEV_READ_ACKNOWLEDGEMENT),
        "PROPERTY_CATALOG_PROD_READ_ACK": "",
        "PROPERTY_CATALOG_DATABASE": "property_catalog_dev_clean",
        "PROPERTY_CATALOG_CH_HOST": "catalog.internal",
        "PROPERTY_CATALOG_CH_PORT": 9440,
        "PROPERTY_CATALOG_CH_USER": "property_catalog_reader",
        "PROPERTY_CATALOG_CH_PASSWORD": "not-logged",
        "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": ("workspace-1",),
        "PROPERTY_CATALOG_PROD_WORKSPACE_ALLOWLIST": (),
        "PROPERTY_CATALOG_PROD_WORKSPACE_SCOPE_MODE": "allowlist",
        "CLICKHOUSE_V2": {"CH25_USER": "source_v2"},
        "CLICKHOUSE": {"CH_USERNAME": "source_v1"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _prod_settings(**overrides):
    values = {
        "ENV_TYPE": "production",
        "CLOUD_DEPLOYMENT": "US",
        "PROPERTY_CATALOG_DEV_READ_ACK": "",
        "PROPERTY_CATALOG_PROD_READ_ACK": (PROPERTY_CATALOG_PROD_READ_ACKNOWLEDGEMENT),
        "PROPERTY_CATALOG_DATABASE": "property_catalog",
        "PROPERTY_CATALOG_CH_DATABASE": "property_catalog",
        "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": (),
        "PROPERTY_CATALOG_PROD_WORKSPACE_ALLOWLIST": ("workspace-1",),
    }
    values.update(overrides)
    return _read_settings(**values)


@pytest.mark.parametrize("environment_type", ["prod", "production"])
def test_property_catalog_connection_accepts_supported_production_env_aliases(
    environment_type,
):
    config = PropertyCatalogConnectionConfig.from_settings(
        _prod_settings(ENV_TYPE=environment_type)
    )

    assert config.database == "property_catalog"


class FakeClient:
    def __init__(self):
        self.calls = []

    def execute_read(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params, timeout_ms, settings))
        return [("ok",)], [("status", "String")], None


def test_property_catalog_client_keeps_server_readonly_and_sends_bounded_settings(
    monkeypatch,
):
    from tracer.services.clickhouse.v2.property_catalog import connection

    client_factory = Mock()
    monkeypatch.setattr(connection, "ClickHouseClient", client_factory)
    reset_property_catalog_read_client()
    try:
        get_property_catalog_read_client(CONFIG)
    finally:
        reset_property_catalog_read_client()

    assert client_factory.call_args.kwargs["server_enforced_readonly"] is True
    assert (
        client_factory.call_args.kwargs["allow_query_settings_with_server_readonly"]
        is True
    )


def test_property_catalog_table_allowlist_is_exact():
    assert PROPERTY_CATALOG_TABLES == {
        "property_definition_catalog",
        "span_attribute_value_catalog",
        "property_catalog_checkpoints",
        "property_catalog_activations",
        "property_catalog_activation_control_events",
        "property_catalog_deliveries",
        "property_catalog_source_streams",
    }


def test_property_catalog_connection_requires_isolated_dev_identity():
    CONFIG.validate(source_users={"application_reader"})

    with pytest.raises(ValueError):
        PropertyCatalogConnectionConfig(
            host="catalog.internal",
            port=9440,
            database="futureagi",
            user="property_catalog_reader",
            password="secret",
        ).validate(source_users=set())

    with pytest.raises(ValueError):
        CONFIG.validate(
            source_users={"property_catalog_reader"},
        )

    for unsafe_database in (
        "default",
        "system",
        "PROPERTY_CATALOG_DEV_UPPER",
        "property_catalog_dev_bad-name",
    ):
        with pytest.raises(ValueError):
            PropertyCatalogConnectionConfig(
                host="catalog.internal",
                port=9440,
                database=unsafe_database,
                user="property_catalog_reader",
                password="secret",
            ).validate(source_users=set())


def test_property_catalog_connection_preserves_acknowledged_dev_admission():
    assert PropertyCatalogConnectionConfig.from_settings(_read_settings()) == CONFIG


@pytest.mark.parametrize("read_mode", ["read", "shadow"])
@pytest.mark.parametrize("environment_type", ["prod", "production"])
def test_property_catalog_connection_admits_bounded_production_reads(
    read_mode, environment_type
):
    config = PropertyCatalogConnectionConfig.from_settings(
        _prod_settings(
            ENV_TYPE=environment_type,
            PROPERTY_CATALOG_READ_MODE=read_mode,
        )
    )

    assert config.database == "property_catalog"
    config.validate(
        source_users={"source_v1", "source_v2"},
        deployment="prod",
    )


def test_property_catalog_connection_accepts_maximum_production_allowlist():
    source = _prod_settings(
        PROPERTY_CATALOG_PROD_WORKSPACE_ALLOWLIST=tuple(
            f"workspace-{index}" for index in range(256)
        )
    )
    assert PropertyCatalogConnectionConfig.from_settings(source).database == (
        "property_catalog"
    )


def test_property_catalog_connection_accepts_global_production_workspace_scope():
    source = _prod_settings(
        PROPERTY_CATALOG_PROD_WORKSPACE_SCOPE_MODE="all",
        PROPERTY_CATALOG_PROD_WORKSPACE_ALLOWLIST=(),
        PROPERTY_CATALOG_READ_DEPLOYMENT="prod",
    )

    assert PropertyCatalogConnectionConfig.from_settings(source).database == (
        "property_catalog"
    )
    assert property_catalog_reads_all_production_workspaces(source) is True


@pytest.mark.parametrize(
    "overrides",
    (
        {
            "PROPERTY_CATALOG_PROD_WORKSPACE_SCOPE_MODE": "all",
            "PROPERTY_CATALOG_PROD_WORKSPACE_ALLOWLIST": ("workspace-1",),
        },
        {"PROPERTY_CATALOG_PROD_WORKSPACE_SCOPE_MODE": "invalid"},
    ),
)
def test_property_catalog_connection_rejects_invalid_global_production_scope(
    overrides,
):
    with pytest.raises(ValueError):
        PropertyCatalogConnectionConfig.from_settings(_prod_settings(**overrides))


def test_property_catalog_connection_rejects_global_scope_in_dev():
    with pytest.raises(ValueError, match="production-only"):
        PropertyCatalogConnectionConfig.from_settings(
            _read_settings(PROPERTY_CATALOG_PROD_WORKSPACE_SCOPE_MODE="all")
        )


def test_property_catalog_read_workspace_allowlist_is_deployment_bound():
    dev = _read_settings(PROPERTY_CATALOG_READ_DEPLOYMENT="dev")
    prod = _prod_settings(PROPERTY_CATALOG_READ_DEPLOYMENT="prod")

    assert property_catalog_read_workspace_allowlist(dev) == ("workspace-1",)
    assert property_catalog_read_workspace_allowlist(prod) == ("workspace-1",)
    assert (
        property_catalog_read_workspace_allowlist(
            SimpleNamespace(PROPERTY_CATALOG_READ_DEPLOYMENT=None)
        )
        == ()
    )


def test_property_catalog_read_mode_off_never_builds_a_runtime_connection():
    source = _read_settings(
        PROPERTY_CATALOG_READ_MODE="off",
        PROPERTY_CATALOG_DEV_READ_ACK="",
        PROPERTY_CATALOG_CH_HOST="",
        PROPERTY_CATALOG_CH_PORT=0,
        PROPERTY_CATALOG_DATABASE="",
        PROPERTY_CATALOG_CH_USER="",
        PROPERTY_CATALOG_CH_PASSWORD="",
        PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST=(),
    )

    with pytest.raises(ValueError, match="disabled"):
        PropertyCatalogConnectionConfig.from_settings(source)

    assert (
        validate_property_catalog_read_admission(
            read_mode="off",
            environment_type="production",
            cloud_deployment="US",
            dev_acknowledgement="wrong",
            prod_acknowledgement="wrong",
            database="unsafe",
            host="",
            port=0,
            api_read_user="",
            password="",
            source_users=None,
            dev_workspace_allowlist=None,
            prod_workspace_allowlist=None,
        )
        is None
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"ENV_TYPE": "local"}, "supported DEV or production"),
        ({"ENV_TYPE": "staging", "CLOUD_DEPLOYMENT": "US"}, "supported DEV"),
        ({"CLOUD_DEPLOYMENT": "DEV"}, "supported DEV or production"),
        (
            {
                "PROPERTY_CATALOG_PROD_READ_ACK": "wrong",
            },
            "deployment-specific acknowledgement",
        ),
        (
            {
                "PROPERTY_CATALOG_DEV_READ_ACK": (
                    PROPERTY_CATALOG_DEV_READ_ACKNOWLEDGEMENT
                ),
            },
            "deployment-specific acknowledgement",
        ),
        (
            {
                "PROPERTY_CATALOG_DATABASE": "property_catalog_dev_clean",
            },
            "namespace does not match",
        ),
        (
            {
                "PROPERTY_CATALOG_DATABASE": "property_catalog_backup",
            },
            "namespace does not match",
        ),
        (
            {"PROPERTY_CATALOG_PROD_WORKSPACE_ALLOWLIST": ()},
            "allowlist must contain 1 to 256",
        ),
        (
            {
                "PROPERTY_CATALOG_DEV_WORKSPACE_ALLOWLIST": ("dev-cross-wire",),
            },
            "allowlists must be deployment-specific",
        ),
        (
            {
                "PROPERTY_CATALOG_PROD_WORKSPACE_ALLOWLIST": tuple(
                    f"workspace-{index}" for index in range(257)
                )
            },
            "allowlist must contain 1 to 256",
        ),
        (
            {"PROPERTY_CATALOG_CH_USER": "source_v2"},
            "dedicated API identity",
        ),
        (
            {"PROPERTY_CATALOG_CH_USER": "source_v1"},
            "dedicated API identity",
        ),
    ],
)
def test_property_catalog_production_admission_fails_closed(overrides, message):
    with pytest.raises(ValueError, match=message):
        PropertyCatalogConnectionConfig.from_settings(_prod_settings(**overrides))


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "PROPERTY_CATALOG_DATABASE": "property_catalog",
        },
        {
            "PROPERTY_CATALOG_DEV_READ_ACK": "",
            "PROPERTY_CATALOG_PROD_READ_ACK": (
                PROPERTY_CATALOG_PROD_READ_ACKNOWLEDGEMENT
            ),
        },
    ],
)
def test_property_catalog_dev_admission_rejects_production_cross_wiring(overrides):
    with pytest.raises(ValueError):
        PropertyCatalogConnectionConfig.from_settings(_read_settings(**overrides))


@pytest.mark.parametrize(
    ("database", "deployment"),
    [
        ("property_catalog_dev_clean", "dev"),
        ("legacy_catalog_dev_snapshot", "dev"),
        ("property_catalog", "prod"),
    ],
)
def test_property_catalog_database_validator_accepts_exact_namespaces(
    database, deployment
):
    assert (
        validate_property_catalog_database(database, deployment=deployment) == database
    )


def test_property_catalog_database_validator_accepts_configured_production_database(
    monkeypatch,
):
    database = "th7247_catalog_prod_20260823a"
    monkeypatch.setenv("PROPERTY_CATALOG_PRODUCTION_DATABASE", database)

    assert validate_property_catalog_database(database, deployment="prod") == database
    with pytest.raises(ValueError, match="namespace does not match"):
        validate_property_catalog_database("property_catalog", deployment="prod")
    with pytest.raises(ValueError, match="namespace does not match"):
        validate_property_catalog_database(database, deployment="dev")


def test_configured_production_database_binds_reader_and_writer_validation(
    monkeypatch,
):
    database = "th7247_catalog_prod_20260823a"
    monkeypatch.setenv("PROPERTY_CATALOG_PRODUCTION_DATABASE", database)

    assert configured_production_property_catalog_database() == database
    assert require_prod_catalog_database(database) == database
    with pytest.raises(PropertyCatalogPublishError, match="configured production"):
        require_prod_catalog_database("property_catalog")
    with pytest.raises(PropertyCatalogPublishError, match="isolated from production"):
        require_dev_catalog_database(database)


@pytest.mark.parametrize("database", ["", "default", "Bad-Name", "property.catalog"])
def test_configured_production_database_rejects_unsafe_identifiers(database):
    with pytest.raises(ValueError, match="safe, isolated"):
        configured_production_property_catalog_database(
            {"PROPERTY_CATALOG_PRODUCTION_DATABASE": database}
        )


@pytest.mark.parametrize(
    ("database", "deployment"),
    [
        ("property_catalog", "dev"),
        ("property_catalog_dev_clean", "prod"),
        ("property_catalog_backup", "prod"),
        ("PROPERTY_CATALOG", "prod"),
    ],
)
def test_property_catalog_database_validator_rejects_cross_wiring_and_unsafe_names(
    database, deployment
):
    with pytest.raises(ValueError):
        validate_property_catalog_database(database, deployment=deployment)


def test_property_catalog_executor_reads_only_allowlisted_qualified_tables():
    client = FakeClient()
    executor = PropertyCatalogReadExecutor(
        config=CONFIG,
        client_factory=lambda _config: client,
    )

    result = executor.execute(
        "SELECT status FROM `property_catalog_dev_clean`.property_catalog_activations",
        {},
        timeout_ms=1_500,
        settings={"max_result_rows": 2, "max_result_bytes": 1024},
    )

    assert result.data == [{"status": "ok"}]
    assert client.calls[0][3]["readonly"] == 1
    assert client.calls[0][3]["max_execution_time"] <= 1.5


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM `property_catalog_dev_clean`.spans",
        "SELECT * FROM `property_catalog_dev_clean`.span_attribute_key_catalog",
        "SELECT * FROM property_definition_catalog",
        "INSERT INTO `property_catalog_dev_clean`.property_definition_catalog VALUES ()",
        "SELECT * FROM `other_dev`.property_definition_catalog",
    ],
)
def test_property_catalog_executor_rejects_other_tables_and_mutations(query):
    client = FakeClient()
    executor = PropertyCatalogReadExecutor(
        config=CONFIG,
        client_factory=lambda _config: client,
    )

    with pytest.raises((RuntimeError, ValueError)):
        executor.execute(
            query,
            {},
            timeout_ms=1_000,
            settings={"max_result_rows": 1},
        )

    assert client.calls == []


def test_property_catalog_executor_uses_one_shrinking_wall():
    ticks = iter((10.0, 10.1, 10.2, 10.3, 10.5, 10.6, 10.7))
    client = FakeClient()
    executor = PropertyCatalogReadExecutor(
        config=CONFIG,
        client_factory=lambda _config: client,
        clock=lambda: next(ticks),
    )
    query = (
        "SELECT status FROM `property_catalog_dev_clean`.property_catalog_activations"
    )

    executor.execute(query, {}, timeout_ms=10_000, settings={"max_result_rows": 1})
    executor.execute(query, {}, timeout_ms=10_000, settings={"max_result_rows": 1})

    assert client.calls[1][2] < client.calls[0][2]


@pytest.mark.parametrize(
    "max_wall_ms",
    [0, -1, True],
)
def test_property_catalog_executor_rejects_invalid_request_wall(max_wall_ms):
    with pytest.raises(ValueError, match="max_wall_ms"):
        PropertyCatalogReadExecutor(
            config=CONFIG,
            client_factory=lambda _config: FakeClient(),
            max_wall_ms=max_wall_ms,
        )


def test_property_catalog_executor_honors_smaller_request_owned_wall():
    ticks = iter((10.0, 10.04, 10.041, 10.042))
    client = FakeClient()
    executor = PropertyCatalogReadExecutor(
        config=CONFIG,
        client_factory=lambda _config: client,
        clock=lambda: next(ticks),
        max_wall_ms=50,
    )

    executor.execute(
        "SELECT status FROM `property_catalog_dev_clean`.property_catalog_activations",
        {},
        timeout_ms=2_000,
        settings={"max_result_rows": 1},
    )

    assert 1 <= client.calls[0][2] <= 10
    assert client.calls[0][3]["max_execution_time"] <= 0.01


def test_property_catalog_reader_sql_stays_inside_physical_allowlist():
    reader = PropertyCatalogReader(
        SimpleExecutor(), catalog_database="property_catalog_dev_clean"
    )

    for query in (reader._activation_sql, reader._conflict_sql, reader._page_sql):
        _validate_catalog_query(
            query,
            database="property_catalog_dev_clean",
            allowed_tables=PROPERTY_CATALOG_TABLES,
        )


class SimpleExecutor:
    def execute(self, *_args, **_kwargs):
        raise AssertionError("SQL validation must not execute a query")
