from temporalio.client import ScheduleOverlapPolicy

from tfc.deployment_telemetry.config import (
    get_telemetry_interval_hours,
    get_telemetry_jitter_seconds,
)
from tfc.deployment_telemetry.sender import run_telemetry_cycle
from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig


# ``max_retries=0`` is deliberate (and locked in by
# ``test_schedule_disables_temporal_retries``). The buffered heartbeat windows
# already give us at-least-once delivery: the next cycle replays anything that
# did not flush. Letting Temporal re-run the activity on its own would re-
# collect the counters (re-querying ClickHouse and Postgres) on top of the
# replay, which dogpiles under a receiver outage and can produce two heartbeat
# rows for the same window from one cycle. Leave at 0.
@temporal_activity(time_limit=60, queue="default", max_retries=0)
def send_deployment_telemetry_heartbeat():
    return run_telemetry_cycle()


_interval = get_telemetry_interval_hours() * 3600

DEPLOYMENT_TELEMETRY_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="deployment-telemetry-heartbeat",
        activity_name="send_deployment_telemetry_heartbeat",
        interval_seconds=_interval,
        jitter_seconds=get_telemetry_jitter_seconds(),
        queue="default",
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        description="Register self-hosted deployments and send usage telemetry",
    ),
]
