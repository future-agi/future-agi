from temporalio.client import ScheduleOverlapPolicy

from tfc.temporal.drop_in import temporal_activity
from tfc.temporal.schedules.config import ScheduleConfig

HEARTBEAT_INTERVAL_SECONDS = 24 * 3600
HEARTBEAT_JITTER_SECONDS = 30 * 60


@temporal_activity(time_limit=30, queue="default", max_retries=1)
def send_enterprise_heartbeat():
    try:
        from ee.licensing.heartbeat import send_heartbeat

        return send_heartbeat()
    except ImportError:
        return False


ENTERPRISE_HEARTBEAT_SCHEDULES: list[ScheduleConfig] = [
    ScheduleConfig(
        schedule_id="enterprise-license-heartbeat",
        activity_name="send_enterprise_heartbeat",
        interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
        jitter_seconds=HEARTBEAT_JITTER_SECONDS,
        queue="default",
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        description="Send enterprise license heartbeat to FutureAGI control plane",
    ),
]
