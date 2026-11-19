"""Read-only Temporal checks for the health sentinel management command."""

import asyncio
import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import requests
import structlog
from django.conf import settings
from django.core.cache import cache
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client, ScheduleInfo, ScheduleListInfo, ScheduleSpec

from tfc.temporal import TASK_QUEUES, TEMPORAL_HOST, TEMPORAL_NAMESPACE
from tfc.temporal.common.client import get_client
from tfc.utils.slack import send_critical_slack_notification

logger = structlog.get_logger(__name__)

POLLER_LIVENESS_WINDOW = timedelta(minutes=2)
STALENESS_INTERVAL_MULTIPLIER = 3
HEALTH_CHECK_TIMEOUT_SECONDS = 60
HEARTBEAT_TIMEOUT_SECONDS = 10
ALERT_REPEAT_SECONDS = 3600

_POLLED_TASK_QUEUE_TYPES = (
    TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
    TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
)


def get_sentinel_task_queues() -> list[str]:
    """Expected queues, with an optional deployment-specific complete override.

    TEMPORAL_HEALTH_TASK_QUEUES is a comma-separated list. Include
    simulation_runner only in deployments that provision that worker.
    """
    configured = os.getenv("TEMPORAL_HEALTH_TASK_QUEUES")
    if configured is not None:
        queues = [queue.strip() for queue in configured.split(",") if queue.strip()]
        if not queues:
            raise ValueError("TEMPORAL_HEALTH_TASK_QUEUES must contain a queue")
    else:
        queues = [
            settings.EXACT_AGGREGATION_TASK_QUEUE
            if queue == "exact_aggregation"
            else queue
            for queue in TASK_QUEUES.values()
        ]
    return list(dict.fromkeys(queues))


@dataclass
class HealthReport:
    """Outcome of one sentinel run; empty lists mean healthy."""

    dead_queues: list[str] = field(default_factory=list)
    paused_schedules: list[str] = field(default_factory=list)
    stale_schedules: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not (self.dead_queues or self.paused_schedules or self.stale_schedules)

    def to_slack_message(self) -> str:
        lines = ["HC-SENTINEL: Temporal health check found problems"]
        if self.dead_queues:
            lines.append(
                f"*Dead task queues (no live pollers):* {', '.join(sorted(self.dead_queues))}"
            )
        if self.paused_schedules:
            lines.append(
                f"*Paused schedules:* {', '.join(sorted(self.paused_schedules))}"
            )
        if self.stale_schedules:
            lines.append(
                f"*Stale schedules:* {', '.join(sorted(self.stale_schedules))}"
            )
        return "\n".join(lines)


async def a_queue_has_live_poller(client: Client, queue: str, now: datetime) -> bool:
    """True if any workflow or activity poller polled the queue recently."""
    for task_queue_type in _POLLED_TASK_QUEUE_TYPES:
        response = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=client.namespace,
                task_queue=TaskQueue(name=queue),
                task_queue_type=task_queue_type,
            )
        )
        for poller in response.pollers:
            if not poller.HasField("last_access_time"):
                continue
            last_access = poller.last_access_time.ToDatetime(tzinfo=UTC)
            if now - last_access <= POLLER_LIVENESS_WINDOW:
                return True
    return False


def _staleness_threshold(spec: ScheduleSpec, now: datetime) -> timedelta | None:
    # Temporal normalizes cron expressions into calendars. Neither calendars
    # nor exclusion windows have a reliable fixed interval to multiply.
    if spec.calendars or spec.cron_expressions or spec.skip:
        return None
    if (spec.start_at and now < spec.start_at) or (spec.end_at and now > spec.end_at):
        return None
    intervals = [
        interval.every for interval in spec.intervals if interval.every > timedelta(0)
    ]
    if not intervals:
        return None
    return min(intervals) * STALENESS_INTERVAL_MULTIPLIER + (
        spec.jitter or timedelta(0)
    )


