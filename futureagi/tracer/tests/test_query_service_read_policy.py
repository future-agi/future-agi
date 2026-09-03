import pytest
from django.conf import settings

from tracer.services.clickhouse import query_service as query_service_module
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
    service = AnalyticsQueryService(ch_client=client)

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
    _, _, timeout_ms, query_settings = client.calls[0]
    assert timeout_ms == settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    assert "max_rows_to_read" not in query_settings
    assert query_settings["max_memory_usage"] == 2 * 1024 * 1024 * 1024
    assert query_settings["max_bytes_to_read"] == 512 * 1024 * 1024
    assert query_settings["max_threads"] == 2


def test_application_query_service_supplies_memory_policy_when_omitted():
    client = _Client()
    service = AnalyticsQueryService(ch_client=client)

    service.execute_ch_query("SELECT 1", {})

    _, _, timeout_ms, query_settings = client.calls[0]
    assert timeout_ms == settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    assert query_settings == {
        "max_memory_usage": query_service_module.APPLICATION_READ_MAX_MEMORY_USAGE,
        "max_bytes_to_read": query_service_module.APPLICATION_READ_MAX_BYTES_TO_READ,
    }


@pytest.mark.parametrize(
    ("requested_timeout_ms", "expected_timeout_ms"),
    [
        (4_000, 4_000),
        (12_000, 12_000),
        (
            settings.GRAPH_BACKGROUND_WALL_MS,
            settings.GRAPH_BACKGROUND_WALL_MS,
        ),
        (120_000, 120_000),
        (None, settings.GRAPH_BACKGROUND_WALL_MS),
    ],
)
def test_application_query_service_explicit_ceiling_preserves_tighter_deadline(
    requested_timeout_ms,
    expected_timeout_ms,
):
    client = _Client()
    service = AnalyticsQueryService(
        ch_client=client,
        read_timeout_ceiling_ms=settings.GRAPH_BACKGROUND_WALL_MS,
    )

    service.execute_ch_query(
        "SELECT 1",
        {},
        timeout_ms=requested_timeout_ms,
    )

    assert service.read_timeout_ceiling_ms == settings.GRAPH_BACKGROUND_WALL_MS
    assert client.calls[0][2] == expected_timeout_ms


@pytest.mark.parametrize(
    "read_timeout_ceiling_ms",
    [True, 0, -1, settings.CLICKHOUSE_REVIEWED_READ_TIMEOUT_CEILING_MS + 1],
)
def test_application_query_service_rejects_unreviewed_timeout_ceiling(
    read_timeout_ceiling_ms,
):
    with pytest.raises(ValueError, match="timeout ceiling"):
        AnalyticsQueryService(read_timeout_ceiling_ms=read_timeout_ceiling_ms)


def test_injected_client_does_not_read_or_mutate_global_client(monkeypatch):
    client = _Client()
    monkeypatch.setattr(
        query_service_module,
        "get_clickhouse_client",
        lambda: pytest.fail("global client must not be read"),
    )
    service = AnalyticsQueryService(
        ch_client=client,
        read_timeout_ceiling_ms=settings.GRAPH_BACKGROUND_WALL_MS,
    )

    service.execute_ch_query("SELECT 1", {}, timeout_ms=1_234)

    assert service.ch_client is client
    assert client.calls[0][2] == 1_234


def test_subclass_without_base_constructor_retains_interactive_ceiling():
    class _LegacyClientOwningService(AnalyticsQueryService):
        def __init__(self, client):
            self._ch_client = client

    client = _Client()
    service = _LegacyClientOwningService(client)

    service.execute_ch_query("SELECT 1", {}, timeout_ms=120_000)

    assert (
        service.read_timeout_ceiling_ms
        == settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    )
    assert client.calls[0][2] == settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS


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
    service = AnalyticsQueryService(ch_client=client)
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

    _, _, timeout_ms, query_settings = client.calls[0]
    assert timeout_ms == settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
    assert query_settings == requested_settings


def test_application_query_service_does_not_revive_exhausted_timeout():
    client = _Client()
    service = AnalyticsQueryService(ch_client=client)

    service.execute_ch_query("SELECT 1", {}, timeout_ms=0)

    assert client.calls[0][2] == 1
