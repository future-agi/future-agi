"""Receipt adapter boundary only; fake native observations, no live transport."""

from types import SimpleNamespace
from unittest.mock import Mock, call
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    reader_activation_client as subject,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ACTIVATION_CONTROL_COLUMNS,
    ACTIVATION_CONTROL_TABLE,
)
from tracer.services.clickhouse.v2.property_catalog.durable_native_writer import (
    DurableNativeCatalogWriter,
    NativeWriteUnresolved,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
    NativeWriteProofError,
)
from tracer.tests.test_property_catalog_control_read_agreement import (
    DATABASE,
    Driver,
    adapter,
    delegation_client,
    event,
    query_case,
)


def receipt_args(database=DATABASE):
    control_event = event(1)
    return {
        "table": f"`{database}`.`{ACTIVATION_CONTROL_TABLE}`",
        "rows": (control_event.as_row(),),
        "columns": ACTIVATION_CONTROL_COLUMNS,
        "timeout_ms": 1000,
        "deduplication_token": (
            "property-catalog-activation-control-v1:"
            f"{control_event.request_id}:{control_event.control_sha256}"
        ),
    }


def receipt_client(database=DATABASE, deployment="dev"):
    client, driver, writer = delegation_client(database, deployment)
    # This is the actual adapter and an exact-typed writer. The writer's receipt
    # algorithm is another module's responsibility, mocked at this boundary.
    assert type(writer) is DurableNativeCatalogWriter
    writer.confirm_receipt = Mock(return_value=None)
    writer.insert = Mock(side_effect=AssertionError("receipt must never INSERT"))
    writer.drain = Mock(side_effect=AssertionError("receipt must not drain globally"))
    driver.execute = Mock(wraps=driver.execute)
    return client, driver, writer


def assert_no_alternative_path(driver, writer):
    driver.execute_read.assert_not_called()
    writer.query.assert_not_called()
    writer.insert.assert_not_called()
    writer.drain.assert_not_called()


@pytest.mark.parametrize(
    "database,deployment", [(DATABASE, "dev"), ("property_catalog", "prod")]
)
def test_receipt_refreshes_identity_and_grants_then_forwards_all_metadata(
    database, deployment
):
    client, driver, writer = receipt_client(database, deployment)
    args = receipt_args(database)
    # Preserve native values and row metadata, including subsecond controlled_at.
    row = args["rows"][0]
    row["request_id"] = UUID(row["request_id"])
    args["columns"] = list(ACTIVATION_CONTROL_COLUMNS)
    writer.confirm_receipt.side_effect = lambda *a, **kw: (
        None if driver.execute.call_count == 2 else pytest.fail("unattested receipt")
    )
    assert client.confirm_receipt(**args) is None
    assert driver.execute.call_args_list == [
        call(subject._CLICKHOUSE_PROVENANCE_SQL),
        call(subject._CLICKHOUSE_GRANTS_SQL),
    ]
    writer.confirm_receipt.assert_called_once_with(
        args["table"],
        args["rows"],
        columns=ACTIVATION_CONTROL_COLUMNS,
        timeout_ms=args["timeout_ms"],
        deduplication_token=args["deduplication_token"],
    )
    forwarded = writer.confirm_receipt.call_args.args[1]
    assert forwarded is args["rows"] and forwarded[0] is row
    assert forwarded[0]["request_id"] is row["request_id"]
    assert forwarded[0]["controlled_at"].microsecond == 123456
    assert_no_alternative_path(driver, writer)


@pytest.mark.parametrize("has_writer", [False, True])
def test_receipt_capability_is_read_only_and_does_not_query_or_prove_anything(
    has_writer,
):
    client, driver, writer = receipt_client()
    if not has_writer:
        client._durable_writer = None
    assert client.receipt_confirmation_available is has_writer
    with pytest.raises(AttributeError):
        client.receipt_confirmation_available = not has_writer
    driver.execute.assert_not_called()
    writer.confirm_receipt.assert_not_called()
    assert_no_alternative_path(driver, writer)


