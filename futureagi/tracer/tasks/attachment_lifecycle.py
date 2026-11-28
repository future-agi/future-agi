"""Scheduled attachment lifecycle: outbox drain, sweeps and the orphan sweep.

Each activity is one bounded, idempotent pass of
``tracer.services.attachments.lifecycle``; a failed pass is simply retried by
the next tick. Counts and reason codes only: no identifiers, references or
payloads are logged.
"""

import structlog
from django.conf import settings

from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)


def _disabled():
    return not getattr(settings, "FI_ATTACHMENTS_ENABLED", False)


@temporal_activity(time_limit=240, queue="tasks_s", max_retries=0)
def drain_attachment_outbox():
    if _disabled():
        return {"disabled": True}
    from tracer.services.attachments.lifecycle import drain_outbox

    counts = drain_outbox()
    logger.info("attachment_outbox_drain", **counts)
    return counts


@temporal_activity(time_limit=240, queue="tasks_s", max_retries=0)
def sweep_attachment_lifecycle():
    if _disabled():
        return {"disabled": True}
    from tracer.services.attachments.lifecycle import sweep

    counts = sweep()
    logger.info("attachment_lifecycle_sweep", **counts)
    return counts


@temporal_activity(time_limit=240, queue="tasks_s", max_retries=0)
def sweep_attachment_orphans():
    if _disabled():
        return {"disabled": True}
    from tracer.services.attachments.lifecycle import sweep_orphans

    counts = sweep_orphans()
    logger.info("attachment_orphan_sweep", **counts)
    return counts
