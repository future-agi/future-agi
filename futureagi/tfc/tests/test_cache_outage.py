"""/health/ and the setup screen keep answering while the cache is down.

Standalone's Redis runs inside the app container and restarts on its own;
while it is down, every anonymous request went through AuthMonitoringMiddleware's
cache.get and came back an HTML 500, so the setup screen could never show its
"Cache and session store" row as down.
"""

import socket
from unittest.mock import patch

import pytest
from django.test import override_settings

from tfc.middleware import auth_monitoring
from tfc.settings.settings import MIDDLEWARE as DEPLOYED_MIDDLEWARE


def _closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def cache_down():
    """The default cache on a Redis nothing listens on, behind the middleware
    a deployment runs (the test settings trim it)."""
    with override_settings(
        MIDDLEWARE=DEPLOYED_MIDDLEWARE,
        CACHES={
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": f"redis://127.0.0.1:{_closed_port()}/0",
                "OPTIONS": {
                    "CLIENT_CLASS": "django_redis.client.DefaultClient",
                    "SOCKET_CONNECT_TIMEOUT": 1,
                    "SOCKET_TIMEOUT": 1,
                },
            }
        },
    ):
        yield


@pytest.mark.django_db
def test_health_answers_with_the_cache_down(api_client, cache_down):
    response = api_client.get("/health/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_setup_checks_report_the_cache_as_down_instead_of_failing(
    api_client, cache_down
):
    from tfc.views.setup_checks import CHECKS

    probes = {check["id"]: True for check in CHECKS}
    probes["cache"] = False
    with (
        patch("tfc.views.setup_checks.is_oss", return_value=True),
        patch("tfc.views.setup_checks._run_probes", return_value=probes),
    ):
        response = api_client.get("/api/setup-checks/")

    assert response.status_code == 200
    row = next(c for c in response.json()["result"]["checks"] if c["id"] == "cache")
    assert row["status"] == "failed"


def test_a_cache_outage_is_logged_once_and_again_after_recovery(monkeypatch):
    middleware = auth_monitoring.AuthMonitoringMiddleware(lambda request: None)
    monkeypatch.setattr(auth_monitoring, "_cache_outage_logged", False)

    class Down:
        def get(self, *args, **kwargs):
            raise ConnectionError("Redis is restarting")

        set = get

    class Up:
        def get(self, key, default=None):
            return default

        def set(self, *args, **kwargs):
            pass

    with patch.object(auth_monitoring, "logger") as logger:
        monkeypatch.setattr(auth_monitoring, "cache", Down())
        middleware.count_attempt("10.0.0.1")
        middleware.count_attempt("10.0.0.1")
        assert logger.warning.call_count == 1

        monkeypatch.setattr(auth_monitoring, "cache", Up())
        middleware.count_attempt("10.0.0.1")
        monkeypatch.setattr(auth_monitoring, "cache", Down())
        middleware.count_attempt("10.0.0.1")
        assert logger.warning.call_count == 2