@pytest.mark.parametrize(
    "database,deployment", [(DATABASE, "dev"), ("property_catalog", "prod")]
)
def test_no_durable_writer_never_confirms_visible_rows_or_silently_succeeds(
    database, deployment
):
    driver = Driver(database, deployment)
    client = adapter(driver, None)
    args = receipt_args(database)
    driver.execute = Mock(wraps=driver.execute)
    driver.execute_read = Mock(
        return_value=(list(args["rows"]), ACTIVATION_CONTROL_COLUMNS, {})
    )
    assert client.receipt_confirmation_available is False
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="requires a durable native writer",
    ):
        client.confirm_receipt(**args)
    driver.execute_read.assert_not_called()
    assert driver.execute.call_args_list == [
        call(subject._CLICKHOUSE_PROVENANCE_SQL),
        call(subject._CLICKHOUSE_GRANTS_SQL),
    ]


@pytest.mark.parametrize(
    "field,value",
    [
        ("database", "foreign"),
        ("user", "foreign"),
        ("server_enforced_readonly", True),
    ],
)
def test_identity_guard_precedes_receipt_delegation(field, value):
    client, driver, writer = receipt_client()
    setattr(driver, field, value)
    with pytest.raises(
        subject.ProductionActivationCommandError, match="identity changed"
    ):
        client.confirm_receipt(**receipt_args())
    driver.execute.assert_not_called()
    writer.confirm_receipt.assert_not_called()
    assert_no_alternative_path(driver, writer)


@pytest.mark.parametrize(
    "bad",
    [
        "hostname",
        "database",
        "user",
        "readonly",
        "locked",
        "extra_grant",
        "missing_grant",
    ],
)
def test_actual_provenance_or_grant_mismatch_blocks_receipt(bad):
    client, driver, writer = receipt_client()
    original = driver.execute._mock_wraps

    def execute(sql):
        rows = original(sql)
        if sql == subject._CLICKHOUSE_PROVENANCE_SQL and bad in {
            "hostname",
            "database",
            "user",
            "readonly",
            "locked",
        }:
            row = list(rows[0])
            index = {
                "hostname": 0,
                "database": 1,
                "user": 2,
                "readonly": 3,
                "locked": 4,
            }[bad]
            row[index] = "foreign" if index < 3 else 1
            return [tuple(row)]
        if sql == subject._CLICKHOUSE_GRANTS_SQL:
            if bad == "extra_grant":
                return [*rows, (f"GRANT SELECT ON source.spans TO {driver.user}",)]
            if bad == "missing_grant":
                return rows[:-1]
        return rows

    driver.execute.side_effect = execute
    with pytest.raises(subject.ProductionActivationCommandError):
        client.confirm_receipt(**receipt_args())
    writer.confirm_receipt.assert_not_called()
    assert_no_alternative_path(driver, writer)


@pytest.mark.parametrize(
    "table",
    [
        ACTIVATION_CONTROL_TABLE,
        f"{DATABASE}.{ACTIVATION_CONTROL_TABLE}",
        f"`foreign`.`{ACTIVATION_CONTROL_TABLE}`",
        f"`{DATABASE}`.`property_catalog_activations`",
        f"`{DATABASE}`.`property_catalog_source_streams`",
        f"`{DATABASE}`.`{ACTIVATION_CONTROL_TABLE}`; SELECT 1",
    ],
)
def test_only_exact_allowlisted_control_table_can_confirm(table):
    client, driver, writer = receipt_client()
    args = {**receipt_args(), "table": table}
    with pytest.raises(
        subject.ProductionActivationCommandError, match="non-ledger receipt"
    ):
        client.confirm_receipt(**args)
    writer.confirm_receipt.assert_not_called()
    assert_no_alternative_path(driver, writer)


