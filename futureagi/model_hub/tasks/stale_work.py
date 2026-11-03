"""Scheduled recovery of work abandoned mid-run (``recover-stale-work``).

Report-only unless ``STALE_WORK_RECOVERY_APPLY`` is on: the tick then logs what
it would close and what it leaves alone, and changes nothing.
"""

import structlog
from django.conf import settings

from model_hub.services.stale_work import recover_stale_work
from tfc.temporal.drop_in import temporal_activity

logger = structlog.get_logger(__name__)

# Per kind and tick; an hourly tick sees at most the work one deploy abandoned.
STALE_WORK_BATCH_SIZE = 500


@temporal_activity(time_limit=900, max_retries=0, queue="default")
def recover_stale_work_activity() -> dict:
    apply = settings.STALE_WORK_RECOVERY_APPLY
    report = recover_stale_work(apply=apply, batch_size=STALE_WORK_BATCH_SIZE)
    counts = report.counts()
    result = {
        "mode": "apply" if apply else "report_only",
        # Report-only ticks close nothing; say so in the key a reader greps.
        "recovered" if apply else "would_recover": counts["recovered"],
        "excluded": counts["excluded"],
        "skipped": counts["skipped"],
    }
    logger.info("stale_work_recovery_tick", **result)
    return result
