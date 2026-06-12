from temporalio.client import ScheduleOverlapPolicy

from tfc.deployment_telemetry.config import (
    get_telemetry_interval_hours,
    get_telemetry_interval_seconds_override,
    get_telemetry_jitter_seconds,
)
from tfc.deployment_telemetry.sender import run_telemetry_cycle
from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig


@temporal_activity(time_limit=60, queue="default", max_retries=0)
def send_deployment_telemetry_heartbeat():
    return run_telemetry_cycle()


_interval = get_telemetry_interval_seconds_override() or (
    get_telemetry_interval_hours() * 3600
)

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
