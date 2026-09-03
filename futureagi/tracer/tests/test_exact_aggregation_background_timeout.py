"""Dedicated ClickHouse timeout lane for exact Observe aggregation workers."""

import pytest
from django.conf import settings


class _DedicatedClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.closed = False
        self.server_enforced_readonly = False
        self.server_profile_locked = False
        self.instances.append(self)

    def execute_read(self, query, params, *, timeout_ms, settings):
        self.calls.append((query, params, timeout_ms, settings))
        return [(1,)], [("value", "UInt8")], 1.0

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_dedicated_clients():
    _DedicatedClient.instances = []


@pytest.mark.unit
def test_exact_observe_lane_owns_fresh_background_client(monkeypatch):
    from tracer.services.clickhouse import client as client_module
    from tracer.services.clickhouse import query_service as query_service_module
    from tracer.services.clickhouse import v2 as v2_module
    from tracer.tasks import exact_aggregation

    config = {
        "host": "ch25.internal",
        "tcp_port": 9000,
        "user": "exact-reader",
        "password": "secret",
        "database": "futureagi",
        "server_enforced_readonly": False,
    }
    monkeypatch.setattr(client_module, "ClickHouseClient", _DedicatedClient)
    monkeypatch.setattr(v2_module, "get_v2_config", lambda: config)
    monkeypatch.setattr(
        query_service_module,
        "get_clickhouse_client",
        lambda: pytest.fail("exact worker must not use the shared client"),
    )

    services = []
    for _ in range(2):
        with exact_aggregation._exact_observe_analytics() as analytics:
            services.append(analytics)
            analytics.execute_ch_query("SELECT 1", {}, timeout_ms=12_345)
            analytics.execute_ch_query(
                "SELECT 2",
                {},
                timeout_ms=settings.GRAPH_BACKGROUND_WALL_MS,
            )
            assert not analytics.ch_client.closed

    first, second = _DedicatedClient.instances
    assert first is not second
    assert services[0].ch_client is first
    assert services[1].ch_client is second
    assert first.kwargs == {
        "host": config["host"],
        "port": config["tcp_port"],
        "user": config["user"],
        "password": config["password"],
        "database": config["database"],
        "server_enforced_readonly": config["server_enforced_readonly"],
        "read_timeout_ceiling_ms": settings.GRAPH_BACKGROUND_WALL_MS,
    }
    assert [call[2] for call in first.calls] == [
        12_345,
        settings.GRAPH_BACKGROUND_WALL_MS,
    ]
    assert [call[2] for call in second.calls] == [
        12_345,
        settings.GRAPH_BACKGROUND_WALL_MS,
    ]
    assert first.closed is True
    assert second.closed is True


@pytest.mark.unit
def test_exact_observe_lane_closes_client_when_reader_fails(monkeypatch):
    from tracer.services.clickhouse import client as client_module
    from tracer.services.clickhouse import v2 as v2_module
    from tracer.tasks import exact_aggregation

    monkeypatch.setattr(client_module, "ClickHouseClient", _DedicatedClient)
    monkeypatch.setattr(
        v2_module,
        "get_v2_config",
        lambda: {
            "host": "ch25.internal",
            "tcp_port": 9000,
            "user": "exact-reader",
            "password": "",
            "database": "futureagi",
            "server_enforced_readonly": True,
        },
    )

    with pytest.raises(RuntimeError, match="reader failed"):
        with exact_aggregation._exact_observe_analytics():
            raise RuntimeError("reader failed")

    assert len(_DedicatedClient.instances) == 1
    assert _DedicatedClient.instances[0].closed is True
