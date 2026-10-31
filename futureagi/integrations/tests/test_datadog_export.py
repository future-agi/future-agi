"""Datadog exports partition arrivals, not overlapping request start times."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest

from accounts.models.workspace import Workspace
from agentcc.models import AgentccRequestLog
from integrations.models import ConnectionStatus, SyncLog, SyncStatus
from integrations.services.datadog_service import DatadogService
from integrations.temporal.activities import _sync_export_integration

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def datadog(integration_connection):
    connection = integration_connection
    connection.platform = "datadog"
    connection.last_synced_at = BASE
    connection.sync_cursor = {"unrelated": "preserve"}
    connection.save(update_fields=["platform", "last_synced_at", "sync_cursor"])
    service = Mock()
    service.export_logs.side_effect = lambda credentials, logs: {"sent": len(logs)}
    service.export_metrics.side_effect = lambda credentials, series: {
        "sent": len(series)
    }
    with (
        patch(
            "integrations.services.credentials.CredentialManager.decrypt",
            return_value={},
        ),
        patch(
            "integrations.services.base.get_integration_service", return_value=service
        ),
    ):
        yield connection, service


def sync_at(connection, seconds):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return BASE + timedelta(seconds=seconds)

    with patch("integrations.temporal.activities.datetime", Clock):
        _sync_export_integration(connection)
    connection.refresh_from_db()


def create_log(connection, request_id, arrived, started=90, **kwargs):
    log = AgentccRequestLog.no_workspace_objects.create(
        organization=connection.organization,
        workspace=connection.workspace,
        request_id=request_id,
        started_at=BASE + timedelta(seconds=started) if started is not None else None,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        cost="0.2",
        latency_ms=100,
        **kwargs,
    )
    AgentccRequestLog.no_workspace_objects.filter(pk=log.pk).update(
        created_at=BASE + timedelta(seconds=arrived)
    )
    return log


def logs_sent(service):
    return [log for call in service.export_logs.call_args_list for log in call.args[1]]


def metric_values(service, suffix):
    return [
        item["points"][0]["value"]
        for call in service.export_metrics.call_args_list
        for item in call.args[1]
        if item["metric"] == f"agentcc.gateway.{suffix}"
    ]


@pytest.mark.django_db
def test_successive_datadog_windows_do_not_reexport_or_recount(datadog):
    connection, service = datadog
    create_log(connection, "original", arrived=90, is_error=True)
    sync_at(connection, 120)
    sync_at(connection, 180)

    assert [log["attributes"]["request_id"] for log in logs_sent(service)] == [
        "original"
    ]
    assert metric_values(service, "requests") == [1]
    assert metric_values(service, "errors") == [1]
    assert metric_values(service, "input_tokens") == [10]
    assert metric_values(service, "output_tokens") == [5]
    assert metric_values(service, "cost") == [0.2]
    assert connection.last_synced_at == BASE + timedelta(seconds=180)
    assert connection.sync_cursor == {"unrelated": "preserve"}


@pytest.mark.django_db
def test_late_arrivals_and_null_start_are_exported_in_arrival_interval(datadog):
    connection, service = datadog
    sync_at(connection, 120)
    create_log(connection, "late", arrived=150, started=-3600)
    create_log(connection, "null-start", arrived=151, started=None)
    sync_at(connection, 180)
    sync_at(connection, 240)

    assert {log["attributes"]["request_id"] for log in logs_sent(service)} == {
        "late",
        "null-start",
    }
    assert len(logs_sent(service)) == 2
    assert metric_values(service, "requests") == [2]
    assert metric_values(service, "input_tokens") == [20]
    assert metric_values(service, "cost") == [0.4]
    late = next(
        log for log in logs_sent(service) if log["attributes"]["request_id"] == "late"
    )
    assert late["date"] == (BASE - timedelta(hours=1)).isoformat()


@pytest.mark.django_db
def test_arrival_boundaries_are_half_open(datadog):
    connection, service = datadog
    create_log(connection, "at-start", arrived=0, started=-1)
    create_log(connection, "at-end", arrived=120, started=120)
    create_log(connection, "before-start", arrived=-1, started=1)
    sync_at(connection, 120)
    assert [log["attributes"]["request_id"] for log in logs_sent(service)] == [
        "at-start"
    ]
    sync_at(connection, 180)
    assert [log["attributes"]["request_id"] for log in logs_sent(service)] == [
        "at-start",
        "at-end",
    ]
    assert metric_values(service, "requests") == [1, 1]


@pytest.mark.django_db
def test_metrics_failure_does_not_replay_acknowledged_logs(datadog):
    connection, service = datadog
    create_log(connection, "original", arrived=90)
    service.export_metrics.side_effect = RuntimeError("synthetic sink failure")
    sync_at(connection, 120)
    assert connection.status == ConnectionStatus.ERROR
    assert connection.last_synced_at == BASE
    assert (
        SyncLog.objects.filter(connection=connection).latest("created_at").status
        == SyncStatus.FAILED
    )

    create_log(connection, "later", arrived=150)
    service.export_metrics.side_effect = lambda credentials, series: {
        "sent": len(series)
    }
    sync_at(connection, 180)
    assert len(logs_sent(service)) == 1
    assert connection.last_synced_at == BASE + timedelta(seconds=120)
    points = [c.args[1][0]["points"] for c in service.export_metrics.call_args_list]
    assert points[0] == points[1]
    sync_at(connection, 240)
    assert len(logs_sent(service)) == 2
    assert metric_values(service, "requests") == [1, 1, 1]  # first call failed


@pytest.mark.django_db
def test_partial_log_batch_does_not_advance_watermarks(datadog):
    connection, service = datadog
    create_log(connection, "original", arrived=90)
    service.export_logs.side_effect = None
    service.export_logs.return_value = {"sent": 0}
    sync_at(connection, 120)
    assert connection.last_synced_at == BASE
    assert connection.status == ConnectionStatus.ERROR
    service.export_metrics.assert_not_called()


@pytest.mark.django_db
def test_retry_resumes_after_last_acknowledged_batch_at_equal_timestamps(datadog):
    connection, service = datadog
    rows = AgentccRequestLog.no_workspace_objects.bulk_create(
        [
            AgentccRequestLog(
                organization=connection.organization,
                workspace=connection.workspace,
                request_id=f"request-{i}",
                started_at=BASE + timedelta(seconds=90),
            )
            for i in range(501)
        ]
    )
    AgentccRequestLog.no_workspace_objects.filter(pk__in=[r.pk for r in rows]).update(
        created_at=BASE + timedelta(seconds=90)
    )
    service.export_logs.side_effect = [
        {"sent": 500},
        RuntimeError("synthetic log failure"),
    ]
    sync_at(connection, 120)
    assert connection.last_synced_at == BASE
    accepted = {
        log["attributes"]["request_id"]
        for log in service.export_logs.call_args_list[0].args[1]
    }
    assert len(accepted) == 500

    service.export_logs.reset_mock()
    service.export_logs.side_effect = lambda credentials, logs: {"sent": len(logs)}
    sync_at(connection, 180)
    retried = {log["attributes"]["request_id"] for log in logs_sent(service)}
    assert len(retried) == 1
    assert not accepted.intersection(retried)
    assert metric_values(service, "requests") == [501]
    assert connection.last_synced_at == BASE + timedelta(seconds=120)


@pytest.mark.django_db
def test_datadog_scopes_arrivals_and_metrics_to_workspace(datadog):
    connection, service = datadog
    create_log(connection, "in-workspace", arrived=90)
    other = Workspace.objects.create(
        organization=connection.organization,
        name="other",
        created_by=connection.created_by,
    )
    log = create_log(connection, "other-workspace", arrived=90)
    AgentccRequestLog.no_workspace_objects.filter(pk=log.pk).update(workspace=other)
    sync_at(connection, 120)
    assert [log["attributes"]["request_id"] for log in logs_sent(service)] == [
        "in-workspace"
    ]
    assert metric_values(service, "requests") == [1]


@pytest.mark.django_db
def test_metrics_short_acknowledgement_preserves_pending_window(datadog):
    connection, service = datadog
    create_log(connection, "original", arrived=90)
    service.export_metrics.side_effect = None
    service.export_metrics.return_value = {"sent": 0}
    sync_at(connection, 120)
    assert connection.status == ConnectionStatus.ERROR
    assert connection.last_synced_at == BASE
    assert not connection.sync_cursor["datadog_export"]["metrics_complete"]
    assert connection.sync_cursor["datadog_export"]["logs_complete"]


@pytest.mark.django_db
def test_checkpoint_save_failure_does_not_replay_completed_delivery(datadog):
    connection, service = datadog
    create_log(connection, "original", arrived=90)
    save = connection.save

    def fail_checkpoint(*args, **kwargs):
        if "last_synced_at" in kwargs.get("update_fields", []):
            raise RuntimeError("synthetic checkpoint write failure")
        return save(*args, **kwargs)

    with patch.object(connection, "save", side_effect=fail_checkpoint):
        sync_at(connection, 120)
    assert connection.last_synced_at == BASE
    assert connection.sync_cursor["datadog_export"]["metrics_complete"]
    sync_at(connection, 180)
    assert len(logs_sent(service)) == 1
    service.export_metrics.assert_called_once()
    assert connection.last_synced_at == BASE + timedelta(seconds=120)
    assert connection.sync_cursor == {"unrelated": "preserve"}


@pytest.mark.django_db
@pytest.mark.parametrize("backfill", [False, True])
def test_initial_sync_uses_configured_arrival_backfill(datadog, backfill):
    connection, service = datadog
    connection.last_synced_at = None
    connection.backfill_from = BASE if backfill else None
    connection.backfill_completed = False
    connection.save(
        update_fields=["last_synced_at", "backfill_from", "backfill_completed"]
    )
    create_log(connection, "arrived-recently", arrived=90, started=-90000)
    create_log(connection, "arrived-before-backfill", arrived=-90000, started=90)
    sync_at(connection, 120)
    assert [log["attributes"]["request_id"] for log in logs_sent(service)] == [
        "arrived-recently"
    ]
    assert connection.backfill_completed


@pytest.mark.django_db
def test_other_export_platforms_keep_their_existing_lookback(datadog):
    connection, _ = datadog
    connection.platform = "posthog"
    connection.save(update_fields=["platform"])
    with patch(
        "integrations.temporal.activities._export_posthog",
        return_value={"logs_sent": 0},
    ) as export:
        sync_at(connection, 120)
    assert export.call_args.args[3:] == (
        BASE - timedelta(minutes=1),
        BASE + timedelta(seconds=120),
    )


@pytest.mark.django_db
def test_real_datadog_adapter_receives_one_log_and_count_window(datadog):
    connection, _ = datadog
    create_log(connection, "original", arrived=90)
    with (
        patch(
            "integrations.services.base.get_integration_service",
            return_value=DatadogService(),
        ),
        patch("integrations.services.datadog_service.requests.post") as post,
    ):
        sync_at(connection, 120)
        sync_at(connection, 180)
    assert post.call_count == 2  # one log request, one metric request
    log_call, metrics_call = post.call_args_list
    assert log_call.args[0].endswith("/api/v2/logs")
    assert log_call.kwargs["json"][0]["attributes"]["request_id"] == "original"
    assert metrics_call.args[0].endswith("/api/v2/series")
    requests = [
        s
        for s in metrics_call.kwargs["json"]["series"]
        if s["metric"].endswith(".requests")
    ]
    assert requests[0]["points"][0]["value"] == 1
