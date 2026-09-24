"""
Distributed Lock Manager using Redis.

This module provides distributed locking capabilities for multi-instance deployments.
It replaces local threading.Lock() patterns with Redis-based locks that work across
all instances of the application.

Usage:
    from tfc.utils.distributed_locks import distributed_lock_manager, DistributedLock

    # Context manager pattern
    with distributed_lock_manager.lock("my_resource_name"):
        # Critical section - only one instance can execute this at a time
        do_something()

    # Or use the decorator
    @DistributedLock("evaluation_{eval_id}")
    def process_evaluation(eval_id: int):
        # This function is protected by a distributed lock
        pass
"""

import functools
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Optional, Union

import redis
from redis.exceptions import LockError, LockNotOwnedError, RedisError

logger = logging.getLogger(__name__)


@dataclass
class LockConfig:
    """Configuration for distributed locks."""

    # Default lock timeout (auto-release after this many seconds)
    default_timeout: int = 30

    # How long to wait to acquire a lock before giving up
    default_blocking_timeout: int = 10

    # Retry interval when waiting for lock
    retry_interval: float = 0.1

    # Prefix for all lock keys in Redis
    key_prefix: str = "distributed_lock:"

    # Whether to use thread-local token for lock ownership
    thread_local: bool = True


class DistributedLockManager:
    """
    Manages distributed locks using Redis.

    This class provides a centralized way to acquire and release distributed locks
    across multiple application instances. It uses Redis's atomic operations to
    ensure that only one instance can hold a lock at any given time.

    Features:
    - Automatic lock expiration (prevents deadlocks if instance crashes)
    - Lock extension for long-running operations
    - Graceful fallback to local locking if Redis is unavailable
    - Lock ownership verification (prevents accidental release by other instances)
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        config: Optional[LockConfig] = None,
        fallback_to_local: bool = True,
    ):
        """
        Initialize the distributed lock manager.

        Args:
            redis_url: Redis connection URL. Defaults to REDIS_LOCK_URL or REDIS_URL env var.
            config: Lock configuration options.
            fallback_to_local: If True, fall back to local threading locks when Redis is unavailable.
        """
        self.config = config or LockConfig()
        self.fallback_to_local = fallback_to_local
        self._local_locks: dict = {}  # Fallback local locks
        self._instance_id = str(uuid.uuid4())[:8]  # Unique ID for this instance

        # Initialize Redis connection
        self._redis_url = redis_url or os.getenv(
            "REDIS_LOCK_URL", os.getenv("REDIS_URL", "redis://localhost:6379/2")
        )
        self._redis_client: Optional[redis.Redis] = None
        self._redis_available = False

        self._connect_redis()

    def _connect_redis(self) -> None:
        """Establish connection to Redis."""
        try:
            self._redis_client = redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            # Test connection
            self._redis_client.ping()
            self._redis_available = True
            logger.info(
                f"Distributed lock manager connected to Redis at {self._redis_url}"
            )
        except RedisError as e:
            logger.warning(
                f"Failed to connect to Redis for distributed locks: {e}. "
                f"Falling back to local locks."
            )
            self._redis_available = False
            self._redis_client = None

    def _get_lock_key(self, name: str) -> str:
        """Generate the full Redis key for a lock."""
        return f"{self.config.key_prefix}{name}"

    @contextmanager
    def lock(
        self,
        name: str,
        timeout: Optional[int] = None,
        blocking_timeout: Optional[int] = None,
        blocking: bool = True,
        thread_local: Optional[bool] = None,
    ):
        """
        Acquire a distributed lock as a context manager.

        Args:
            name: Unique name for the lock (e.g., "evaluation_123", "model_loading")
            timeout: Lock auto-expiration in seconds. Defaults to config.default_timeout.
            blocking_timeout: Max time to wait for lock. Defaults to config.default_blocking_timeout.
            blocking: If False, raise immediately if lock is not available.
            thread_local: Override config.thread_local for this lock only.
                redis-py stores the ownership token in thread-local storage by
                default, which makes ``extend()``/``release()`` fail from any
                thread other than the acquirer. Pass False when a helper
                thread must renew the lock (see run_prompt's OwnershipLease).
                Only safe when a single thread acquires this Lock instance.

        Yields:
            The lock object (can be used to extend the lock if needed).

        Raises:
            LockAcquisitionError: If the lock cannot be acquired within the timeout.

        Example:
            with lock_manager.lock("my_resource"):
                # Protected code here
                pass
        """
        timeout = timeout or self.config.default_timeout
        blocking_timeout = blocking_timeout if blocking else 0

        if blocking_timeout is None:
            blocking_timeout = self.config.default_blocking_timeout

        lock_key = self._get_lock_key(name)

        if self._redis_available and self._redis_client:
            # Use Redis distributed lock
            redis_lock = self._redis_client.lock(
                lock_key,
                timeout=timeout,
                blocking_timeout=blocking_timeout,
                thread_local=(
                    self.config.thread_local if thread_local is None else thread_local
                ),
            )

            acquired = False
            try:
                acquired = redis_lock.acquire(blocking=blocking)
                if not acquired:
                    logger.warning(
                        f"Could not acquire distributed lock: {name} "
                        f"(blocking={blocking}, timeout={blocking_timeout}s)"
                    )
                    raise LockAcquisitionError(
                        f"Could not acquire distributed lock: {name}"
                    )
                logger.debug(f"Acquired distributed lock: {name} (timeout={timeout}s)")
                yield redis_lock
            except LockError as e:
                logger.error(f"Redis lock error for {name}: {e}", exc_info=True)
                raise LockAcquisitionError(f"Lock error for {name}: {e}") from e
            except RedisError as e:
                logger.error(
                    f"Redis connection error during lock {name}: {e}", exc_info=True
                )
                raise LockAcquisitionError(f"Redis error for lock {name}: {e}") from e
            finally:
                if acquired:
                    try:
                        redis_lock.release()
                        logger.debug(f"Released distributed lock: {name}")
                    except LockNotOwnedError:
                        logger.warning(
                            f"Lock {name} was already released (possibly expired after {timeout}s)"
                        )
                    except RedisError as e:
                        logger.error(
                            f"Failed to release lock {name}: {e}", exc_info=True
                        )
        else:
            # Fallback to local threading lock
            if self.fallback_to_local:
                with self._local_lock(name, blocking_timeout, blocking) as lock:
                    yield lock
            else:
                raise RedisUnavailableError(
                    "Redis is not available and fallback is disabled"
                )

    @contextmanager
    def _local_lock(self, name: str, timeout: float, blocking: bool):
        """Fallback local lock implementation."""
        import threading

        if name not in self._local_locks:
            self._local_locks[name] = threading.Lock()

        local_lock = self._local_locks[name]
        # Can't specify timeout when blocking=False in threading.Lock
        if blocking:
            acquired = local_lock.acquire(blocking=True, timeout=timeout)
        else:
            acquired = local_lock.acquire(blocking=False)

        if not acquired:
            raise LockAcquisitionError(f"Could not acquire local lock: {name}")

        try:
            logger.debug(f"Acquired local lock (fallback): {name}")
            yield local_lock
        finally:
            local_lock.release()
            logger.debug(f"Released local lock (fallback): {name}")

    def try_lock(
        self, name: str, timeout: Optional[int] = None
    ) -> Optional["AcquiredLock"]:
        """
        Try to acquire a lock without blocking.

        Args:
            name: Unique name for the lock.
            timeout: Lock auto-expiration in seconds.

        Returns:
            AcquiredLock object if successful, None if lock is already held.

        Example:
            lock = lock_manager.try_lock("my_resource")
            if lock:
                try:
                    # Do work
                finally:
                    lock.release()
            else:
                print("Resource is busy")
        """
        timeout = timeout or self.config.default_timeout
        lock_key = self._get_lock_key(name)

        if self._redis_available and self._redis_client:
            redis_lock = self._redis_client.lock(
                lock_key,
                timeout=timeout,
                thread_local=self.config.thread_local,
            )
            if redis_lock.acquire(blocking=False):
                return AcquiredLock(name, redis_lock, self)
            return None
        else:
            # Local fallback
            import threading

            if name not in self._local_locks:
                self._local_locks[name] = threading.Lock()

            if self._local_locks[name].acquire(blocking=False):
                return AcquiredLock(name, self._local_locks[name], self)
            return None

    def is_locked(self, name: str) -> bool:
        """
        Check if a lock is currently held.

        Args:
            name: Name of the lock to check.

        Returns:
            True if the lock is held, False otherwise.
        """
        lock_key = self._get_lock_key(name)

        if self._redis_available and self._redis_client:
            return self._redis_client.exists(lock_key) > 0
        else:
            if name in self._local_locks:
                # Try to acquire and immediately release to check
                if self._local_locks[name].acquire(blocking=False):
                    self._local_locks[name].release()
                    return False
                return True
            return False

    def force_release(self, name: str) -> bool:
        """
        Force release a lock (use with caution - only for stuck locks).

        Args:
            name: Name of the lock to release.

        Returns:
            True if the lock was released, False if it didn't exist.
        """
        lock_key = self._get_lock_key(name)

        if self._redis_available and self._redis_client:
            result = self._redis_client.delete(lock_key)
            if result:
                logger.warning(f"Force released distributed lock: {name}")
            return result > 0
        return False

    def get_lock_info(self, name: str) -> Optional[dict]:
        """
        Get information about a lock.

        Args:
            name: Name of the lock.

        Returns:
            Dict with lock information or None if lock doesn't exist.
        """
        lock_key = self._get_lock_key(name)

        if self._redis_available and self._redis_client:
            ttl = self._redis_client.ttl(lock_key)
            if ttl == -2:  # Key doesn't exist
                return None
            owner = self._redis_client.get(lock_key)
            return {
                "name": name,
                "key": lock_key,
                "owner": owner,
                "ttl_seconds": ttl if ttl > 0 else None,
                "is_locked": True,
            }
        return None

    @property
    def is_distributed(self) -> bool:
        """Return True if using Redis distributed locks, False if using local fallback."""
        return self._redis_available

    def health_check(self) -> dict:
        """
        Perform a health check on the lock manager.

        Returns:
            Dict with health status information.
        """
        status = {
            "instance_id": self._instance_id,
            "redis_available": self._redis_available,
            "fallback_enabled": self.fallback_to_local,
            "mode": "distributed" if self._redis_available else "local",
        }

        if self._redis_available and self._redis_client:
            try:
                self._redis_client.ping()
                status["redis_ping"] = "ok"
            except RedisError as e:
                status["redis_ping"] = f"error: {e}"
                self._redis_available = False

        return status


class AcquiredLock:
    """Represents an acquired lock that can be manually released."""

    def __init__(
        self,
        name: str,
        lock: Union[redis.lock.Lock, "threading.Lock"],
        manager: DistributedLockManager,
    ):
        self.name = name
        self._lock = lock
        self._manager = manager
        self._released = False

    def release(self) -> None:
        """Release the lock."""
        if self._released:
            return

        try:
            self._lock.release()
            self._released = True
            logger.debug(f"Manually released lock: {self.name}")
        except (LockNotOwnedError, RuntimeError) as e:
            logger.warning(f"Error releasing lock {self.name}: {e}")

    def extend(self, additional_time: int) -> bool:
        """
        Extend the lock's TTL.

        Args:
            additional_time: Additional seconds to add to the lock's TTL.

        Returns:
            True if extended successfully, False otherwise.
        """
        if hasattr(self._lock, "extend"):
            try:
                self._lock.extend(additional_time)
                logger.debug(f"Extended lock {self.name} by {additional_time}s")
                return True
            except LockError as e:
                logger.warning(f"Failed to extend lock {self.name}: {e}")
                return False
        return False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


class LockAcquisitionError(Exception):
    """Raised when a lock cannot be acquired."""

    pass


class RedisUnavailableError(Exception):
    """Raised when Redis is unavailable and fallback is disabled."""

    pass


def DistributedLock(
    lock_name: str,
    timeout: int = 30,
    blocking_timeout: int = 10,
):
    """
    Decorator for protecting a function with a distributed lock.

    The lock name can include placeholders that will be filled from function arguments.

    Args:
        lock_name: Name of the lock. Can include {arg_name} placeholders.
        timeout: Lock auto-expiration in seconds.
        blocking_timeout: Max time to wait for lock.

    Example:
        @DistributedLock("evaluation_{eval_id}")
        def process_evaluation(eval_id: int):
            # This function is protected by a lock named "evaluation_123"
            pass

        @DistributedLock("model_loading_{model_name}")
        def load_model(model_name: str):
            pass
    """

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Build the actual lock name from function arguments
            import inspect

            sig = inspect.signature(func)
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()

            actual_lock_name = lock_name.format(**bound.arguments)

            with distributed_lock_manager.lock(
                actual_lock_name,
                timeout=timeout,
                blocking_timeout=blocking_timeout,
            ):
                return func(*args, **kwargs)

        return wrapper

    return decorator


# Global singleton instance
_lock_manager: Optional[DistributedLockManager] = None


def get_distributed_lock_manager() -> DistributedLockManager:
    """Get or create the global distributed lock manager instance."""
    global _lock_manager
    if _lock_manager is None:
        _lock_manager = DistributedLockManager()
    return _lock_manager


# Convenience alias
distributed_lock_manager = get_distributed_lock_manager()