def _last_action_time(info: ScheduleInfo | ScheduleListInfo) -> datetime | None:
    return max((action.started_at for action in info.recent_actions), default=None)


async def a_check_schedules(
    client: Client, now: datetime
) -> tuple[list[str], list[str]]:
    """Return paused IDs and confirmed stale interval schedules.

    Describe only missing/incomplete listings and stale candidates, to obtain
    creation time and active executions and confirm against fresh server state.
    Cron/calendar schedules are checked for paused state only.
    """
    paused: list[str] = []
    stale: list[str] = []
    async for entry in await client.list_schedules():
        if entry.schedule is not None:
            if entry.schedule.state.paused:
                paused.append(entry.id)
                continue
            threshold = _staleness_threshold(entry.schedule.spec, now)
            if threshold is None:
                continue
            last_run = _last_action_time(entry.info) if entry.info is not None else None
            if last_run is not None and now - last_run <= threshold:
                continue

        description = await client.get_schedule_handle(entry.id).describe()
        schedule, info = description.schedule, description.info
        if schedule.state.paused:
            paused.append(entry.id)
            continue
        threshold = _staleness_threshold(schedule.spec, now)
        if threshold is None or info.running_actions:
            continue
        if schedule.state.limited_actions and schedule.state.remaining_actions == 0:
            continue
        last_run = _last_action_time(info)
        reference = last_run or info.created_at
        if schedule.spec.start_at:
            reference = max(reference, schedule.spec.start_at)
        if now - reference > threshold:
            detail = (
                f"last run {last_run.isoformat()}" if last_run else "no recorded runs"
            )
            stale.append(f"{entry.id} ({detail})")
    return paused, stale


async def a_run_health_checks(
    client: Client, *, now: datetime | None = None
) -> HealthReport:
    """Run all sentinel checks against an already-connected client."""
    now = now or datetime.now(UTC)
    report = HealthReport()
    for queue in get_sentinel_task_queues():
        if not await a_queue_has_live_poller(client, queue, now):
            report.dead_queues.append(queue)
    report.paused_schedules, report.stale_schedules = await a_check_schedules(
        client, now
    )
    return report


def run_health_checks() -> HealthReport:
    """Bound connection and all checks so an unresponsive server also alerts."""

    async def _run() -> HealthReport:
        client = await get_client()
        return await a_run_health_checks(client)

    async def _bounded_run() -> HealthReport:
        return await asyncio.wait_for(_run(), timeout=HEALTH_CHECK_TIMEOUT_SECONDS)

    return asyncio.run(_bounded_run())


def send_health_alert(message: str) -> bool:
    """Suppress identical alerts for an hour using the shared Django cache.

    Cache failures fail open: notifying operators is more important than
    deduplication. Failed deliveries release the key so the next run retries.
    """
    digest = hashlib.sha256(
        f"{TEMPORAL_HOST}\0{TEMPORAL_NAMESPACE}\0{message}".encode()
    ).hexdigest()
    key = f"temporal-health-alert:{digest}"
    try:
        if not cache.add(key, True, timeout=ALERT_REPEAT_SECONDS):
            return True
    except Exception as exc:
        logger.warning("sentinel_alert_cache_failed", error_type=type(exc).__name__)

    sent = send_critical_slack_notification(message)
    if not sent:
        try:
            cache.delete(key)
        except Exception as exc:
            logger.warning("sentinel_alert_cache_failed", error_type=type(exc).__name__)
    return sent


def ping_heartbeat(url: str) -> bool:
    """Record a sentinel attempt without exposing URL secrets in error logs."""
    try:
        response = requests.get(url, timeout=HEARTBEAT_TIMEOUT_SECONDS)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        # Exception messages can contain the secret URL as well as the URL field.
        logger.warning("sentinel_heartbeat_failed", error_type=type(exc).__name__)
        return False
