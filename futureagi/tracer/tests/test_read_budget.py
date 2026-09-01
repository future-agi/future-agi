import pytest
from clickhouse_connect.driver.exceptions import (
    DatabaseError as ClickHouseConnectDatabaseError,
)
from clickhouse_connect.driver.exceptions import OperationalError
from clickhouse_driver.errors import NetworkError, ServerException, SocketTimeoutError

from tracer.services.clickhouse.read_budget import (
    ReadDeadlineExceeded,
    is_clickhouse_api_read_unavailable_error,
    is_clickhouse_query_error,
    is_clickhouse_query_size_error,
    is_read_budget_error,
)


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
    assert is_read_budget_error(
        ClickHouseConnectDatabaseError(
            "Code: 159. DB::Exception: Timeout exceeded: private"
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
    assert not is_read_budget_error(
        ClickHouseConnectDatabaseError("Code: 62. DB::Exception: syntax error")
    )


def test_max_query_size_is_narrowly_classified_for_identity_batching() -> None:
    assert is_clickhouse_query_size_error(
        ServerException("Max query size exceeded at position 262133", code=62)
    )
    assert is_clickhouse_query_size_error(
        ClickHouseConnectDatabaseError(
            "Received ClickHouse exception, code: 62, server response: "
            "Max query size exceeded at position 262133"
        )
    )


def test_other_syntax_errors_are_not_query_size_errors() -> None:
    assert not is_clickhouse_query_size_error(
        ServerException("Syntax error at position 12", code=62)
    )
    assert not is_clickhouse_query_size_error(
        RuntimeError("Max query size exceeded at position 262133")
    )


def test_request_owned_deadline_is_a_budget_error() -> None:
    assert is_read_budget_error(ReadDeadlineExceeded("private timeout detail"))


def test_native_driver_socket_timeout_is_a_budget_error() -> None:
    assert is_read_budget_error(SocketTimeoutError("private socket detail"))


def test_arbitrary_builtin_timeout_is_not_a_clickhouse_budget_error() -> None:
    assert not is_read_budget_error(TimeoutError("private unrelated timeout"))


@pytest.mark.parametrize("code", [47, 60, 62])
def test_clickhouse_query_error_classifier_rejects_query_defects(code: int) -> None:
    assert not is_clickhouse_query_error(ServerException("private detail", code=code))
    assert not is_clickhouse_query_error(
        ClickHouseConnectDatabaseError(
            f"Received ClickHouse exception, code: {code}, server response: private"
        )
    )
    assert not is_clickhouse_query_error(
        OperationalError(
            f"Received ClickHouse exception, code: {code}, server response: private"
        )
    )


@pytest.mark.parametrize("status_code", [408, 429, 502, 503, 504])
def test_clickhouse_query_error_classifier_allows_canonical_http_transients(
    status_code: int,
) -> None:
    assert is_clickhouse_query_error(
        ClickHouseConnectDatabaseError(
            f"HTTP driver received HTTP status {status_code} (for url private)"
        )
    )
    assert is_clickhouse_query_error(
        OperationalError(
            f"HTTP driver received HTTP status {status_code} (for url private)"
        )
    )


def test_clickhouse_query_error_classifier_allows_narrow_transport_failures() -> None:
    assert is_clickhouse_query_error(NetworkError("private network detail"))
    assert is_clickhouse_query_error(
        OperationalError("Network Error: private connection detail")
    )


@pytest.mark.parametrize("code", [47, 60, 62])
def test_clickhouse_server_code_wins_over_transient_http_text(code: int) -> None:
    assert not is_clickhouse_query_error(
        ClickHouseConnectDatabaseError(
            f"Received ClickHouse exception, code: {code}, server response: "
            "HTTP driver received HTTP status 503"
        )
    )


def test_clickhouse_query_error_classifier_rejects_untyped_or_config_errors() -> None:
    assert not is_clickhouse_query_error(RuntimeError("Code: 60 missing table"))
    assert not is_clickhouse_query_error(
        OperationalError("Unsupported compression type: private")
    )


@pytest.mark.parametrize("code", [159, 241, 307, 386])
def test_api_read_unavailable_classifier_accepts_customer_failure_codes(
    code: int,
) -> None:
    native = ServerException("private server SQL and stack", code=code)
    http = ClickHouseConnectDatabaseError(
        f"Received ClickHouse exception, code: {code}, server response: private"
    )

    assert is_clickhouse_api_read_unavailable_error(native)
    assert is_clickhouse_api_read_unavailable_error(http)


def test_api_read_unavailable_classifier_keeps_query_defects_and_text_fail_closed():
    assert not is_clickhouse_api_read_unavailable_error(
        ServerException("private unknown column", code=47)
    )
    assert not is_clickhouse_api_read_unavailable_error(
        RuntimeError("Received ClickHouse exception, code: 386, private")
    )