@pytest.mark.parametrize(
    "columns",
    [
        (),
        ACTIVATION_CONTROL_COLUMNS[:-1],
        tuple(reversed(ACTIVATION_CONTROL_COLUMNS)),
        (*ACTIVATION_CONTROL_COLUMNS, "extra"),
        (*ACTIVATION_CONTROL_COLUMNS[:-1], ACTIVATION_CONTROL_COLUMNS[0]),
        ",".join(ACTIVATION_CONTROL_COLUMNS),
        None,
    ],
)
def test_missing_reordered_or_extra_columns_cannot_confirm(columns):
    client, driver, writer = receipt_client()
    with pytest.raises((subject.ProductionActivationCommandError, TypeError)):
        client.confirm_receipt(**{**receipt_args(), "columns": columns})
    writer.confirm_receipt.assert_not_called()
    assert_no_alternative_path(driver, writer)


@pytest.mark.parametrize(
    "bad",
    [
        "empty",
        "two",
        "missing",
        "extra",
        "none",
        "nonmapping",
        "keys_only",
        "mapping_not_sequence",
    ],
)
def test_receipt_requires_one_complete_mapping_without_erasing_metadata(bad):
    client, driver, writer = receipt_client()
    args = receipt_args()
    row = args["rows"][0]
    missing = dict(row)
    missing.pop("controlled_at")
    args["rows"] = {
        "empty": (),
        "two": (row, row),
        "missing": (missing,),
        "extra": ({**row, "extra": "not-exact"},),
        "none": None,
        "nonmapping": (None,),
        "keys_only": (tuple(ACTIVATION_CONTROL_COLUMNS),),
        "mapping_not_sequence": row,
    }[bad]
    with pytest.raises(
        subject.ProductionActivationCommandError, match="one complete row"
    ):
        client.confirm_receipt(**args)
    writer.confirm_receipt.assert_not_called()
    assert_no_alternative_path(driver, writer)


@pytest.mark.parametrize(
    "bad", ["duck_typed", "different_driver", "different_database"]
)
def test_receipt_rejects_replaced_writer_identity_even_after_initial_admission(bad):
    client, driver, writer = receipt_client()
    if bad == "duck_typed":
        client._durable_writer = SimpleNamespace(
            driver=driver, database=DATABASE, confirm_receipt=writer.confirm_receipt
        )
    elif bad == "different_driver":
        writer.driver = Driver()
    else:
        writer.database = "foreign"
    with pytest.raises(
        subject.ProductionActivationCommandError, match="receipt identity differs"
    ):
        client.confirm_receipt(**receipt_args())
    writer.confirm_receipt.assert_not_called()
    assert_no_alternative_path(driver, writer)


def test_typed_writer_without_receipt_method_cannot_use_insert_or_query_as_fallback():
    client, driver, writer = receipt_client()
    writer.confirm_receipt = None
    with pytest.raises(
        subject.ProductionActivationCommandError,
        match="cannot confirm an exact control receipt",
    ):
        client.confirm_receipt(**receipt_args())
    assert_no_alternative_path(driver, writer)


@pytest.mark.parametrize(
    "error",
    [
        NativeWriteUnresolved("exact receipt pending"),
        NativeWriteProofError("completion absent"),
        TimeoutError("original ACK absent"),
    ],
)
def test_receipt_uncertainty_propagates_without_retry_insert_or_global_drain(error):
    client, driver, writer = receipt_client()
    writer.confirm_receipt.side_effect = error
    with pytest.raises(type(error)) as raised:
        client.confirm_receipt(**receipt_args())
    assert raised.value is error
    assert writer.confirm_receipt.call_count == 1
    assert_no_alternative_path(driver, writer)


def test_legacy_history_status_read_does_not_require_receipt_capability():
    driver = Driver()
    client = adapter(driver, None)
    driver.execute_read = Mock(
        return_value=([(1,)], [("catalog_history_exists", "UInt8")], {})
    )
    sql, params, _ = query_case("history")
    assert client.receipt_confirmation_available is False
    assert client.query(sql, params, timeout_ms=1000) == (
        {"catalog_history_exists": 1},
    )
    driver.execute_read.assert_called_once()
