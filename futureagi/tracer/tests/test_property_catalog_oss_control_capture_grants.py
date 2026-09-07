"""Exact OSS reader-control capture grants; offline, no server mutations."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    reader_activation_client as control,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ACTIVATION_CONTROL_COLUMNS,
    ACTIVATION_CONTROL_TABLE,
)
from tracer.tests.test_property_catalog_reader_activation import Driver

DATABASE = "property_catalog_dev_oss"
USER = "control_writer"
CAPTURE = DATABASE + "_source_capture"
ACCESS = ("SELECT", "INSERT", "ALTER DELETE", "ALTER TTL", "CREATE TABLE", "DROP TABLE")


def grant(access, database, table="*", *, user=USER):
    return (f"GRANT {access} ON {database}.{table} TO {user}",)


def base():
    return [
        grant("SELECT, INSERT", DATABASE),
        *(
            grant("SELECT", "system", name)
            for name in ("databases", "settings", "tables")
        ),
    ]


def capture(source="default"):
    return [grant("SELECT", source, "spans"), grant(", ".join(ACCESS), CAPTURE)]


def validate(rows, source="default"):
    return control._validate_oss_control_writer_grants(
        rows, database=DATABASE, user=USER, source_database=source
    )


def test_exact_bootstrap_bundle_and_reordered_show_grants():
    assert validate(base() + capture()) is None
    assert validate(list(reversed(base() + capture()))) is None


def test_nondefault_source_comes_from_config_not_observed_grants():
    validate(base() + capture("tenant_source"), source="tenant_source")
    with pytest.raises(control.ProductionActivationCommandError):
        validate(base() + capture("tenant_source"))


def test_legacy_no_capture_contract_does_not_adopt_new_grants():
    validate(base(), source=None)
    with pytest.raises(control.ProductionActivationCommandError):
        validate(base() + capture(), source=None)
    with pytest.raises(control.ProductionActivationCommandError):
        validate(base())


def test_quoted_and_split_exact_privileges_are_equivalent():
    rows = [grant("SELECT", "`default`", "`spans`", user=f"`{USER}`")]
    rows += [grant(access, f"`{CAPTURE}`", user=f"`{USER}`") for access in ACCESS]
    validate(base() + rows)


@pytest.mark.parametrize("missing", ACCESS)
def test_every_capture_privilege_required(missing):
    rows = base() + [
        grant("SELECT", "default", "spans"),
        grant(", ".join(access for access in ACCESS if access != missing), CAPTURE),
    ]
    with pytest.raises(
        control.ProductionActivationCommandError, match="bootstrap contract"
    ):
        validate(rows)


@pytest.mark.parametrize("missing", range(6))
def test_every_catalog_metadata_source_and_capture_scope_required(missing):
    rows = base() + capture()
    del rows[missing]
    with pytest.raises(control.ProductionActivationCommandError):
        validate(rows)


@pytest.mark.parametrize(
    "extra",
    [
        grant("INSERT", "default", "spans"),
        grant("DROP TABLE", "default", "spans"),
        grant("SELECT", "default"),
        grant("SELECT", "other_source", "spans"),
        grant("SELECT", "system"),
        grant("SELECT", "system", "users"),
        grant("CREATE TABLE", DATABASE),
        grant("CREATE TABLE", "property_catalog_dev_old_source_capture"),
        grant("CREATE DATABASE", CAPTURE),
        grant("ALTER", CAPTURE),
        grant("TRUNCATE", CAPTURE),
        grant("ALL", CAPTURE),
        grant("SELECT", "*"),
        grant("SELECT", "default", "spans", user="somebody_else"),
        grant("SELECT", "default", "spans", user=USER + " WITH GRANT OPTION"),
        (f"GRANT capture_role TO {USER}",),
        (f"GRANT capture_role TO {USER} WITH ADMIN OPTION",),
        grant("SELECT", "default", "spans"),
        grant("SELECT, SELECT", CAPTURE),
        (f"GRANT SELECT ON `default.spans TO {USER}",),
        (f"GRANT SELECT ON default.`spans TO {USER}",),
        (f"GRANT SELECT ON default.spans TO `{USER}",),
        grant("SELECT", CAPTURE, "`*`"),
        (f"REVOKE SELECT ON {CAPTURE}.* FROM {USER}",),
        (f"GRANT SELECT ON default.spans TO {USER}; SELECT 1",),
    ],
)
def test_unrelated_broad_delegated_duplicate_malformed_grants_fail(extra):
    with pytest.raises(control.ProductionActivationCommandError):
        validate(base() + capture() + [extra])


@pytest.mark.parametrize(
    "source",
    [
        "",
        "default.spans",
        "`default`",
        "default;SELECT 1",
        "system",
        DATABASE,
        CAPTURE,
        1,
    ],
)
def test_source_namespace_is_explicit_safe_and_disjoint(source):
    with pytest.raises(
        control.ProductionActivationCommandError, match="capture source"
    ):
        validate(base() + capture(), source=source)


@pytest.mark.parametrize(
    "rows", [None, "GRANT SELECT", [()], [(1,)], [("grant", "extra")]]
)
def test_invalid_grant_result_shape_rejected(rows):
    with pytest.raises(control.ProductionActivationCommandError):
        validate(rows)


def client(driver, *, source="default", deployment="dev"):
    return control._ActivationControlClient(
        driver,
        database=driver.database,
        user=driver.user,
        expected_hostnames=("catalog-0",),
        deployment=deployment,
        source_database=source,
    )


def test_constructor_and_every_write_reattest_exact_configured_bundle():
    driver = Driver(DATABASE, deployment="dev")
    driver.grants = base() + capture("tenant_source")
    guarded = client(driver, source="tenant_source")
    assert not driver.writes
    kwargs = {
        "columns": ACTIVATION_CONTROL_COLUMNS,
        "timeout_ms": 1000,
        "deduplication_token": "test-exact",
    }
    table = f"`{DATABASE}`.`{ACTIVATION_CONTROL_TABLE}`"
    row = dict.fromkeys(ACTIVATION_CONTROL_COLUMNS, "test")
    guarded.insert(table, (row,), **kwargs)
    assert len(driver.writes) == 1
    driver.grants.append(grant("SELECT", "property_catalog_dev_old_source_capture"))
    with pytest.raises(control.ProductionActivationCommandError):
        guarded.insert(table, (row,), **kwargs)
    with pytest.raises(control.ProductionActivationCommandError):
        guarded.confirm_receipt(table, (row,), **kwargs)
    assert len(driver.writes) == 1


def test_capture_privileges_do_not_expand_control_adapter_sql():
    driver = Driver(DATABASE, deployment="dev")
    driver.grants = base() + capture()
    guarded = client(driver)
    with pytest.raises(
        control.ProductionActivationCommandError, match="non-reviewed read"
    ):
        guarded.query("SELECT * FROM default.spans", {}, timeout_ms=1000)
    with pytest.raises(
        control.ProductionActivationCommandError, match="non-ledger insert"
    ):
        guarded.insert(
            f"`{CAPTURE}`.`spans`",
            ({"value": 1},),
            columns=("value",),
            timeout_ms=1000,
            deduplication_token="test-exact",
        )
    assert not driver.writes


def test_production_two_table_policy_remains_unchanged():
    driver = Driver("property_catalog")
    client(driver, deployment="prod")
    driver.grants += [
        grant("SELECT", "default", "spans"),
        grant(", ".join(ACCESS), "property_catalog_source_capture"),
    ]
    with pytest.raises(control.ProductionActivationCommandError):
        client(driver, deployment="prod")


def test_bootstrap_capture_bundle_matches_guard():
    # Check the actual checked-in bootstrap statement, not a second DDL recipe.
    bootstrap = (
        Path(__file__).resolve().parents[2]
        / "scripts/property_catalog_oss/bootstrap_clickhouse.sh"
    )
    text = bootstrap.read_text()
    lines = [
        line
        for line in text.splitlines()
        if line.startswith('clickhouse --query "GRANT ')
        and line.endswith(' TO $CONTROL_USER"')
    ]
    substitutions = {
        "$SOURCE_DATABASE": "default",
        "$TARGET_DATABASE": DATABASE,
        "$CAPTURE_DATABASE": CAPTURE,
        "$CONTROL_USER": USER,
    }
    grants = []
    for line in lines:
        sql = (
            line.removeprefix('clickhouse --query "')
            .removesuffix('"')
            .replace("\\`", "`")
        )
        for old, new in substitutions.items():
            sql = sql.replace(old, new)
        grants.append((sql,))
    assert len(grants) == 3
    validate(grants + base()[1:])


@pytest.mark.parametrize("configured_capture", [False, True])
def test_real_factory_forwards_only_configured_source_before_any_capture(
    tmp_path, monkeypatch, configured_capture
):
    from tracer.services.clickhouse.v2.property_catalog import reader_activation
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        PropertyCatalogDevRuntimeFactory,
    )
    from tracer.tests.test_property_catalog_dev_rollout import (
        _request,
        _unit_runtime_config,
    )

    config = _unit_runtime_config(str(tmp_path))
    config = replace(config, source=replace(config.source, database="tenant_source"))
    driver = Driver(config.catalog.database, deployment="dev")
    driver.user = config.catalog.user
    hostname = config.provenance_expectation.writer_clickhouse_hostnames[0]
    driver.provenance = [(hostname, driver.database, driver.user, 0, 0)]
    grants = base() + (capture("tenant_source") if configured_capture else [])
    driver.grants = [
        (row[0].replace(DATABASE, driver.database).replace(USER, driver.user),)
        for row in grants
    ]
    driver.close = Mock()
    observed = []

    class Automatic:
        def __init__(self, client, **kwargs):
            # The real ReaderActivationClient and native/schema/grant guards
            # have already run. No capture table or build exists in this test.
            observed.append(client._guarded._source_database)

        def reconcile(self, scope):
            return "selected"

    monkeypatch.setattr(reader_activation, "AutomaticReaderActivation", Automatic)
    runtime = SimpleNamespace(
        config=config,
        bound_request=_request(execute=True),
        _source_capture=object() if configured_capture else None,
        _refresh_project_tenant_authorization=lambda: None,
    )
    factory = PropertyCatalogDevRuntimeFactory(
        settings_object=SimpleNamespace(),
        native_client_factory=lambda _: driver,
    )
    assert factory._reconcile_reader_activation(runtime) == "selected"
    assert observed == ["tenant_source" if configured_capture else None]
    driver.close.assert_called_once()
    assert not driver.writes

    if configured_capture:
        # A valid grant from another source cannot stand in for configuration.
        driver.grants = [
            (row[0].replace("tenant_source.spans", "default.spans"),)
            for row in driver.grants
        ]
        with pytest.raises(control.ProductionActivationCommandError):
            factory._reconcile_reader_activation(runtime)
