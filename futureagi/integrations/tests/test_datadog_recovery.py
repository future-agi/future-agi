"""Regressions for settings races, orphaned workers and durable retry payloads."""

import re
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.db import connection as db_connection
from django.db import router
from django.test.utils import CaptureQueriesContext
from requests import HTTPError, Response, Timeout

from agentcc.models import AgentccRequestLog
from integrations.models import ConnectionStatus, IntegrationConnection
from integrations.temporal.activities import poll_active_integrations
from integrations.tests.test_datadog_export import (
    BASE,
    create_log,
    logs_sent,
    sync_at,
)
from integrations.tests.test_datadog_export import (
    datadog as datadog,
)
from integrations.views.integration_connection import IntegrationConnectionViewSet


@pytest.mark.django_db(transaction=True)
def test_settings_save_during_export_preserves_ownership_and_pending_receipts(datadog):
    connection, service = datadog
    create_log(connection, "settings-race", arrived=90)
    stale = IntegrationConnection.no_workspace_objects.get(pk=connection.pk)
    view = IntegrationConnectionViewSet()
    view.get_object = Mock(return_value=stale)
    snapshots = []

    def accept(credentials, logs):
        response = view._update_connection(
            SimpleNamespace(validated_data={"display_name": "Renamed during export"})
        )
        assert response.status_code == 200
        saved = IntegrationConnection.no_workspace_objects.get(pk=connection.pk)
        snapshots.append(deepcopy(saved.sync_cursor))
        return {"sent": len(logs)}

    service.export_logs.side_effect = accept
    sync_at(connection, 120)
    assert "datadog_export_owner" in snapshots[0]
    assert "datadog_export" in snapshots[0]
    assert connection.status == ConnectionStatus.ACTIVE
    assert connection.last_synced_at != BASE
    assert connection.display_name == "Renamed during export"
    service.export_metrics.assert_called_once()
    sync_at(connection, 180)
    assert len(logs_sent(service)) == 1


@pytest.mark.django_db(transaction=True)
def test_lock_loss_without_successor_returns_to_pollable_state(datadog):
    connection, service = datadog
    create_log(connection, "lock-lost", arrived=90)

    def accept(credentials, logs):
        connection._datadog_export_lock.close()
        return {"sent": len(logs)}

    service.export_logs.side_effect = accept
    sync_at(connection, 120)
    assert connection.status == ConnectionStatus.ACTIVE
    assert "datadog_export" in connection.sync_cursor
    service.export_metrics.assert_not_called()
    service.export_logs.side_effect = lambda credentials, logs: {"sent": len(logs)}
    with patch(
        "integrations.temporal.activities.sync_integration_connection.delay"
    ) as dispatch:
        poll_active_integrations()
    dispatch.assert_called_once_with(str(connection.pk))
    sync_at(connection, 180)
    assert len(logs_sent(service)) == 1
    service.export_metrics.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_poller_recovers_datadog_after_process_dies_in_syncing(datadog):
    connection, _ = datadog
    connection.status = ConnectionStatus.SYNCING
    connection.sync_cursor["datadog_export_owner"] = "dead-process"
    connection.save(update_fields=["status", "sync_cursor"])
    with patch(
        "integrations.temporal.activities.sync_integration_connection.delay"
    ) as dispatch:
        poll_active_integrations()
    dispatch.assert_called_once_with(str(connection.pk))
    sync_at(connection, 180)
    assert connection.status == ConnectionStatus.ACTIVE
    assert "datadog_export_owner" not in connection.sync_cursor


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("failure", ["timeout", "500", "503"])
def test_transient_sink_failure_is_retried_by_the_scheduler(datadog, failure):
    connection, service = datadog
    create_log(connection, "retry-me", arrived=90)
    if failure == "timeout":
        error = Timeout("synthetic intake timeout")
    else:
        response = Response()
        response.status_code = int(failure)
        error = HTTPError("synthetic intake failure", response=response)
    service.export_metrics.side_effect = error
    sync_at(connection, 120)
    assert connection.status == ConnectionStatus.ACTIVE
    with patch(
        "integrations.temporal.activities.sync_integration_connection.delay"
    ) as dispatch:
        poll_active_integrations()
    dispatch.assert_called_once_with(str(connection.pk))
    service.export_metrics.side_effect = lambda credentials, series: {
        "sent": len(series)
    }
    sync_at(connection, 180)
    assert len(logs_sent(service)) == 1
    assert connection.last_synced_at != BASE


