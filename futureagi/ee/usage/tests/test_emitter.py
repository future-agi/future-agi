"""
Phase 2: Emitter Tests

Tests that emit() writes to Redis Stream correctly and handles failures.
Requires Redis running locally.
"""

# Check if Redis is available (test port 16379 per docs/TESTING.md)
import os
from unittest.mock import patch

import pytest

from ee.usage.schemas.events import UsageEvent

REDIS_TEST_URL = os.environ.get("REDIS_URL", "redis://localhost:16379/0")

try:
    import redis as _redis_lib

    _r = _redis_lib.Redis.from_url(REDIS_TEST_URL)
    _r.ping()
    REDIS_AVAILABLE = True
except Exception:
    REDIS_AVAILABLE = False

skip_no_redis = pytest.mark.skipif(not REDIS_AVAILABLE, reason="Redis not available")


@pytest.fixture(autouse=True)
def _usage_events_on(settings):
    """An install whose consumer drains the stream (Future AGI Cloud)."""
    settings.USAGE_EVENTS_ENABLED = True


@skip_no_redis
class TestEmitter:
    def setup_method(self):
        """Clear the stream before each test."""
        # Reset singleton for test isolation
        import ee.usage.services.emitter as emitter_mod
        from ee.usage.services.emitter import STREAM_KEY, get_redis

        emitter_mod._redis_client = None

        r = get_redis()
        r.delete(STREAM_KEY)

    def test_emit_writes_to_stream(self):
        from ee.usage.services.emitter import STREAM_KEY, emit, get_redis

        emit(UsageEvent(org_id="org-1", event_type="test"))
        assert get_redis().xlen(STREAM_KEY) == 1

    def test_emit_event_is_readable(self):
        from ee.usage.services.emitter import STREAM_KEY, emit, get_redis

        emit(UsageEvent(org_id="org-1", event_type="turing_large", amount=20))
        entries = get_redis().xrange(STREAM_KEY)
        assert len(entries) == 1
        _, data = entries[0]
        assert data["org_id"] == "org-1"
        assert data["event_type"] == "turing_large"
        assert data["amount"] == "20.0"

    def test_emit_preserves_properties(self):
        import json

        from ee.usage.services.emitter import STREAM_KEY, emit, get_redis

        emit(UsageEvent(org_id="org-1", event_type="test", properties={"key": "val"}))
        entries = get_redis().xrange(STREAM_KEY)
        _, data = entries[0]
        props = json.loads(data["properties"])
        assert props["key"] == "val"

    def test_emit_auto_generates_event_id(self):
        from ee.usage.services.emitter import STREAM_KEY, emit, get_redis

        emit(UsageEvent(org_id="org-1", event_type="test"))
        entries = get_redis().xrange(STREAM_KEY)
        _, data = entries[0]
        assert len(data["event_id"]) == 36  # UUID format

    def test_emit_multiple_events(self):
        from ee.usage.services.emitter import STREAM_KEY, emit, get_redis

        for i in range(10):
            emit(UsageEvent(org_id="org-1", event_type="test", amount=i))
        assert get_redis().xlen(STREAM_KEY) == 10

    def test_emit_returns_none(self):
        from ee.usage.services.emitter import emit

        result = emit(UsageEvent(org_id="org-1", event_type="test"))
        assert result is None


class TestEmitterErrorHandling:
    def test_does_not_raise_on_redis_failure(self):
        from ee.usage.services.emitter import emit

        with patch("ee.usage.services.emitter.get_redis") as mock_redis:
            mock_redis.return_value.xadd.side_effect = ConnectionError("Redis down")
            # Should not raise
            emit(UsageEvent(org_id="org-1", event_type="test"))

    def test_logs_on_redis_failure(self):
        from ee.usage.services.emitter import emit

        with patch("ee.usage.services.emitter.get_redis") as mock_redis:
            mock_redis.return_value.xadd.side_effect = ConnectionError("Redis down")
            with patch("ee.usage.services.emitter.logger") as mock_logger:
                emit(UsageEvent(org_id="org-1", event_type="test"))
                mock_logger.exception.assert_called_once()

    def test_emits_alarm_signal_on_redis_failure(self):
        # Lost billing must be a first-class, Redis-independent alarm — pinned via
        # an explicit Sentry capture tagged emit_failed_total (a stack-grouped log
        # is not an alert).
        from ee.usage.services.emitter import emit

        with patch("ee.usage.services.emitter.get_redis") as mock_redis:
            mock_redis.return_value.xadd.side_effect = ConnectionError("Redis down")
            with patch("ee.usage.services.emitter.capture_message") as mock_capture:
                emit(UsageEvent(org_id="org-1", event_type="test"))
                mock_capture.assert_called_once()
                assert (
                    mock_capture.call_args.kwargs["tags"]["alarm"]
                    == "emit_failed_total"
                )


class TestEmitterWithoutAConsumer:
    """Only Future AGI Cloud drains usage:events. Anywhere else the stream would
    only grow, until Redis is full and every request fails."""

    def test_off_writes_nothing_and_starts_no_consumer(self, settings):
        from ee.usage.services.emitter import emit

        settings.USAGE_EVENTS_ENABLED = False
        with (
            patch("ee.usage.services.emitter.get_redis") as mock_redis,
            patch("tfc.temporal.common.client.get_client_sync") as mock_client,
        ):
            emit(UsageEvent(org_id="org-1", event_type="test"))

        mock_redis.assert_not_called()
        mock_client.assert_not_called()

    def test_default_follows_whether_the_consumer_ships(self, monkeypatch):
        import importlib

        from tfc.ee_loader import usage_event_consumer_available

        if os.environ.get("USAGE_EVENTS_ENABLED"):
            pytest.skip("USAGE_EVENTS_ENABLED is set in this environment")
        # The settings module itself: the autouse fixture overrides the value.
        loaded = importlib.import_module(os.environ["DJANGO_SETTINGS_MODULE"])
        assert loaded.USAGE_EVENTS_ENABLED is usage_event_consumer_available()

        import tfc.ee_loader as ee_loader

        monkeypatch.setattr(ee_loader, "has_ee", lambda module: False)
        assert ee_loader.usage_event_consumer_available() is False
        monkeypatch.setattr(
            ee_loader, "has_ee", lambda module: module == "ee.cloud.temporal"
        )
        assert ee_loader.usage_event_consumer_available() is True

    def test_the_stream_is_capped(self, settings):
        from ee.usage.services.emitter import STREAM_KEY, emit

        settings.USAGE_EVENTS_MAX_LEN = 1234
        with patch("ee.usage.services.emitter.get_redis") as mock_redis:
            emit(UsageEvent(org_id="org-1", event_type="test"))

        args, kwargs = mock_redis.return_value.xadd.call_args
        assert args[0] == STREAM_KEY
        assert kwargs["maxlen"] == 1234
