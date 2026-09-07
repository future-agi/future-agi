"""Process health independent of workspace reconciliation outcomes.

A background heartbeat prevents a large tenant inventory from looking dead,
but it is only live within the current operation's explicit deadline. A stuck
worker cannot keep itself healthy merely because this thread still runs.
"""

from __future__ import annotations

import math
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .codec import canonical_json

HEALTH_FORMAT = "futureagi.property-catalog-lifecycle-health"
HEALTH_VERSION = 2


def write_health_record(
    path: str,
    *,
    healthy: bool,
    observed_at: datetime,
    detail: Mapping[str, Any],
    live: bool | None = None,
    ready: bool | None = None,
    phase: str = "idle",
    progress_at: datetime | None = None,
) -> None:
    """Atomically publish the same bounded health contract in Compose and Helm."""

    def iso_z(value: datetime) -> str:
        return (
            value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        )

    raw = (
        canonical_json(
            {
                "detail": dict(detail),
                "format": HEALTH_FORMAT,
                "version": HEALTH_VERSION,
                "healthy": healthy,
                "live": healthy if live is None else live,
                "ready": healthy if ready is None else ready,
                "phase": phase,
                "observed_at": iso_z(observed_at),
                "progress_at": iso_z(progress_at or observed_at),
            },
            max_bytes=256 * 1024,
        )
        + "\n"
    ).encode("utf-8")
    target = Path(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".property-catalog-lifecycle-health-",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("catalog health write was incomplete")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


class ControllerHealth:
    def __init__(
        self,
        publish: Callable[..., None],
        *,
        interval_seconds: float = 10,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("health interval must be positive and finite")
        self._publish = publish
        self._interval = interval_seconds
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._lock = threading.Lock()
        self._publication_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._thread: threading.Thread | None = None
        self._failure: Exception | None = None
        self._ready = False
        self._detail: dict[str, Any] = {}
        self._phase = "starting"
        self._progress_at = utc_now()
        self._deadline = monotonic() + 120

    def progress(
        self,
        phase: str,
        *,
        timeout_seconds: float,
        ready: bool | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("health progress deadline must be positive and finite")
        with self._lock:
            if self._failure is not None:
                raise RuntimeError("catalog health publisher failed") from self._failure
            self._phase = phase
            self._deadline = self._monotonic() + timeout_seconds
            self._progress_at = self._utc_now()
            if ready is not None:
                self._ready = ready
            if detail is not None:
                self._detail = dict(detail)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            live = (
                not self._shutdown.is_set()
                and self._failure is None
                and self._monotonic() <= self._deadline
            )
            ready = self._ready and live
            return {
                "healthy": ready
                and not self._detail.get("failed_count", 0)
                and not self._detail.get("cycle_error"),
                "live": live,
                "ready": ready,
                "phase": self._phase,
                "observed_at": self._utc_now(),
                "progress_at": self._progress_at,
                "detail": dict(self._detail),
            }

    def publish(self) -> None:
        # Serialize snapshots with their writes, so a heartbeat taken before
        # shutdown cannot overwrite the final not-live record afterwards.
        with self._publication_lock:
            self._publish(**self.snapshot())

    def _heartbeat(self) -> None:
        while not self._shutdown.wait(self._interval):
            try:
                self.publish()
            except Exception as exc:
                # Let the existing health record expire and propagate failure
                # to the worker on its next progress update.
                with self._lock:
                    self._failure = exc
                return

    def __enter__(self) -> ControllerHealth:
        if self._thread is not None:
            raise RuntimeError("catalog health publisher cannot be restarted")
        self.publish()
        self._thread = threading.Thread(
            target=self._heartbeat, name="catalog-health", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 1)
        # A graceful shutdown must not leave a fresh Ready record behind.
        self.publish()


__all__ = ["ControllerHealth", "write_health_record"]
