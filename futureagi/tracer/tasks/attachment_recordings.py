"""Bounded durable attachment work, selected before per-owner prerequisites.

Feature-off touches neither settings-derived storage nor source/decoder keys.
One unavailable binding never suppresses the next selected item or metadata GC.
"""

import structlog
from django.conf import settings

from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)
JOBS_PER_TICK = 2
DERIVATIVES_PER_TICK = 20
DERIVATIVE_PURGE_PER_SWEEP = 1000


def _isolated(operation, **kwargs):
    try:
        return operation(**kwargs)
    except Exception:  # noqa: BLE001 - independent scheduled work; no raw errors
        logger.warning(
            "attachment_scheduled_work_deferred", operation=operation.__name__
        )
        return None


@temporal_activity(time_limit=3 * 3600, queue="tasks_xl", max_retries=0)
def run_attachment_recording_jobs():
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        return
    from tracer.services.attachments import derivatives, imports, provider_recordings

    # Provider callbacks may have committed COMPLETE before dispatch crashed.
    # Each provider operation selects indexed fixed-ID work, never starts Egress.
    _isolated(provider_recordings.resume_pending_completions, limit=JOBS_PER_TICK)
    _isolated(provider_recordings.reconcile_registered, limit=JOBS_PER_TICK)
    _isolated(provider_recordings.dispatch_completed, limit=JOBS_PER_TICK)
    for job_id, scope_id in imports.pending(limit=JOBS_PER_TICK):
        try:
            imports.run(job_id, scope_id=scope_id)
        except Exception:  # noqa: BLE001 - binding/source failure isolated per job
            logger.warning("attachment_import_tick_deferred", job_id=str(job_id))
    for derivative_id, scope_id in derivatives.pending(limit=DERIVATIVES_PER_TICK):
        try:
            derivatives.generate(derivative_id, scope_id=scope_id)
        except Exception:  # noqa: BLE001 - independent derivative owner
            _isolated(derivatives.defer, derivative_id=derivative_id)
            logger.warning(
                "attachment_derivative_tick_deferred", derivative_id=str(derivative_id)
            )


@temporal_activity(time_limit=600, queue="tasks_xl", max_retries=0)
def sweep_attachment_recordings():
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        return
    from tracer.services.attachments import (
        derivatives,
        envelopes,
        imports,
        provider_recordings,
    )

    _isolated(provider_recordings.sweep_pending_completions, limit=500)
    _isolated(envelopes.sweep)
    _isolated(derivatives.purge, limit=DERIVATIVE_PURGE_PER_SWEEP)
    _isolated(imports.sweep)
