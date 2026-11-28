"""Scheduled binding of uploaded attachment occurrences to persisted spans.

An occurrence becomes readable only after this tick finds its exact parent
span live in ClickHouse, referencing it, with known retention
(``tracer.services.attachments.control_plane``). Each tick is bounded and
idempotent; a failed tick is simply retried by the next one. Counts only: no
identifiers, references or payloads are logged.
"""

import structlog
from django.conf import settings

from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)


@temporal_activity(time_limit=120, queue="tasks_s", max_retries=0)
def reconcile_attachment_bindings():
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        return {"disabled": True}
    from tracer.services.attachments.control_plane import reconcile_tick

    counts = reconcile_tick()
    logger.info("attachment_binding_tick", **counts)
    return counts
