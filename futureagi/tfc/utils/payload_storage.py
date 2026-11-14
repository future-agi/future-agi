"""
Temporary storage for large payloads that exceed Temporal's blob size limit.

This module provides utilities to store large payloads (like trace data) in Redis
and retrieve them using a reference key. This avoids passing large data through
Temporal workflow inputs which have a hard 4MB limit.

Supports both string and binary (bytes) payloads. Binary payloads are stored
as-is; string payloads are encoded to UTF-8 before storage. Retrieval always
returns bytes (or None).

Usage:
    from tfc.utils.payload_storage import payload_storage

    key = payload_storage.store(large_json_string, ttl=300)
    key = payload_storage.store(protobuf_bytes, ttl=300)
    data = payload_storage.retrieve(key)
"""

import json
import os
import uuid
from typing import Any, Optional, Union

import redis
import structlog
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

PAYLOAD_DEFAULT_TTL = 3600  # 1 hour


class PayloadStorage:
    """
    Manages temporary storage of large payloads in Redis.

    This is designed for data that's too large to pass directly through
    Temporal workflow inputs (which have a 4MB hard limit).
    """

    def __init__(self, redis_url: Optional[str] = None, key_prefix: str = "payload:"):
        """
        Initialize payload storage.

        Args:
            redis_url: Redis connection URL. Defaults to REDIS_URL env var.
            key_prefix: Prefix for all payload keys in Redis.
        """
        self.key_prefix = key_prefix
        self._redis_url = redis_url or os.getenv("REDIS_URL")
        self._redis_client: Optional[redis.Redis] = None
        self._redis_available = False

        self._connect_redis()

    def _connect_redis(self) -> None:
        """Establish connection to Redis."""
        try:
            logger.info("payload storage connecting to redis")
            self._redis_client = redis.from_url(
                self._redis_url,
                decode_responses=False,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            # Test connection
            self._redis_client.ping()
            self._redis_available = True
            logger.info("payload storage connected to redis")
        except RedisError as e:
            logger.error(
                "payload storage redis connection failed",
                error=str(e),
            )
            self._redis_available = False
            self._redis_client = None
        except Exception as e:
            logger.exception(
                "payload storage unexpected error",
                error=str(e),
                error_type=type(e).__name__,
            )
            self._redis_available = False
            self._redis_client = None

    def store(self, data: Union[str, bytes], ttl: int = PAYLOAD_DEFAULT_TTL) -> str:
        """
        Store a payload in Redis and return a reference key.

        Args:
            data: Data to store (str or bytes). Strings are encoded to UTF-8.
            ttl: Time-to-live in seconds (default: 1 hour).

        Returns:
            A unique reference key that can be used to retrieve the data.

        Raises:
            RuntimeError: If Redis is not available.
        """
        if not self._redis_available or not self._redis_client:
            raise RuntimeError(
                "Redis is not available for payload storage. "
                "Cannot store large payloads."
            )

        key = f"{self.key_prefix}{uuid.uuid4().hex}"
        raw = data.encode("utf-8") if isinstance(data, str) else data

        try:
            self._redis_client.setex(key, ttl, raw)
            return key
        except RedisError as e:
            logger.exception(
                "failed to store payload in redis",
                key=key,
                error=str(e),
            )
            raise RuntimeError(f"Failed to store payload: {e}") from e

    def store_json(self, data: Any, ttl: int = PAYLOAD_DEFAULT_TTL) -> str:
        """
        Store a JSON-serializable object and return a reference key.

        Args:
            data: Any JSON-serializable object (dict, list, etc.).
            ttl: Time-to-live in seconds (default: 1 hour).

        Returns:
            A unique reference key for retrieval.
        """
        return self.store(json.dumps(data), ttl=ttl)

    def retrieve_json(self, key: str, delete_after: bool = False) -> Any:
        """
        Retrieve and JSON-deserialize a stored payload.

        Args:
            key: The reference key returned by store_json().
            delete_after: If True, delete after retrieval. Default False
                for retry safety (Temporal may re-execute activities).

        Returns:
            The deserialized object, or None if not found.
        """
        raw = self.retrieve(key, delete_after=delete_after)
        if raw is None:
            return None
        return json.loads(raw)

    def retrieve(self, key: str, delete_after: bool = True) -> Optional[bytes]:
        """
        Retrieve a payload from Redis using its reference key.

        Args:
            key: The reference key returned by store().
            delete_after: If True, delete the payload after retrieval.

        Returns:
            The stored data as bytes, or None if not found.

        Raises:
            RuntimeError: If Redis is not available.
        """
        if not self._redis_available or not self._redis_client:
            raise RuntimeError(
                "Redis is not available for payload storage. Cannot retrieve payloads."
            )

        try:
            if delete_after:
                data = self._redis_client.getdel(key)
            else:
                data = self._redis_client.get(key)

            if data is None:
                logger.warning(
                    "payload_not_found_in_redis",
                    key=key,
                )
                return None

            return data
        except RedisError as e:
            logger.exception(
                "failed_to_retrieve_payload_from_redis",
                key=key,
                error=str(e),
            )
            raise RuntimeError(f"Failed to retrieve payload: {e}") from e


_payload_storage: Optional[PayloadStorage] = None


def get_payload_storage() -> PayloadStorage:
    """Get or create the global payload storage instance."""
    global _payload_storage
    if _payload_storage is None:
        _payload_storage = PayloadStorage()
    return _payload_storage


class _PayloadStorageProxy:
    """Proxy to delay PayloadStorage initialization until first use."""

    def __getattr__(self, name):
        return getattr(get_payload_storage(), name)


payload_storage = _PayloadStorageProxy()
