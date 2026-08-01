from clickhouse_connect.driver.exceptions import (
    DatabaseError as ClickHouseConnectDatabaseError,
)
from clickhouse_connect.driver.exceptions import OperationalError
from clickhouse_driver.errors import ServerException

from tracer.services.clickhouse.read_budget import is_read_budget_error


def test_native_driver_budget_code_is_classified() -> None:
    assert is_read_budget_error(ServerException("private detail", code=241))


def test_http_driver_budget_codes_are_classified_from_canonical_prefix() -> None:
    assert is_read_budget_error(
        ClickHouseConnectDatabaseError(
            "Received ClickHouse exception, code: 241, server response: private"
        )
    )
    assert is_read_budget_error(
        OperationalError(
            "Received ClickHouse exception, code: 159, server response: private"
        )
    )


def test_http_driver_arbitrary_code_substring_is_not_classified() -> None:
    assert not is_read_budget_error(
        ClickHouseConnectDatabaseError("application detail mentioned code: 241")
    )
    assert not is_read_budget_error(
        RuntimeError("Received ClickHouse exception, code: 241")
    )


def test_non_budget_codes_are_not_classified_for_either_driver() -> None:
    assert not is_read_budget_error(ServerException("syntax", code=62))
    assert not is_read_budget_error(
        ClickHouseConnectDatabaseError(
            "Received ClickHouse exception, code: 62, server response: private"
        )
    )


def test_builtin_timeout_remains_a_budget_error() -> None:
    assert is_read_budget_error(TimeoutError("private timeout detail"))