@pytest.mark.django_db(transaction=True)
def test_delivery_reads_never_depend_on_a_read_replica(datadog):
    connection, service = datadog
    create_log(connection, "primary-visible", arrived=90)
    routed_reads = []
    original = router.db_for_read

    def stale_replica(model, **hints):
        if model._meta.label_lower in {
            "integrations.integrationconnection",
            "integrations.datadogexportreceipt",
            "prism.agentccrequestlog",
        }:
            routed_reads.append(model._meta.label_lower)
            raise AssertionError("delivery state must read its own primary writes")
        return original(model, **hints)

    with patch.object(router, "db_for_read", side_effect=stale_replica):
        sync_at(connection, 120)
    assert routed_reads == []
    assert [row["attributes"]["request_id"] for row in logs_sent(service)] == [
        "primary-visible"
    ]
    service.export_metrics.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_retry_uses_frozen_metrics_even_when_source_values_change(datadog):
    connection, service = datadog
    source = create_log(connection, "mutable-source", arrived=90)
    service.export_metrics.side_effect = Timeout("synthetic metric timeout")
    sync_at(connection, 120)
    first_payload = deepcopy(service.export_metrics.call_args.args[1])
    AgentccRequestLog.no_workspace_objects.filter(pk=source.pk).update(
        input_tokens=999, latency_ms=200, is_error=True, cost="99"
    )
    service.export_metrics.side_effect = lambda credentials, series: {
        "sent": len(series)
    }
    sync_at(connection, 180)
    assert service.export_metrics.call_args.args[1] == first_payload
    assert len(logs_sent(service)) == 1


@pytest.mark.django_db(transaction=True)
def test_snapshot_selection_is_bounded_with_server_cursors_disabled(datadog):
    connection, service = datadog
    create_log(connection, "bounded", arrived=90)
    with patch.dict(db_connection.settings_dict, {"DISABLE_SERVER_SIDE_CURSORS": True}):
        with CaptureQueriesContext(db_connection) as captured:
            sync_at(connection, 120)
    selections = [
        query["sql"]
        for query in captured.captured_queries
        if query["sql"].startswith(("SELECT", "DECLARE"))
        and 'FROM "agentcc_request_log"' in query["sql"]
        and "COUNT(" not in query["sql"]
    ]
    assert selections
    assert all(re.search(r"LIMIT \d+\s*;?$", sql) for sql in selections), selections
    assert len(logs_sent(service)) == 1


@pytest.mark.django_db(transaction=True)
def test_successive_poll_does_not_antijoin_the_entire_export_history(datadog):
    connection, _ = datadog
    create_log(connection, "already-delivered", arrived=90)
    sync_at(connection, 120)
    with CaptureQueriesContext(db_connection) as captured:
        sync_at(connection, 180)
    source_queries = [
        query["sql"]
        for query in captured.captured_queries
        if query["sql"].startswith(("SELECT", "DECLARE"))
        and 'FROM "agentcc_request_log"' in query["sql"]
    ]
    assert not any("NOT EXISTS" in sql for sql in source_queries), source_queries
    assert any("LIMIT " in sql for sql in source_queries)


