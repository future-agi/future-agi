from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from tracer.models.dashboard import DashboardWidget
from tracer.models.derived_metric import (
    DerivedMetricMonitorDelivery,
    DerivedMetricRun,
    DerivedMetricVersion,
)
from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.models.project import Project
from tracer.services.derived_metrics.integrations import dashboard_result
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.service import execute_run
from tracer.tests import test_derived_metric_clickhouse as metric_fixtures

metric_ch = metric_fixtures.metric_ch
seed_span = metric_fixtures.seed_span

ROOT = "/tracer/derived-metrics/"
RUNS = "/tracer/derived-metric-runs/"


def body(project):
    return {
        "project_id": str(project.id),
        "name": "Resolution ratio",
        "description": "Persisted test metric",
        "formula": "ratio(resolved == 1 and escalated == 0, resolved >= 0)",
        "selection_unit": "session",
        "weighting": "none",
        "cohort_key": "region",
        "filters": [],
        "inputs": [
            {
                "alias": key,
                "source": "span",
                "field": "attribute_number",
                "attribute_key": key,
                "reducer": "latest",
            }
            for key in ["resolved", "escalated"]
        ],
    }


def run_body():
    now = timezone.now().replace(microsecond=0)
    return {
        "window_start": (now - timedelta(days=1)).isoformat(),
        "window_end": now.isoformat(),
        "filters": [],
        "idempotency_key": str(uuid4()),
    }


@pytest.fixture
def saved(auth_client, observe_project):
    response = auth_client.post(ROOT, body(observe_project), format="json")
    assert response.status_code == 201, response.data
    return response.data


@pytest.mark.django_db
def test_definition_validation_versioning_and_old_version_runs(
    auth_client, observe_project, saved
):
    assert saved["definition"]["version"] == 1
    edited = {
        **body(observe_project),
        "formula": "mean(resolved + escalated)",
        "expected_version": 1,
    }
    response = auth_client.put(f"{ROOT}{saved['id']}/", edited, format="json")
    assert response.status_code == 200, response.data
    assert response.data["current_version"] == 2
    assert (
        DerivedMetricVersion.objects.get(metric_id=saved["id"], version=1).formula
        == saved["definition"]["formula"]
    )
    stale = auth_client.put(f"{ROOT}{saved['id']}/", edited, format="json")
    assert stale.status_code == 400
    with patch("tracer.views.derived_metric._enqueue"):
        historical = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", {**run_body(), "version": 1}, format="json"
        )
    assert historical.status_code == 202, historical.data
    assert historical.data["metric_version"] == 1
    assert historical.data["source_manifest"] is None


@pytest.mark.django_db
def test_idempotent_run_key_and_conflicting_request(auth_client, saved):
    data = run_body()
    with patch("tracer.views.derived_metric._enqueue") as enqueue:
        first = auth_client.post(f"{ROOT}{saved['id']}/runs/", data, format="json")
        second = auth_client.post(f"{ROOT}{saved['id']}/runs/", data, format="json")
        collision = auth_client.post(
            f"{ROOT}{saved['id']}/runs/",
            {**data, "filters": [{"field": "model", "value": "other"}]},
            format="json",
        )
    assert first.status_code == second.status_code == 202
    assert first.data["id"] == second.data["id"]
    assert collision.status_code == 400
    enqueue.assert_called_once()
    assert DerivedMetricRun.objects.count() == 1


@pytest.mark.django_db
def test_permissions_isolate_definitions_runs_and_results(
    auth_client, saved, user, workspace, observe_project
):
    with patch("tracer.views.derived_metric._enqueue"):
        run = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", run_body(), format="json"
        ).data
    other = Workspace.objects.create(
        name="Other workspace", organization=user.organization, created_by=user
    )
    observe_project.workspace = other
    observe_project.save()
    assert auth_client.get(f"{ROOT}{saved['id']}/").status_code == 404
    assert auth_client.get(f"{RUNS}{run['id']}/").status_code == 404
    assert auth_client.get(f"{RUNS}{run['id']}/results/").status_code == 404
    assert (
        auth_client.post(f"{RUNS}{run['id']}/recompute/", {}, format="json").status_code
        == 404
    )
    execute_run(run["id"])
    assert DerivedMetricRun.objects.get(pk=run["id"]).status == "failed"


