import json
import os

import redis
import structlog

logger = structlog.get_logger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
STREAM_TTL = 300  # 5 minutes after agent completes
STREAM_PREFIX = "falcon:stream:"
STATUS_PREFIX = "falcon:status:"


class StreamBuffer:
    """Buffers agent streaming events in Redis for reconnection support.

    Events are stored in a Redis list so that when a user disconnects and
    reconnects, the frontend can request a replay of all events that were
    emitted while the socket was down.
    """

    _pool = None

    @classmethod
    def _get_redis(cls):
        if cls._pool is None:
            cls._pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)
        return redis.Redis(connection_pool=cls._pool)

    def __init__(self, conversation_id):
        self.conversation_id = str(conversation_id)
        self.stream_key = f"{STREAM_PREFIX}{self.conversation_id}"
        self.status_key = f"{STATUS_PREFIX}{self.conversation_id}"
        self._redis = self._get_redis()

    def set_status(self, status):
        """Set conversation agent status: 'running', 'done', 'error', 'cancelled'."""
        try:
            self._redis.set(self.status_key, status, ex=STREAM_TTL)
        except Exception as e:
            logger.warning("stream_buffer_set_status_failed", error=str(e))

    def get_status(self):
        """Get current agent status for this conversation."""
        try:
            return self._redis.get(self.status_key)
        except Exception as e:
            logger.warning("stream_buffer_get_status_failed", error=str(e))
            return None

    def append_event(self, event_data):
        """Append an event to the stream buffer."""
        try:
            self._redis.rpush(self.stream_key, json.dumps(event_data))
            # Refresh TTL on each append to keep the list alive during streaming
            self._redis.expire(self.stream_key, STREAM_TTL)
        except Exception as e:
            logger.warning("stream_buffer_append_failed", error=str(e))

    def get_all_events(self):
        """Get all buffered events (for replay on reconnect)."""
        try:
            events = self._redis.lrange(self.stream_key, 0, -1)
            return [json.loads(e) for e in events]
        except Exception as e:
            logger.warning("stream_buffer_read_failed", error=str(e))
            return []

    def clear(self):
        """Clear the stream buffer (events list only, status is set separately)."""
        try:
            self._redis.delete(self.stream_key)
        except Exception as e:
            logger.warning("stream_buffer_clear_failed", error=str(e))

    def cleanup_after_done(self):
        """Mark the stream as done and set TTL so both keys expire."""
        try:
            self._redis.expire(self.stream_key, STREAM_TTL)
            self._redis.set(self.status_key, "done", ex=STREAM_TTL)
        except Exception as e:
            logger.warning("stream_buffer_cleanup_failed", error=str(e))
