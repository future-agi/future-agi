from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
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

# These are enabled-feature scenarios; rollout denial has a separate suite.
pytestmark = pytest.mark.usefixtures("enabled_metrics")

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
def saved(auth_client, observe_project, enabled_metrics):
    # Other suites import this fixture without this module's usefixtures mark.
    response = auth_client.post(ROOT, body(observe_project), format="json")
    assert response.status_code == 201, response.data
    # Legacy frozen-run tests start with a completed active definition. A fresh
    # v2 create remains a draft until the refresh writer completes its backfill.
    from tracer.models.derived_metric import DerivedMetric

    definition = DerivedMetricVersion.objects.get(
        metric_id=response.data["id"], version=1
    )
    DerivedMetricVersion.objects.filter(pk=definition.pk).update(
        backfill_status="complete"
    )
    DerivedMetric.activate_version(
        response.data["id"], definition.id, expected_active_id=None
    )
    return auth_client.get(f"{ROOT}{response.data['id']}/").data


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
    assert historical.data["definition"] == saved["definition"]
    detail = auth_client.get(f"{RUNS}{historical.data['id']}/")
    assert detail.status_code == 200
    assert detail.data["definition"] == saved["definition"]
    listing = auth_client.get(f"{ROOT}{saved['id']}/runs/")
    assert listing.status_code == 200
    assert listing.data["results"][0]["definition"] == saved["definition"]


@pytest.mark.django_db
def test_a_paused_project_says_why_on_its_metrics(auth_client, saved, settings):
    from tracer.models.derived_metric import MetricProjectRefresh

    settings.METRICS_MAX_CHECKPOINT_ROWS = 5_000
    detail = auth_client.get(f"{ROOT}{saved['id']}/").data
    assert detail["calculation_paused"] is None
    since = timezone.now().replace(microsecond=0)
    MetricProjectRefresh.objects.update_or_create(
        project_id=saved["project_id"],
        defaults={
            "paused_reason": "too_large",
            "paused_at": since,
            "paused_rows": 7_500,
        },
    )

    def paused(metric):
        value = dict(metric["calculation_paused"])
        value["since"] = datetime.fromisoformat(value["since"].replace("Z", "+00:00"))
        return value

    expected = {"reason": "too_large", "since": since, "rows": 7_500, "limit": 5_000}
    assert paused(auth_client.get(f"{ROOT}{saved['id']}/").data) == expected
    listing = auth_client.get(ROOT, {"project_id": saved["project_id"]}).data
    (item,) = [m for m in listing["results"] if m["id"] == saved["id"]]
    assert paused(item) == expected


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
        # The default row shape is unchanged; the interval is opt-in.
        assert "uncertainty" not in result
        detailed = auth_client.get(
            f"{RUNS}{run.id}/results/", {"include_uncertainty": "true"}
        ).data["results"][0]
        assert {k: v for k, v in detailed.items() if k != "uncertainty"} == result
        # Live policy (Poisson design), sampled sessions kept with p = 0.5 and
        # 0.25, outcomes 1 and 0; the unsampled session is missing, not a zero.
        interval = detailed["uncertainty"]
        # Two probabilities inside one live label and a merely observed-constant
        # numeric denominator are unsupported, despite positive pooled variance.
        # Keep the weighted 1/3 point and provenance, but never invent confidence.
        assert (
            interval["status"] == "not_estimable"
            and interval["reason"] == "unstable_variance"
        )
        assert interval["method"] == "poisson" and interval["interval"] == ""
        assert interval["confidence_level"] == 0.95
        assert (
            interval["std_error"]
            is interval["ci_low"]
            is interval["ci_high"]
            is interval["degrees_of_freedom"]
            is None
        )
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
        point = dashboard.data["result"]["metrics"][0]["series"][0]["data"][0]
        assert point["ci_low"] is point["ci_high"] is None
        assert point["ci_reason"] == "unstable_variance"
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