@pytest.mark.django_db
def test_cross_organization_project_and_evaluator_are_rejected(
    auth_client, observe_project, user, custom_eval_config
):
    other_org = Organization.objects.create(name="Foreign organization")
    foreign = Project.no_workspace_objects.create(
        name="Foreign project",
        organization=other_org,
        model_type=observe_project.model_type,
        trace_type="observe",
    )
    response = auth_client.post(ROOT, body(foreign), format="json")
    assert response.status_code == 404
    custom_eval_config.project = foreign
    custom_eval_config.save()
    data = body(observe_project)
    data["inputs"][0] = {
        "alias": "resolved",
        "source": "eval",
        "config_id": str(custom_eval_config.id),
        "reducer": "latest",
    }
    response = auth_client.post(ROOT, data, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_viewer_reads_but_cannot_validate_save_run_or_recompute(
    auth_client, saved, user, workspace
):
    OrganizationMembership.no_workspace_objects.filter(user=user).update(level=1)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=1)
    assert auth_client.get(f"{ROOT}{saved['id']}/").status_code == 200
    assert auth_client.post(f"{ROOT}validate/", {}, format="json").status_code == 403
    assert (
        auth_client.post(
            f"{ROOT}{saved['id']}/runs/", run_body(), format="json"
        ).status_code
        == 403
    )
    assert (
        auth_client.put(f"{ROOT}{saved['id']}/", {}, format="json").status_code == 403
    )
    assert (
        auth_client.post(
            "/tracer/user-alerts/",
            {"metric_type": "derived_metric", "metric": saved["id"]},
            format="json",
        ).status_code
        == 403
    )
    assert (
        auth_client.post(
            "/tracer/user-alerts/bulk-mute/",
            {"ids": [], "is_mute": True},
            format="json",
        ).status_code
        == 403
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "data",
    [{"limit": "no"}, {"limit": 101}, {"before": "invalid"}, {"unexpected": "x"}],
)
def test_collection_queries_are_strict_and_bounded(auth_client, data):
    assert auth_client.get(ROOT, data).status_code == 400


@pytest.mark.django_db
def test_formula_rejects_execution_and_invalid_windows(
    auth_client, observe_project, saved
):
    data = body(observe_project)
    data["formula"] = "__import__('os').getcwd()"
    response = auth_client.post(f"{ROOT}validate/", data, format="json")
    assert response.status_code == 400
    run = run_body()
    run["window_start"] = run["window_end"]
    assert (
        auth_client.post(f"{ROOT}{saved['id']}/runs/", run, format="json").status_code
        == 400
    )
    run = run_body()
    run["window_start"] = (timezone.now() - timedelta(days=32)).isoformat()
    assert (
        auth_client.post(f"{ROOT}{saved['id']}/runs/", run, format="json").status_code
        == 400
    )


