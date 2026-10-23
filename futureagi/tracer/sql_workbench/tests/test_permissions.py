"""Real ORM isolation tests; run against the normal PostgreSQL test database."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from unittest.mock import patch

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.ai_model import AIModel
from tracer.models.project import Project
from tracer.models.sql_query import SQLExecution
from tracer.sql_workbench.access import AccessDenied, Actor, authorize
from tracer.sql_workbench import service

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenants(monkeypatch):
    monkeypatch.setenv("SQL_WORKBENCH_DERIVED_METRICS_ENABLED", "false")
    values = []
    for i in range(2):
        org = Organization.objects.create(name=f"sql-org-{uuid4()}")
        user = User(
            id=uuid4(),
            email=f"sql-{uuid4()}@example.test",
            name="SQL reader",
            organization=org,
        )
        User.objects.bulk_create([user])
        workspace = Workspace(
            id=uuid4(), name=f"sql-workspace-{i}", organization=org, created_by=user
        )
        Workspace.no_workspace_objects.bulk_create([workspace])
        om = OrganizationMembership(id=uuid4(), user=user, organization=org, level=3)
        OrganizationMembership.no_workspace_objects.bulk_create([om])
        wm = WorkspaceMembership(
            id=uuid4(),
            user=user,
            workspace=workspace,
            level=3,
            organization_membership=om,
        )
        WorkspaceMembership.no_workspace_objects.bulk_create([wm])
        project = Project(
            id=uuid4(),
            name=f"sql-project-{i}",
            organization=org,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        Project.no_workspace_objects.bulk_create([project])
        values.append((Actor(user.id, org.id, workspace.id), project, user))
    return values


def create_job(actor, project, **kwargs):
    now = timezone.now()
    values = {
        "sql": "SELECT * FROM spans",
        "start": now - timedelta(hours=1),
        "end": now,
        "deadline": now + timedelta(seconds=90),
        "expires_at": now + timedelta(hours=24),
        **kwargs,  # A test may override any default, e.g. an expired deadline.
    }
    return SQLExecution.objects.create(
        project=project,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        created_by_id=actor.user_id,
        **values,
    )


def test_members_can_access_only_exact_project_scope(tenants):
    (actor, project, _), (other, foreign, _) = tenants
    assert authorize(actor, project.id) == project
    for candidate in [foreign.id, None, uuid4()]:
        with pytest.raises(AccessDenied):
            authorize(actor, candidate)
    for bad_actor in [
        Actor(actor.user_id, other.organization_id, actor.workspace_id),
        Actor(actor.user_id, actor.organization_id, other.workspace_id),
        Actor(actor.user_id, actor.organization_id, None),
    ]:
        with pytest.raises(AccessDenied):
            authorize(bad_actor, project.id)


def test_null_and_other_workspace_projects_are_not_legacy_bypasses(tenants):
    (actor, project, user), _ = tenants
    Project.no_workspace_objects.filter(id=project.id).update(workspace=None)
    with pytest.raises(AccessDenied):
        authorize(actor, project.id)
    ws = Workspace.no_workspace_objects.create(
        name=f"other-{uuid4()}", organization_id=actor.organization_id, created_by=user
    )
    Project.no_workspace_objects.filter(id=project.id).update(workspace=ws)
    with pytest.raises(AccessDenied):
        authorize(actor, project.id)


def test_viewer_can_read_but_not_save_or_share(tenants):
    (actor, project, _), _ = tenants
    OrganizationMembership.no_workspace_objects.filter(user_id=actor.user_id).update(
        level=1
    )
    assert authorize(actor, project.id) == project
    with pytest.raises(AccessDenied):
        authorize(actor, project.id, write=True)
    with pytest.raises(AccessDenied):
        service.save_query(
            actor,
            project_id=project.id,
            name="forbidden",
            sql="SELECT * FROM spans",
            visibility="project",
            chart={},
            window_hours=24,
        )


@pytest.mark.parametrize("membership", ["organization", "workspace"])
def test_revocation_denies_history_result_export_and_saved_query(tenants, membership):
    (actor, project, _), _ = tenants
    job = create_job(
        actor, project, state="succeeded", result={"columns": [], "rows": []}
    )
    saved = service.save_query(
        actor,
        project_id=project.id,
        name="saved",
        sql=job.sql,
        visibility="project",
        chart={},
        window_hours=24,
    )
    model = (
        OrganizationMembership if membership == "organization" else WorkspaceMembership
    )
    model.no_workspace_objects.filter(user_id=actor.user_id).update(is_active=False)
    for operation in [
        lambda: service.history(actor, project.id, 0),
        lambda: service.execution(actor, project.id, job.id),
        lambda: service.export_result(actor, project.id, job.id, "csv"),
        lambda: service.cancel(actor, project.id, job.id),
        lambda: service.saved_query(actor, project.id, saved.id),
    ]:
        with pytest.raises(AccessDenied):
            operation()


def test_other_tenant_cannot_read_job_even_with_exact_id(tenants):
    (actor, project, _), (other, foreign, _) = tenants
    job = create_job(
        actor, project, state="succeeded", result={"columns": [], "rows": []}
    )
    for project_id in [project.id, foreign.id]:
        with pytest.raises(AccessDenied):
            service.execution(other, project_id, job.id)
        with pytest.raises(AccessDenied):
            service.export_result(other, project_id, job.id, "parquet")
    assert service.history(other, foreign.id, 0)["items"] == []


def test_project_share_does_not_share_result_or_private_queries(tenants):
    (actor, project, user), (other, _, other_user) = tenants
    org_membership = OrganizationMembership(
        id=uuid4(), user=other_user, organization_id=actor.organization_id, level=1
    )
    OrganizationMembership.no_workspace_objects.bulk_create([org_membership])
    WorkspaceMembership.no_workspace_objects.bulk_create(
        [
            WorkspaceMembership(
                id=uuid4(),
                user=other_user,
                workspace_id=actor.workspace_id,
                level=1,
                organization_membership=org_membership,
            )
        ]
    )
    viewer = Actor(other.user_id, actor.organization_id, actor.workspace_id)
    shared = service.save_query(
        actor,
        project_id=project.id,
        name="shared",
        sql="SELECT * FROM spans",
        visibility="project",
        chart={},
        window_hours=24,
    )
    private = service.save_query(
        actor,
        project_id=project.id,
        name="private",
        sql="SELECT * FROM spans",
        visibility="private",
        chart={},
        window_hours=24,
    )
    assert service.saved_query(viewer, project.id, shared.id).id == shared.id
    with pytest.raises(AccessDenied):
        service.saved_query(viewer, project.id, private.id)
    job = create_job(actor, project)
    with pytest.raises(AccessDenied):
        service.execution(viewer, project.id, job.id)
    with pytest.raises(AccessDenied):
        service.delete_saved(viewer, project.id, shared.id)


def test_export_escapes_spreadsheet_formula_and_parquet_roundtrips(tenants):
    import io
    import pyarrow.parquet as pq

    (actor, project, _), _ = tenants
    result = {
        "columns": [
            {"name": "label", "type": "String"},
            {"name": "value", "type": "Int64"},
        ],
        "rows": [['  =HYPERLINK("evil")', 2], ["safe", None]],
    }
    job = create_job(actor, project, state="succeeded", result=result)
    data, kind = service.export_result(actor, project.id, job.id, "csv")
    assert "'  =HYPERLINK" in data.decode("utf-8-sig")
    data, kind = service.export_result(actor, project.id, job.id, "parquet")
    assert kind == "application/vnd.apache.parquet"
    assert pq.read_table(io.BytesIO(data)).to_pydict() == {
        "label": [result["rows"][0][0], "safe"],
        "value": [2, None],
    }


def test_expired_results_not_served(tenants):
    (actor, project, _), _ = tenants
    job = create_job(
        actor, project, state="succeeded", result={"columns": [], "rows": []}
    )
    job.expires_at = timezone.now() - timedelta(seconds=1)
    job.save()
    assert service.snapshot(job)["result"] == {}
    assert service.snapshot(job)["expired"]
    with pytest.raises(service.QueryUnavailable):
        service.export_result(actor, project.id, job.id, "csv")


def test_run_admission_is_locked_and_bounded(
    tenants, django_capture_on_commit_callbacks
):
    (actor, project, _), _ = tenants
    now = timezone.now()
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async") as enqueue,
        django_capture_on_commit_callbacks(execute=True),
    ):
        service.submit(
            actor,
            project_id=project.id,
            sql="SELECT count() FROM spans",
            start=now - timedelta(hours=1),
            end=now,
        )
        with pytest.raises(service.AdmissionFull):
            service.submit(
                actor,
                project_id=project.id,
                sql="SELECT count() FROM spans",
                start=now - timedelta(hours=1),
                end=now,
            )
        assert enqueue.call_count == 0
    # Publish happens only after the surrounding transaction commits.
    assert enqueue.call_count == 1
    assert len(SQLExecution.objects.filter(project=project)) == 1


def test_saved_query_cannot_be_attached_to_other_tenant_dashboard(tenants):
    from tracer.models.dashboard import Dashboard

    (actor, project, _), (other, _, _) = tenants
    saved = service.save_query(
        actor,
        project_id=project.id,
        name="shared",
        sql="SELECT * FROM spans",
        visibility="project",
        chart={},
        window_hours=24,
    )
    dashboard = Dashboard.no_workspace_objects.create(
        workspace_id=other.workspace_id, name="other", created_by_id=other.user_id
    )
    with pytest.raises(AccessDenied):
        service.add_widget(actor, project.id, saved.id, dashboard.id)


def api_request(actor, user, method, path, data):
    from rest_framework.test import APIRequestFactory, force_authenticate
    from types import SimpleNamespace

    request = getattr(APIRequestFactory(), method)(path, data, format="json")
    request.organization = SimpleNamespace(id=actor.organization_id)
    request.workspace = SimpleNamespace(id=actor.workspace_id)
    force_authenticate(request, user=user)
    return request


def test_catalog_api_and_saved_api_are_scoped_and_no_store(tenants):
    from tracer.views.sql_query import SQLQueryViewSet, SavedSQLQueryViewSet

    (actor, project, user), (_, foreign, _) = tenants
    view = SQLQueryViewSet.as_view({"get": "catalog"})
    response = view(
        api_request(
            actor,
            user,
            "get",
            "/tracer/sql-query/catalog/",
            {"project_id": str(project.id)},
        )
    )
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert {entry["name"] for entry in response.data["datasets"]} == {
        "spans",
        "traces",
        "sessions",
        "users",
        "annotations",
        "eval_results",
        "derived_metrics",
    }
    # The vetted example gallery travels with the catalog it was written for.
    from tracer.sql_workbench.examples import EXAMPLES

    gallery = response.data["examples"]
    assert [item["id"] for item in gallery] == [example.id for example in EXAMPLES]
    assert len(gallery) >= 12
    for item in gallery:
        assert item["title"] and item["description"] and item["sql"]
        assert set(item["datasets"]) <= {
            entry["name"] for entry in response.data["datasets"]
        }
    response = view(
        api_request(
            actor,
            user,
            "get",
            "/tracer/sql-query/catalog/",
            {"project_id": str(foreign.id)},
        )
    )
    assert response.status_code == 403
    create = SavedSQLQueryViewSet.as_view({"post": "create"})
    response = create(
        api_request(
            actor,
            user,
            "post",
            "/tracer/saved-sql-query/",
            {
                "project_id": str(project.id),
                "name": "Real API save",
                "sql": "SELECT name FROM spans",
                "visibility": "project",
            },
        )
    )
    assert response.status_code == 201
    assert response.data["project_id"] == project.id


@pytest.mark.parametrize(
    "bad_project",
    [None, "NULL", "';DROP TABLE spans--", "00000000-0000-0000-0000-000000000000"],
)
def test_api_missing_or_malicious_project_fails_closed(tenants, bad_project):
    from tracer.views.sql_query import SQLQueryViewSet

    actor, project, user = tenants[0]
    body = {} if bad_project is None else {"project_id": bad_project}
    response = SQLQueryViewSet.as_view({"get": "catalog"})(
        api_request(actor, user, "get", "/tracer/sql-query/catalog/", body)
    )
    assert response.status_code in {400, 403}


def test_binary_download_api_does_not_collide_with_drf_format_negotiation(tenants):
    from tracer.views.sql_query import SQLQueryViewSet

    actor, project, user = tenants[0]
    job = create_job(
        actor,
        project,
        state="succeeded",
        result={"columns": [{"name": "n", "type": "Int64"}], "rows": [[1]]},
    )
    response = SQLQueryViewSet.as_view({"get": "download"})(
        api_request(
            actor,
            user,
            "get",
            f"/tracer/sql-query/{job.id}/download/",
            {"project_id": str(project.id), "file_format": "csv"},
        ),
        pk=str(job.id),
    )
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    assert response["Content-Disposition"] == 'attachment; filename="query-results.csv"'
    assert response.content.decode("utf-8-sig") == "n\r\n1\r\n"


def test_saved_query_owner_updates_but_other_project_cannot(tenants):
    (actor, project, _), (other, foreign, _) = tenants
    saved = service.save_query(
        actor,
        project_id=project.id,
        name="owned",
        sql="SELECT * FROM spans",
        visibility="private",
        chart={},
        window_hours=24,
    )
    update = dict(
        project_id=project.id,
        name="shared",
        sql="SELECT count() FROM spans",
        visibility="project",
        chart={"type": "table"},
        window_hours=1,
    )
    assert service.update_saved(actor, saved.id, **update).visibility == "project"
    with pytest.raises(AccessDenied):
        service.update_saved(other, saved.id, **update)
    with pytest.raises(AccessDenied):
        service.update_saved(actor, saved.id, **{**update, "project_id": foreign.id})


def test_history_does_not_load_result_payloads(tenants, django_assert_num_queries):
    actor, project, _ = tenants[0]
    create_job(actor, project, state="succeeded", result={"rows": [["private"]]})
    # Three authorization reads plus one metadata-only history query, not N+1
    # deferred loads of up to 8MB per historical result.
    with django_assert_num_queries(4) as captured:
        result = service.history(actor, project.id, 0)
    assert result["items"][0]["result"] == {}
    assert '"result"' not in captured.captured_queries[-1]["sql"]


@pytest.mark.django_db(transaction=True)
def test_postgres_concurrent_admission_serializes_same_actor(tenants):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connection, connections

    if connection.vendor != "postgresql":
        pytest.skip("Row-lock concurrency requires PostgreSQL, not SQLite.")
    actor, project, _ = tenants[0]
    barrier = Barrier(2)
    now = timezone.now()

    def submit_once():
        try:
            barrier.wait(timeout=5)
            try:
                service.submit(
                    actor,
                    project_id=project.id,
                    sql="SELECT count() FROM spans",
                    start=now - timedelta(hours=1),
                    end=now,
                )
                return "accepted"
            except service.AdmissionFull:
                return "rejected"
        finally:
            connections.close_all()

    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async"),
    ):
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: submit_once(), range(2)))
    assert sorted(outcomes) == ["accepted", "rejected"]
    assert SQLExecution.objects.filter(project=project).count() == 1


def test_rolling_result_storage_budget_rejects_before_dispatch(tenants):
    actor, project, _ = tenants[0]
    now = timezone.now()
    create_job(
        actor,
        project,
        state="succeeded",
        output_bytes=service.MAX_ACTOR_RESULT_BYTES_24H,
    )
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async") as dispatch,
    ):
        with pytest.raises(service.AdmissionFull, match="storage budget"):
            service.submit(
                actor,
                project_id=project.id,
                sql="SELECT count() FROM spans",
                start=now - timedelta(hours=1),
                end=now,
            )
    dispatch.assert_not_called()


def test_expiry_purge_erases_body_and_quota_accounting_without_deleting_audit(tenants):
    actor, project, _ = tenants[0]
    job = create_job(
        actor,
        project,
        state="succeeded",
        output_bytes=1024,
        result={"rows": [["retained until expiry"]]},
    )
    SQLExecution.objects.filter(id=job.id).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert service.purge_expired_results(batch_size=1) == {
        "expired_result_bodies": 1,
        "old_audit_rows": 0,
    }
    job.refresh_from_db()
    assert job.result == {} and job.output_bytes == 0 and job.state == "succeeded"
    assert SQLExecution.objects.filter(
        id=job.id, project=project, created_by_id=actor.user_id
    ).exists()


def test_temporal_result_cleanup_is_registered_every_fifteen_minutes():
    import django

    django.setup()
    from tfc.temporal.schedules import ALL_SCHEDULES
    from tracer.sql_workbench.tasks import purge_expired_sql_results

    schedule = next(
        value
        for value in ALL_SCHEDULES
        if value.schedule_id == "sql-workbench-result-retention"
    )
    assert schedule.interval_seconds == 900 and schedule.queue == "exact_aggregation"
    assert schedule.activity_name == purge_expired_sql_results._activity_name
    assert purge_expired_sql_results._metadata["max_retries"] == 0


def test_organization_result_storage_budget_counts_other_members(tenants):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level

    actor, project, owner = tenants[0]
    workspace = owner.workspace_memberships.first().workspace
    now = timezone.now()
    members = []
    for index in range(5):
        user = User.objects.create_user(
            email=f"sql-quota-{uuid4()}@example.test",
            password="password",
            name="Quota member",
            organization=owner.organization,
        )
        org_membership = OrganizationMembership.objects.create(
            user=user,
            organization=owner.organization,
            level=Level.MEMBER,
            is_active=True,
        )
        WorkspaceMembership.objects.create(
            user=user,
            workspace=workspace,
            organization_membership=org_membership,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
        )
        members.append(Actor(user.id, owner.organization_id, workspace.id))
    for member in members[:4]:
        create_job(
            member,
            project,
            state="succeeded",
            output_bytes=service.MAX_ACTOR_RESULT_BYTES_24H,
        )
    # The fifth active member has no personal usage but the org already reached its cap.
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async") as dispatch,
    ):
        with pytest.raises(service.AdmissionFull, match="storage budget"):
            service.submit(
                members[4],
                project_id=project.id,
                sql="SELECT count() FROM spans",
                start=now - timedelta(hours=1),
                end=now,
            )
    dispatch.assert_not_called()


def test_result_cleanup_schedule_is_unique():
    import django

    django.setup()
    from tfc.temporal.schedules import ALL_SCHEDULES

    ids = [schedule.schedule_id for schedule in ALL_SCHEDULES]
    assert len(ids) == len(set(ids))
    assert ids.count("sql-workbench-result-retention") == 1


def test_query_submission_limit_bounds_sql_audit_growth(tenants):
    actor, project, _ = tenants[0]
    now = timezone.now()
    # The state (including failures/cancellations) is the audit row budget.
    rows = [
        SQLExecution(
            project=project,
            organization_id=actor.organization_id,
            workspace_id=actor.workspace_id,
            created_by_id=actor.user_id,
            sql="SELECT 1",
            start=now - timedelta(hours=1),
            end=now,
            state="failed",
            deadline=now,
            expires_at=now + timedelta(days=1),
        )
        for _ in range(service.MAX_ACTOR_QUERIES_24H)
    ]
    SQLExecution.objects.bulk_create(rows)
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async") as dispatch,
    ):
        with pytest.raises(service.AdmissionFull, match="audit submission limit"):
            service.submit(
                actor,
                project_id=project.id,
                sql="SELECT count() FROM spans",
                start=now - timedelta(hours=1),
                end=now,
            )
    dispatch.assert_not_called()


@pytest.mark.parametrize(
    "state", ["queued", "running", "succeeded", "failed", "cancelled"]
)
def test_default_preserves_old_audit_metadata_and_configured_policy_never_deletes_active_jobs(
    tenants, settings, state
):
    actor, project, _ = tenants[0]
    now = timezone.now()
    job = create_job(actor, project, state=state)
    SQLExecution.objects.filter(id=job.id).update(
        created_at=now - timedelta(days=180), expires_at=now - timedelta(days=1)
    )
    settings.SQL_WORKBENCH_AUDIT_RETENTION_DAYS = None
    assert service.purge_expired_results()["old_audit_rows"] == 0
    assert SQLExecution.objects.filter(id=job.id).exists()
    settings.SQL_WORKBENCH_AUDIT_RETENTION_DAYS = 90
    assert service.purge_expired_results()["old_audit_rows"] == int(
        state in service.TERMINAL_STATES
    )
    assert SQLExecution.objects.filter(id=job.id).exists() == (
        state not in service.TERMINAL_STATES
    )


def test_resource_limits_are_operator_configurable(tenants, settings):
    actor, project, _ = tenants[0]
    now = timezone.now()
    create_job(actor, project, state="failed")
    settings.SQL_WORKBENCH_MAX_ACTOR_QUERIES_24H = 1
    with patch("tracer.sql_workbench.service.connection_config"):
        with pytest.raises(service.AdmissionFull, match="audit submission"):
            service.submit(
                actor,
                project_id=project.id,
                sql="SELECT count() FROM spans",
                start=now - timedelta(hours=1),
                end=now,
            )


@pytest.mark.parametrize(
    "state", ["queued", "running", "succeeded", "failed", "cancelled"]
)
def test_dispatch_timeout_never_overwrites_an_accepted_workflow_state(
    tenants, django_capture_on_commit_callbacks, state
):
    actor, project, _ = tenants[0]
    now = timezone.now()
    stored = (
        {"columns": [{"name": "n", "type": "UInt64"}], "rows": [[7]]}
        if state == "succeeded"
        else {}
    )

    def accepted_then_timeout(*, args, **kwargs):
        SQLExecution.objects.filter(id=args[0]).update(
            state=state,
            result=stored,
            error="original worker error" if state == "failed" else "",
            finished_at=now if state in service.TERMINAL_STATES else None,
        )
        raise TimeoutError("ACK lost after acceptance")

    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch(
            "tracer.sql_workbench.tasks.execute_sql.apply_async",
            side_effect=accepted_then_timeout,
        ),
        django_capture_on_commit_callbacks(execute=True),
    ):
        job = service.submit(
            actor,
            project_id=project.id,
            sql="SELECT count() FROM spans",
            start=now - timedelta(hours=1),
            end=now,
        )
    assert job.state == ("failed" if state == "queued" else state)
    assert job.result == stored
    if state == "failed":
        assert job.error == "original worker error"
    if state == "succeeded":
        assert service.snapshot(job)["result"]["rows"] == [[7]]


def test_table_chart_without_axes_saves_and_round_trips(tenants):
    from tracer.views.sql_query import SavedSQLQueryViewSet

    actor, project, user = tenants[0]
    response = SavedSQLQueryViewSet.as_view({"post": "create"})(
        api_request(
            actor,
            user,
            "post",
            "/tracer/saved-sql-query/",
            {
                "project_id": str(project.id),
                "name": "table before execution",
                "sql": "SELECT name FROM spans",
                "chart": {"type": "table"},
            },
        )
    )
    assert response.status_code == 201
    saved = service.saved_query(actor, project.id, response.data["id"])
    assert saved.chart == {"type": "table"}


MALFORMED_IDS = [
    "------------------------------------",
    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa-",
    "a" * 36,
    "00000000-0000-0000-0000-00000000000-",
    # Not 36 characters: these reach the view too and are equally not found.
    "abc",
    "not-a-uuid",
    "00000000-0000-0000-0000-00000000000g",
    "a" * 37,
    "1%27%20OR%201%3D1",
]


def test_list_level_actions_are_never_routed_as_an_id():
    from django.urls import resolve

    for url, action in [
        ("/tracer/sql-query/catalog/", "catalog"),
        ("/tracer/sql-query/run/", "run"),
        ("/tracer/sql-query/run-saved/", "run_saved"),
        ("/tracer/sql-query/estimate/", "estimate"),
    ]:
        match = resolve(url)
        assert "pk" not in match.kwargs, url
        assert action in match.func.actions.values(), (url, match.func.actions)


@pytest.mark.parametrize("bad_id", MALFORMED_IDS)
@pytest.mark.parametrize(
    "method,path,data",
    [
        ("get", "/tracer/sql-query/{id}/", {}),
        ("post", "/tracer/sql-query/{id}/cancel/", {}),
        ("get", "/tracer/sql-query/{id}/download/", {"file_format": "csv"}),
        ("get", "/tracer/saved-sql-query/{id}/", {}),
        (
            "patch",
            "/tracer/saved-sql-query/{id}/",
            {"name": "n", "sql": "SELECT name FROM spans", "visibility": "private"},
        ),
        ("delete", "/tracer/saved-sql-query/{id}/", {}),
        ("post", "/tracer/saved-sql-query/{id}/add-widget/", {"dashboard_id": None}),
    ],
)
def test_malformed_route_ids_are_not_found_on_every_id_endpoint(
    tenants, bad_id, method, path, data
):
    """The router admits any single path segment; a non-UUID must be a 404."""
    from django.urls import resolve

    actor, project, user = tenants[0]
    url = path.format(id=bad_id)
    match = resolve(url)  # The real tracer router accepts the malformed id.
    assert match.kwargs == {"pk": bad_id}
    body = {"project_id": str(project.id), **data}
    if "dashboard_id" in body:
        body["dashboard_id"] = str(uuid4())
    response = match.func(api_request(actor, user, method, url, body), **match.kwargs)
    assert response.status_code == 404, response.data
    assert response["Cache-Control"] == "no-store"


@pytest.mark.parametrize("bad_id", MALFORMED_IDS)
def test_service_never_sends_a_malformed_id_to_the_database(tenants, bad_id):
    (actor, project, _), _ = tenants
    for call in (
        lambda: service.execution(actor, project.id, bad_id),
        lambda: service.cancel(actor, project.id, bad_id),
        lambda: service.export_result(actor, project.id, bad_id, "csv"),
        lambda: service.saved_query(actor, project.id, bad_id),
        lambda: service.delete_saved(actor, project.id, bad_id),
    ):
        with pytest.raises(AccessDenied):
            call()
    saved = service.save_query(
        actor,
        project_id=project.id,
        name="shared",
        sql="SELECT name FROM spans",
        visibility="project",
        chart={},
        window_hours=24,
    )
    with pytest.raises(AccessDenied):
        service.add_widget(actor, project.id, saved.id, bad_id)


def _submit(actor, project):
    now = timezone.now()
    return service.submit(
        actor,
        project_id=project.id,
        sql="SELECT count() FROM spans",
        start=now - timedelta(hours=1),
        end=now,
    )


def test_cancelled_scan_holds_its_admission_slot_until_the_worker_confirms(
    tenants, django_capture_on_commit_callbacks
):
    (actor, project, _), _ = tenants
    job = create_job(actor, project, state="running")
    snapshot = service.cancel(actor, project.id, job.id)
    assert snapshot["state"] == "cancelled"
    job.refresh_from_db()
    # The native query may still be running until the worker's watchdog acts.
    assert job.state == "cancelled" and job.finished_at is None
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        with pytest.raises(service.AdmissionFull):
            _submit(actor, project)
        assert service.confirm_stopped(job.id) == 1
        assert service.confirm_stopped(job.id) == 0
        _submit(actor, project)
    job.refresh_from_db()
    assert job.finished_at is not None


def test_a_queued_cancel_frees_the_slot_at_once(
    tenants, django_capture_on_commit_callbacks
):
    (actor, project, _), _ = tenants
    job = create_job(actor, project)
    service.cancel(actor, project.id, job.id)
    job.refresh_from_db()
    assert job.state == "cancelled" and job.finished_at is not None
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        _submit(actor, project)


def test_a_lost_worker_holds_a_cancelled_slot_only_until_its_deadline(
    tenants, django_capture_on_commit_callbacks
):
    (actor, project, _), _ = tenants
    create_job(
        actor,
        project,
        state="cancelled",
        deadline=timezone.now() - timedelta(seconds=1),
    )
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        _submit(actor, project)


def test_worker_releases_a_cancelled_slot_only_after_the_scan_returns(tenants):
    from tracer.sql_workbench.runner import QueryCancelled
    from tracer.sql_workbench.tasks import execute_sql

    (actor, project, _), _ = tenants
    job = create_job(actor, project)
    observed = {}

    def scan(sql, query_id, cancelled, *, table_filters):
        assert "'project_id = toUUID(" in table_filters and not cancelled()
        service.cancel(actor, project.id, job.id)  # The user cancels mid-scan.
        observed["held"] = SQLExecution.objects.filter(
            service.active_filter(timezone.now()), id=job.id
        ).exists()
        observed["seen"] = cancelled()  # The watchdog's next poll sees it.
        raise QueryCancelled()

    with patch("tracer.sql_workbench.tasks.run", scan):
        # The activity body itself: the Temporal wrapper would close this
        # test's transactional connection.
        execute_sql._original_func(str(job.id))
    assert observed == {"held": True, "seen": True}
    job.refresh_from_db()
    assert job.state == "cancelled" and job.finished_at is not None
    assert not SQLExecution.objects.filter(
        service.active_filter(timezone.now()), id=job.id
    ).exists()


def test_execution_deadline_is_anchored_to_the_actual_start(tenants, monkeypatch):
    from types import SimpleNamespace

    from tracer.sql_workbench import tasks
    from tracer.sql_workbench.runner import QueryCancelled

    (actor, project, _), _ = tenants
    job = create_job(actor, project)
    # The job waited 85 of its 90 queue seconds before a worker picked it up.
    started = timezone.now()
    SQLExecution.objects.filter(id=job.id).update(
        deadline=started + timedelta(seconds=5)
    )
    clock = SimpleNamespace(now=lambda: started)
    monkeypatch.setattr(tasks, "timezone", clock)

    def scan(sql, query_id, cancelled, *, table_filters):
        # Twenty seconds into a scan bounded by 30 s on the server: alive.
        clock.now = lambda: started + timedelta(seconds=20)
        if cancelled():
            raise QueryCancelled()
        return {
            "columns": [{"name": "n", "type": "UInt64"}],
            "rows": [[1]],
            "truncated": False,
            "row_count": 1,
            "result_bytes": 10,
            "elapsed_ms": 20_000,
        }

    with patch.object(tasks, "run", scan):
        tasks.execute_sql._original_func(str(job.id))
    job.refresh_from_db()
    assert job.state == "succeeded", job.error
    assert job.deadline == started + timedelta(seconds=service.EXECUTION_SECONDS)


def test_a_job_whose_queue_window_expired_never_starts(tenants):
    from tracer.sql_workbench import tasks

    (actor, project, _), _ = tenants
    job = create_job(actor, project, deadline=timezone.now() - timedelta(seconds=1))
    with patch.object(tasks, "run") as scan:
        tasks.execute_sql._original_func(str(job.id))
    scan.assert_not_called()
    job.refresh_from_db()
    assert job.state == "queued"
    assert service.snapshot(job)["state"] == "failed"


def _configs(project, count, *, deleted=False):
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.tests.eval_task_factories import make_template

    template = make_template(
        organization=project.organization, workspace=project.workspace
    )
    rows = [
        CustomEvalConfig(
            id=uuid4(),
            name=f"config-{uuid4()}",
            project=project,
            eval_template=template,
            config={},
            mapping={},
            filters={},
            deleted=deleted,
        )
        for _ in range(count)
    ]
    CustomEvalConfig.objects.bulk_create(rows)
    return [row.id for row in rows]


def test_eval_scope_keeps_deleted_configuration_results_flagged(tenants):
    from tracer.sql_workbench.catalog import sources

    (actor, project, _), (_, foreign, _) = tenants
    live = _configs(project, 2)
    gone = _configs(project, 1, deleted=True)
    _configs(foreign, 1)
    now = timezone.now()
    sql = "SELECT config_id, config_deleted, score FROM eval_results"
    scope = service.query_scope(actor, project, now - timedelta(hours=1), now, sql)
    assert set(scope.eval_config_ids) == set(live + gone)
    assert scope.deleted_eval_config_ids == tuple(gone)
    source = sources(scope)["eval_results"]
    assert (
        f"toUInt8(custom_eval_config_id IN (toUUID('{gone[0]}'))) AS config_deleted"
        in (source)
    )
    # Scopes without eval_results never load configurations at all.
    assert (
        service.query_scope(
            actor, project, now - timedelta(hours=1), now, "SELECT id FROM spans"
        ).eval_config_ids
        == ()
    )


def test_more_than_the_configuration_limit_is_explicit_never_partial(
    tenants, django_capture_on_commit_callbacks
):
    from tracer.sql_workbench.catalog import (
        EVAL_CONFIG_LIMIT_MESSAGE,
        MAX_CONFIGS,
        EvalConfigLimitExceeded,
    )
    from tracer.views.sql_query import SQLQueryViewSet

    (actor, project, user), _ = tenants
    _configs(project, MAX_CONFIGS - 1)
    _configs(project, 1, deleted=True)
    now = timezone.now()
    sql = "SELECT count() FROM eval_results"
    assert (
        len(
            service.query_scope(
                actor, project, now - timedelta(hours=1), now, sql
            ).eval_config_ids
        )
        == MAX_CONFIGS
    )
    assert service.eval_results_unavailable_reason(project) == ""
    _configs(project, 1, deleted=True)  # Deleted ones count: they keep results.
    with pytest.raises(EvalConfigLimitExceeded):
        service.query_scope(actor, project, now - timedelta(hours=1), now, sql)
    assert service.eval_results_unavailable_reason(project) == EVAL_CONFIG_LIMIT_MESSAGE
    catalog = SQLQueryViewSet.as_view({"get": "catalog"})(
        api_request(
            actor,
            user,
            "get",
            "/tracer/sql-query/catalog/",
            {"project_id": str(project.id)},
        )
    )
    datasets = {entry["name"]: entry for entry in catalog.data["datasets"]}
    assert datasets["eval_results"]["available"] is False
    assert datasets["eval_results"]["unavailable_reason"] == EVAL_CONFIG_LIMIT_MESSAGE
    assert datasets["spans"]["available"] is True
    run = SQLQueryViewSet.as_view({"post": "run"})
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async") as dispatch,
        django_capture_on_commit_callbacks(execute=True),
    ):
        refused = run(
            api_request(
                actor,
                user,
                "post",
                "/tracer/sql-query/run/",
                {
                    "project_id": str(project.id),
                    "sql": sql,
                    "start": (now - timedelta(hours=1)).isoformat(),
                    "end": now.isoformat(),
                },
            )
        )
        assert refused.status_code == 400
        assert "1,000" in str(refused.data)
        # Other datasets keep working for the same project.
        accepted = run(
            api_request(
                actor,
                user,
                "post",
                "/tracer/sql-query/run/",
                {
                    "project_id": str(project.id),
                    "sql": "SELECT count() FROM spans",
                    "start": (now - timedelta(hours=1)).isoformat(),
                    "end": now.isoformat(),
                },
            )
        )
        assert accepted.status_code == 202
    assert dispatch.call_count == 1


def test_a_hidden_simulation_project_is_never_queryable_or_savable(
    tenants, django_capture_on_commit_callbacks
):
    """A simulated run's project is left out by the project managers.

    Every SQL entry point resolves its project through ``access.authorize``,
    so even the exact id of a hidden project in the actor's own organization
    and workspace is refused before a scope, job or saved query is created.
    """
    from simulate.models.agent_definition import AgentDefinition
    from tfc.middleware.workspace_context import clear_workspace_context
    from tracer.models.sql_query import SavedSQLQuery
    from tracer.views.sql_query import SQLQueryViewSet

    (actor, project, user), _ = tenants
    clear_workspace_context()
    agent = AgentDefinition.objects.create(
        agent_name="simulated agent",
        description="simulated agent",
        agent_type="text",
        inbound=True,
        organization=project.organization,
        workspace=project.workspace,
    )
    clear_workspace_context()
    hidden = Project.with_simulation.create(
        name=f"simulation-{uuid4()}",
        organization=project.organization,
        workspace=project.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        simulation_agent=agent,
    )
    assert not Project.no_workspace_objects.filter(id=hidden.id).exists()
    now = timezone.now()
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async") as dispatch,
        django_capture_on_commit_callbacks(execute=True),
    ):
        for call in (
            lambda: authorize(actor, hidden.id),
            lambda: service.submit(
                actor,
                project_id=hidden.id,
                sql="SELECT count() FROM spans",
                start=now - timedelta(hours=1),
                end=now,
            ),
            lambda: service.save_query(
                actor,
                project_id=hidden.id,
                name="hidden",
                sql="SELECT count() FROM spans",
                visibility="project",
                chart={},
                window_hours=24,
            ),
            lambda: service.history(actor, hidden.id, 0),
            lambda: list(service.saved_queries(actor, hidden.id)),
        ):
            with pytest.raises(AccessDenied):
                call()
    dispatch.assert_not_called()
    assert not SQLExecution.objects.filter(project_id=hidden.id).exists()
    assert not SavedSQLQuery.all_objects.filter(project_id=hidden.id).exists()
    response = SQLQueryViewSet.as_view({"get": "catalog"})(
        api_request(
            actor,
            user,
            "get",
            "/tracer/sql-query/catalog/",
            {"project_id": str(hidden.id)},
        )
    )
    assert response.status_code == 403


# Advisory pre-run estimate: same authorization and compiled SQL as a run, no
# execution record or admission slot, rate-limited per user, never blocking.
SPANS_LAYOUT = ("project_id, id", "ReplacingMergeTree ORDER BY (project_id, id)")
SHARED_LAYOUT = (
    "trace_id, custom_eval_config_id, id",
    "ReplacingMergeTree ORDER BY (trace_id, custom_eval_config_id, id) "
    "SETTINGS index_granularity = 8192",
)


def _raw_estimate(database, *, eval_rows=500):
    return {
        "tables": [
            {
                "database": database,
                "table": "spans",
                "parts": 2,
                "rows": 9000,
                "marks": 3,
            },
            {
                "database": database,
                "table": "tracer_eval_logger",
                "parts": 1,
                "rows": eval_rows,
                "marks": 1,
            },
        ],
        "layout": {"spans": SPANS_LAYOUT, "tracer_eval_logger": SHARED_LAYOUT},
    }


def _estimate_request(actor, user, project, sql):
    from tracer.views.sql_query import SQLQueryViewSet

    now = timezone.now()
    return SQLQueryViewSet.as_view({"post": "estimate"})(
        api_request(
            actor,
            user,
            "post",
            "/tracer/sql-query/estimate/",
            {
                "project_id": str(project.id),
                "sql": sql,
                "start": (now - timedelta(hours=1)).isoformat(),
                "end": now.isoformat(),
            },
        )
    )


@pytest.fixture
def estimate_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def test_estimate_reports_tenant_local_sources_and_withholds_shared_indexes(
    tenants, estimate_cache, settings
):
    (actor, project, user), _ = tenants
    database = settings.CLICKHOUSE_V2["CH25_DATABASE"]
    sql = "SELECT e.score FROM eval_results AS e JOIN spans AS s ON e.span_id = s.id"
    with patch(
        "tracer.sql_workbench.runner.estimate",
        return_value=_raw_estimate(database),
    ) as index:
        response = _estimate_request(actor, user, project, sql)
    assert response.status_code == 200, response.data
    assert response["Cache-Control"] == "no-store"
    # The compiled, scoped SQL is what is estimated, with the server filter.
    compiled = index.call_args.args[0]
    assert f"project_id = toUUID('{project.id}')" in compiled
    assert index.call_args.kwargs["table_filters"].startswith("{'")
    data = response.data
    assert data["status"] == "ok"
    assert data["max_rows_to_read"] == 10_000_000
    assert data["exceeds_scan_bound"] is None
    by_dataset = {entry["dataset"]: entry for entry in data["sources"]}
    # Granules x granularity, never the raw granule row total (9000).
    assert by_dataset["spans"] == {
        "dataset": "spans",
        "estimated": True,
        "parts": 2,
        "marks": 3,
        "max_rows": 3 * 8192,
        "reason": "",
    }
    shared = by_dataset["eval_results"]
    assert shared["estimated"] is False
    assert shared["parts"] is shared["marks"] is shared["max_rows"] is None
    assert "shared across projects" in shared["reason"]
    assert "9000" not in str(data) and "500" not in str(data)
    # Advisory: no execution record, so no admission slot or audit row.
    assert not SQLExecution.objects.filter(project=project).exists()


def test_estimate_withholds_scan_bound_when_any_source_is_withheld(
    tenants, estimate_cache, settings
):
    from tracer.sql_workbench.runner import QUERY_SETTINGS

    (actor, project, user), _ = tenants
    database = settings.CLICKHOUSE_V2["CH25_DATABASE"]
    bound = QUERY_SETTINGS["max_rows_to_read"]
    responses = []
    for eval_rows in (bound - 9001, bound - 8999):
        with (
            patch(
                "tracer.sql_workbench.runner.estimate",
                return_value=_raw_estimate(database, eval_rows=eval_rows),
            ),
            patch("tracer.sql_workbench.service.time.monotonic", return_value=42.0),
        ):
            response = _estimate_request(
                actor,
                user,
                project,
                "SELECT e.score FROM eval_results AS e JOIN spans AS s ON e.span_id = s.id",
            )
        assert response.status_code == 200
        assert response["Cache-Control"] == "no-store"
        assert str(eval_rows) not in str(response.data)
        responses.append(response.data)
    # Compare the whole serialized response, not just the withheld source. Only
    # elapsed time is controlled; raw rows cross the actual configured bound.
    assert responses[0] == responses[1]
    assert responses[0]["exceeds_scan_bound"] is None
    assert responses[0]["elapsed_ms"] == 0
    assert not SQLExecution.objects.filter(project=project).exists()


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("estimate timed out"),
        service.QueryUnavailable(
            "Configure the dedicated read-only SQL connection first."
        ),
        RuntimeError("server error 202: too many simultaneous queries"),
    ],
)
def test_estimate_failures_are_advisory_and_never_block_a_run(
    tenants, estimate_cache, failure, django_capture_on_commit_callbacks
):
    (actor, project, user), _ = tenants
    with patch("tracer.sql_workbench.runner.estimate", side_effect=failure):
        response = _estimate_request(actor, user, project, "SELECT count() FROM spans")
    assert response.status_code == 200
    assert response.data["status"] == "unavailable"
    assert response.data["exceeds_scan_bound"] is None
    assert response.data["sources"] == []
    assert str(failure) not in str(response.data)
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        job = _submit(actor, project)
    assert job.state == "queued"


def test_estimate_holds_no_admission_slot(tenants, estimate_cache, settings):
    """An actor whose one slot is taken can still estimate, and estimating
    never takes the slot a run needs."""
    (actor, project, user), _ = tenants
    create_job(actor, project, state="running")
    database = settings.CLICKHOUSE_V2["CH25_DATABASE"]
    with patch(
        "tracer.sql_workbench.runner.estimate", return_value=_raw_estimate(database)
    ):
        for _ in range(3):
            response = _estimate_request(
                actor, user, project, "SELECT count() FROM spans"
            )
            assert response.status_code == 200
    assert SQLExecution.objects.filter(project=project).count() == 1


def test_estimate_uses_run_authorization_and_validation(
    tenants, estimate_cache, settings
):
    (actor, project, user), (_, foreign, _) = tenants
    database = settings.CLICKHOUSE_V2["CH25_DATABASE"]
    with patch(
        "tracer.sql_workbench.runner.estimate", return_value=_raw_estimate(database)
    ) as index:
        assert (
            _estimate_request(actor, user, foreign, "SELECT 1 FROM spans").status_code
            == 403
        )
        rejected = _estimate_request(actor, user, project, "SELECT * FROM system.users")
        assert rejected.status_code == 400
        assert "detail" in rejected.data
        # A read-only member may run queries, so they may estimate them.
        OrganizationMembership.no_workspace_objects.filter(
            user_id=actor.user_id
        ).update(level=1)
        assert (
            _estimate_request(actor, user, project, "SELECT 1 FROM spans").status_code
            == 200
        )
        WorkspaceMembership.no_workspace_objects.filter(user_id=actor.user_id).update(
            is_active=False
        )
        assert (
            _estimate_request(actor, user, project, "SELECT 1 FROM spans").status_code
            == 403
        )
    # Only the one authorized, valid request reached ClickHouse.
    assert index.call_count == 1


def test_estimates_are_rate_limited_per_user(tenants, estimate_cache, settings):
    (actor, project, user), (other, other_project, other_user) = tenants
    settings.SQL_WORKBENCH_ESTIMATES_PER_MINUTE = 2
    database = settings.CLICKHOUSE_V2["CH25_DATABASE"]
    with (
        patch("tracer.sql_workbench.service._estimate_window", return_value=1),
        patch(
            "tracer.sql_workbench.runner.estimate",
            return_value=_raw_estimate(database),
        ) as index,
    ):
        codes = [
            _estimate_request(actor, user, project, "SELECT 1 FROM spans").status_code
            for _ in range(3)
        ]
        assert codes == [200, 200, 429]
        # Another user has an independent budget.
        assert (
            _estimate_request(
                other, other_user, other_project, "SELECT 1 FROM spans"
            ).status_code
            == 200
        )
    assert index.call_count == 3
    with (
        patch("tracer.sql_workbench.service._estimate_window", return_value=2),
        patch(
            "tracer.sql_workbench.runner.estimate",
            return_value=_raw_estimate(database),
        ),
    ):
        assert (
            _estimate_request(actor, user, project, "SELECT 1 FROM spans").status_code
            == 200
        )


def test_estimate_without_a_working_limiter_is_refused(tenants, estimate_cache):
    (actor, project, user), _ = tenants
    with (
        patch("django.core.cache.cache.incr", side_effect=ConnectionError("down")),
        patch("tracer.sql_workbench.runner.estimate") as index,
    ):
        response = _estimate_request(actor, user, project, "SELECT 1 FROM spans")
    assert response.status_code == 429
    index.assert_not_called()


@pytest.mark.parametrize(
    "sorting_key,ownership,project_column,local",
    [
        ("project_id, id", ("project_id", "org_id"), "project_id", True),
        (
            "org_id, project_id, start_time",
            ("project_id", "org_id"),
            "project_id",
            True,
        ),
        (
            "project_id, toDate(created_at), trace_id, id",
            ("project_id",),
            "project_id",
            True,
        ),
        (
            "trace_id, custom_eval_config_id, id",
            ("custom_eval_config_id",),
            "custom_eval_config_id",
            False,
        ),
        (
            "label_id, created_at, id",
            ("organization_id", "workspace_id", "tracer_project_id"),
            "tracer_project_id",
            False,
        ),
        (
            "organization_id, created_at, id",
            ("organization_id", "workspace_id", "tracer_project_id"),
            "tracer_project_id",
            False,
        ),
        ("id", ("project_id",), "project_id", False),
        ("", ("project_id",), "project_id", False),
        ("tuple(project_id, id)", ("project_id",), "project_id", False),
    ],
)
def test_estimates_are_reported_only_for_project_leading_sort_keys(
    sorting_key, ownership, project_column, local
):
    assert service._tenant_local(sorting_key, ownership, project_column) is local


# Result reuse: the same person, under the same access and catalog, repeating
# the same (normalized) query over the same exact UTC window within the
# reuse window gets their completed result back without a new scan.
REUSE_SQL = "SELECT count() FROM spans"


def _reuse_window():
    end = timezone.now().replace(second=10, microsecond=0)
    return end - timedelta(hours=1), end


def _submit_counting(capture, actor, project, sql=REUSE_SQL, start=None, end=None):
    """Submit with the worker dispatch captured; returns (job, dispatch mock).

    ``capture`` is pytest-django's on-commit capture: it runs the dispatch
    callback while the patches are still active.
    """
    if start is None:
        start, end = _reuse_window()
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async") as dispatch,
        capture(execute=True),
    ):
        job = service.submit(
            actor, project_id=project.id, sql=sql, start=start, end=end
        )
    return job, dispatch


def _complete(job, *, finished_at=None):
    SQLExecution.objects.filter(id=job.id).update(
        state="succeeded",
        result={"columns": [{"name": "n", "type": "UInt64"}], "rows": [[7]]},
        output_bytes=40,
        finished_at=finished_at or timezone.now(),
    )


def _colleague(actor, level=3):
    """Another person with the same role in the same organization and workspace."""
    org = Organization.objects.get(id=actor.organization_id)
    user = User(
        id=uuid4(), email=f"sql-{uuid4()}@example.test", name="Peer", organization=org
    )
    User.objects.bulk_create([user])
    om = OrganizationMembership(id=uuid4(), user=user, organization=org, level=level)
    OrganizationMembership.no_workspace_objects.bulk_create([om])
    WorkspaceMembership.no_workspace_objects.bulk_create(
        [
            WorkspaceMembership(
                id=uuid4(),
                user=user,
                workspace_id=actor.workspace_id,
                level=level,
                organization_membership=om,
            )
        ]
    )
    return Actor(user.id, actor.organization_id, actor.workspace_id), user


def test_an_identical_request_reuses_the_completed_result_without_a_scan(
    tenants, django_capture_on_commit_callbacks
):
    (actor, project, user), _ = tenants
    start, end = _reuse_window()
    first, dispatch = _submit_counting(
        django_capture_on_commit_callbacks, actor, project, start=start, end=end
    )
    assert dispatch.call_count == 1 and len(first.cache_key) == 64
    _complete(first)
    # Whitespace, a comment and keyword case differ; the exact window is equal.
    again, dispatch = _submit_counting(
        django_capture_on_commit_callbacks,
        actor,
        project,
        sql="select   count()\n-- same question\nfrom spans",
        start=start,
        end=end,
    )
    assert again.id == first.id
    dispatch.assert_not_called()
    assert SQLExecution.objects.filter(project=project).count() == 1
    # Through the API: the existing completed snapshot, no new execution.
    from tracer.views.sql_query import SQLQueryViewSet

    with patch("tracer.sql_workbench.service.connection_config"):
        response = SQLQueryViewSet.as_view({"post": "run"})(
            api_request(
                actor,
                user,
                "post",
                "/tracer/sql-query/run/",
                {
                    "project_id": str(project.id),
                    "sql": REUSE_SQL,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
            )
        )
    assert response.status_code == 202
    assert response.data["id"] == str(first.id)
    assert response.data["state"] == "succeeded"
    assert response.data["result"]["rows"] == [[7]]
    assert SQLExecution.objects.filter(project=project).count() == 1


@pytest.mark.parametrize("shift", [timedelta(seconds=39), timedelta(microseconds=1)])
def test_reuse_never_substitutes_a_different_exact_window(
    tenants, django_capture_on_commit_callbacks, shift
):
    (actor, project, _), _ = tenants
    start = timezone.now().replace(second=1, microsecond=0) - timedelta(hours=1)
    end = start + timedelta(seconds=1)
    first, dispatch = _submit_counting(
        django_capture_on_commit_callbacks, actor, project, start=start, end=end
    )
    assert dispatch.call_count == 1
    _complete(first)
    second, dispatch = _submit_counting(
        django_capture_on_commit_callbacks,
        actor,
        project,
        start=start + shift,
        end=end + shift,
    )
    assert second.id != first.id
    assert second.cache_key != first.cache_key
    assert (second.start, second.end) == (start + shift, end + shift)
    assert dispatch.call_count == 1


def test_reuse_never_crosses_people_projects_or_tenants(
    tenants, django_capture_on_commit_callbacks
):
    (actor, project, _), (other, foreign, _) = tenants
    first, _ = _submit_counting(django_capture_on_commit_callbacks, actor, project)
    _complete(first)
    # A colleague with the same role in the same project scans for themselves.
    peer, _ = _colleague(actor)
    theirs, dispatch = _submit_counting(
        django_capture_on_commit_callbacks, peer, project
    )
    assert theirs.id != first.id and dispatch.call_count == 1
    assert theirs.cache_key != first.cache_key
    # Another tenant's identical query never sees this tenant's result.
    foreign_job, dispatch = _submit_counting(
        django_capture_on_commit_callbacks, other, foreign
    )
    assert foreign_job.id != first.id and dispatch.call_count == 1
    # Even a forged row carrying the same key under another person or
    # project is never returned: the lookup is pinned to the requester.
    now = timezone.now()
    for owner, where in [(peer, project), (other, foreign)]:
        assert service.reusable_result(owner, where, first.cache_key, now) is None
    forged = create_job(
        peer, project, state="succeeded", cache_key=first.cache_key, finished_at=now
    )
    assert service.reusable_result(actor, project, first.cache_key, now) == first
    assert service.reusable_result(peer, project, first.cache_key, now) == forged


def test_reuse_ends_with_a_membership_or_catalog_or_scope_change(
    tenants, django_capture_on_commit_callbacks, monkeypatch
):
    (actor, project, _), _ = tenants
    start, end = _reuse_window()

    def run(sql=REUSE_SQL, **window):
        job, dispatch = _submit_counting(
            django_capture_on_commit_callbacks,
            actor,
            project,
            sql=sql,
            start=window.get("start", start),
            end=window.get("end", end),
        )
        _complete(job)
        return job, dispatch.call_count

    first, _ = run()
    assert run()[0].id == first.id
    # A role change, even one written without touching updated_at.
    OrganizationMembership.no_workspace_objects.filter(
        user_id=actor.user_id, organization_id=actor.organization_id
    ).update(level=8)
    promoted, scans = run()
    assert promoted.id != first.id and scans == 1
    # Any saved edit of a membership row (updated_at moves).
    membership = WorkspaceMembership.no_workspace_objects.get(
        user_id=actor.user_id, workspace_id=actor.workspace_id
    )
    membership.save()
    edited, scans = run()
    assert edited.id != promoted.id and scans == 1
    # A catalog version bump.
    monkeypatch.setattr(service, "CATALOG_VERSION", service.CATALOG_VERSION + 1)
    bumped, scans = run()
    assert bumped.id != edited.id and scans == 1
    # A different minute, a different query.
    moved, scans = run(start=start - timedelta(minutes=1), end=end)
    assert moved.id != bumped.id and scans == 1
    other_sql, scans = run(sql="SELECT count() FROM traces")
    assert other_sql.id != moved.id and scans == 1
    # The evaluation configurations in scope.
    evals = "SELECT count() FROM eval_results"
    before_config, _ = run(sql=evals)
    assert run(sql=evals)[0].id == before_config.id
    _configs(project, 1)
    added, scans = run(sql=evals)
    assert added.id != before_config.id and scans == 1


def test_reuse_window_states_and_configuration(
    tenants, django_capture_on_commit_callbacks, settings
):
    (actor, project, _), _ = tenants
    start, end = _reuse_window()
    first, _ = _submit_counting(
        django_capture_on_commit_callbacks, actor, project, start=start, end=end
    )
    # Never a job that has not succeeded.
    for state in ("queued", "running", "failed", "cancelled"):
        SQLExecution.objects.filter(id=first.id).update(
            state=state, finished_at=timezone.now()
        )
        assert (
            service.reusable_result(actor, project, first.cache_key, timezone.now())
            is None
        )
    # Older than the reuse window: scanned again.
    _complete(first, finished_at=timezone.now() - timedelta(seconds=61))
    stale, dispatch = _submit_counting(
        django_capture_on_commit_callbacks, actor, project, start=start, end=end
    )
    assert stale.id != first.id and dispatch.call_count == 1
    _complete(stale)
    # An expired result body is never reused.
    SQLExecution.objects.filter(id=stale.id).update(expires_at=timezone.now())
    assert (
        service.reusable_result(actor, project, stale.cache_key, timezone.now()) is None
    )
    # 0 turns reuse off entirely; no key is stored.
    settings.SQL_WORKBENCH_RESULT_CACHE_SECONDS = 0
    off, dispatch = _submit_counting(
        django_capture_on_commit_callbacks, actor, project, start=start, end=end
    )
    assert off.cache_key == "" and dispatch.call_count == 1
    # A configurable window; invalid values fail closed.
    settings.SQL_WORKBENCH_RESULT_CACHE_SECONDS = 300
    _complete(first, finished_at=timezone.now() - timedelta(seconds=200))
    SQLExecution.objects.exclude(id=first.id).filter(project=project).delete()
    assert (
        _submit_counting(
            django_capture_on_commit_callbacks, actor, project, start=start, end=end
        )[0].id
        == first.id
    )
    for invalid in (-1, 3601, "60", None):
        settings.SQL_WORKBENCH_RESULT_CACHE_SECONDS = invalid
        with pytest.raises(service.QueryUnavailable):
            _submit_counting(
                django_capture_on_commit_callbacks, actor, project, start=start, end=end
            )


def test_reuse_needs_no_admission_slot_but_a_new_scan_still_does(
    tenants, django_capture_on_commit_callbacks
):
    (actor, project, _), _ = tenants
    first, _ = _submit_counting(django_capture_on_commit_callbacks, actor, project)
    _complete(first)
    # The person's one slot is taken by another running query.
    create_job(actor, project, state="running")
    again, dispatch = _submit_counting(
        django_capture_on_commit_callbacks, actor, project
    )
    assert again.id == first.id
    dispatch.assert_not_called()
    with pytest.raises(service.AdmissionFull):
        _submit_counting(
            django_capture_on_commit_callbacks,
            actor,
            project,
            sql="SELECT count() FROM traces",
        )


def test_reuse_requires_current_access(tenants, django_capture_on_commit_callbacks):
    (actor, project, _), _ = tenants
    first, _ = _submit_counting(django_capture_on_commit_callbacks, actor, project)
    _complete(first)
    WorkspaceMembership.no_workspace_objects.filter(user_id=actor.user_id).update(
        is_active=False
    )
    with pytest.raises(AccessDenied):
        _submit_counting(django_capture_on_commit_callbacks, actor, project)


@pytest.mark.parametrize("when", ["queued", "running"])
@pytest.mark.parametrize(
    "change", ["membership", "catalog", "configuration", "disabled"]
)
def test_reuse_key_is_cleared_if_context_changes_before_completion(
    tenants, django_capture_on_commit_callbacks, monkeypatch, settings, when, change
):
    from tracer.sql_workbench import tasks

    (actor, project, _), _ = tenants
    job, _ = _submit_counting(
        django_capture_on_commit_callbacks,
        actor,
        project,
        sql="SELECT count() FROM eval_results",
    )
    original_key = job.cache_key
    assert original_key

    def mutate():
        if change == "membership":
            OrganizationMembership.no_workspace_objects.filter(
                user_id=actor.user_id, organization_id=actor.organization_id
            ).update(level=8)
        elif change == "catalog":
            monkeypatch.setattr(service, "CATALOG_VERSION", service.CATALOG_VERSION + 1)
        elif change == "configuration":
            _configs(project, 1)
        else:
            settings.SQL_WORKBENCH_RESULT_CACHE_SECONDS = 0

    def scan(*args, **kwargs):
        if when == "running":
            mutate()
        return {
            "columns": [{"name": "n", "type": "UInt64"}],
            "rows": [[0]],
            "row_count": 1,
            "result_bytes": 20,
            "elapsed_ms": 1,
        }

    if when == "queued":
        mutate()
    with patch.object(tasks, "run", scan):
        tasks.execute_sql._original_func(str(job.id))
    job.refresh_from_db()
    assert job.state == "succeeded", job.error
    assert job.cache_key == ""
    assert service.reusable_result(actor, project, original_key, timezone.now()) is None


def test_reuse_key_tracks_connection_and_effective_legacy_roles(
    tenants, settings, monkeypatch
):
    from tfc.constants.roles import OrganizationRoles

    (actor, project, _), _ = tenants
    start, end = _reuse_window()
    scope = service.query_scope(actor, project, start, end, REUSE_SQL)

    def key():
        return service.result_cache_key(actor, project, scope, REUSE_SQL)

    before = key()
    settings.CLICKHOUSE_V2 = {
        **settings.CLICKHOUSE_V2,
        "CH25_HOST": "another-store.invalid",
    }
    assert key() != before
    before = key()
    monkeypatch.setenv("SQL_WORKBENCH_CH_USER", "another-read-only-principal")
    assert key() != before
    OrganizationMembership.no_workspace_objects.filter(user_id=actor.user_id).update(
        level=None, role=OrganizationRoles.MEMBER
    )
    before = key()
    OrganizationMembership.no_workspace_objects.filter(user_id=actor.user_id).update(
        role=OrganizationRoles.MEMBER_VIEW_ONLY
    )
    assert key() != before


def test_saved_runs_preserve_fresh_execution_behavior(
    tenants, django_capture_on_commit_callbacks
):
    from tracer.views.sql_query import SQLQueryViewSet

    (actor, project, user), _ = tenants
    start, end = _reuse_window()
    first, _ = _submit_counting(
        django_capture_on_commit_callbacks, actor, project, start=start, end=end
    )
    _complete(first)
    saved = service.save_query(
        actor,
        project_id=project.id,
        name="Fresh dashboard",
        sql=REUSE_SQL,
        visibility="project",
        chart={"type": "table"},
        window_hours=1,
    )
    with (
        patch("tracer.sql_workbench.service.connection_config"),
        patch("tracer.sql_workbench.tasks.execute_sql.apply_async") as dispatch,
        django_capture_on_commit_callbacks(execute=True),
    ):
        response = SQLQueryViewSet.as_view({"post": "run_saved"})(
            api_request(
                actor,
                user,
                "post",
                "/tracer/sql-query/run-saved/",
                {
                    "project_id": str(project.id),
                    "saved_query_id": str(saved.id),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
            )
        )
    assert response.status_code == 202
    assert response.data["id"] != str(first.id)
    assert dispatch.call_count == 1
    assert SQLExecution.objects.get(id=response.data["id"]).cache_key == ""
