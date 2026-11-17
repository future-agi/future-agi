"""The ``pg_notify`` Queue adapter (DESIGN.md sections 3 and 13.1, R4).

Producers ``NOTIFY webhooks`` inside their transaction (``webhooks.publish``). The dispatcher ``LISTEN``s on a direct
(non-pgbouncer) connection, ``WEBHOOKS_LISTEN_DSN``; without one, or while that connection is down, ``wait`` is a
plain sleep and the 1 s poll alone keeps the 2 s p95.

The DSN carries a password (K2). A failed connection is logged by its error type only: psycopg's message can quote
the DSN (libpq echoes a malformed one back), and its frames hold it under names no scrubber matches. The log call runs
after the handler, so no frame on the stack refers to the error when Sentry attaches the stack and its variables.
"""

from __future__ import annotations

import logging
import threading

from webhooks.publish import NOTIFY_CHANNEL, notify

log = logging.getLogger("webhooks.queue")


class PgQueue:
    def __init__(self, dsn: str | None):
        self.dsn = dsn
        self._conn = None
        self._sleep = threading.Event()

    def __repr__(self) -> str:
        return f"PgQueue(listen={'on' if self.dsn else 'off'})"

    def kick(self) -> None:
        notify()

    def _connect(self):
        import psycopg

        conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=5)
        conn.execute(f"LISTEN {NOTIFY_CHANNEL}")
        return conn

    def wait(self, timeout_s: float) -> bool:
        if not self.dsn:
            self._sleep.wait(timeout_s)
            return False
        try:
            if self._conn is None or self._conn.closed:
                self._conn = self._connect()
            got = False
            for _ in self._conn.notifies(timeout=timeout_s, stop_after=1):
                got = True
            # Drain anything else that arrived, so one wake-up covers a burst.
            for _ in self._conn.notifies(timeout=0.0):
                pass
            return got
        except Exception as exc:  # noqa: BLE001 - the poll keeps working without LISTEN
            error_type = type(exc).__name__
        # Outside the handler, by type only (K2, module docstring): no message, no exc_info, no live reference.
        log.warning(
            "webhooks LISTEN connection failed; polling",
            extra={"metric": "webhooks.listen.failed", "error_type": error_type},
        )
        self.close()
        self._sleep.wait(timeout_s)
        return False

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._conn = None