@pytest.mark.django_db
def test_formula_to_frozen_result_dashboard_and_monitor_with_late_data(
    auth_client, observe_project, workspace, saved, metric_ch
):
    now = timezone.now().replace(microsecond=0)
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    end_user = uuid4()
    sessions = [uuid4(), uuid4()]
    for index in range(3):
        seed_span(
            metric_ch,
            scope,
            f"first-{index}",
            sessions[0],
            end_user,
            now - timedelta(hours=1),
            escalated=0,
        )
    seed_span(
        metric_ch,
        scope,
        "second",
        sessions[1],
        end_user,
        now - timedelta(hours=1),
        escalated=1,
    )
    monitor = UserAlertMonitor.objects.create(
        name="Resolution monitor",
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        metric_type="derived_metric",
        metric=saved["id"],
        threshold_operator="less_than",
        threshold_type="static",
        critical_threshold_value=0.75,
        notification_emails=[],
        alert_frequency=60,
    )
    with (
        patch("tracer.views.derived_metric._enqueue"),
        patch(
            "tracer.services.derived_metrics.service.get_clickhouse_client",
            return_value=metric_ch,
        ),
    ):
        response = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", run_body(), format="json"
        )
        assert response.status_code == 202, response.data
        run_id = response.data["id"]
        execute_run(run_id)
        run = DerivedMetricRun.objects.get(pk=run_id)
        assert run.status == "complete"
        snapshot_id, result_id = run.snapshot_id, run.result_id
        stored = auth_client.get(f"{RUNS}{run_id}/results/")
        assert stored.status_code == 200, stored.data
        assert stored.data["results"][0]["value"] == 0.5
        assert int(stored.data["results"][0]["unit_count"]) == 2
        assert (
            UserAlertMonitorLog.objects.filter(
                alert=monitor,
                time_window_start=run.window_start,
                time_window_end=run.window_end,
            ).count()
            == 1
        )
        config = {
            "project_ids": [str(observe_project.id)],
            "time_range": {
                "custom_start": run.window_start,
                "custom_end": run.window_end,
            },
            "granularity": "day",
            "metrics": [
                {
                    "name": saved["name"],
                    "type": "derived_metric",
                    "source": "derived_metrics",
                    "derived_run_id": str(run.id),
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        assert (
            dashboard_result(config, workspace)["metrics"][0]["series"][0]["data"][0][
                "value"
            ]
            == 0.5
        )
        dashboard_response = auth_client.post(
            "/tracer/dashboard/", {"name": "Frozen result dashboard"}, format="json"
        )
        assert dashboard_response.status_code == 200, dashboard_response.data
        dashboard_id = dashboard_response.data["result"]["id"]
        widget_response = auth_client.post(
            f"/tracer/dashboard/{dashboard_id}/widgets/",
            {
                "name": saved["name"],
                "query_config": config,
                "chart_config": {"chart_type": "metric"},
            },
            format="json",
        )
        assert widget_response.status_code == 200, widget_response.data
        assert DashboardWidget.objects.filter(dashboard_id=dashboard_id).count() == 1
        dashboard_query = auth_client.post(
            "/tracer/dashboard/query/", config, format="json"
        )
        assert dashboard_query.status_code == 200, dashboard_query.data
        assert (
            dashboard_query.data["result"]["metrics"][0]["series"][0]["data"][0][
                "value"
            ]
            == 0.5
        )
        # Late source data changes a new snapshot, never an existing run replay.
        seed_span(
            metric_ch,
            scope,
            "late",
            uuid4(),
            end_user,
            now - timedelta(hours=1),
            escalated=0,
        )
        assert (
            auth_client.post(
                f"{RUNS}{run_id}/recompute/", {}, format="json"
            ).status_code
            == 202
        )
        assert (
            auth_client.get(f"{RUNS}{run_id}/results/").data["results"][0]["value"]
            == 0.5
        )
        with (
            patch(
                "tracer.services.derived_metrics.service._compute",
                side_effect=RuntimeError("transient database outage"),
            ),
            pytest.raises(RuntimeError),
        ):
            execute_run(run_id)
        assert (
            auth_client.get(f"{RUNS}{run_id}/results/").data["results"][0]["value"]
            == 0.5
        )
        assert (
            auth_client.post(
                f"{RUNS}{run_id}/recompute/", {}, format="json"
            ).status_code
            == 202
        )
        execute_run(run_id)
        run.refresh_from_db()
        assert run.snapshot_id == snapshot_id and run.result_id != result_id
        assert (
            auth_client.get(f"{RUNS}{run_id}/results/").data["results"][0]["value"]
            == 0.5
        )
        assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == 1
        # SQL dataset sees one committed attempt, not duplicate retry rows.
        visible = metric_ch.execute(
            "SELECT count(), any(value) FROM derived_metrics WHERE run_id = %(run)s AND rollup = 'overall'",
            {"run": run_id},
        )
        assert int(visible[0][0]) == 1 and visible[0][1] == 0.5
        publication_version = metric_ch.execute(
            "SELECT max(version) FROM derived_metric_commits WHERE run_id = %(run)s",
            {"run": run_id},
        )[0][0]
        assert int(publication_version) == 2
        next_response = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", run_body(), format="json"
        )
        execute_run(next_response.data["id"])
        new_value = auth_client.get(f"{RUNS}{next_response.data['id']}/results/").data[
            "results"
        ][0]["value"]
        assert new_value == pytest.approx(2 / 3)


@pytest.mark.django_db
def test_admission_stale_lease_recovery_and_sanitized_read_failure(auth_client, saved):
    with patch("tracer.views.derived_metric._enqueue"):
        first = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", run_body(), format="json"
        )
        second = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", run_body(), format="json"
        )
        third = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", run_body(), format="json"
        )
        assert first.status_code == second.status_code == 202
        assert third.status_code == 400
        run = DerivedMetricRun.objects.get(pk=first.data["id"])
        run.lease_expires_at = timezone.now() - timedelta(seconds=1)
        run.save(update_fields=["lease_expires_at"])
        assert (
            auth_client.post(
                f"{RUNS}{run.id}/recompute/", {}, format="json"
            ).status_code
            == 202
        )
    with patch(
        "tracer.services.derived_metrics.service.get_clickhouse_client",
        side_effect=RuntimeError("sensitive-host secret-query"),
    ):
        response = auth_client.post(
            f"{ROOT}{saved['id']}/preview/", run_body(), format="json"
        )
        assert response.status_code == 503
        assert "sensitive-host" not in str(response.data)


