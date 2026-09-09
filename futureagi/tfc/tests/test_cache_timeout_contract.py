from __future__ import annotations

from tfc.settings import settings as base_settings


def test_remote_cache_socket_operations_are_bounded_below_request_wall() -> None:
    cache_config = base_settings.CACHES["default"]
    if cache_config["BACKEND"].endswith("LocMemCache"):
        # The qualifier deliberately replaces Redis with an in-process cache;
        # no network socket exists in that mode.
        return

    options = cache_config["OPTIONS"]
    assert 0.05 <= options["SOCKET_CONNECT_TIMEOUT"] <= 2.0
    assert 0.05 <= options["SOCKET_TIMEOUT"] <= 2.0


def test_postgres_connection_open_is_bounded_inside_interactive_wall() -> None:
    for config in base_settings.DATABASES.values():
        if "postgresql" not in config.get("ENGINE", ""):
            continue
        assert config["CONN_MAX_AGE"] == 0
        assert 1 <= config["OPTIONS"]["connect_timeout"] <= 5
