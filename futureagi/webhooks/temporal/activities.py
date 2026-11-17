"""The dispatcher activity and the boot-time starter (DESIGN.md section 13.1).

``dispatch_loop`` is a sync activity (FA's worker runs those on its thread pool). It wires the core
:class:`~webhooks.core.dispatch.Dispatcher` to the hosted adapters, heartbeats every pass (at most ~1 s apart, inside
the 30 s heartbeat timeout) and stops when Temporal cancels it.

When sending is off (``FI_WEBHOOKS_ENABLED=false``) or not allowed on this install yet (the hosted gates in
:func:`webhooks.config.send_blockers`), it waits out its ``run_for_s``, heartbeating, and returns: the workflow then
continues as new every 10 minutes instead of in a hot loop, and events keep queueing.

Both paths run under ``activity.shield_thread_cancel_exception()``: Temporal's cancel is seen at the next heartbeat
(``stop``), the senders drain, and only then is ``CancelledError`` raised, never in the middle of a pass.
"""

from __future__ import annotations

import logging
import threading

from django.db import close_old_connections
from temporalio import activity

from webhooks import beats, config
from webhooks.core.attempt import Sender
from webhooks.core.dispatch import Dispatcher, idle
from webhooks.queue import PgQueue
from webhooks.store import OrmStore
from webhooks.temporal.workflows import dispatcher_workflow_id
from webhooks.transport import (
    PinnedTransport,
    SystemClock,
    SystemResolver,
    hosted_address_guard,
)

log = logging.getLogger("webhooks.dispatch")


def build_dispatcher(heartbeat=lambda: None) -> Dispatcher:
    hosted = config.is_hosted()
    clock = SystemClock()
    sender = Sender(
        profile=config.profile(hosted=hosted),
        public_suffix=config.public_suffix,
        resolver=SystemResolver(),
        transport=PinnedTransport(extra_ca_file=config.extra_ca_file(hosted=hosted)),
        clock=clock,
        address_guard=hosted_address_guard if hosted else None,
    )
    scale = config.time_scale(hosted=hosted)
    return Dispatcher(
        store=OrmStore(scale=scale),
        sender=sender,
        clock=clock,
        queue=PgQueue(config.listen_dsn()),
        scale=scale,
        heartbeat=heartbeat,
        after_send=close_old_connections,
    )


@activity.defn(name="webhooks_dispatch_loop")
def dispatch_loop(loop: int, run_for_s: int) -> None:
    stop = threading.Event()
    record_beat = beats.Throttled(dispatcher_workflow_id(loop))
    busy = {"normal": 0, "slow": 0}

    def beat() -> None:
        close_old_connections()
        activity.heartbeat(loop)
        if activity.is_cancelled():
            stop.set()
        try:
            # The loop's heartbeat in the database, for WA2 and the slots gauge (every 10 s).
            record_beat(d.busy() if d is not None else busy)
        except Exception as exc:  # noqa: BLE001 - never stops the loop; WA2 fires if it keeps failing
            log.warning(
                "webhooks dispatcher beat not recorded",
                extra={
                    "metric": "webhooks.beat.failed",
                    "error_type": type(exc).__name__,
                },
            )

    d = None

    blockers = config.send_blockers()
    if blockers:
        level = (
            logging.WARNING
            if blockers == ["FI_WEBHOOKS_ENABLED=false"]
            else logging.ERROR
        )
        log.log(
            level,
            "webhooks dispatcher idle; events still queue: %s",
            "; ".join(blockers),
            extra={"metric": "webhooks.dispatcher.idle", "loop": loop},
        )
        with activity.shield_thread_cancel_exception():
            idle(stop, run_for_s, beat)
        return

    d = build_dispatcher(heartbeat=beat)
    log.info(
        "dispatcher loop started",
        extra={"metric": "webhooks.dispatcher.started", "loop": loop},
    )
    # A sync activity gets CancelledError thrown into its thread at any instruction; shielded, the loop sees the
    # cancel at its next heartbeat instead, stops, drains its senders, and the cancel is raised after that.
    with activity.shield_thread_cancel_exception():
        d.run(stop, run_for_s=run_for_s)


@activity.defn(name="webhooks_ensure_dispatchers")
async def ensure_dispatchers() -> int:
    """Start every ``webhooks-dispatcher-<n>`` that is not running (``webhooks-health`` calls this every 60 s)."""
    from tfc.temporal.common.client import get_client
    from webhooks.temporal.boot import start_dispatchers

    return await start_dispatchers(await get_client())