@pytest.mark.django_db
def test_worker_rechecks_revoked_requester_before_query(auth_client, saved, user):
    with patch("tracer.views.derived_metric._enqueue"):
        response = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", run_body(), format="json"
        )
    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        is_active=False
    )
    with patch(
        "tracer.services.derived_metrics.service.get_clickhouse_client"
    ) as client:
        execute_run(response.data["id"])
    client.assert_not_called()
    assert DerivedMetricRun.objects.get(pk=response.data["id"]).status == "failed"


@pytest.mark.django_db
def test_weighted_eval_formula_persists_and_drives_real_dashboard_and_monitor_apis(
    auth_client, observe_project, workspace, custom_eval_config, metric_ch
):
    from tracer.models.custom_eval_config import CustomEvalConfig

    custom_eval_config.project = observe_project
    custom_eval_config.save()
    escalated = CustomEvalConfig.objects.create(
        name="Escalation evaluator",
        project=observe_project,
        eval_template=custom_eval_config.eval_template,
    )
    configs = [custom_eval_config, escalated]
    data = body(observe_project)
    data["name"], data["weighting"] = "Weighted resolution", "inverse_probability"
    data["cohort_key"] = ""
    data["inputs"] = [
        {
            "alias": alias,
            "source": "eval",
            "config_id": str(config.id),
            "config_hash": "a" * 64,
            "reducer": "latest",
        }
        for alias, config in zip(["resolved", "escalated"], configs, strict=True)
    ]
    response = auth_client.post(ROOT, data, format="json")
    assert response.status_code == 201, response.data
    metric_id = response.data["id"]
    monitor_response = auth_client.post(
        "/tracer/user-alerts/",
        {
            "name": "Weighted resolution monitor",
            "project": str(observe_project.id),
            "metric_type": "derived_metric",
            "metric": metric_id,
            "threshold_type": "static",
            "threshold_operator": "less_than",
            "critical_threshold_value": 0.5,
            "alert_frequency": 60,
            "filters": {},
        },
        format="json",
    )
    assert monitor_response.status_code == 200, monitor_response.data
    now = timezone.now().replace(microsecond=0)
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    for index, probability in enumerate([0.5, 0.25]):
        session = uuid4()
        for child in range(3 if index == 0 else 1):
            seed_span(
                metric_ch,
                scope,
                f"{index}-{child}",
                session,
                uuid4(),
                now - timedelta(hours=1),
            )
        for config_index, config in enumerate(configs):
            metric_fixtures.seed_eval(
                metric_ch,
                config.id,
                session,
                now - timedelta(minutes=10),
                1 if config_index == 0 else index,
                probability,
            )
    seed_span(metric_ch, scope, "unsampled", uuid4(), uuid4(), now - timedelta(hours=1))
    with (
        patch("tracer.views.derived_metric._enqueue"),
        patch(
            "tracer.services.derived_metrics.service.get_clickhouse_client",
            return_value=metric_ch,
        ),
    ):
        response = auth_client.post(
            f"{ROOT}{metric_id}/runs/", run_body(), format="json"
        )
        execute_run(response.data["id"])
        run = DerivedMetricRun.objects.get(pk=response.data["id"])
        assert run.status == "complete"
        result = auth_client.get(f"{RUNS}{run.id}/results/").data["results"][0]
        assert result["value"] == pytest.approx(1 / 3)
        assert (result["numerator"], result["denominator"]) == (2, 6)
        assert run.source_manifest["sampling_scope"].startswith(
            "11111111-1111-1111-1111-111111111111:" + "c" * 64 + ":"
        )
        assert len(run.source_manifest["sampling_scope"].split(":")[-1]) == 64
        config = {
            "project_ids": [str(observe_project.id)],
            "time_range": {
                "custom_start": run.window_start,
                "custom_end": run.window_end,
            },
            "granularity": "day",
            "metrics": [
                {
                    "name": "Weighted resolution",
                    "type": "derived_metric",
                    "source": "derived_metrics",
                    "derived_run_id": str(run.id),
                    "aggregation": "avg",
                }
            ],
            "filters": [],
            "breakdowns": [],
        }
        dashboard = auth_client.post("/tracer/dashboard/query/", config, format="json")
        assert dashboard.status_code == 200, dashboard.data
        assert dashboard.data["result"]["metrics"][0]["series"][0]["data"][0][
            "value"
        ] == pytest.approx(1 / 3)
        first_page = auth_client.get(
            f"{RUNS}{run.id}/results/", {"rollup": "cohort", "limit": 1}
        )
        assert first_page.status_code == 200, first_page.data
        assert first_page.data["results"][0]["group_key"] == ""
        cursor = first_page.data["next_cursor"]
        assert cursor
        second_page = auth_client.get(
            f"{RUNS}{run.id}/results/",
            {"rollup": "cohort", "limit": 1, "after": cursor},
        )
        assert second_page.status_code == 200, second_page.data
        assert second_page.data["results"][0]["group_key"] == "region-west"
        assert second_page.data["next_cursor"] is None
        assert (
            auth_client.get(
                f"{RUNS}{run.id}/results/", {"rollup": "user", "after": cursor}
            ).status_code
            == 400
        )
        monitor = UserAlertMonitor.objects.get(name="Weighted resolution monitor")
        assert (
            UserAlertMonitorLog.objects.filter(alert=monitor, type="critical").count()
            == 1
        )
        from tracer.services.derived_metrics.integrations import (
            DerivedMonitorQueryBuilder,
        )

        builder = DerivedMonitorQueryBuilder(monitor)
        sql, params = builder.build_metric_value_query(
            "derived_metric", now - timedelta(days=1), run.window_end
        )
        assert metric_ch.execute(sql, params)[0][0] == pytest.approx(1 / 3)


