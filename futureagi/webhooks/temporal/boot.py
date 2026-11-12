"""Start the dispatcher loops when a worker that polls ``webhooks`` boots (DESIGN.md section 13.1): workflow ids
``webhooks-dispatcher-<n>``, conflict policy "use existing", so a running loop is left alone. ``webhooks-health``
(WH-1b) calls the same function every 60 s."""

from __future__ import annotations

import logging

log = logging.getLogger("webhooks.boot")


async def start_dispatchers(client) -> int:
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

    from webhooks import config
    from webhooks.temporal import QUEUE
    from webhooks.temporal.workflows import (
        WebhooksDispatcherWorkflow,
        dispatcher_workflow_id,
    )

    blockers = config.send_blockers()
    if blockers:
        # Nothing to run: a loop would only idle. They start at the next boot, or from webhooks-health, once sending
        # is on and allowed.
        log.warning(
            "webhooks dispatchers not started: %s",
            "; ".join(blockers),
            extra={"metric": "webhooks.dispatcher.not_started"},
        )
        return 0
    n = config.dispatcher_loops()
    for i in range(1, n + 1):
        await client.start_workflow(
            WebhooksDispatcherWorkflow.run,
            i,
            id=dispatcher_workflow_id(i),
            task_queue=QUEUE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
    log.info(
        "webhooks dispatchers ensured",
        extra={"metric": "webhooks.dispatcher.ensured", "loops": n},
    )
    return n


async def start_dispatchers_safely(client) -> None:
    """Boot hook: a failure is logged, never fatal to the worker (the health schedule retries it)."""
    try:
        await start_dispatchers(client)
    except Exception:  # noqa: BLE001
        log.exception(
            "could not start the webhooks dispatchers",
            extra={"metric": "webhooks.dispatcher.boot_failed"},
        )


def is_hosted_install() -> bool:
    """For the worker command: whether this is the hosted install (its generic --all-queues worker then leaves the
    webhooks queue to the dedicated deployment behind the egress policy and NAT, section 6.4)."""
    from webhooks import config

    return config.is_hosted()


async def start_dispatchers_if_polled(client, queues_to_poll) -> bool:
    """Boot hook for ``manage.py start_temporal_worker`` (hosted helm and the docker entrypoint): start the loops when
    this worker polls the ``webhooks`` queue, single-queue or ``--all-queues``. Returns whether it tried; a failure is
    logged inside and never stops the worker."""
    from webhooks.temporal import QUEUE

    if QUEUE not in queues_to_poll:
        return False
    await start_dispatchers_safely(client)
    return True
