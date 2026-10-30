"""
Unit tests for the Temporal health sentinel.

Run with: pytest tfc/temporal/tests/test_health.py -v
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from structlog.testing import capture_logs
from temporalio.api.workflowservice.v1 import DescribeTaskQueueResponse
from temporalio.client import ScheduleIntervalSpec, ScheduleSpec

from tfc.temporal.health import (
    DEFAULT_STALENESS_THRESHOLD,
    POLLER_LIVENESS_WINDOW,
    a_run_health_checks,
    get_sentinel_task_queues,
    ping_heartbeat,
)

NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


def _describe_response(*last_access_times: datetime) -> DescribeTaskQueueResponse:
    """Build a DescribeTaskQueue response with one poller per access time."""
    response = DescribeTaskQueueResponse()
    for last_access in last_access_times:
        poller = response.pollers.add()
        poller.last_access_time.FromDatetime(last_access)
    return response


class _AsyncIterMock:
    """Minimal async iterator mocking ``client.list_schedules()``'s value."""

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _schedule_entry(
    schedule_id: str,
    *,
    paused: bool = False,
    interval: timedelta | None = timedelta(minutes=10),
    last_run: datetime | None = None,
):
    intervals = [ScheduleIntervalSpec(every=interval)] if interval else []
    recent_actions = (
        [SimpleNamespace(started_at=last_run, scheduled_at=None)] if last_run else []
    )
    return SimpleNamespace(
        id=schedule_id,
        schedule=SimpleNamespace(
            state=SimpleNamespace(paused=paused),
            spec=ScheduleSpec(intervals=intervals),
        ),
        info=SimpleNamespace(recent_actions=recent_actions),
    )


def _client(schedule_entries=(), dead_queues=()):
    """Mock client: live workflow pollers everywhere except ``dead_queues``."""
    client = MagicMock()
    client.namespace = "default"

    async def describe_task_queue(request):
        if request.task_queue.name in dead_queues:
            # One poller that went silent well before the liveness window.
            return _describe_response(NOW - POLLER_LIVENESS_WINDOW * 5)
        return _describe_response(NOW - timedelta(seconds=30))

    client.workflow_service.describe_task_queue = AsyncMock(
        side_effect=describe_task_queue
    )
    client.list_schedules = AsyncMock(return_value=_AsyncIterMock(schedule_entries))
    return client