@pytest.mark.django_db(transaction=True)
def test_large_history_reconciliation_eventually_finds_a_late_commit(datadog):
    connection, service = datadog
    connection.backfill_completed = False
    connection.save(update_fields=["backfill_completed"])
    rows = AgentccRequestLog.no_workspace_objects.bulk_create(
        [
            AgentccRequestLog(
                organization_id=connection.organization_id,
                workspace_id=connection.workspace_id,
                request_id=f"history-{i}",
            )
            for i in range(10001)
        ],
        batch_size=500,
    )
    AgentccRequestLog.no_workspace_objects.filter(
        pk__in=[row.pk for row in rows]
    ).update(created_at=BASE + timedelta(seconds=90))
    sync_at(connection, 120)
    # Source discovery, not just HTTP batching, is bounded per scheduled run.
    assert len(logs_sent(service)) == 5000
    assert not connection.backfill_completed
    sync_at(connection, 180)
    assert not connection.backfill_completed
    # Arrives behind both scan positions after the first historical page was read.
    create_log(connection, "late-behind-sweep", arrived=1)
    for seconds in range(240, 661, 60):
        sync_at(connection, seconds)
    delivered = [row["attributes"]["request_id"] for row in logs_sent(service)]
    assert len(delivered) == len(set(delivered)) == 10002
    assert delivered.count("late-behind-sweep") == 1
    assert connection.backfill_completed


@pytest.mark.django_db(transaction=True)
def test_auth_failure_requires_user_action_without_automatic_retry(datadog):
    connection, service = datadog
    create_log(connection, "auth-failure", arrived=90)
    response = Response()
    response.status_code = 403
    service.export_logs.side_effect = HTTPError("forbidden", response=response)
    sync_at(connection, 120)
    assert connection.status == ConnectionStatus.ERROR
    assert "datadog_export" in connection.sync_cursor
    with patch(
        "integrations.temporal.activities.sync_integration_connection.delay"
    ) as dispatch:
        poll_active_integrations()
    dispatch.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_pause_during_delivery_is_not_overwritten_by_worker_cleanup(datadog):
    connection, service = datadog
    create_log(connection, "paused-after-acceptance", arrived=90)

    def accept(credentials, logs):
        IntegrationConnection.no_workspace_objects.filter(pk=connection.pk).update(
            status=ConnectionStatus.PAUSED
        )
        return {"sent": len(logs)}

    service.export_logs.side_effect = accept
    sync_at(connection, 120)
    assert connection.status == ConnectionStatus.PAUSED
    service.export_metrics.assert_not_called()
    sync_at(connection, 180)
    assert len(logs_sent(service)) == 1
    assert connection.status == ConnectionStatus.PAUSED

    IntegrationConnection.no_workspace_objects.filter(pk=connection.pk).update(
        status=ConnectionStatus.ACTIVE
    )
    service.export_logs.side_effect = lambda credentials, logs: {"sent": len(logs)}
    sync_at(connection, 240)
    assert len(logs_sent(service)) == 1
    service.export_metrics.assert_called_once()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("default_workspace", [True, False])
def test_unassigned_gateway_logs_belong_only_to_the_org_default_workspace(
    datadog, default_workspace
):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace

    connection, service = datadog
    other_workspace = Workspace.objects.create(
        organization=connection.organization,
        name="Other export workspace",
        created_by=connection.created_by,
    )
    if not default_workspace:
        original = connection.workspace
        connection.workspace = other_workspace
        connection.save(update_fields=["workspace"])
        other_workspace = original
    create_log(connection, "explicit-workspace", arrived=90)
    legacy = create_log(connection, "unassigned-gateway", arrived=90)
    AgentccRequestLog.no_workspace_objects.filter(pk=legacy.pk).update(workspace=None)
    excluded = create_log(connection, "different-workspace", arrived=90)
    AgentccRequestLog.no_workspace_objects.filter(pk=excluded.pk).update(
        workspace=other_workspace
    )
    foreign = create_log(connection, "foreign-org-unassigned", arrived=90)
    other_org = Organization.objects.create(name="Other Datadog organization")
    AgentccRequestLog.no_workspace_objects.filter(pk=foreign.pk).update(
        organization=other_org, workspace=None
    )
    sync_at(connection, 120)
    sync_at(connection, 180)
    expected = (
        ["explicit-workspace", "unassigned-gateway"]
        if default_workspace
        else ["explicit-workspace"]
    )
    assert (
        sorted(row["attributes"]["request_id"] for row in logs_sent(service))
        == expected
    )
    counts = [
        series["points"][0]["value"]
        for call in service.export_metrics.call_args_list
        for series in call.args[1]
        if series["metric"] == "agentcc.gateway.requests"
    ]
    assert counts == [len(expected)]
