import pytest
from django.conf import settings
from django.test import override_settings

from tracer.services.clickhouse import query_service as query_service_module
from tracer.services.clickhouse.application_read_policy import application_read_settings
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
    assert timeout_ms is None
    assert query_settings["max_rows_to_read"] == 0
    assert query_settings["max_memory_usage"] == 2 * 1024 * 1024 * 1024
    assert query_settings["max_bytes_to_read"] == 0
    assert query_settings["max_threads"] == 2


def test_application_query_service_supplies_memory_policy_when_omitted():
    client = _Client()
    service = AnalyticsQueryService(ch_client=client)

    service.execute_ch_query("SELECT 1", {})

    _, _, timeout_ms, query_settings = client.calls[0]
    assert timeout_ms is None
    assert query_settings == application_read_settings()
    assert (
        query_settings["max_memory_usage"]
        == settings.CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES
    )


@pytest.mark.parametrize(
    "requested_timeout_ms",
    [4_000, 12_000, 180_000, 120_000, None],
)
def test_application_query_service_legacy_ceiling_does_not_cut_off_statement(
    requested_timeout_ms,
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
    assert client.calls[0][2] is None
    assert client.calls[0][3]["max_execution_time"] == 0


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
    assert client.calls[0][2] is None


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
    assert client.calls[0][2] is None


def test_span_reader_defaults_apply_the_application_read_policy():
    assert current_settings() == application_read_settings()
    assert current_settings()["max_execution_time"] == 0


def test_span_reader_context_removes_statement_caps_retaining_memory():
    with ch_query_settings(
        max_rows_to_read=1,
        max_memory_usage=1_000_000,
        max_execution_time=120,
        max_threads=1,
    ):
        settings = current_settings()

    assert settings["max_memory_usage"] == 1_000_000
    assert settings["max_threads"] == 1
    assert settings["max_execution_time"] == 0
    assert settings["max_rows_to_read"] == 0
    assert settings["max_result_rows"] == 0
    assert settings["max_result_bytes"] == 0


def test_span_reader_context_removes_result_caps_but_preserves_thread_caps():
    with ch_query_settings(
        max_threads=2,
        max_result_rows=123,
        max_result_bytes=4_096,
    ):
        settings = current_settings()

    assert settings["max_threads"] == 2
    assert settings["max_result_rows"] == 0
    assert settings["max_result_bytes"] == 0


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


def test_span_reader_context_preserves_tighter_memory_only():
    tight_cap = 64 * 1024 * 1024
    with ch_query_settings(
        max_memory_usage=tight_cap,
        max_bytes_to_read=tight_cap,
    ):
        settings = current_settings()

    assert settings["max_memory_usage"] == tight_cap
    assert settings["max_bytes_to_read"] == 0

    with ch_query_settings(max_execution_time=0):
        assert current_settings()["max_execution_time"] == 0


def test_application_query_service_cannot_override_locked_profile():
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
    assert timeout_ms is None
    assert query_settings == requested_settings


def test_application_query_service_legacy_zero_is_not_a_statement_timeout():
    client = _Client()
    service = AnalyticsQueryService(ch_client=client)

    service.execute_ch_query("SELECT 1", {}, timeout_ms=0)

    assert client.calls[0][2] is None


@pytest.mark.parametrize("memory_cap", [8 * 1024**3, 24 * 1024**3, 36 * 1024**3])
def test_native_and_v2_memory_policy_share_deployment_configuration(memory_cap):
    with override_settings(CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES=memory_cap):
        client = _Client()
        AnalyticsQueryService(ch_client=client).execute_ch_query("SELECT 1")
        assert client.calls[0][3]["max_memory_usage"] == memory_cap
        assert current_settings()["max_memory_usage"] == memory_cap


@pytest.mark.parametrize("requested", [0, -1, 256 * 1024**3, None])
def test_memory_safety_cannot_be_disabled_or_exceeded(requested):
    with override_settings(CLICKHOUSE_APPLICATION_READ_MAX_MEMORY_BYTES=12 * 1024**3):
        assert (
            application_read_settings({"max_memory_usage": requested})[
                "max_memory_usage"
            ]
            == 12 * 1024**3
        )


@pytest.mark.parametrize(
    "name",
    [
        "max_execution_time",
        "max_execution_time_leaf",
        "max_estimated_execution_time",
        "min_execution_speed",
        "min_execution_speed_bytes",
        "max_rows_to_read",
        "max_bytes_to_read",
        "max_rows_to_read_leaf",
        "max_bytes_to_read_leaf",
        "max_result_rows",
        "max_result_bytes",
        "max_rows_to_group_by",
        "max_rows_in_distinct",
        "max_bytes_in_distinct",
        "max_rows_in_set",
        "max_bytes_in_set",
        "max_rows_in_join",
        "max_bytes_in_join",
        "max_rows_to_transfer",
        "max_bytes_to_transfer",
    ],
)
def test_all_statement_abort_caps_are_explicitly_disabled(name):
    requested = {name: 1}
    normalized = application_read_settings(requested)
    assert normalized[name] == 0
    assert requested == {name: 1}
    assert normalized["readonly"] == 2


def test_memory_spill_and_concurrency_settings_are_not_removed():
    requested = {
        "max_bytes_before_external_group_by": 2 * 1024**3,
        "max_bytes_before_external_sort": 2 * 1024**3,
        "max_concurrent_queries_for_user": 3,
        "max_memory_usage_for_user": 24 * 1024**3,
        "max_threads": 2,
    }
    normalized = application_read_settings(requested)
    assert all(normalized[key] == value for key, value in requested.items())
