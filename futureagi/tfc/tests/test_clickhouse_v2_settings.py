"""The v2 ClickHouse client inherits the single-cluster CH_* connection.

The Helm chart sets CH_HOST/CH_USERNAME/CH_PASSWORD/CH_DATABASE/CH_HTTP_PORT and
no CH25_* connection variable; the v2 client must still connect with that
password (and not with an empty CH25_PASSWORD).
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from tfc.settings import settings as settings_module
from tracer.services.clickhouse.v2 import get_v2_config

LEGACY = {
    "CH_HOST": "clickhouse.svc",
    "CH_PORT": "9000",
    "CH_USERNAME": "app",
    "CH_PASSWORD": "chart-generated",
    "CH_DATABASE": "futureagi",
}
CHART_ENV = {"CH_HTTP_PORT": "8124", "CH_PORT": "9000"}


def test_unset_ch25_values_fall_back_to_the_ch_values():
    assert settings_module._clickhouse_v2_connection(CHART_ENV, LEGACY) == {
        "CH25_HOST": "clickhouse.svc",
        "CH25_HTTP_PORT": "8124",
        "CH25_TCP_PORT": "9000",
        "CH25_USER": "app",
        "CH25_PASSWORD": "chart-generated",
        "CH25_DATABASE": "futureagi",
    }


@pytest.mark.parametrize(
    "name,legacy",
    [
        ("CH25_HOST", "CH_HOST"),
        ("CH25_USER", "CH_USERNAME"),
        ("CH25_PASSWORD", "CH_PASSWORD"),
        ("CH25_DATABASE", "CH_DATABASE"),
    ],
)
def test_an_empty_ch25_value_counts_as_unset(name, legacy):
    connection = settings_module._clickhouse_v2_connection(
        {**CHART_ENV, name: ""}, LEGACY
    )
    assert connection[name] == LEGACY[legacy]


def test_explicit_ch25_values_win():
    env = {
        **CHART_ENV,
        "CH25_HOST": "ch25.svc",
        "CH25_HTTP_PORT": "18123",
        "CH25_TCP_PORT": "19000",
        "CH25_USER": "v2",
        "CH25_PASSWORD": "v2-secret",
        "CH25_DATABASE": "spans",
    }
    assert settings_module._clickhouse_v2_connection(env, LEGACY) == {
        name: env[name]
        for name in (
            "CH25_HOST",
            "CH25_HTTP_PORT",
            "CH25_TCP_PORT",
            "CH25_USER",
            "CH25_PASSWORD",
            "CH25_DATABASE",
        )
    }


def test_nothing_configured_keeps_the_previous_defaults():
    legacy = {
        "CH_HOST": None,
        "CH_USERNAME": "default",
        "CH_PASSWORD": "",
        "CH_DATABASE": "futureagi",
    }
    connection = settings_module._clickhouse_v2_connection({}, legacy)
    assert connection["CH25_HTTP_PORT"] is None  # get_v2_config: 8123
    assert connection["CH25_TCP_PORT"] is None  # get_v2_config: CH_PORT
    assert connection["CH25_PASSWORD"] == ""


def test_v2_client_config_connects_with_ch_password():
    v2 = settings_module._clickhouse_v2_connection(
        {**CHART_ENV, "CH25_PASSWORD": ""}, LEGACY
    )
    with override_settings(CLICKHOUSE=LEGACY, CLICKHOUSE_V2=v2):
        config = get_v2_config()
    assert config["password"] == "chart-generated"
    assert config["user"] == "app"
    assert config["host"] == "clickhouse.svc"
    assert config["http_port"] == 8124
    assert config["database"] == "futureagi"
