"""The per-attempt deadline watchdog (DESIGN.md section 13.1, R1).

urllib3's timeouts are per socket operation, so a receiver that trickles one byte every 14 s could hold a slot for
hours. One thread holds a heap of deadlines and runs each expired entry's callback once (the transport passes a
callback that shuts the socket down). Standard library only; the watchdog itself does no I/O.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from collections.abc import Callable


class Watchdog:
    def __init__(
        self,
        monotonic: Callable[[], float] = time.monotonic,
        name: str = "webhooks-watchdog",
    ):
        self._monotonic = monotonic
        self._heap: list[tuple[float, int]] = []
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._fired: set[int] = set()
        self._ids = itertools.count(1)
        self._cv = threading.Condition()
        self._stopped = False
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._thread.start()

    def register(self, deadline: float, callback: Callable[[], None]) -> int:
        """Run ``callback`` once at ``deadline`` (monotonic seconds) unless cancelled first. Returns a token."""
        with self._cv:
            token = next(self._ids)
            self._callbacks[token] = callback
            heapq.heappush(self._heap, (deadline, token))
            self._cv.notify()
            return token

    def cancel(self, token: int) -> bool:
        """True when the callback had not fired (and now never will)."""
        with self._cv:
            return self._callbacks.pop(token, None) is not None

    def fired(self, token: int) -> bool:
        with self._cv:
            return token in self._fired

    def stop(self) -> None:
        with self._cv:
            self._stopped = True
            self._cv.notify()
        self._thread.join(timeout=5)

    def pending(self) -> int:
        with self._cv:
            return len(self._callbacks)

    def _loop(self) -> None:
        while True:
            due: list[Callable[[], None]] = []
            with self._cv:
                if self._stopped:
                    return
                now = self._monotonic()
                while self._heap and self._heap[0][0] <= now:
                    _, token = heapq.heappop(self._heap)
                    cb = self._callbacks.pop(token, None)
                    if cb is not None:
                        self._fired.add(token)
                        if len(self._fired) > 10_000:
                            self._fired.clear()
                            self._fired.add(token)
                        due.append(cb)
                if not due:
                    wait = (self._heap[0][0] - now) if self._heap else 1.0
                    self._cv.wait(timeout=max(0.005, min(wait, 1.0)))
                    continue
            for cb in due:
                try:
                    cb()
                except Exception:  # noqa: BLE001 - a failed shutdown must not stop the watchdog
                    pass
