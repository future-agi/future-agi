"""Console batch schedule for the agentcc gateway.

Every minute, ask the gateway about each console batch not yet reported:
the first terminal status sends batch.completed once and revokes the batch's
minted key, and a minted key past its 24 h lifetime is revoked. Registered by
the ``*AGENTCC_BATCH_SCHEDULES,`` line in ``ALL_SCHEDULES``.
"""

import structlog

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

logger = structlog.get_logger(__name__)


@temporal_activity(time_limit=300, queue="default")
def poll_agentcc_batches_activity():
    """Poll the open console batches (agentcc.services.batch_jobs)."""
    from agentcc.services.batch_jobs import poll_open_batches

    result = poll_open_batches()
    logger.info("agentcc_batches_activity_completed", **result)
    return result


AGENTCC_BATCH_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="agentcc-batches",
        activity_name="poll_agentcc_batches_activity",
        interval_seconds=60,
        queue="default",
        description=(
            "Report finished console batches (batch.completed, once) and revoke "
            "their keys; revoke batch keys past 24 h (every minute)"
        ),
    ),
]