class TestRunHealthChecks:
    """Tests for a_run_health_checks against a mocked Temporal client."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_all_healthy(self):
        client = _client(
            schedule_entries=[
                _schedule_entry("fresh", last_run=NOW - timedelta(minutes=5))
            ]
        )

        report = await a_run_health_checks(client, now=NOW)

        assert report.healthy is True
        assert report.dead_queues == []
        assert report.paused_schedules == []
        assert report.stale_schedules == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_covers_task_queues_and_simulation_runner(self):
        client = _client()

        await a_run_health_checks(client, now=NOW)

        described = {
            call.args[0].task_queue.name
            for call in client.workflow_service.describe_task_queue.await_args_list
        }
        assert described == set(get_sentinel_task_queues())
        assert "simulation_runner" in described

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_dead_queue_is_reported_after_checking_both_queue_types(self):
        client = _client(dead_queues={"trace_ingestion"})

        report = await a_run_health_checks(client, now=NOW)

        assert report.dead_queues == ["trace_ingestion"]
        assert report.healthy is False
        # A dead verdict requires both WORKFLOW and ACTIVITY poller lists.
        dead_types = {
            call.args[0].task_queue_type
            for call in client.workflow_service.describe_task_queue.await_args_list
            if call.args[0].task_queue.name == "trace_ingestion"
        }
        assert len(dead_types) == 2

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_queue_with_no_pollers_at_all_is_dead(self):
        client = _client()
        client.workflow_service.describe_task_queue = AsyncMock(
            return_value=_describe_response()
        )

        report = await a_run_health_checks(client, now=NOW)

        assert report.dead_queues == get_sentinel_task_queues()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_paused_schedule_is_reported(self):
        client = _client(
            schedule_entries=[
                _schedule_entry("paused-one", paused=True),
                _schedule_entry("running", last_run=NOW - timedelta(minutes=5)),
            ]
        )

        report = await a_run_health_checks(client, now=NOW)

        assert report.paused_schedules == ["paused-one"]
        assert report.stale_schedules == []
        assert report.healthy is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_stale_schedule_uses_three_times_its_interval(self):
        # 10 min interval -> stale after 30 min without a run.
        client = _client(
            schedule_entries=[
                _schedule_entry("stale", last_run=NOW - timedelta(minutes=31)),
                _schedule_entry("fresh", last_run=NOW - timedelta(minutes=29)),
            ]
        )

        report = await a_run_health_checks(client, now=NOW)

        assert len(report.stale_schedules) == 1
        assert report.stale_schedules[0].startswith("stale (last run ")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_schedule_without_interval_uses_default_threshold(self):
        # Cron-style specs have no intervals; the 2h default applies.
        inside = NOW - DEFAULT_STALENESS_THRESHOLD + timedelta(minutes=1)
        outside = NOW - DEFAULT_STALENESS_THRESHOLD - timedelta(minutes=1)
        client = _client(
            schedule_entries=[
                _schedule_entry("cron-fresh", interval=None, last_run=inside),
                _schedule_entry("cron-stale", interval=None, last_run=outside),
            ]
        )

        report = await a_run_health_checks(client, now=NOW)

        assert len(report.stale_schedules) == 1
        assert report.stale_schedules[0].startswith("cron-stale (last run ")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_schedule_with_no_recorded_runs_is_reported(self):
        client = _client(schedule_entries=[_schedule_entry("never-ran")])

        report = await a_run_health_checks(client, now=NOW)

        assert report.stale_schedules == ["never-ran (no recorded runs)"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_slack_message_lists_every_problem_under_one_prefix(self):
        client = _client(
            dead_queues={"tasks_xl"},
            schedule_entries=[
                _schedule_entry("paused-one", paused=True),
                _schedule_entry("stale", last_run=NOW - timedelta(hours=5)),
            ],
        )

        report = await a_run_health_checks(client, now=NOW)
        message = report.to_slack_message()

        assert message.startswith("HC-SENTINEL:")
        assert "tasks_xl" in message
        assert "paused-one" in message
        assert "stale" in message


class TestPingHeartbeat:
    """Tests for the dead-man's-switch ping."""

    @pytest.mark.unit
    @patch("tfc.temporal.health.requests.get")
    def test_successful_ping(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)

        assert ping_heartbeat("https://hc-ping.test/abc") is True
        mock_get.assert_called_once_with("https://hc-ping.test/abc", timeout=10)

    @pytest.mark.unit
    @patch("tfc.temporal.health.requests.get")
    def test_failed_ping_is_logged_not_raised(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("boom")

        with capture_logs() as records:
            result = ping_heartbeat("https://hc-ping.test/abc")

        assert result is False
        warnings = [r for r in records if r["log_level"] == "warning"]
        assert len(warnings) == 1
        assert warnings[0]["event"] == "sentinel_heartbeat_failed"


class TestCheckTemporalHealthCommand:
    """Tests for the thin management command wiring."""

    COMMAND_MODULE = "tfc.management.commands.check_temporal_health"

    def _report(self, **overrides):
        from tfc.temporal.health import HealthReport

        return HealthReport(**overrides)

    @pytest.mark.unit
    def test_healthy_run_sends_no_alert_but_pings_heartbeat(self):
        with (
            patch(
                f"{self.COMMAND_MODULE}.run_health_checks",
                return_value=self._report(),
            ),
            patch(
                f"{self.COMMAND_MODULE}.send_critical_slack_notification"
            ) as mock_slack,
            patch(f"{self.COMMAND_MODULE}.ping_heartbeat") as mock_ping,
        ):
            call_command(
                "check_temporal_health", heartbeat_url="https://hc-ping.test/abc"
            )

        mock_slack.assert_not_called()
        mock_ping.assert_called_once_with("https://hc-ping.test/abc")

    @pytest.mark.unit
    def test_unhealthy_run_alerts_pings_heartbeat_and_exits_nonzero(self):
        report = self._report(dead_queues=["tasks_xl"])
        with (
            patch(f"{self.COMMAND_MODULE}.run_health_checks", return_value=report),
            patch(
                f"{self.COMMAND_MODULE}.send_critical_slack_notification"
            ) as mock_slack,
            patch(f"{self.COMMAND_MODULE}.ping_heartbeat") as mock_ping,
        ):
            with pytest.raises(CommandError, match="HC-SENTINEL"):
                call_command(
                    "check_temporal_health",
                    heartbeat_url="https://hc-ping.test/abc",
                )

        mock_slack.assert_called_once_with(report.to_slack_message())
        # The heartbeat means "the check ran", so an unhealthy result pings too.
        mock_ping.assert_called_once_with("https://hc-ping.test/abc")

    @pytest.mark.unit
    def test_heartbeat_is_skipped_without_url(self):
        with (
            patch(
                f"{self.COMMAND_MODULE}.run_health_checks",
                return_value=self._report(),
            ),
            patch(f"{self.COMMAND_MODULE}.ping_heartbeat") as mock_ping,
        ):
            call_command("check_temporal_health")

        mock_ping.assert_not_called()
