"""The LISTEN side of the ``pg_notify`` queue (DESIGN.md sections 3 and 13.1, R4; K2).

``WEBHOOKS_LISTEN_DSN`` set: a committed ``publish()`` wakes a blocked ``PgQueue.wait()`` well before its timeout. A
DSN that cannot connect: ``wait()`` sleeps out its timeout (the poll goes on) and logs the error's type only. The DSN,
its password and psycopg's message are in no log record, and Sentry's event for the warning (warnings made events,
as a lowered ``SENTRY_LOG_LEVEL`` does; frame variables and an attached stack as FA sends them; no scrubbing) carries
none of them. Needs FA's Django settings; the wake-up needs the test database. Every address is local (127.0.0.1).
"""

from __future__ import annotations

import logging
import threading
import time

import psycopg
import pytest
from django.db import connection
from psycopg.conninfo import make_conninfo

from webhooks import queue
from webhooks.queue import PgQueue
from webhooks.tests.factories import make_endpoint, use_test_keys
from webhooks.tests.sentry_capture import frame_vars, leaked, record_text, sentry_events
from webhooks.tests.test_store import pub

LISTEN_USER = "wh-listen-user"
LISTEN_PW = "lpw-31f7-k2"
# Port 9 on 127.0.0.1 is closed: refused at once, nothing leaves the host.
DSNS = {
    "refused": f"postgresql://{LISTEN_USER}:{LISTEN_PW}@127.0.0.1:9/wh",
    # Not valid percent-encoding: libpq refuses the DSN before connecting, quoting the bad part.
    "malformed": f"postgresql://{LISTEN_USER}:{LISTEN_PW}%zz@127.0.0.1:9/wh",
    "keywords": f"host=127.0.0.1 port=9 user={LISTEN_USER} password={LISTEN_PW} connect_timeout=2",
    # psycopg patched to fail with the whole DSN in its message, as a driver might.
    "quoted": f"postgresql://{LISTEN_USER}:{LISTEN_PW}@127.0.0.1:9/wh?application_name=quoted",
}


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


@pytest.mark.parametrize("case", sorted(DSNS))
def test_a_listen_dsn_that_fails_is_logged_by_type_only(case, monkeypatch):
    if case == "quoted":

        def connect(conninfo, **_kw):
            raise psycopg.OperationalError(f"could not connect with {conninfo}")

        monkeypatch.setattr(psycopg, "connect", connect)
    records = _Records()
    queue.log.addHandler(records)
    q = PgQueue(DSNS[case])  # by key: no frame of this test holds the DSN
    try:
        with sentry_events(event_level=logging.WARNING) as events:
            t0 = time.monotonic()
            got = q.wait(0.5)
            took = time.monotonic() - t0
    finally:
        queue.log.removeHandler(records)
        q.close()
    assert (
        got is False and took >= 0.45
    )  # it slept out its timeout: the 1 s poll goes on, no busy loop
    (rec,) = records.records
    assert (rec.levelno, rec.getMessage()) == (
        logging.WARNING,
        "webhooks LISTEN connection failed; polling",
    )
    assert rec.exc_info is None and rec.error_type in (
        "OperationalError",
        "ProgrammingError",
    )
    needles = [LISTEN_PW, LISTEN_USER, DSNS[case], "could not connect"]
    assert [n for n in needles if n in record_text(rec)] == []
    (event,) = events
    assert (
        event["logger"] == "webhooks.queue"
        and event["extra"]["error_type"] == rec.error_type
    )
    # The stack Sentry attached has wait()'s frame with its variables, and none of them is the error.
    (wait_vars,) = frame_vars(event, "webhooks.queue", "wait")
    assert {"self", "timeout_s", "error_type"} <= set(
        wait_vars
    ) and "exc" not in wait_vars
    assert wait_vars["self"] == "PgQueue(listen=on)"
    assert leaked(events, needles) == []


def _test_db_dsn() -> str:
    s = connection.settings_dict
    return make_conninfo(
        host=s["HOST"] or None,
        port=s["PORT"] or None,
        user=s["USER"],
        password=s["PASSWORD"],
        dbname=s["NAME"],
    )


@pytest.mark.django_db(transaction=True)
def test_a_committed_publish_wakes_a_waiting_listener(
    organization, workspace, monkeypatch
):
    use_test_keys(monkeypatch)
    make_endpoint(organization, workspace)
    q = PgQueue(_test_db_dsn())
    woke: list[tuple[bool, float]] = []

    def waiter():
        t0 = time.monotonic()
        woke.append((q.wait(10.0), time.monotonic() - t0))

    try:
        assert q.wait(0.05) is False  # connects and LISTENs; nothing has been published
        th = threading.Thread(target=waiter)
        th.start()
        time.sleep(0.3)  # the waiter is blocked in wait() when the producer commits
        t_commit = time.monotonic()
        pub(organization, workspace, 1)
        th.join(12)
        assert not th.is_alive()
        ((got, _took),) = woke
        assert got is True
        assert (
            time.monotonic() - t_commit < 2.0
        )  # woken by NOTIFY, not by its 10 s timeout
        assert q.wait(0.3) is False  # nothing stale left to wake the next wait
    finally:
        q.close()
