"""Native write acknowledgement policy; not replica durability evidence."""

import pytest

from tracer.services.clickhouse.v2.property_catalog import dev_runtime
from tracer.services.clickhouse.v2.property_catalog import (
    reader_activation_client as control,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ACTIVATION_CONTROL_COLUMNS,
    ACTIVATION_CONTROL_TABLE,
)


class Driver:
    database = "property_catalog"
    user = "catalog_control_writer"
    server_enforced_readonly = False

    def __init__(self, *, error=None):
        self.error = error
        self.writes = []
        # Simulate deployment defaults that must not override this protocol.
        self.profile = {"async_insert": 1, "wait_for_async_insert": 0}

    def execute(self, sql, values=None, *, settings=None):
        if sql == control._CLICKHOUSE_PROVENANCE_SQL:
            return [("catalog-0", self.database, self.user, 0, 0)]
        if sql == control._CLICKHOUSE_GRANTS_SQL:
            return [
                (
                    f"GRANT SELECT ON {self.database}.property_catalog_activations TO {self.user}",
                ),
                (
                    f"GRANT SELECT, INSERT ON {self.database}.{ACTIVATION_CONTROL_TABLE} TO {self.user}",
                ),
            ]
        assert sql.startswith("INSERT INTO ")
        self.writes.append((sql, values, settings))
        effective = {**self.profile, **(settings or {})}
        assert effective["async_insert"] == 0, "early buffered ACK is unsafe"
        if self.error is not None:
            raise self.error
        return []


def adapter(kind, driver):
    if kind == "catalog":
        client = dev_runtime.NativeCatalogClient(driver, database=driver.database)
        table = "property_catalog_checkpoints"
        columns = ("catalog_revision",)
        rows = ({"catalog_revision": 1},)
    else:
        client = control._ActivationControlClient(
            driver,
            database=driver.database,
            user=driver.user,
            expected_hostnames=("catalog-0",),
        )
        table = ACTIVATION_CONTROL_TABLE
        columns = ACTIVATION_CONTROL_COLUMNS
        rows = (dict.fromkeys(columns, "value"),)
    return client, f"`{driver.database}`.`{table}`", columns, rows


@pytest.mark.parametrize("kind", ("catalog", "reader_control"))
def test_native_ack_pins_synchronous_insert_without_changing_retry_identity(kind):
    driver = Driver()
    client, table, columns, rows = adapter(kind, driver)
    client.insert(
        table,
        rows,
        columns=columns,
        timeout_ms=500,
        deduplication_token="same-request:same-digest",
    )
    assert len(driver.writes) == 1
    sql, values, settings = driver.writes[0]
    assert sql == f"INSERT INTO {table} ({', '.join(columns)}) VALUES"
    assert values == [tuple(rows[0][column] for column in columns)]
    assert settings == {
        "async_insert": 0,
        "insert_deduplication_token": "same-request:same-digest",
        "max_execution_time": 0.5,
    }
    assert driver.profile == {"async_insert": 1, "wait_for_async_insert": 0}


@pytest.mark.parametrize("kind", ("catalog", "reader_control"))
def test_native_insert_error_or_uncertain_ack_propagates_without_adapter_retry(kind):
    error = TimeoutError("response lost after the server may have committed")
    driver = Driver(error=error)
    client, table, columns, rows = adapter(kind, driver)
    with pytest.raises(TimeoutError) as raised:
        client.insert(
            table,
            rows,
            columns=columns,
            timeout_ms=500,
            deduplication_token="same-request:same-digest",
        )
    assert raised.value is error
    assert len(driver.writes) == 1


@pytest.mark.parametrize("kind", ("catalog", "reader_control"))
def test_native_ack_policy_does_not_bypass_destination_identity(kind):
    driver = Driver()
    client, table, columns, rows = adapter(kind, driver)
    driver.database = "foreign_catalog"
    with pytest.raises(
        (
            dev_runtime.PropertyCatalogDevRuntimeError,
            control.ProductionActivationCommandError,
        )
    ):
        client.insert(
            table,
            rows,
            columns=columns,
            timeout_ms=500,
            deduplication_token="same-request:same-digest",
        )
    assert not driver.writes
