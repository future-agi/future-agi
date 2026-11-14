"""Publishing a key-cache eviction is best effort and must return promptly.

Key creation and signup publish after commit, synchronously. These tests run
the publisher against a local Redis stand-in that accepts connections but
never replies, and check that the caller gets its answer within the
configured Redis socket timeouts instead of waiting for the connection to be
closed.
"""

import socket
import threading
import time

import pytest
from django.test import override_settings
from django.urls import get_resolver

from accounts.key_cache import publish_key_revocation

# The stand-in closes its connections this long after the first one arrives,
# so a publisher without its own timeouts returns then rather than never.
SILENT_REDIS_HOLD_SECONDS = 8.0
# Socket timeouts used by the tests, and the time a publish may take with them.
TEST_SOCKET_TIMEOUT_SECONDS = 0.2
PUBLISH_DEADLINE_SECONDS = 3.0


class SilentRedis:
    """A TCP listener on 127.0.0.1 that accepts connections and never replies."""

    def __init__(self):
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self._listener.settimeout(0.05)
        self.port = self._listener.getsockname()[1]
        self._conns = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._timer = threading.Timer(SILENT_REDIS_HOLD_SECONDS, self.close)
        self._timer.daemon = True

    @property
    def url(self):
        return f"redis://127.0.0.1:{self.port}/0"

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if not self._conns:
                self._timer.start()
            self._conns.append(conn)

    def start(self):
        self._thread.start()
        return self

    def close(self):
        self._stop.set()
        self._timer.cancel()
        for conn in self._conns:
            try:
                conn.close()
            except OSError:
                pass
        try:
            self._listener.close()
        except OSError:
            pass


@pytest.fixture
def silent_redis():
    server = SilentRedis().start()
    yield server
    server.close()


@pytest.fixture
def short_redis_timeouts(silent_redis):
    with override_settings(
        REDIS_URL=silent_redis.url,
        REDIS_CACHE_SOCKET_CONNECT_TIMEOUT_SECONDS=TEST_SOCKET_TIMEOUT_SECONDS,
        REDIS_CACHE_SOCKET_TIMEOUT_SECONDS=TEST_SOCKET_TIMEOUT_SECONDS,
    ):
        yield


def test_publish_returns_within_socket_timeouts_when_redis_does_not_reply(
    short_redis_timeouts,
):
    started = time.monotonic()
    publish_key_revocation("api-key-synthetic", "secret-key-synthetic")
    elapsed = time.monotonic() - started
    assert elapsed < PUBLISH_DEADLINE_SECONDS, (
        f"publish took {elapsed:.1f}s with Redis not replying, "
        f"want under {PUBLISH_DEADLINE_SECONDS}s"
    )


def test_publish_keeps_its_timeouts_when_the_redis_url_sets_longer_ones(silent_redis):
    url = f"{silent_redis.url}?socket_timeout=30&socket_connect_timeout=30"
    with override_settings(
        REDIS_URL=url,
        REDIS_CACHE_SOCKET_CONNECT_TIMEOUT_SECONDS=TEST_SOCKET_TIMEOUT_SECONDS,
        REDIS_CACHE_SOCKET_TIMEOUT_SECONDS=TEST_SOCKET_TIMEOUT_SECONDS,
    ):
        started = time.monotonic()
        publish_key_revocation("api-key-synthetic", "secret-key-synthetic")
        elapsed = time.monotonic() - started
    assert elapsed < PUBLISH_DEADLINE_SECONDS, (
        f"publish took {elapsed:.1f}s with Redis not replying and URL timeouts "
        f"of 30s, want under {PUBLISH_DEADLINE_SECONDS}s"
    )


@pytest.mark.django_db
def test_key_creation_returns_credentials_when_redis_does_not_reply(
    auth_client, short_redis_timeouts, django_capture_on_commit_callbacks
):
    # Load the URLconf first: the first request of a process imports every
    # view module, which is not what this test times.
    assert get_resolver().url_patterns
    started = time.monotonic()
    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client.post(
            "/accounts/key/generate_secret_key/",
            {"key_name": "Key With Redis Not Replying"},
            format="json",
        )
    elapsed = time.monotonic() - started
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["api_key"] and result["secret_key"]
    assert elapsed < PUBLISH_DEADLINE_SECONDS, (
        f"key creation took {elapsed:.1f}s with Redis not replying, "
        f"want under {PUBLISH_DEADLINE_SECONDS}s"
    )
