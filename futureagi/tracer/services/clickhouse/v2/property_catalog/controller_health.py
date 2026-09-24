"""Process heartbeat independent of reconciliation success, with bounded work.

Only progress() renews the monotonic operation deadline. A running heartbeat
thread cannot keep a stuck operation live. No lifecycle or configuration policy
is implemented here. The command owns atomic publication of the v2 record.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any


class ControllerHealth:
    def __init__(
        self,
        publish: Callable[..., None],
        *,
        stop: threading.Event,
        interval_seconds: float = 10,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("health interval must be positive and finite")
        self._publish = publish
        self._stop = stop
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

    def set_ready(self, ready: bool) -> None:
        """An observed failure clears readiness without extending a deadline."""
        with self._lock:
            self._ready = ready

    def progress(
        self,
        phase: str,
        *,
        timeout_seconds: float,
        ready: bool | None = None,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("health deadline must be positive and finite")
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
                and not self._stop.is_set()
                and self._failure is None
                and self._monotonic() <= self._deadline
            )
            ready = self._ready and live
            return {
                "healthy": ready,
                "live": live,
                "ready": ready,
                "phase": self._phase,
                "observed_at": self._utc_now(),
                "progress_at": self._progress_at,
                "detail": dict(self._detail),
            }

    def publish(self) -> None:
        # Serialize snapshot and write so no old heartbeat overwrites shutdown.
        with self._publication_lock:
            try:
                self._publish(**self.snapshot())
            except Exception as exc:
                with self._lock:
                    self._failure = exc
                raise

    def _heartbeat(self) -> None:
        while not self._shutdown.wait(self._interval):
            try:
                self.publish()
            except Exception:
                # Fail the next progress update; the last file also expires.
                return

    def __enter__(self) -> ControllerHealth:
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
        self.publish()