@pytest.mark.django_db
def test_scope_permission_rejects_foreign_and_deleted_workspaces(user):
    from types import SimpleNamespace

    from tracer.permissions.metrics import MetricWorkspacePermission

    other = Organization.objects.create(name="Unrelated organization")
    workspace = Workspace.no_workspace_objects.create(
        name="Foreign workspace", organization=other, created_by=user
    )
    request = SimpleNamespace(
        user=user, workspace=workspace, organization=other, method="POST"
    )
    assert MetricWorkspacePermission().has_permission(request, None) is False
    own = Workspace.no_workspace_objects.get(
        organization=user.organization, is_default=True
    )
    own.deleted = True
    request = SimpleNamespace(
        user=user, workspace=own, organization=user.organization, method="GET"
    )
    assert MetricWorkspacePermission().has_permission(request, None) is False


@pytest.mark.django_db
def test_monitor_delivery_is_idempotent_for_nonconsecutive_replay(
    auth_client, observe_project, workspace, saved, metric_ch
):
    now = timezone.now().replace(microsecond=0)
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    sessions = [uuid4(), uuid4()]
    for index, session in enumerate(sessions):
        seed_span(
            metric_ch,
            scope,
            f"delivery-{index}",
            session,
            uuid4(),
            now - timedelta(hours=1),
            escalated=index,
        )
    monitor = UserAlertMonitor.objects.create(
        name="Replay-safe monitor",
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        metric_type="derived_metric",
        metric=saved["id"],
        threshold_operator="less_than",
        threshold_type="static",
        critical_threshold_value=0.75,
        notification_emails=[],
        alert_frequency=60,
    )
    with (
        patch("tracer.views.derived_metric._enqueue"),
        patch(
            "tracer.services.derived_metrics.service.get_clickhouse_client",
            return_value=metric_ch,
        ),
    ):
        first = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", run_body(), format="json"
        ).data
        execute_run(first["id"])
        second = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", run_body(), format="json"
        ).data
        execute_run(second["id"])
        initial_snapshot = DerivedMetricRun.objects.get(pk=first["id"]).snapshot_id
        assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == 2
        assert (
            auth_client.post(
                f"{RUNS}{first['id']}/recompute/", {}, format="json"
            ).status_code
            == 202
        )
        execute_run(first["id"])
        assert (
            DerivedMetricRun.objects.get(pk=first["id"]).snapshot_id == initial_snapshot
        )
    assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == 2
    assert DerivedMetricMonitorDelivery.objects.filter(monitor=monitor).count() == 2
    assert {
        str(value)
        for value in DerivedMetricMonitorDelivery.objects.filter(
            monitor=monitor
        ).values_list("run_id", flat=True)
    } == {first["id"], second["id"]}
    monitor.refresh_from_db()
    assert str(monitor.last_derived_run_id) == second["id"]


