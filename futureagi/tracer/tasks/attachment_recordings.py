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
    from tracer.models.attachment_recording import (
        AttachmentRecordingDerivative,
        AttachmentRecordingSegment,
        RecordingSegmentState,
    )
    from tracer.services.attachments import derivatives, imports
    from tracer.services.attachments.contract import AttachmentError

    for job_id, scope_id in imports.pending(limit=JOBS_PER_TICK):
        state = imports.run(job_id, scope_id=scope_id, storage=storage)
        logger.info("attachment_import_tick", job_id=str(job_id), state=state)
    done = AttachmentRecordingDerivative.objects.filter(
        published_at__isnull=False, representation="peaks"
    ).values("segment_id")
    for segment_id, scope_id in (
        AttachmentRecordingSegment.objects.filter(
            state=RecordingSegmentState.PUBLISHED
        )
        .exclude(pk__in=done)
        .values_list("pk", "scope_id")[:DERIVATIVES_PER_TICK]
    ):
        try:
            derivatives.generate(segment_id, scope_id=scope_id, storage=storage)
        except AttachmentError as error:
            logger.info("attachment_derivative_skipped", reason=error.code)


@temporal_activity(time_limit=600, queue="tasks_xl", max_retries=0)
def sweep_attachment_recordings():
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        return
    from tracer.services.attachments import envelopes, imports

    envelopes.sweep()
    storage = _ready()
    if storage is not None:
        imports.sweep(storage=storage)
