"""Dispatcher heartbeats in the database (``webhooks_dispatcher_beat``): each loop records when it last passed and how
many slots of each lane it had busy. The health pass reads them for WA2 ("no dispatcher heartbeat in 2 min while
deliveries are due") and the ``webhooks.slots.busy`` gauge (DESIGN.md sections 13.2 and 13.3)."""

from __future__ import annotations

import os
import socket
import time
from datetime import UTC, datetime

from webhooks.models import DispatcherBeat

BEAT_EVERY_S = 10.0


def loop_ids(loops: int) -> list[str]:
    """``webhooks-dispatcher-1..loops``: the ids the loops beat under (``temporal.workflows.dispatcher_workflow_id``,
    which stays Django-free for the workflow sandbox, so the format is repeated here and a test keeps them equal)."""
    return [f"webhooks-dispatcher-{n}" for n in range(1, loops + 1)]


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:200]


def record(loop: str, busy: dict, now: datetime | None = None) -> None:
    DispatcherBeat.objects.update_or_create(
        loop=loop,
        defaults={
            "beat_at": now or datetime.now(UTC),
            "busy_normal": int(busy.get("normal", 0)),
            "busy_slow": int(busy.get("slow", 0)),
            "worker": worker_name(),
        },
    )


class Throttled:
    """Calls :func:`record` at most every ``BEAT_EVERY_S`` (the loop heartbeats about once a second)."""

    def __init__(self, loop: str, every_s: float = BEAT_EVERY_S, clock=time.monotonic):
        self.loop, self.every_s, self.clock = loop, every_s, clock
        self._last: float | None = None

    def __call__(self, busy: dict) -> bool:
        t = self.clock()
        if self._last is not None and t - self._last < self.every_s:
            return False
        record(self.loop, busy)
        self._last = t
        return True
