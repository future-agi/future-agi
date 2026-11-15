"""No-SQLSTATE transport failures at both pre-evaluation SQL boundaries."""

import socket
from unittest.mock import patch

import psycopg
import pytest
from django.db import OperationalError, connection
from psycopg.errors import InvalidPassword, QueryCanceled

from model_hub.models.choices import CellStatus
from model_hub.tests.test_eval_metadata_retry import ReadFault, run_case

pytestmark = pytest.mark.django_db(transaction=True, databases="__all__")


def django_error(driver_error):
    error = OperationalError(str(driver_error))
    error.__cause__ = driver_error
    return error


@pytest.fixture
def refused_error():
    # Obtain a genuine libpq refusal (not a hand-assigned SQLSTATE). The bound,
    # non-listening socket reserves the port so another process cannot take it.
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        with pytest.raises(psycopg.OperationalError) as failure:
            psycopg.connect(
                host="127.0.0.1",
                port=reservation.getsockname()[1],
                user="metadata_test",
                dbname="metadata_test",
                connect_timeout=1,
            )
    assert failure.value.sqlstate is None
    assert "Connection refused" in str(failure.value)
    return failure.value


def driver_fault(kind, refused_error):
    if kind == "refused":
        return refused_error
    return psycopg.OperationalError("server closed the connection unexpectedly")


def assert_success(case, path, result):
    assert result.get("reason") == "matched", result
    case.logger.assert_called_once()
    if path == "preview":
        case.evaluator.run.assert_called_once_with(text="sample")
        assert result["output"] == "1.0"
    else:
        assert case.output_cell.status == CellStatus.PASS.value
        assert case.output_cell.value == "1.0"
        case.runner._run_evaluation.assert_called_once()
        case.runner._emit_cell_usage.assert_called_once()


@pytest.mark.parametrize("path", ["threaded", "direct", "preview"])
@pytest.mark.parametrize("table", ["model_hub_column", "accounts_workspace"])
@pytest.mark.parametrize("kind", ["refused", "lost"])
def test_transient_transport_without_sqlstate_recovers(
    evaluation_case,
    refused_error,
    path,
    table,
    kind,
):
    case = evaluation_case
    driver = driver_fault(kind, refused_error)
    assert driver.sqlstate is None
    fault = ReadFault(table, error_factory=lambda: django_error(driver))
    with (
        connection.execute_wrapper(fault),
        patch.object(
            connection,
            "close",
            wraps=connection.close,
        ) as close,
    ):
        result = run_case(case, path)
    print(
        f"transport kind={kind} path={path} table={table} sqlstate={driver.sqlstate} "
        f"reads={fault.calls} closes={close.call_count} logs={case.logger.call_count} "
        f"evals={case.runner._run_evaluation.call_count + case.evaluator.run.call_count} "
        f"result={result}"
    )
    assert_success(case, path, result)
    assert fault.calls == 2
    assert close.call_count >= 1


@pytest.mark.parametrize("path", ["threaded", "direct", "preview"])
@pytest.mark.parametrize("table", ["model_hub_column", "accounts_workspace"])
@pytest.mark.parametrize("kind", ["refused", "lost"])
def test_persistent_transport_without_sqlstate_is_infrastructure(
    evaluation_case,
    refused_error,
    path,
    table,
    kind,
):
    case = evaluation_case
    driver = driver_fault(kind, refused_error)
    fault = ReadFault(table, failures=10, error_factory=lambda: django_error(driver))
    with connection.execute_wrapper(fault):
        result = run_case(case, path)
    print(f"persistent {kind} {path} {table}: reads={fault.calls} result={result}")
    assert result.get("error_code") == "EVALUATION_METADATA_UNAVAILABLE", result
    assert fault.calls == 3
    case.logger.assert_not_called()
    case.runner._run_evaluation.assert_not_called()
    case.evaluator.run.assert_not_called()
    case.runner._emit_cell_usage.assert_not_called()


@pytest.mark.parametrize("table", ["model_hub_column", "accounts_workspace"])
@pytest.mark.parametrize(
    "driver",
    [
        QueryCanceled("canceling statement due to statement timeout"),
        InvalidPassword("password authentication failed for user metadata_test"),
        psycopg.OperationalError(
            "password authentication failed for user metadata_test"
        ),
    ],
)
def test_nontransport_errors_are_not_retried(evaluation_case, table, driver):
    from model_hub.models.develop_dataset import Row

    case = evaluation_case
    row = Row.objects.get(pk=case.row_id)
    fault = ReadFault(table, error_factory=lambda: django_error(driver))
    # Measure the metadata/core runner only. The worker entry and exit routinely
    # call close_old_connections even when there is no metadata retry at all.
    with (
        connection.execute_wrapper(fault),
        patch.object(
            connection,
            "close",
            wraps=connection.close,
        ) as close,
    ):
        result, status, value = case.runner._process_eval_result(row, case.mappings)
    assert fault.calls == 1
    close.assert_not_called()
    assert "error_code" not in result
    assert status == CellStatus.ERROR.value
    case.runner._create_cell(
        case.dataset, case.runner.column, row, result, value, status
    )
    case.output_cell.refresh_from_db()
    assert case.output_cell.status == CellStatus.ERROR.value
    case.logger.assert_not_called()
    case.runner._run_evaluation.assert_not_called()


@pytest.mark.parametrize(
    "message",
    [
        'invalid connection option "unknown"',
        'could not translate host name "invalid.example" to address: Name or service not known',
        'connection failed: FATAL: database "missing" does not exist',
        'connection failed: FATAL: no pg_hba.conf entry for host "127.0.0.1"',
        "connection failed: SSL error: certificate verify failed",
        "connection failed: password authentication failed; Connection refused",
        "canceling statement due to statement timeout",
    ],
)
def test_nontransport_without_sqlstate_is_not_retried(evaluation_case, message):
    from model_hub.utils.eval_metadata import read_evaluation_column_types

    driver = psycopg.OperationalError(message)
    fault = ReadFault("model_hub_column", error_factory=lambda: django_error(driver))
    with connection.execute_wrapper(fault), patch.object(connection, "close") as close:
        with pytest.raises(OperationalError):
            read_evaluation_column_types(evaluation_case.mappings.values())
    assert fault.calls == 1
    close.assert_not_called()
