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
    return SQLExecution.objects.create(
        project=project,
        organization_id=actor.organization_id,
        workspace_id=actor.workspace_id,
        created_by_id=actor.user_id,
        sql="SELECT * FROM spans",
        start=now - timedelta(hours=1),
        end=now,
        deadline=now + timedelta(seconds=90),
        expires_at=now + timedelta(hours=24),
        **kwargs,
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
]


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
    """The router admits any 36 hex/dash characters; a non-UUID must be a 404."""
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
