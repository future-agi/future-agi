"""The dispatcher activity and boot when sending is off or not allowed (WH-1a review must-fix 3; DESIGN.md section
13.1). With ``FI_WEBHOOKS_ENABLED=false`` the activity waits out its run, heartbeating, instead of returning at once
(which made the workflow continue as new in a hot loop), stops early when cancelled, and boot starts no loop. Uses
Temporal's ``ActivityEnvironment``; needs Django settings, no database.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from temporalio.exceptions import CancelledError
from temporalio.testing import ActivityEnvironment

from webhooks import config
from webhooks.temporal import activities, boot


def _off(monkeypatch):
    monkeypatch.setenv("FI_WEBHOOKS_ENABLED", "false")
    monkeypatch.setattr(config, "is_hosted", lambda: False)

    def never(**_kw):
        raise AssertionError("no dispatcher is built while sending is off")

    monkeypatch.setattr(activities, "build_dispatcher", never)


def test_sending_off_idles_heartbeating_for_the_whole_run(monkeypatch):
    _off(monkeypatch)
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    beats = []
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *details: beats.append(details)
    t0 = time.monotonic()
    env.run(activities.dispatch_loop, 1, 2)
    took = time.monotonic() - t0
    assert (
        2.0 <= took < 5.0
    )  # not a hot loop: the workflow continues as new only after its run
    assert beats and beats[0] == (1,)


def test_sending_off_stops_when_cancelled(monkeypatch):
    _off(monkeypatch)
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    env = ActivityEnvironment()
    threading.Timer(0.3, env.cancel).start()
    t0 = time.monotonic()
    # Shielded: the idle loop ends at its next heartbeat, then Temporal's cancel is raised (never mid-pass).
    with pytest.raises(CancelledError):
        env.run(activities.dispatch_loop, 1, 600)
    assert time.monotonic() - t0 < 8.0


def test_boot_starts_no_loop_while_sending_is_off(monkeypatch):
    _off(monkeypatch)

    class Client:
        started = 0

        async def start_workflow(self, *_a, **_kw):
            Client.started += 1

    assert asyncio.run(boot.start_dispatchers(Client())) == 0
    assert Client.started == 0


def test_boot_starts_the_number_of_loops_the_operator_sets(monkeypatch):
    # WEBHOOKS_DISPATCHER_LOOPS (re-review should-fix 4): one workflow per loop, "use existing", on the webhooks queue.
    monkeypatch.delenv("FI_WEBHOOKS_ENABLED", raising=False)
    monkeypatch.setattr(config, "is_hosted", lambda: False)

    class Client:
        def __init__(self):
            self.started = []

        async def start_workflow(self, _run, loop, **kw):
            self.started.append(
                (loop, kw["id"], kw["task_queue"], kw["id_conflict_policy"].name)
            )

    monkeypatch.setenv("WEBHOOKS_DISPATCHER_LOOPS", "3")
    client = Client()
    assert asyncio.run(boot.start_dispatchers(client)) == 3
    assert client.started == [
        (n, f"webhooks-dispatcher-{n}", "webhooks", "USE_EXISTING") for n in (1, 2, 3)
    ]
    # Unset or not a whole number: 1 off hosted, 2 on hosted; never fewer than 1.
    for raw, off_hosted, on_hosted in (
        ("", 1, 2),
        ("0", 1, 1),
        ("two", 1, 2),
        ("-3", 1, 2),
        (" 5 ", 5, 5),
    ):
        monkeypatch.setenv("WEBHOOKS_DISPATCHER_LOOPS", raw)
        assert (
            config.dispatcher_loops(hosted=False),
            config.dispatcher_loops(hosted=True),
        ) == (off_hosted, on_hosted), raw