# V2 definition/read contracts (PG only; telemetry calls are never substituted for
# authentication, membership, version activation or project scoping).
@pytest.fixture
def signed_in_client(user, workspace):
    from rest_framework.test import APIClient

    client = APIClient()
    response = client.post(
        "/accounts/token/",
        {"email": user.email, "password": "testpassword123"},
        format="json",
    )
    assert response.status_code == 200, response.data
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {response.data['access']}",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return client


@pytest.mark.django_db
def test_v2_real_sign_in_viewer_reads_member_writes_and_cross_workspace_404(
    signed_in_client, user, workspace, observe_project
):
    from tracer.tests.test_derived_metric_spec import sentence

    response = signed_in_client.post(ROOT, sentence(observe_project.id), format="json")
    assert response.status_code == 201, response.data
    metric_id = response.data["id"]
    assert response.data["definition"] is None
    assert response.data["draft_definition"]["kind"] == "share"
    OrganizationMembership.no_workspace_objects.filter(user=user).update(level=1)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=1)
    assert signed_in_client.get(f"{ROOT}{metric_id}/").status_code == 200
    assert signed_in_client.get(f"{ROOT}capabilities/").status_code == 200
    for action in ("preview/", "preview/cancel/"):
        assert (
            signed_in_client.post(f"{ROOT}{action}", {}, format="json").status_code
            == 403
        )
    assert (
        signed_in_client.post(
            ROOT, sentence(observe_project.id, name="Denied"), format="json"
        ).status_code
        == 403
    )
    assert (
        signed_in_client.post(
            f"{ROOT}{metric_id}/refresh/", {}, format="json"
        ).status_code
        == 403
    )
    # Read POST is marked on its exact handler, never on the whole viewset.
    from tracer.tests.test_derived_metric_preview import window

    read_body = {key: value for key, value in window().items() if key != "request_id"}
    response = signed_in_client.post(
        f"{ROOT}{metric_id}/unit-values/",
        {**read_body, "unit_ids": [str(uuid4())], "context": "sessions"},
        format="json",
    )
    assert response.status_code == 503, response.data
    OrganizationMembership.no_workspace_objects.filter(user=user).update(level=3)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=3)
    assert (
        signed_in_client.post(
            ROOT, sentence(observe_project.id, name="Member metric"), format="json"
        ).status_code
        == 201
    )
    other = Workspace.no_workspace_objects.create(
        name="Second workspace", organization=user.organization, created_by=user
    )
    WorkspaceMembership.no_workspace_objects.create(workspace=other, user=user, level=3)
    signed_in_client.credentials(
        HTTP_AUTHORIZATION=signed_in_client._credentials["HTTP_AUTHORIZATION"],
        HTTP_X_WORKSPACE_ID=str(other.id),
    )
    assert signed_in_client.get(f"{ROOT}{metric_id}/").status_code == 404
    assert (
        signed_in_client.get(ROOT, {"project_id": str(observe_project.id)}).status_code
        == 404
    )
    assert (
        signed_in_client.post(
            ROOT, sentence(observe_project.id, name="Wrong project"), format="json"
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_draft_metadata_does_not_change_active_reads_and_users_projection(
    auth_client, saved, observe_project
):
    from tracer.models.derived_metric import DerivedMetric
    from tracer.tests.test_derived_metric_spec import sentence
    from tracer.utils.filter_operators import FILTER_TYPE_ALLOWED_OPS

    metric = DerivedMetric.objects.get(pk=saved["id"])
    response = auth_client.put(
        f"{ROOT}{metric.id}/",
        sentence(
            observe_project.id,
            expected_version=1,
            value_labels={"match": "French", "other": "Other"},
        ),
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["definition"]["version"] == 1
    assert response.data["draft_definition"]["version"] == 2
    definition = metric.versions.get(version=2)
    assert (
        auth_client.post(
            f"{ROOT}{metric.id}/activate/",
            {"version": 2, "expected_active_id": str(metric.active_version_id)},
            format="json",
        ).status_code
        == 400
    )
    DerivedMetricVersion.objects.filter(pk=definition.id).update(
        backfill_status="complete"
    )
    activated = auth_client.post(
        f"{ROOT}{metric.id}/activate/",
        {"version": 2, "expected_active_id": str(metric.active_version_id)},
        format="json",
    )
    assert activated.status_code == 200, activated.data
    assert activated.data["definition"]["version"] == 2
    sessions = auth_client.get(
        f"{ROOT}{metric.id}/fields/", {"context": "sessions"}
    ).data["results"][0]
    users = auth_client.get(f"{ROOT}{metric.id}/fields/", {"context": "users"}).data[
        "results"
    ][0]
    assert sessions["value_type"] == "boolean" and sessions["value_labels"] == {
        "match": "French",
        "other": "Other",
    }
    assert users["value_type"] == "number" and users["value_labels"] == {}
    assert users["value_format"] == "percent" and users["projection"] is True
    assert set(users["operators"]) == FILTER_TYPE_ALLOWED_OPS["number"]
    assert sessions["id"] == users["id"] == f"metric_field:{metric.id}"


@pytest.mark.django_db
@pytest.mark.parametrize("level", ["span", "user"])
def test_snapshot_support_rejects_unsupported_grains(
    auth_client, observe_project, level
):
    from tracer.models.derived_metric import DerivedMetric
    from tracer.tests.test_derived_metric_preview import window
    from tracer.tests.test_derived_metric_spec import sentence

    data = sentence(observe_project.id, level=level)
    if level == "user":
        data["user_rule"] = "any"
    response = auth_client.post(ROOT, data, format="json")
    assert response.status_code == 201, response.data
    metric_id = response.data["id"]
    definition = DerivedMetricVersion.objects.get(metric_id=metric_id, version=1)
    DerivedMetricVersion.objects.filter(pk=definition.pk).update(
        backfill_status="complete"
    )
    DerivedMetric.activate_version(metric_id, definition.id, expected_active_id=None)
    response = auth_client.get(f"{ROOT}{metric_id}/")
    assert response.data["snapshot_supported"] is False
    assert f"{level} metrics" in response.data["snapshot_unsupported_reason"]
    request = {key: value for key, value in window().items() if key != "request_id"}
    response = auth_client.post(
        f"{ROOT}{metric_id}/snapshots/",
        {**request, "idempotency_key": "snapshot"},
        format="json",
    )
    assert response.status_code == 400 and "snapshot_not_supported" in str(
        response.data
    )


@pytest.mark.django_db
def test_migration_0126_legacy_user_recipe_is_readable_but_never_calculated(
    auth_client, saved, observe_project, user
):
    from importlib import import_module

    from django.apps import apps

    from tracer.models.derived_metric import DerivedMetric
    from tracer.services.derived_metrics import service
    from tracer.services.derived_metrics.errors import MetricInputError
    from tracer.tests.test_derived_metric_spec import sentence

    metric = DerivedMetric.objects.get(pk=saved["id"])
    definition = metric.active_version
    # Reproduce the pre-migration row and execute its actual data function.
    DerivedMetricVersion.objects.filter(pk=definition.pk).update(
        selection_unit="user", kind="custom", user_rule="", level="conversation"
    )
    import_module(
        "tracer.migrations.0126_derived_metric_refresh"
    ).level_from_legacy_selection_unit(apps, None)
    response = auth_client.get(f"{ROOT}{metric.id}/")
    assert response.data["definition"]["needs_rebuild"] is True
    with patch.object(service, "get_clickhouse_client") as client:
        assert (
            auth_client.post(
                f"{ROOT}{metric.id}/preview/", run_body(), format="json"
            ).status_code
            == 400
        )
        assert (
            auth_client.post(
                f"{ROOT}{metric.id}/refresh/", {}, format="json"
            ).status_code
            == 400
        )
        assert (
            auth_client.post(
                f"{ROOT}{metric.id}/activate/",
                {"version": 1, "expected_active_id": str(definition.id)},
                format="json",
            ).status_code
            == 400
        )
        with pytest.raises(MetricInputError, match="needs_rebuild"):
            service.create_run(
                metric,
                {
                    **run_body(),
                    "window_start": timezone.now() - timedelta(days=1),
                    "window_end": timezone.now(),
                },
                user,
            )
    client.assert_not_called()
    response = auth_client.put(
        f"{ROOT}{metric.id}/",
        sentence(
            observe_project.id, level="user", user_rule="every", expected_version=1
        ),
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["definition"]["needs_rebuild"] is True
    assert response.data["draft_definition"]["level"] == "user"
    assert response.data["draft_definition"]["needs_rebuild"] is False


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["kpi", "trend", "drivers"])
def test_reads_without_committed_manifest_are_unavailable_not_successful_zeros(
    auth_client, saved, action
):
    data = run_body()
    query = (
        {}
        if action == "history"
        else {key: data[key] for key in ("window_start", "window_end")}
    )
    with patch(
        "tracer.services.derived_metrics.service.get_clickhouse_client"
    ) as client:
        response = auth_client.get(f"{ROOT}{saved['id']}/{action}/", query)
    assert response.status_code == 503
    assert response.data["code"] == "metric_unavailable"
    assert "results" not in response.data and "value" not in response.data
    client.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("formula", [None, "", "   "])
@pytest.mark.parametrize("explicit_kind", [False, True])
def test_custom_formula_rejected_before_save_or_backfill(
    auth_client, observe_project, formula, explicit_kind
):
    from tracer.models.derived_metric import DerivedMetric, MetricProjectRefresh

    data = body(observe_project)
    data["inputs"] = [data["inputs"][0]]
    data.pop("formula")
    if formula is not None:
        data["formula"] = formula
    if explicit_kind:
        data["kind"] = "custom"
    before = (
        DerivedMetric.objects.count(),
        DerivedMetricVersion.objects.count(),
        MetricProjectRefresh.objects.count(),
    )
    for endpoint in (ROOT + "validate/", ROOT):
        response = auth_client.post(endpoint, data, format="json")
        assert response.status_code == 400, response.data
        assert "nonempty formula" in str(response.data)
    assert before == (
        DerivedMetric.objects.count(),
        DerivedMetricVersion.objects.count(),
        MetricProjectRefresh.objects.count(),
    )


@pytest.mark.django_db
def test_historical_snapshot_delete_and_activation_ignore_draft_prefetch(
    auth_client, saved, observe_project
):
    definition = DerivedMetricVersion.objects.get(metric_id=saved["id"], version=1)
    run = DerivedMetricRun.objects.create(
        definition=definition,
        kind="snapshot",
        supported_grain="conversation",
        as_of=timezone.now(),
        fingerprint=uuid4().hex,
        window_start=timezone.now() - timedelta(days=1),
        window_end=timezone.now(),
    )
    delete_url = f"{ROOT}{saved['id']}/snapshots/{run.id}/"
    edited = auth_client.put(
        f"{ROOT}{saved['id']}/",
        {**body(observe_project), "expected_version": 1},
        format="json",
    )
    assert edited.status_code == 200, edited.data
    # The historical (version 1) snapshot stays listed after a new draft exists.
    listed = auth_client.get(f"{ROOT}{saved['id']}/snapshots/").data["results"]
    assert [row["id"] for row in listed] == [str(run.id)]
    # B18 implemented deletion: the historical snapshot is accepted for cleanup,
    # leaves the list, and repeating the delete is idempotent.
    deleted = auth_client.delete(delete_url)
    assert deleted.status_code == 202 and deleted.data == {"status": "cleanup_pending"}
    assert auth_client.get(f"{ROOT}{saved['id']}/snapshots/").data["results"] == []
    again = auth_client.delete(delete_url)
    assert again.status_code == 202 and again.data["status"] in {
        "cleanup_pending",
        "deleted",
    }
    assert (
        auth_client.delete(f"{ROOT}{saved['id']}/snapshots/{uuid4()}/").status_code
        == 404
    )
    other = auth_client.post(
        ROOT, {**body(observe_project), "name": "Another metric"}, format="json"
    )
    assert (
        auth_client.delete(f"{ROOT}{other.data['id']}/snapshots/{run.id}/").status_code
        == 404
    )
    # Explicit historical activation must not inherit the current-draft filter.
    response = auth_client.post(
        f"{ROOT}{saved['id']}/activate/",
        {"version": 1, "expected_active_id": str(definition.id)},
        format="json",
    )
    assert response.status_code == 200, response.data


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["average", "total"])
@pytest.mark.parametrize(
    "value_format,unit,field",
    [("currency", "USD", "cost"), ("duration", "ms", "latency_ms")],
)
def test_users_numeric_projection_preserves_catalog_format(
    auth_client, observe_project, kind, value_format, unit, field
):
    from tracer.models.derived_metric import DerivedMetric
    from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
        _METRIC_VERSION_FIELDS,
        _metric_definition,
    )
    from tracer.tests.test_derived_metric_spec import sentence

    data = sentence(
        observe_project.id,
        kind=kind,
        field_spec={},
        value_format=value_format,
        unit=unit,
        inputs=[
            {
                "alias": "amount",
                "source": "span",
                "field": field,
                "value_type": "number",
                "reducer": "mean",
            }
        ],
    )
    created = auth_client.post(ROOT, data, format="json")
    assert created.status_code == 201, created.data
    definition = DerivedMetricVersion.objects.get(
        metric_id=created.data["id"], version=1
    )
    DerivedMetricVersion.objects.filter(pk=definition.pk).update(
        backfill_status="complete"
    )
    DerivedMetric.activate_version(
        created.data["id"], definition.id, expected_active_id=None
    )
    row = (
        DerivedMetric.objects.filter(pk=created.data["id"])
        .values(
            "id",
            "project_id",
            "name",
            *[f"active_version__{key}" for key in _METRIC_VERSION_FIELDS],
        )
        .get()
    )
    catalog = _metric_definition(row, context="users")
    for context in ("sessions", "users"):
        result = auth_client.get(
            f"{ROOT}{created.data['id']}/fields/", {"context": context}
        ).data["results"][0]
        assert result["value_format"] == catalog.details["value_format"] == value_format
        assert result["unit"] == catalog.details["unit"] == unit


def test_v2_openapi_freezes_all_routes_and_user_and_snapshot_serializers():
    from django.urls import include, path
    from drf_yasg import openapi
    from drf_yasg.generators import OpenAPISchemaGenerator
    from rest_framework.routers import SimpleRouter

    from tracer.views.derived_metric import (
        DerivedMetricRunViewSet,
        DerivedMetricViewSet,
    )

    router = SimpleRouter()
    router.register("derived-metrics", DerivedMetricViewSet, basename="derived-metric")
    router.register(
        "derived-metric-runs", DerivedMetricRunViewSet, basename="derived-metric-run"
    )
    schema = OpenAPISchemaGenerator(
        info=openapi.Info(title="Metrics contract", default_version="v2"),
        patterns=[path("tracer/", include(router.urls))],
    ).get_schema(public=True)
    paths = schema["paths"]
    snapshots = next(
        value for key, value in paths.items() if key.endswith("/{id}/snapshots/")
    )
    assert "get" in snapshots and "post" in snapshots
    assert any(
        "delete" in value
        for key, value in paths.items()
        if "/snapshots/{snapshot_id}/" in key
    )
    assert not any(key.endswith("/compare/") for key in paths)
    for name in [
        "kpi",
        "trend",
        "drivers",
        "history",
        "fields",
        "unit-values",
        "refresh",
        "preview/cancel",
    ]:
        assert any(key.endswith(f"/{name}/") for key in paths), name
    definitions = schema["definitions"]
    assert "counted_users" in definitions["MetricPoint"]["properties"]
    assert (
        "sampled_inputs_not_supported"
        in definitions["MetricPoint"]["properties"]["point_status"]["enum"]
    )
    assert "snapshot_supported" in definitions["DerivedMetric"]["properties"]
    assert any(key.endswith("/derived-metrics/preview/") for key in paths)
    assert any(key.endswith("/derived-metrics/preview/cancel/") for key in paths)
    assert {"project_id", "definition"} <= set(
        definitions["MetricDraftPreviewRequest"]["properties"]
    )
    assert "sample_units" in definitions["MetricPreview"]["properties"]
    assert {
        "unit_id",
        "inputs",
        "inputs_bool",
        "inputs_text",
        "status",
        "value_label",
    } <= set(definitions["MetricPreviewSample"]["properties"])
    for item in paths.values():
        for method in ("get", "post", "put", "delete"):
            if method in item:
                assert item[method]["x-runtime-response-validation"] is True


@pytest.mark.django_db
def test_run_now_persists_and_coalesces_then_honors_start_throttle(auth_client, saved):
    from tracer.models.derived_metric import MetricProjectRefresh
    from tracer.services.derived_metrics.admission import RUN_NOW_THROTTLE

    url = f"{ROOT}{saved['id']}/refresh/"
    response = auth_client.post(url, {}, format="json")
    assert response.status_code == 202 and response.data == {"status": "queued"}
    state = MetricProjectRefresh.objects.get(project_id=saved["project_id"])
    requested_at = state.refresh_requested_at
    assert requested_at is not None
    response = auth_client.post(url, {}, format="json")
    assert response.status_code == 202 and response.data == {"status": "already_queued"}
    state.refresh_from_db()
    assert state.refresh_requested_at == requested_at
    MetricProjectRefresh.objects.filter(pk=state.pk).update(
        refresh_requested_at=None, last_started_at=timezone.now()
    )
    response = auth_client.post(url, {}, format="json")
    assert response.status_code == 202 and response.data == {"status": "already_queued"}
    state.refresh_from_db()
    assert state.refresh_requested_at is None
    MetricProjectRefresh.objects.filter(pk=state.pk).update(
        last_started_at=timezone.now() - RUN_NOW_THROTTLE - timedelta(seconds=1)
    )
    response = auth_client.post(url, {}, format="json")
    assert response.status_code == 202 and response.data == {"status": "queued"}
    state.refresh_from_db()
    assert state.refresh_requested_at > requested_at


@pytest.mark.django_db
def test_run_now_off_and_unconfirmed_do_not_persist_or_dispatch(
    auth_client, saved, settings
):
    from tracer.models.derived_metric import DerivedMetric, MetricProjectRefresh
    from tracer.services.derived_metrics.service import (
        MetricUnavailableError,
        admit_refresh,
    )

    metric = DerivedMetric.objects.get(pk=saved["id"])
    url = f"{ROOT}{metric.id}/refresh/"
    response = auth_client.post(url, {"confirm": False}, format="json")
    assert response.status_code == 400
    settings.METRICS_ENABLED = 0
    with patch(
        "tracer.services.derived_metrics.admission.request_refresh"
    ) as request_refresh:
        assert auth_client.post(url, {}, format="json").status_code == 404
        with pytest.raises(MetricUnavailableError, match="not enabled"):
            admit_refresh(metric)
    request_refresh.assert_not_called()
    assert not MetricProjectRefresh.objects.filter(
        project=metric.project, refresh_requested_at__isnull=False
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("foreign_org", [False, True])
def test_real_sign_in_run_now_preserves_viewer_and_tenant_boundaries(
    signed_in_client, saved, user, workspace, observe_project, foreign_org
):
    from tracer.models.derived_metric import MetricProjectRefresh

    url = f"{ROOT}{saved['id']}/refresh/"
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=1)
    OrganizationMembership.no_workspace_objects.filter(user=user).update(level=1)
    assert signed_in_client.post(url, {}, format="json").status_code == 403
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=3)
    OrganizationMembership.no_workspace_objects.filter(user=user).update(level=3)
    organization = (
        Organization.objects.create(name="Synthetic foreign org")
        if foreign_org
        else user.organization
    )
    other = Workspace.objects.create(
        name="Synthetic foreign workspace", organization=organization, created_by=user
    )
    observe_project.workspace = other
    observe_project.organization = organization
    observe_project.save(update_fields=["workspace", "organization"])
    assert signed_in_client.post(url, {}, format="json").status_code == 404
    assert not MetricProjectRefresh.objects.filter(
        project=observe_project, refresh_requested_at__isnull=False
    ).exists()
