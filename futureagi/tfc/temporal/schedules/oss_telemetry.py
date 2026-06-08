from temporalio.client import ScheduleOverlapPolicy

from tfc.oss_telemetry.config import (
    get_telemetry_interval_hours,
    get_telemetry_jitter_seconds,
)
from tfc.oss_telemetry.sender import run_telemetry_cycle
from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig


@temporal_activity(time_limit=60, queue="default", max_retries=0)
def send_oss_telemetry_heartbeat():
    return run_telemetry_cycle()


OSS_TELEMETRY_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="oss-telemetry-heartbeat",
        activity_name="send_oss_telemetry_heartbeat",
        interval_seconds=get_telemetry_interval_hours() * 3600,
        jitter_seconds=get_telemetry_jitter_seconds(),
        queue="default",
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        description="Register OSS instances and send fixed-window usage telemetry",
    ),
]
