"""
Distributed State Management using Redis.

This module provides distributed state tracking for resources that need to be
coordinated across multiple application instances. It replaces in-memory
dictionaries with Redis-backed storage.

Primary use cases:
- Tracking running evaluations across instances
- Propagating cancel signals across instances
- Managing shared state for long-running tasks

Usage:
    from tfc.utils.distributed_state import (
        DistributedEvaluationTracker,
        evaluation_tracker,
    )

    # Track a running evaluation
    evaluation_tracker.mark_running(eval_id=123, runner_info={"worker": "instance-1"})

    # Check if an evaluation is running
    if evaluation_tracker.is_running(123):
        # Request cancellation (works across all instances)
        evaluation_tracker.request_cancel(123)

    # In the evaluation runner, check for cancel signals
    if evaluation_tracker.should_cancel(123):
        # Clean up and exit
        pass
"""

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

import redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


@dataclass
class RunningTaskInfo:
    """Information about a running task."""

    task_id: str
    instance_id: str
    started_at: str
    task_type: str = "evaluation"
    metadata: Dict[str, Any] = field(default_factory=dict)
    cancel_requested: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RunningTaskInfo":
        return cls(**data)


class DistributedStateManager:
    """
    Base class for distributed state management using Redis.

    Provides common functionality for tracking state across multiple instances:
    - Key-value storage with TTL
    - Pub/Sub for real-time notifications
    - Atomic operations for race-condition-free updates
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        key_prefix: str = "distributed_state:",
        default_ttl: int = 3600,  # 1 hour
    ):
        self.key_prefix = key_prefix
        self.default_ttl = default_ttl
        self._instance_id = f"{os.getenv('HOSTNAME', 'local')}_{uuid.uuid4().hex[:8]}"

        # Initialize Redis connection
        self._redis_url = redis_url or os.getenv(
            "REDIS_STATE_URL", os.getenv("REDIS_URL", "redis://localhost:6379/2")
        )
        self._redis_client: Optional[redis.Redis] = None
        self._pubsub: Optional[redis.client.PubSub] = None
        self._redis_available = False
        self._subscribers: Dict[str, List[Callable]] = {}
        self._listener_thread: Optional[threading.Thread] = None
        self._stop_listener = threading.Event()

        self._connect_redis()

    def _connect_redis(self) -> None:
        """Establish connection to Redis."""
        try:
            self._redis_client = redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            self._redis_client.ping()
            self._redis_available = True
            logger.info(f"Distributed state manager connected to Redis")
        except RedisError as e:
            logger.warning(f"Failed to connect to Redis for state management: {e}")
            self._redis_available = False

    def _get_key(self, name: str) -> str:
        """Generate the full Redis key."""
        return f"{self.key_prefix}{name}"

    def set(
        self, key: str, value: Any, ttl: Optional[int] = None, nx: bool = False
    ) -> bool:
        """
        Set a value in distributed state.

        Args:
            key: The key name.
            value: The value to store (will be JSON serialized).
            ttl: Time-to-live in seconds.
            nx: If True, only set if key doesn't exist.

        Returns:
            True if set successfully.
        """
        if not self._redis_available:
            return False

        full_key = self._get_key(key)
        ttl = ttl or self.default_ttl

        try:
            serialized = json.dumps(value) if not isinstance(value, str) else value
            if nx:
                return bool(
                    self._redis_client.set(full_key, serialized, ex=ttl, nx=True)
                )
            else:
                return bool(self._redis_client.set(full_key, serialized, ex=ttl))
        except RedisError as e:
            logger.error(f"Failed to set key {key}: {e}")
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a value from distributed state.

        Args:
            key: The key name.
            default: Default value if key doesn't exist.

        Returns:
            The stored value or default.
        """
        if not self._redis_available:
            return default

        full_key = self._get_key(key)

        try:
            value = self._redis_client.get(full_key)
            if value is None:
                return default
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        except RedisError as e:
            logger.error(f"Failed to get key {key}: {e}")
            return default

    def delete(self, key: str) -> bool:
        """Delete a key from distributed state."""
        if not self._redis_available:
            return False

        full_key = self._get_key(key)
        try:
            return bool(self._redis_client.delete(full_key))
        except RedisError as e:
            logger.error(f"Failed to delete key {key}: {e}")
            return False

    def exists(self, key: str) -> bool:
        """Check if a key exists."""
        if not self._redis_available:
            return False

        full_key = self._get_key(key)
        try:
            return bool(self._redis_client.exists(full_key))
        except RedisError as e:
            logger.error(f"Failed to check key {key}: {e}")
            return False

    def publish(self, channel: str, message: Any) -> int:
        """
        Publish a message to a channel.

        Args:
            channel: Channel name.
            message: Message to publish (will be JSON serialized).

        Returns:
            Number of subscribers that received the message.
        """
        if not self._redis_available:
            return 0

        try:
            serialized = (
                json.dumps(message) if not isinstance(message, str) else message
            )
            return self._redis_client.publish(
                f"{self.key_prefix}channel:{channel}", serialized
            )
        except RedisError as e:
            logger.error(f"Failed to publish to {channel}: {e}")
            return 0

    @property
    def is_available(self) -> bool:
        """Return True if Redis is available."""
        return self._redis_available

    @property
    def instance_id(self) -> str:
        """Return the unique instance identifier."""
        return self._instance_id


