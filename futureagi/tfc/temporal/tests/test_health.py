"""Regression tests for Temporal health checks, alerts, and command failures."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from structlog.testing import capture_logs
from temporalio.api.schedule.v1 import ScheduleListEntry
from temporalio.api.workflowservice.v1 import (
    DescribeScheduleResponse,
    DescribeTaskQueueResponse,
)
from temporalio.client import (
    Schedule,
    ScheduleActionExecutionStartWorkflow,
    ScheduleActionResult,
    ScheduleActionStartWorkflow,
    ScheduleCalendarSpec,
    ScheduleDescription,
    ScheduleInfo,
    ScheduleIntervalSpec,
    ScheduleListActionStartWorkflow,
    ScheduleListDescription,
    ScheduleListInfo,
    ScheduleListSchedule,
    ScheduleListState,
    ScheduleRange,
    ScheduleSpec,
    ScheduleState,
)
from temporalio.common import TypedSearchAttributes
from temporalio.converter import DataConverter

from tfc.temporal import TASK_QUEUES
from tfc.temporal.health import (
    ALERT_REPEAT_SECONDS,
    POLLER_LIVENESS_WINDOW,
    HealthReport,
    a_check_schedules,
    a_run_health_checks,
    get_sentinel_task_queues,
    ping_heartbeat,
    run_health_checks,
    send_health_alert,
)

pytestmark = pytest.mark.unit
NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
HEALTH_MODULE = "tfc.temporal.health"
COMMAND_MODULE = "tfc.management.commands.check_temporal_health"


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch, settings):
    monkeypatch.delenv("TEMPORAL_HEALTH_TASK_QUEUES", raising=False)
    settings.EXACT_AGGREGATION_TASK_QUEUE = "tasks_xl"
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "sentinel-tests",
        }
    }
    cache.clear()
    yield
    cache.clear()


def _describe_response(*last_access_times):
    response = DescribeTaskQueueResponse()
    for last_access in last_access_times:
        response.pollers.add().last_access_time.FromDatetime(last_access)
    return response


def _schedule(
    schedule_id="schedule",
    *,
    spec=None,
    last_run=None,
    created_at=None,
    paused=False,
    running=False,
    limited_actions=False,
    remaining_actions=0,
):
    """Use SDK models so fake attribute names cannot conceal SDK mismatches."""
    spec = spec or ScheduleSpec(
        intervals=[ScheduleIntervalSpec(every=timedelta(minutes=10))]
    )
    execution = ScheduleActionExecutionStartWorkflow("workflow-id", "run-id")
    recent = [ScheduleActionResult(last_run, last_run, execution)] if last_run else []
    common = {
        "id": schedule_id,
        "typed_search_attributes": TypedSearchAttributes.empty,
        "search_attributes": {},
        "data_converter": DataConverter.default,
    }
    entry = ScheduleListDescription(
        **common,
        schedule=ScheduleListSchedule(
            action=ScheduleListActionStartWorkflow("workflow"),
            spec=spec,
            state=ScheduleListState(note=None, paused=paused),
        ),
        info=ScheduleListInfo(recent_actions=recent, next_action_times=[]),
        raw_entry=ScheduleListEntry(),
    )
    description = ScheduleDescription(
        **common,
        schedule=Schedule(
            action=ScheduleActionStartWorkflow(
                "workflow", id="workflow-id", task_queue="default"
            ),
            spec=spec,
            state=ScheduleState(
                paused=paused,
                limited_actions=limited_actions,
                remaining_actions=remaining_actions,
            ),
        ),
        info=ScheduleInfo(
            num_actions=len(recent),
            num_actions_missed_catchup_window=0,
            num_actions_skipped_overlap=0,
            running_actions=[execution] if running else [],
            recent_actions=recent,
            next_action_times=[],
            created_at=created_at or NOW - timedelta(days=2),
            last_updated_at=None,
        ),
        raw_description=DescribeScheduleResponse(),
    )
    return entry, description


def _client(*schedules, dead_queues=()):
    client = MagicMock()
    client.namespace = "test-namespace"

    async def describe_queue(request):
        age = (
            POLLER_LIVENESS_WINDOW * 5
            if request.task_queue.name in dead_queues
            else timedelta(seconds=30)
        )
        return _describe_response(NOW - age)

    async def entries():
        for entry, _ in schedules:
            yield entry

    async def listing():
        return entries()

    handles = {
        entry.id: MagicMock(describe=AsyncMock(return_value=description))
        for entry, description in schedules
    }
    client.workflow_service.describe_task_queue = AsyncMock(side_effect=describe_queue)
    client.list_schedules = AsyncMock(side_effect=listing)
    client.get_schedule_handle.side_effect = handles.__getitem__
    return client


class TestQueues:
    def test_defaults_follow_exact_aggregation_fallback(self):
        queues = get_sentinel_task_queues()
        assert set(queues) == set(TASK_QUEUES.values()) - {"exact_aggregation"}
        assert queues.count("tasks_xl") == 1
        assert "simulation_runner" not in queues

    def test_dedicated_exact_aggregation(self, settings):
        settings.EXACT_AGGREGATION_TASK_QUEUE = "exact_aggregation"
        assert "exact_aggregation" in get_sentinel_task_queues()

    def test_custom_exact_aggregation_queue(self, settings):
        settings.EXACT_AGGREGATION_TASK_QUEUE = "dedicated-analytics"
        queues = get_sentinel_task_queues()
        assert "dedicated-analytics" in queues
        assert "exact_aggregation" not in queues

    def test_explicit_deployment_queues_include_simulation(self, monkeypatch):
        monkeypatch.setenv(
            "TEMPORAL_HEALTH_TASK_QUEUES", " default, simulation_runner,default, "
        )
        assert get_sentinel_task_queues() == ["default", "simulation_runner"]

    @pytest.mark.parametrize("configured", ["", " , , "])
    def test_empty_configuration_fails(self, monkeypatch, configured):
        monkeypatch.setenv("TEMPORAL_HEALTH_TASK_QUEUES", configured)
        with pytest.raises(ValueError, match="must contain a queue"):
            get_sentinel_task_queues()

    async def test_all_healthy_and_expected_queues_checked(self):
        client = _client(_schedule(last_run=NOW - timedelta(minutes=5)))
        report = await a_run_health_checks(client, now=NOW)
        assert report == HealthReport()
        calls = client.workflow_service.describe_task_queue.await_args_list
        assert {call.args[0].task_queue.name for call in calls} == set(
            get_sentinel_task_queues()
        )
        assert {call.args[0].namespace for call in calls} == {"test-namespace"}
        client.get_schedule_handle.assert_not_called()

    async def test_dead_queue_checks_both_types(self):
        client = _client(dead_queues={"trace_ingestion"})
        report = await a_run_health_checks(client, now=NOW)
        assert report.dead_queues == ["trace_ingestion"]
        assert not report.healthy
        calls = client.workflow_service.describe_task_queue.await_args_list
        assert {
            call.args[0].task_queue_type
            for call in calls
            if call.args[0].task_queue.name == "trace_ingestion"
        } == {1, 2}

    async def test_only_activity_poller_is_live(self, monkeypatch):
        monkeypatch.setenv("TEMPORAL_HEALTH_TASK_QUEUES", "activity-only")
        client = _client()
        client.workflow_service.describe_task_queue.side_effect = [
            _describe_response(),
            _describe_response(NOW),
        ]
        assert (await a_run_health_checks(client, now=NOW)).healthy

    @pytest.mark.parametrize("missing_timestamp", [False, True])
    async def test_no_valid_pollers_is_dead(self, missing_timestamp):
        response = _describe_response()
        if missing_timestamp:
            response.pollers.add()
        client = _client()
        client.workflow_service.describe_task_queue.side_effect = None
        client.workflow_service.describe_task_queue.return_value = response
        assert (
            await a_run_health_checks(client, now=NOW)
        ).dead_queues == get_sentinel_task_queues()

    async def test_liveness_window_boundary(self, monkeypatch):
        monkeypatch.setenv("TEMPORAL_HEALTH_TASK_QUEUES", "default")
        client = _client()
        client.workflow_service.describe_task_queue.side_effect = None
        client.workflow_service.describe_task_queue.return_value = _describe_response(
            NOW - POLLER_LIVENESS_WINDOW
        )
        assert (await a_run_health_checks(client, now=NOW)).healthy


class TestSchedules:
    async def test_paused_is_reported_without_describing(self):
        client = _client(_schedule("paused", paused=True))
        assert await a_check_schedules(client, NOW) == (["paused"], [])
        client.get_schedule_handle.assert_not_called()

    @pytest.mark.parametrize("age,stale", [(29, False), (30, False), (31, True)])
    async def test_interval_staleness_boundary(self, age, stale):
        client = _client(_schedule(last_run=NOW - timedelta(minutes=age)))
        paused, problems = await a_check_schedules(client, NOW)
        assert paused == []
        assert bool(problems) is stale
        assert client.get_schedule_handle.call_count == int(stale)

    @pytest.mark.parametrize(
        "spec",
        [
            ScheduleSpec(cron_expressions=["0 0 1 * *"]),
            ScheduleSpec(cron_expressions=["30 2 * * *"]),
            ScheduleSpec(
                calendars=[ScheduleCalendarSpec(day_of_month=[ScheduleRange(1)])]
            ),
            ScheduleSpec(
                calendars=[
                    ScheduleCalendarSpec(
                        hour=[ScheduleRange(2)], minute=[ScheduleRange(30)]
                    )
                ]
            ),
            ScheduleSpec(),
        ],
    )
    @pytest.mark.parametrize("never_ran", [False, True])
    async def test_cron_and_normalized_calendars_do_not_report_stale(
        self, spec, never_ran
    ):
        client = _client(
            _schedule(
                spec=spec, last_run=None if never_ran else NOW - timedelta(days=28)
            )
        )
        assert await a_check_schedules(client, NOW) == ([], [])
        client.get_schedule_handle.assert_not_called()

    async def test_paused_cron_is_still_reported(self):
        client = _client(
            _schedule(
                "billing",
                paused=True,
                spec=ScheduleSpec(cron_expressions=["0 0 1 * *"]),
            )
        )
        assert await a_check_schedules(client, NOW) == (["billing"], [])

    @pytest.mark.parametrize("age,stale", [(0, False), (30, False), (31, True)])
    async def test_never_run_schedule_uses_sdk_creation_time(self, age, stale):
        client = _client(_schedule(created_at=NOW - timedelta(minutes=age)))
        assert await a_check_schedules(client, NOW) == (
            [],
            ["schedule (no recorded runs)"] if stale else [],
        )
        client.get_schedule_handle.assert_called_once_with("schedule")

    async def test_running_supervisor_is_not_stale(self):
        client = _client(
            _schedule(
                "gcp-marketplace-consumer",
                running=True,
                last_run=NOW - timedelta(days=1),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=timedelta(minutes=5))]
                ),
            )
        )
        assert await a_check_schedules(client, NOW) == ([], [])
        client.get_schedule_handle.assert_called_once_with("gcp-marketplace-consumer")

    async def test_stale_listing_is_confirmed_against_fresh_description(self):
        entry, description = _schedule(last_run=NOW - timedelta(hours=1))
        description.info.recent_actions = _schedule(last_run=NOW)[1].info.recent_actions
        client = _client((entry, description))
        assert await a_check_schedules(client, NOW) == ([], [])

    async def test_schedule_paused_since_listing(self):
        entry, description = _schedule(last_run=NOW - timedelta(hours=1))
        description.schedule.state.paused = True
        assert await a_check_schedules(_client((entry, description)), NOW) == (
            ["schedule"],
            [],
        )

    @pytest.mark.parametrize("paused", [True, False])
    async def test_legacy_listing_missing_details_is_described(self, paused):
        entry, description = _schedule(paused=paused, last_run=NOW)
        entry.schedule = None
        entry.info = None
        assert await a_check_schedules(_client((entry, description)), NOW) == (
            ["schedule"] if paused else [],
            [],
        )

    async def test_missing_listing_info_is_described(self):
        entry, description = _schedule(last_run=NOW)
        entry.info = None
        assert await a_check_schedules(_client((entry, description)), NOW) == ([], [])

    async def test_exhausted_finite_schedule_is_not_stale(self):
        client = _client(
            _schedule(
                limited_actions=True,
                remaining_actions=0,
                last_run=NOW - timedelta(days=1),
            )
        )
        assert await a_check_schedules(client, NOW) == ([], [])

    @pytest.mark.parametrize(
        "changes",
        [
            {"start_at": NOW + timedelta(hours=1)},
            {"end_at": NOW - timedelta(hours=1)},
            {"skip": [ScheduleCalendarSpec()]},
        ],
    )
    async def test_restricted_schedule_is_not_stale(self, changes):
        spec = ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(minutes=10))], **changes
        )
        client = _client(_schedule(spec=spec))
        assert await a_check_schedules(client, NOW) == ([], [])
        client.get_schedule_handle.assert_not_called()

    async def test_recent_start_at_gets_grace(self):
        spec = ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(minutes=10))],
            start_at=NOW - timedelta(minutes=5),
        )
        assert await a_check_schedules(_client(_schedule(spec=spec)), NOW) == ([], [])

    async def test_jitter_is_included_in_grace(self):
        spec = ScheduleSpec(
            intervals=[ScheduleIntervalSpec(every=timedelta(minutes=10))],
            jitter=timedelta(minutes=5),
        )
        client = _client(_schedule(spec=spec, last_run=NOW - timedelta(minutes=34)))
        assert await a_check_schedules(client, NOW) == ([], [])

    async def test_describe_failure_is_not_reported_as_healthy(self):
        client = _client(_schedule())
        client.get_schedule_handle("schedule").describe.side_effect = RuntimeError(
            "unavailable"
        )
        with pytest.raises(RuntimeError, match="unavailable"):
            await a_check_schedules(client, NOW)


def test_sync_wrapper_connects_and_runs_checks():
    client = _client()
    with patch(f"{HEALTH_MODULE}.get_client", new=AsyncMock(return_value=client)):
        report = run_health_checks()
    assert isinstance(report, HealthReport)
    client.list_schedules.assert_awaited_once()


def test_sync_wrapper_times_out_a_stuck_connection(monkeypatch):
    async def never_connects():
        await asyncio.Event().wait()

    monkeypatch.setattr(f"{HEALTH_MODULE}.HEALTH_CHECK_TIMEOUT_SECONDS", 0.01)
    with patch(f"{HEALTH_MODULE}.get_client", side_effect=never_connects):
        with pytest.raises(TimeoutError):
            run_health_checks()


class TestAlerts:
    def test_consolidated_message_is_stable(self):
        first = HealthReport(
            dead_queues=["b", "a"], paused_schedules=["p"], stale_schedules=["s"]
        )
        second = HealthReport(
            dead_queues=["a", "b"], paused_schedules=["p"], stale_schedules=["s"]
        )
        assert first.to_slack_message() == second.to_slack_message()
        assert "a, b" in first.to_slack_message()
        assert "*Paused schedules:* p" in first.to_slack_message()
        assert "*Stale schedules:* s" in first.to_slack_message()

    def test_repeat_suppressed_but_changed_problem_posts(self):
        with patch(
            f"{HEALTH_MODULE}.send_critical_slack_notification", return_value=True
        ) as send:
            assert send_health_alert("first problem")
            assert send_health_alert("first problem")
            assert send_health_alert("second problem")
        assert send.call_count == 2

    def test_dedup_expiry(self):
        with (
            patch(
                "django.core.cache.backends.locmem.time.time", return_value=1000
            ) as now,
            patch(
                f"{HEALTH_MODULE}.send_critical_slack_notification", return_value=True
            ) as send,
        ):
            send_health_alert("same problem")
            now.return_value += ALERT_REPEAT_SECONDS - 1
            send_health_alert("same problem")
            assert send.call_count == 1
            now.return_value += 2
            send_health_alert("same problem")
            assert send.call_count == 2

    def test_failed_send_retries(self):
        with patch(
            f"{HEALTH_MODULE}.send_critical_slack_notification",
            side_effect=[False, True],
        ) as send:
            assert not send_health_alert("problem")
            assert send_health_alert("problem")
        assert send.call_count == 2

    def test_cache_failure_still_sends(self):
        with (
            patch(
                f"{HEALTH_MODULE}.cache.add",
                side_effect=ConnectionError("redis unavailable"),
            ),
            patch(
                f"{HEALTH_MODULE}.send_critical_slack_notification", return_value=True
            ) as send,
            capture_logs() as logs,
        ):
            assert send_health_alert("problem")
        send.assert_called_once_with("problem")
        assert logs[0]["event"] == "sentinel_alert_cache_failed"

    def test_delete_failure_does_not_mask_failed_delivery(self):
        with (
            patch(f"{HEALTH_MODULE}.cache.delete", side_effect=ConnectionError),
            patch(
                f"{HEALTH_MODULE}.send_critical_slack_notification", return_value=False
            ),
            capture_logs() as logs,
        ):
            assert not send_health_alert("problem")
        assert logs[0]["event"] == "sentinel_alert_cache_failed"

    def test_dedup_scoped_to_temporal_namespace(self):
        with patch(
            f"{HEALTH_MODULE}.send_critical_slack_notification", return_value=True
        ) as send:
            send_health_alert("problem")
            with patch(f"{HEALTH_MODULE}.TEMPORAL_NAMESPACE", "other"):
                send_health_alert("problem")
        assert send.call_count == 2


class TestHeartbeat:
    def test_success(self):
        with patch(f"{HEALTH_MODULE}.requests.get") as get:
            assert ping_heartbeat("https://hc-ping.test/secret")
        get.assert_called_once_with("https://hc-ping.test/secret", timeout=10)
        get.return_value.raise_for_status.assert_called_once()

    @pytest.mark.parametrize(
        "error", [requests.ConnectionError, requests.Timeout, requests.HTTPError]
    )
    def test_failure_logs_no_url_or_exception_secret(self, error):
        url = "https://hc-ping.test/secret-path?token=secret-token"
        with (
            patch(f"{HEALTH_MODULE}.requests.get", side_effect=error(url)),
            capture_logs() as records,
        ):
            assert not ping_heartbeat(url)
        assert records == [
            {
                "event": "sentinel_heartbeat_failed",
                "error_type": error.__name__,
                "log_level": "warning",
            }
        ]
        assert "secret" not in str(records)

    def test_http_error_response(self):
        with patch(f"{HEALTH_MODULE}.requests.get") as get:
            get.return_value.raise_for_status.side_effect = requests.HTTPError("secret")
            assert not ping_heartbeat("https://hc-ping.test/secret")


class TestCommand:
    def test_healthy_pings_without_alert(self):
        with (
            patch(f"{COMMAND_MODULE}.run_health_checks", return_value=HealthReport()),
            patch(f"{COMMAND_MODULE}.send_health_alert") as alert,
            patch(f"{COMMAND_MODULE}.ping_heartbeat") as ping,
        ):
            call_command(
                "check_temporal_health", heartbeat_url="https://hc-ping.test/secret"
            )
        alert.assert_not_called()
        ping.assert_called_once_with("https://hc-ping.test/secret")

    def test_unhealthy_alerts_pings_and_exits_nonzero(self):
        report = HealthReport(dead_queues=["tasks_xl"])
        with (
            patch(f"{COMMAND_MODULE}.run_health_checks", return_value=report),
            patch(f"{COMMAND_MODULE}.send_health_alert") as alert,
            patch(f"{COMMAND_MODULE}.ping_heartbeat") as ping,
        ):
            with pytest.raises(CommandError, match="HC-SENTINEL"):
                call_command(
                    "check_temporal_health", heartbeat_url="https://hc-ping.test/secret"
                )
        alert.assert_called_once_with(report.to_slack_message())
        ping.assert_called_once_with("https://hc-ping.test/secret")

    @pytest.mark.parametrize("error_type", [RuntimeError, TimeoutError])
    def test_failure_alerts_and_preserves_failure(self, error_type):
        error = error_type("connection secret")
        with (
            patch(f"{COMMAND_MODULE}.run_health_checks", side_effect=error),
            patch(f"{COMMAND_MODULE}.send_health_alert") as alert,
            patch(f"{COMMAND_MODULE}.ping_heartbeat") as ping,
        ):
            with pytest.raises(error_type) as raised:
                call_command(
                    "check_temporal_health", heartbeat_url="https://hc-ping.test/secret"
                )
        assert raised.value is error
        assert "Could not complete Temporal health checks" in alert.call_args.args[0]
        assert "secret" not in alert.call_args.args[0]
        ping.assert_called_once()

    def test_without_url_skips_heartbeat(self):
        with (
            patch(f"{COMMAND_MODULE}.run_health_checks", return_value=HealthReport()),
            patch(f"{COMMAND_MODULE}.ping_heartbeat") as ping,
        ):
            call_command("check_temporal_health")
        ping.assert_not_called()

    def test_duplicate_unhealthy_runs_still_exit_nonzero(self):
        with (
            patch(
                f"{COMMAND_MODULE}.run_health_checks",
                return_value=HealthReport(paused_schedules=["paused"]),
            ),
            patch(
                f"{HEALTH_MODULE}.send_critical_slack_notification", return_value=True
            ) as send,
        ):
            for _ in range(2):
                with pytest.raises(CommandError):
                    call_command("check_temporal_health")
        send.assert_called_once()

    def test_failed_alert_does_not_hide_unhealthy_exit(self):
        with (
            patch(
                f"{COMMAND_MODULE}.run_health_checks",
                return_value=HealthReport(dead_queues=["default"]),
            ),
            patch(f"{COMMAND_MODULE}.send_health_alert", return_value=False),
        ):
            with pytest.raises(CommandError):
                call_command("check_temporal_health")
