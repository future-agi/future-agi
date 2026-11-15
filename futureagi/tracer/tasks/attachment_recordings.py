"""Scheduled attachment recording work: imports, derivatives and sweeps.

Feature-off is a no-op that touches nothing. When enabled, each tick first
proves readiness (sandbox, envelope key, private storage) and does a bounded
amount of idempotent work; jobs are durable rows, so a lost tick or worker
restart only delays progress.
"""

import structlog
from django.conf import settings

from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)

JOBS_PER_TICK = 2
DERIVATIVES_PER_TICK = 20
# Terminal segments taken out of the derivative queue per sweep (300 s).
DERIVATIVE_PURGE_PER_SWEEP = 1000


def _ready():
    from tracer.services.attachments import readiness
    from tracer.services.attachments.contract import AttachmentError

    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        return None
    try:
        return readiness.check()
    except AttachmentError as error:
        logger.warning("attachment_worker_not_ready", reason=error.code)
        return None


@temporal_activity(time_limit=3 * 3600, queue="tasks_xl", max_retries=0)
def run_attachment_recording_jobs():
    storage = _ready()
    if storage is None:
        return
    from tracer.services.attachments import derivatives, imports
    from tracer.services.attachments.contract import AttachmentError

    for job_id, scope_id in imports.pending(limit=JOBS_PER_TICK):
        try:
            state = imports.run(job_id, scope_id=scope_id, storage=storage)
        except Exception as error:  # noqa: BLE001 - one job never stalls the tick; the sweep cleans up
            # The type name only: exception text may carry a URL or a path.
            logger.warning(
                "attachment_import_tick_failed",
                job_id=str(job_id),
                error=type(error).__name__,
            )
            continue
        logger.info("attachment_import_tick", job_id=str(job_id), state=state)
    for segment_id, scope_id in derivatives.pending(limit=DERIVATIVES_PER_TICK):
        try:
            derivatives.generate(segment_id, scope_id=scope_id, storage=storage)
        except Exception as error:  # noqa: BLE001 - one segment never stalls the tick
            derivatives.defer(segment_id)
            reason = (
                error.code if isinstance(error, AttachmentError) else "internal_error"
            )
            logger.info("attachment_derivative_skipped", reason=reason)


@temporal_activity(time_limit=600, queue="tasks_xl", max_retries=0)
def sweep_attachment_recordings():
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        return
    from tracer.services.attachments import derivatives, envelopes, imports

    envelopes.sweep()
    # Bookkeeping only (no storage): finished or dead segments leave the queue.
    derivatives.purge(limit=DERIVATIVE_PURGE_PER_SWEEP)
    storage = _ready()
    if storage is not None:
        imports.sweep(storage=storage)