class DistributedEvaluationTracker(DistributedStateManager):
    """
    Tracks running evaluations across all application instances.

    This replaces the in-memory `running_evals` dictionary with a Redis-backed
    solution that works across multiple instances.

    Features:
    - Track which evaluations are currently running
    - Know which instance is processing each evaluation
    - Request cancellation that propagates to all instances
    - Automatic cleanup of stale entries via TTL
    - Pub/Sub for real-time cancel signal propagation
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        default_ttl: int = 7200,  # 2 hours - longer for evaluations
    ):
        super().__init__(
            redis_url=redis_url,
            key_prefix="running_eval:",
            default_ttl=default_ttl,
        )
        self._cancel_callbacks: Dict[str, Callable] = {}
        self._local_running: Set[str] = set()  # Track what this instance is running

    def mark_running(
        self,
        eval_id: int,
        runner_info: Optional[Dict[str, Any]] = None,
        ttl: Optional[int] = None,
    ) -> bool:
        """
        Mark an evaluation as running.

        Args:
            eval_id: The evaluation ID.
            runner_info: Optional metadata about the runner (e.g., worker info).
            ttl: Custom TTL for this evaluation.

        Returns:
            True if successfully marked, False if already running elsewhere.
        """
        key = str(eval_id)

        try:
            info = RunningTaskInfo(
                task_id=key,
                instance_id=self._instance_id,
                started_at=datetime.utcnow().isoformat(),
                task_type="evaluation",
                metadata=runner_info or {},
            )

            # Try to set with NX (only if not exists) to prevent duplicate processing
            if self.set(key, info.to_dict(), ttl=ttl, nx=True):
                self._local_running.add(key)
                logger.info(
                    f"Marked evaluation {eval_id} as running on {self._instance_id}",
                    extra={"eval_id": str(eval_id), "instance_id": self._instance_id},
                )
                return True
            else:
                # Check if we own it (maybe we crashed and restarted)
                existing = self.get_running_info(eval_id)
                if existing and existing.instance_id == self._instance_id:
                    # We own it, just update
                    self.set(key, info.to_dict(), ttl=ttl)
                    self._local_running.add(key)
                    logger.info(
                        f"Re-acquired evaluation {eval_id} on {self._instance_id} after restart",
                        extra={"eval_id": str(eval_id)},
                    )
                    return True

                logger.warning(
                    f"Evaluation {eval_id} is already running on instance "
                    f"{existing.instance_id if existing else 'unknown'}",
                    extra={
                        "eval_id": str(eval_id),
                        "existing_instance": (
                            existing.instance_id if existing else "unknown"
                        ),
                    },
                )
                return False
        except Exception as e:
            logger.exception(
                f"Error marking evaluation {eval_id} as running: {e}",
                extra={"eval_id": str(eval_id), "error": str(e)},
            )
            return False

    def mark_completed(self, eval_id: int) -> bool:
        """
        Mark an evaluation as completed and remove from tracking.

        Args:
            eval_id: The evaluation ID.

        Returns:
            True if successfully removed.
        """
        key = str(eval_id)
        self._local_running.discard(key)

        try:
            result = self.delete(key)
            if result:
                logger.info(
                    f"Marked evaluation {eval_id} as completed",
                    extra={"eval_id": str(eval_id), "instance_id": self._instance_id},
                )
            else:
                logger.debug(
                    f"Evaluation {eval_id} was already marked completed or not found",
                    extra={"eval_id": str(eval_id)},
                )
            return result
        except Exception as e:
            logger.exception(
                f"Error marking evaluation {eval_id} as completed: {e}",
                extra={"eval_id": str(eval_id), "error": str(e)},
            )
            return False

    def refresh_running(self, eval_id: int, ttl: Optional[int] = None) -> bool:
        """
        Renew this instance's lease on a running entry.

        Re-writes the entry with a fresh TTL and stamps
        ``metadata["renewed_at"]`` so other instances can distinguish a live
        owner (recent renewal) from a dead one (stale renewal). Only the
        owning instance may renew.

        Args:
            eval_id: The task ID.
            ttl: TTL for the renewed entry; defaults to default_ttl.

        Returns:
            True if renewed, False when the entry is missing, owned by
            another instance, or Redis is unavailable.
        """
        key = str(eval_id)
        try:
            info = self.get_running_info(eval_id)
            if not info or info.instance_id != self._instance_id:
                return False
            info.metadata = dict(info.metadata or {})
            info.metadata["renewed_at"] = datetime.utcnow().isoformat()
            return self.set(key, info.to_dict(), ttl=ttl)
        except Exception as e:
            logger.warning(f"Failed to refresh running entry {eval_id}: {e}")
            return False

    def is_running(self, eval_id: int) -> bool:
        """
        Check if an evaluation is currently running on any instance.

        Args:
            eval_id: The evaluation ID.

        Returns:
            True if the evaluation is running.
        """
        return self.exists(str(eval_id))

    def is_running_locally(self, eval_id: int) -> bool:
        """
        Check if an evaluation is running on THIS instance.

        Args:
            eval_id: The evaluation ID.

        Returns:
            True if running on this instance.
        """
        return str(eval_id) in self._local_running

    def get_running_info(self, eval_id: int) -> Optional[RunningTaskInfo]:
        """
        Get information about a running evaluation.

        Args:
            eval_id: The evaluation ID.

        Returns:
            RunningTaskInfo if running, None otherwise.
        """
        data = self.get(str(eval_id))
        if data:
            return RunningTaskInfo.from_dict(data)
        return None

    def request_cancel(self, eval_id: int, reason: str = "") -> bool:
        """
        Request cancellation of an evaluation.

        This sets a cancel flag and publishes a cancel message to all instances.

        Args:
            eval_id: The evaluation ID to cancel.
            reason: Optional reason for cancellation.

        Returns:
            True if cancel request was sent.
        """
        key = str(eval_id)
        cancel_key = f"cancel:{key}"

        try:
            # Set cancel flag
            cancel_info = {
                "eval_id": str(eval_id),
                "requested_at": datetime.utcnow().isoformat(),
                "requested_by": self._instance_id,
                "reason": reason,
            }
            self.set(cancel_key, cancel_info, ttl=3600)

            # Update the running info to mark cancel requested
            info = self.get_running_info(eval_id)
            if info:
                info.cancel_requested = True
                self.set(key, info.to_dict())
                logger.info(
                    f"Requested cancellation for evaluation {eval_id}",
                    extra={
                        "eval_id": str(eval_id),
                        "reason": reason,
                        "running_on": info.instance_id,
                        "requested_by": self._instance_id,
                    },
                )
            else:
                logger.warning(
                    f"Cancellation requested for evaluation {eval_id} but it is not currently running",
                    extra={"eval_id": str(eval_id), "reason": reason},
                )

            # Publish cancel message for immediate notification
            self.publish(f"cancel:{eval_id}", cancel_info)

            return True
        except Exception as e:
            logger.exception(
                f"Error requesting cancellation for evaluation {eval_id}: {e}",
                extra={"eval_id": str(eval_id), "error": str(e)},
            )
            return False

    def should_cancel(self, eval_id: int) -> bool:
        """
        Check if an evaluation should be cancelled.

        Call this periodically in long-running evaluation loops.

        Args:
            eval_id: The evaluation ID.

        Returns:
            True if cancellation was requested.
        """
        cancel_key = f"cancel:{str(eval_id)}"
        return self.exists(cancel_key)

    def clear_cancel_flag(self, eval_id: int) -> bool:
        """Clear the cancel flag after handling cancellation."""
        cancel_key = f"cancel:{str(eval_id)}"
        return self.delete(cancel_key)

    def get_all_running(self) -> List[RunningTaskInfo]:
        """
        Get all currently running evaluations.

        Returns:
            List of RunningTaskInfo for all running evaluations.
        """
        if not self._redis_available:
            return []

        try:
            # Scan for all running evaluation keys
            pattern = f"{self.key_prefix}*"
            running = []

            for key in self._redis_client.scan_iter(pattern, count=100):
                # Skip cancel keys and channel keys
                if ":cancel:" in key or ":channel:" in key:
                    continue

                data = self._redis_client.get(key)
                if data:
                    try:
                        info = RunningTaskInfo.from_dict(json.loads(data))
                        running.append(info)
                    except (json.JSONDecodeError, TypeError):
                        pass

            return running
        except RedisError as e:
            logger.error(f"Failed to get all running evaluations: {e}")
            return []

    def get_instance_running(self) -> List[int]:
        """
        Get evaluations running on THIS instance.

        Returns:
            List of evaluation IDs running locally.
        """
        return [int(eid) for eid in self._local_running]

    def cleanup_stale(self, max_age_hours: int = 24) -> int:
        """
        Clean up stale evaluation entries that have been running too long.

        This is a safety mechanism - normally TTL handles cleanup, but this
        catches any edge cases.

        Args:
            max_age_hours: Maximum age in hours before considering stale.

        Returns:
            Number of entries cleaned up.
        """
        if not self._redis_available:
            return 0

        cleaned = 0
        cutoff = datetime.utcnow().timestamp() - (max_age_hours * 3600)

        for info in self.get_all_running():
            try:
                started = datetime.fromisoformat(info.started_at).timestamp()
                if started < cutoff:
                    self.delete(info.task_id)
                    cleaned += 1
                    logger.warning(f"Cleaned up stale evaluation: {info.task_id}")
            except (ValueError, AttributeError):
                pass

        return cleaned

    def register_cancel_callback(
        self, eval_id: int, callback: Callable[[], None]
    ) -> None:
        """
        Register a callback to be called when cancellation is requested.

        This is useful for immediate response to cancel signals via Pub/Sub.

        Args:
            eval_id: The evaluation ID to watch.
            callback: Function to call when cancel is requested.
        """
        self._cancel_callbacks[str(eval_id)] = callback

    def unregister_cancel_callback(self, eval_id: int) -> None:
        """Remove a cancel callback."""
        self._cancel_callbacks.pop(str(eval_id), None)


class CancellableRunner:
    """
    Mixin class for runners that support distributed cancellation.

    Inherit from this class to add cancellation support to your evaluation runners.

    Example:
        class MyRunner(CancellableRunner):
            def __init__(self, eval_id: int):
                super().__init__(eval_id, evaluation_tracker)

            def run(self):
                for item in items:
                    if self.check_cancelled():
                        self.handle_cancellation()
                        return
                    process(item)
    """

    def __init__(self, eval_id: int, tracker: DistributedEvaluationTracker):
        self._eval_id = eval_id
        self._tracker = tracker
        self._cancel_event = False  # Local fast check
        self._last_cancel_check = 0
        self._cancel_check_interval = 1.0  # Check Redis every N seconds

    @property
    def cancel_event(self) -> bool:
        """Check if cancellation was requested (compatible with existing code)."""
        return self.check_cancelled()

    @cancel_event.setter
    def cancel_event(self, value: bool) -> None:
        """Set cancel event (triggers distributed cancel if True)."""
        if value:
            self._tracker.request_cancel(self._eval_id)
        self._cancel_event = value

    def check_cancelled(self) -> bool:
        """
        Check if this evaluation should be cancelled.

        Uses a time-based throttle to avoid hammering Redis.
        """
        if self._cancel_event:
            return True

        now = time.time()
        if now - self._last_cancel_check > self._cancel_check_interval:
            self._last_cancel_check = now
            self._cancel_event = self._tracker.should_cancel(self._eval_id)

        return self._cancel_event

    def handle_cancellation(self) -> None:
        """Called when cancellation is detected. Override in subclass."""
        logger.info(f"Evaluation {self._eval_id} cancelled")
        self._tracker.mark_completed(self._eval_id)
        self._tracker.clear_cancel_flag(self._eval_id)


# Global singleton instances
_evaluation_tracker: Optional[DistributedEvaluationTracker] = None


def get_evaluation_tracker() -> DistributedEvaluationTracker:
    """Get or create the global evaluation tracker instance."""
    global _evaluation_tracker
    if _evaluation_tracker is None:
        _evaluation_tracker = DistributedEvaluationTracker()
    return _evaluation_tracker


# Convenience alias
evaluation_tracker = get_evaluation_tracker()
