from types import SimpleNamespace

import pytest
from conftest import UnsafeClickHouseTestTarget, _ch_test_native_port
from tracer.tests.integration import conftest as integration_conftest


def test_integration_client_uses_guarded_sidecar_opener(monkeypatch):
    calls = []

    class NativeClient:
        def disconnect_connection(self):
            calls.append("disconnect")

    def open_client(**kwargs):
        calls.append(kwargs)
        return NativeClient()

    monkeypatch.setattr(
        integration_conftest,
        "settings",
        SimpleNamespace(
            CLICKHOUSE={
                "CH_DATABASE": "test_tfc",
                "CH_USERNAME": "test_user",
                "CH_PASSWORD": "test_password",
            }
        ),
    )
    monkeypatch.setattr(
        integration_conftest, "_open_ch_test_native_client", open_client
    )

    fixture = integration_conftest.ch_client.__wrapped__()
    next(fixture)
    with pytest.raises(StopIteration):
        next(fixture)

    assert calls == [
        {
            "owned_database": "test_tfc",
            "user": "test_user",
            "password": "test_password",
        },
        "disconnect",
    ]


def test_unnamed_clickhouse_port_skips_before_connecting():
    with pytest.raises(pytest.skip.Exception, match="no test ClickHouse named"):
        _ch_test_native_port({})


def test_forwarded_general_clickhouse_port_is_refused():
    with pytest.raises(UnsafeClickHouseTestTarget, match="forwarded port 19000"):
        _ch_test_native_port({"CH25_TCP_PORT": "19000"})
