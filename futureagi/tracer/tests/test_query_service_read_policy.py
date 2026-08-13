import pytest

from tracer.services.clickhouse.query_service import AnalyticsQueryService
from tracer.services.clickhouse.v2.query_settings import (
    ch_query_settings,
    current_settings,
)


class _Client:
    def __init__(self):
        self.calls = []
        self.server_enforced_readonly = False

    def execute_read(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params, timeout_ms, settings))
        return [(1,)], [("value", "UInt8")], 1.0


def test_application_query_service_normalizes_every_read_policy():
    client = _Client()
    service = AnalyticsQueryService()
    service._ch_client = client

    result = service.execute_ch_query(
        "SELECT 1 AS value",
        {},
        timeout_ms=120_000,
        settings={
            "max_rows_to_read": 1,
            "max_memory_usage": 2 * 1024 * 1024 * 1024,
            "max_bytes_to_read": 512 * 1024 * 1024,
            "max_threads": 2,
        },
    )

    assert result.data == [{"value": 1}]
    _, _, timeout_ms, settings = client.calls[0]
    assert timeout_ms == 9_500
    assert "max_rows_to_read" not in settings
    assert settings["max_memory_usage"] == 2 * 1024 * 1024 * 1024
    assert settings["max_bytes_to_read"] == 512 * 1024 * 1024
    assert settings["max_threads"] == 2


def test_application_query_service_supplies_memory_policy_when_omitted():
    client = _Client()
    service = AnalyticsQueryService()
    service._ch_client = client

    service.execute_ch_query("SELECT 1", {})

    _, _, timeout_ms, settings = client.calls[0]
    assert timeout_ms == 9_500
    assert settings == {
        "max_memory_usage": 36 * 1024 * 1024 * 1024,
        "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
    }


def test_span_reader_defaults_apply_the_application_read_policy():
    assert current_settings() == {
        "max_memory_usage": 36 * 1024 * 1024 * 1024,
        "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
        "max_threads": 4,
        "max_result_rows": 1_000_000,
        "max_result_bytes": 512 * 1024 * 1024,
        "readonly": 2,
        "read_overflow_mode": "throw",
        "timeout_overflow_mode": "throw",
        "result_overflow_mode": "throw",
        "max_execution_time": 9.5,
    }


def test_span_reader_context_strips_rows_and_clamps_timeout():
    with ch_query_settings(
        max_rows_to_read=1,
        max_memory_usage=1_000_000,
        max_execution_time=120,
        max_threads=1,
    ):
        settings = current_settings()

    assert settings == {
        "max_memory_usage": 1_000_000,
        "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
        "max_execution_time": 9.5,
        "max_threads": 1,
        "max_result_rows": 1_000_000,
        "max_result_bytes": 512 * 1024 * 1024,
        "readonly": 2,
        "read_overflow_mode": "throw",
        "timeout_overflow_mode": "throw",
        "result_overflow_mode": "throw",
    }


def test_span_reader_context_preserves_lower_result_and_thread_caps():
    with ch_query_settings(
        max_threads=2,
        max_result_rows=123,
        max_result_bytes=4_096,
    ):
        settings = current_settings()

    assert settings["max_threads"] == 2
    assert settings["max_result_rows"] == 123
    assert settings["max_result_bytes"] == 4_096


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (2, 2),
        (8, 8),
        (64, 8),
    ],
)
def test_span_reader_context_allows_only_explicit_threads_up_to_eight(
    requested, expected
):
    with ch_query_settings(max_threads=requested):
        settings = current_settings()

    assert settings["max_threads"] == expected


def test_span_reader_context_preserves_tighter_memory_and_read_byte_caps():
    tight_cap = 64 * 1024 * 1024
    with ch_query_settings(
        max_memory_usage=tight_cap,
        max_bytes_to_read=tight_cap,
    ):
        settings = current_settings()

    assert settings["max_memory_usage"] == tight_cap
    assert settings["max_bytes_to_read"] == tight_cap

    with ch_query_settings(max_execution_time=0):
        assert current_settings()["max_execution_time"] == 0.001


def test_application_query_service_clamps_server_locked_timeout():
    client = _Client()
    client.server_enforced_readonly = True
    service = AnalyticsQueryService()
    service._ch_client = client
    requested_settings = {
        "max_rows_to_read": 1,
        "max_memory_usage": 2 * 1024 * 1024 * 1024,
    }

    service.execute_ch_query(
        "SELECT 1",
        {},
        timeout_ms=120_000,
        settings=requested_settings,
    )

    _, _, timeout_ms, settings = client.calls[0]
    assert timeout_ms == 9_500
    assert settings == requested_settings


def test_application_query_service_does_not_revive_exhausted_timeout():
    client = _Client()
    service = AnalyticsQueryService()
    service._ch_client = client

    service.execute_ch_query("SELECT 1", {}, timeout_ms=0)

    assert client.calls[0][2] == 1