@pytest.mark.django_db(transaction=True)
def test_monitor_delivery_is_idempotent_under_concurrent_retries(
    auth_client, saved, user, observe_project, workspace
):
    from tracer.models.derived_metric import DerivedMetric, DerivedMetricMonitorDelivery
    from tracer.services.derived_metrics.integrations import notify_run_monitors

    metric = DerivedMetric.objects.get(pk=saved["id"])
    definition = DerivedMetricVersion.objects.get(metric=metric, version=1)
    run = DerivedMetricRun.objects.create(
        definition=definition,
        requested_by=user,
        window_start=timezone.now() - timedelta(minutes=5),
        window_end=timezone.now(),
        as_of=timezone.now(),
        fingerprint=uuid4().hex,
        snapshot_id=uuid4(),
        result_id=uuid4(),
        source_manifest={"schema_version": 1, "compiler_version": 1},
        status="complete",
    )
    monitor = UserAlertMonitor.objects.create(
        name="Concurrent delivery monitor",
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        metric_type="derived_metric",
        metric=str(metric.id),
        threshold_operator="less_than",
        threshold_type="static",
        critical_threshold_value=0.75,
        notification_emails=[],
        alert_frequency=60,
    )

    ready = Barrier(2)

    def deliver():
        close_old_connections()
        ready.wait(timeout=10)
        try:
            notify_run_monitors(
                DerivedMetricRun.objects.select_related("definition__metric").get(
                    pk=run.id
                )
            )
        finally:
            close_old_connections()

    with (
        patch(
            "tracer.services.derived_metrics.integrations.read_results",
            return_value={"results": [{"value": 0.5}]},
        ),
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        list(pool.map(lambda _: deliver(), range(2)))
    assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == 1
    assert (
        DerivedMetricMonitorDelivery.objects.filter(monitor=monitor, run=run).count()
        == 1
    )


@pytest.fixture
def delivery_case(saved, user, observe_project, workspace):
    definition = DerivedMetricVersion.objects.get(metric_id=saved["id"], version=1)
    run = DerivedMetricRun.objects.create(
        definition=definition,
        requested_by=user,
        window_start=timezone.now() - timedelta(minutes=5),
        window_end=timezone.now(),
        as_of=timezone.now(),
        fingerprint=uuid4().hex,
        snapshot_id=uuid4(),
        result_id=uuid4(),
        status="complete",
    )
    monitor = UserAlertMonitor.objects.create(
        name="Delivery rollback monitor",
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        metric_type="derived_metric",
        metric=saved["id"],
        threshold_operator="less_than",
        threshold_type="static",
        critical_threshold_value=0.75,
        notification_emails=[],
        alert_frequency=60,
    )
    return run, monitor


@pytest.mark.django_db
def test_monitor_delivery_rolls_back_on_error_and_retries(delivery_case):
    from tracer.services.derived_metrics.integrations import notify_run_monitors

    run, monitor = delivery_case
    with patch(
        "tracer.services.derived_metrics.integrations.read_results",
        return_value={"results": [{"value": 0.5}]},
    ):
        # Raise after the real alert log insert, not before entering the path.
        with (
            patch(
                "tracer.utils.monitor._send_slack_notification",
                side_effect=RuntimeError("delivery unavailable"),
            ),
            pytest.raises(RuntimeError, match="delivery unavailable"),
        ):
            notify_run_monitors(run)
        assert not UserAlertMonitorLog.objects.filter(alert=monitor).exists()
        assert not DerivedMetricMonitorDelivery.objects.filter(
            monitor=monitor, run=run
        ).exists()
        monitor.refresh_from_db()
        assert monitor.last_derived_run_id is None
        notify_run_monitors(run)
        notify_run_monitors(run)
    assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == 1
    assert (
        DerivedMetricMonitorDelivery.objects.get(monitor=monitor, run=run).alert_type
        == "critical"
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DerivedMetricMonitorDelivery.objects.create(
            monitor=monitor, run=run, alert_type="critical"
        )


@pytest.mark.django_db
def test_healthy_monitor_evaluation_is_durably_consumed(delivery_case):
    from tracer.services.derived_metrics.integrations import notify_run_monitors

    run, monitor = delivery_case
    with patch(
        "tracer.services.derived_metrics.integrations.read_results",
        return_value={"results": [{"value": 1.0}]},
    ):
        notify_run_monitors(run)
        UserAlertMonitor.objects.filter(pk=monitor.pk).update(
            critical_threshold_value=1.5, last_derived_run_id=uuid4()
        )
        notify_run_monitors(run)
    assert not UserAlertMonitorLog.objects.filter(alert=monitor).exists()
    assert (
        DerivedMetricMonitorDelivery.objects.get(monitor=monitor, run=run).alert_type
        == ""
    )


@pytest.mark.django_db
def test_composite_span_identity_persists_two_trace_scoped_units(
    auth_client, observe_project, workspace, metric_ch
):
    now = timezone.now().replace(microsecond=0)
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    source_traces = [uuid4(), uuid4()]
    for index, trace_id in enumerate(source_traces):
        seed_span(
            metric_ch,
            scope,
            "shared-span-id",
            uuid4(),
            uuid4(),
            now - timedelta(hours=1),
            escalated=index,
            trace_id=trace_id,
        )
    data = body(observe_project)
    data["selection_unit"] = "span"
    saved = auth_client.post(ROOT, data, format="json")
    assert saved.status_code == 201, saved.data
    with (
        patch("tracer.views.derived_metric._enqueue"),
        patch(
            "tracer.services.derived_metrics.service.get_clickhouse_client",
            return_value=metric_ch,
        ),
    ):
        response = auth_client.post(
            f"{ROOT}{saved.data['id']}/runs/", run_body(), format="json"
        )
        assert response.status_code == 202, response.data
        execute_run(response.data["id"])
        result = auth_client.get(f"{RUNS}{response.data['id']}/results/").data[
            "results"
        ][0]
        assert (result["unit_count"], result["valid_count"], result["value"]) == (
            2,
            2,
            0.5,
        )
        run = DerivedMetricRun.objects.get(pk=response.data["id"])
        frozen = metric_ch.execute(
            "SELECT unit_id FROM derived_metric_inputs WHERE snapshot_id = %(snapshot)s",
            {"snapshot": str(run.snapshot_id)},
        )
        import json

        assert {tuple(json.loads(row[0])) for row in frozen} == {
            (str(trace_id), "shared-span-id") for trace_id in source_traces
        }
