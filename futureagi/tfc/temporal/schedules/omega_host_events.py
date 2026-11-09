"""The Temporal schedule that drains the revocation outbox (READINESS lane 1.2a).

Every 3 s where Omega is configured: the drain picks up rows written by bulk
``QuerySet.update()`` paths, which fire no signal, and retries failed deliveries. 3 s
plus EE's 30 s stream re-check keeps a removal under L06's 35 s bound even when nothing
kicked the drain. Where Omega is not configured (the process that registers schedules
sees no delegation issuer), it runs hourly and only ages out captured rows.
"""

from temporalio.client import ScheduleOverlapPolicy

from accounts.host_events.config import DISABLED, load_config
from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

SCHEDULE_ID = "omega-host-events-drain"
INTERVAL_SECONDS = 3
IDLE_INTERVAL_SECONDS = 3600


# The drain is idempotent and retries on its own schedule, so Temporal must not re-run it.
@temporal_activity(time_limit=60, queue="tasks_s", max_retries=0)
def drain_omega_host_events():
    from accounts.host_events.relay import drain

    return drain(budget_seconds=20.0)


OMEGA_HOST_EVENT_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id=SCHEDULE_ID,
        activity_name="drain_omega_host_events",
        interval_seconds=(
            IDLE_INTERVAL_SECONDS
            if load_config().state == DISABLED
            else INTERVAL_SECONDS
        ),
        queue="tasks_s",
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        description="Send membership and user revocations to Omega EE (READINESS L06)",
    ),
]
