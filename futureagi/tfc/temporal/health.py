"""
Read-only Temporal health checks for the sentinel CronJob.

The ``check_temporal_health`` management command runs these against the
Temporal server and alerts via Slack. Nothing here mutates server state:
the checks only describe task queues and list schedules.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import requests
import structlog
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client

logger = structlog.get_logger(__name__)

# Queues not in TASK_QUEUES whose workers must still be polling.
EXTRA_TASK_QUEUES = ("simulation_runner",)

# Workers long-poll continuously; a queue with no poll in this window has
# no live worker behind it.
POLLER_LIVENESS_WINDOW = timedelta(minutes=2)

# A schedule is stale when its last run is older than 3x its interval, or
# older than this default when no interval can be derived (cron specs).
STALENESS_INTERVAL_MULTIPLIER = 3
DEFAULT_STALENESS_THRESHOLD = timedelta(hours=2)

HEARTBEAT_TIMEOUT_SECONDS = 10

# Workers poll workflow and activity task queues separately; either kind of
# live poller proves a worker is up for the queue.
_POLLED_TASK_QUEUE_TYPES = (
    TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
    TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
)


def get_sentinel_task_queues() -> list[str]:
    """All task queues the sentinel expects to have live pollers."""
    from tfc.temporal import TASK_QUEUES

    return [*TASK_QUEUES.values(), *EXTRA_TASK_QUEUES]


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
                f"*Dead task queues (no live pollers):* {', '.join(self.dead_queues)}"
            )
        if self.paused_schedules:
            lines.append(f"*Paused schedules:* {', '.join(self.paused_schedules)}")
        if self.stale_schedules:
            lines.append(f"*Stale schedules:* {', '.join(self.stale_schedules)}")
        return "\n".join(lines)


async def a_queue_has_live_poller(client: Client, queue: str, now: datetime) -> bool:
    """True if any workflow or activity poller polled ``queue`` recently."""
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


def _staleness_threshold(schedule) -> timedelta:
    """3x the schedule's shortest interval; default for cron/unknown specs."""
    spec = getattr(schedule, "spec", None)
    intervals = getattr(spec, "intervals", None) or []
    everies = [interval.every for interval in intervals if interval.every]
    if not everies:
        return DEFAULT_STALENESS_THRESHOLD
    return min(everies) * STALENESS_INTERVAL_MULTIPLIER


def _last_action_time(info) -> datetime | None:
    """Most recent run time recorded on a schedule listing, if any."""
    recent_actions = getattr(info, "recent_actions", None) or []
    times = [
        action.started_at or action.scheduled_at
        for action in recent_actions
        if action.started_at or action.scheduled_at
    ]
    return max(times) if times else None


async def a_check_schedules(
    client: Client, now: datetime
) -> tuple[list[str], list[str]]:
    """Return (paused schedule ids, stale schedule descriptions)."""
    paused: list[str] = []
    stale: list[str] = []

    async for entry in await client.list_schedules():
        state = getattr(entry.schedule, "state", None)
        if state is not None and state.paused:
            paused.append(entry.id)
            continue

        threshold = _staleness_threshold(entry.schedule)
        last_run = _last_action_time(entry.info)
        if last_run is None:
            # The listing can't show creation time, so a schedule with no
            # recorded runs is surfaced rather than silently skipped: a
            # schedule wedged since creation looks exactly like this.
            stale.append(f"{entry.id} (no recorded runs)")
        elif now - last_run > threshold:
            stale.append(f"{entry.id} (last run {last_run.isoformat()})")

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
    """Connect via the shared client helper and run all checks."""
    from tfc.temporal.common.client import get_client

    async def _run() -> HealthReport:
        client = await get_client()
        return await a_run_health_checks(client)

    return asyncio.run(_run())


def ping_heartbeat(url: str) -> bool:
    """GET the dead-man's-switch URL to record that the check ran."""
    try:
        response = requests.get(url, timeout=HEARTBEAT_TIMEOUT_SECONDS)
        response.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.warning("sentinel_heartbeat_failed", url=url, error=str(exc))
        return False


__all__ = [
    "HealthReport",
    "a_check_schedules",
    "a_queue_has_live_poller",
    "a_run_health_checks",
    "get_sentinel_task_queues",
    "ping_heartbeat",
    "run_health_checks",
]
