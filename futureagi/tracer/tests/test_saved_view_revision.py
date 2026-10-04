"""TH-4798: ownership, preconditions, per-user order, and migration regressions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
import importlib
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from tracer.models.saved_view import SavedView
from tracer.tests.test_saved_view import (
    BASE_URL, _view_url, saved_view, shared_view, other_user,
    other_workspace, other_workspace_project,
)


def member(workspace, role):
    user = User.objects.create_user(
        email=f"{role}-{uuid.uuid4()}@example.com", password="testpassword123",
        name=role, organization=workspace.organization, organization_role="Member",
    )
    from accounts.models.organization_membership import OrganizationMembership
    org_membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=workspace.organization, role="Member", is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user, workspace=workspace, role=role, is_active=True, organization_membership=org_membership,
    )
    return user


@pytest.fixture
def writer_b(workspace):
    return member(workspace, "workspace_member")


@pytest.fixture
def viewer_c(workspace):
    return member(workspace, "workspace_viewer")


@pytest.fixture
def admin_d(workspace):
    return member(workspace, "workspace_admin")


@pytest.fixture
def client_for(workspace):
    clients = []
    def make(user):
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        clients.append(client)
        return client
    yield make
    for client in clients:
        client.stop_workspace_injection()


def order_model():
    from tracer.models.saved_view import SavedViewTabOrder
    return SavedViewTabOrder


def reorder(client, project, ids, revision=0, **extra):
    payload = {"order": [{"id": str(pk), "position": i} for i, pk in enumerate(ids)]}
    if project:
        payload["project_id"] = str(project.id)
    if revision is not None:
        payload["expected_revision"] = revision
    return client.post(f"{BASE_URL}/reorder/", {**payload, **extra}, format="json")


@pytest.mark.django_db
class TestSavedViewRevision:
    @pytest.mark.parametrize("tab_type", ["traces", "spans"])
    def test_create_restore_revision(self, auth_client, client_for, user, project, tab_type):
        config = {"filters": [], "columns": [], "sort": [], "display": {"compareEnabled": False},
                  "widgets": [], "conversation_id": "fixture", "sub_tab": "trace",
                  "compare_filters": [], "compare_date_filter": {}, "extra_filters": [],
                  "compare_extra_filters": []}
        response = auth_client.post(f"{BASE_URL}/", {
            "project_id": str(project.id), "name": "Full", "tab_type": tab_type, "config": config,
        }, format="json")
        assert response.status_code == 200
        data = client_for(user).get(f"{BASE_URL}/?project_id={project.id}").json()["result"]
        record = data["custom_views"][0]
        assert record["revision"] == 1
        assert record["config"] == config
        assert all(record[key] for key in ("is_owner", "can_edit", "can_delete"))
        assert data["tab_order"] == {"revision": 0, "order": [record["id"]]}

    def test_share_saved_version(self, auth_client, saved_view, writer_b, client_for):
        before = saved_view.config
        response = auth_client.patch(_view_url(saved_view), {
            "visibility": "project", "expected_revision": 1,
        }, format="json")
        assert response.status_code == 200
        result = client_for(writer_b).get(_view_url(saved_view)).json()["result"]
        assert result["revision"] == 2
        assert result["config"] == before

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_nonowner_forbidden_before_precondition(self, client_for, writer_b, shared_view, method):
        response = getattr(client_for(writer_b), method)(_view_url(shared_view), {}, format="json")
        assert response.status_code == 403
        assert response.json()["code"] == "permission_denied"
        shared_view.refresh_from_db()
        assert shared_view.revision == 1 and not shared_view.deleted

    def test_writer_can_duplicate(self, client_for, writer_b, shared_view):
        response = client_for(writer_b).post(_view_url(shared_view, "duplicate/"), {}, format="json")
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["created_by"]["id"] == str(writer_b.id)
        assert result["visibility"] == "personal" and result["revision"] == 1
        shared_view.refresh_from_db()
        assert shared_view.revision == 1

    @pytest.mark.parametrize("orphan", [False, True])
    def test_admin_cleanup_only(self, client_for, admin_d, writer_b, shared_view, saved_view, orphan):
        if orphan:
            shared_view.created_by = None
            shared_view.save()
            saved_view.created_by = None
            saved_view.save()
        admin = client_for(admin_d)
        assert admin.patch(_view_url(shared_view), {"expected_revision": 1}, format="json").status_code == 403
        assert client_for(writer_b).delete(_view_url(shared_view) + "&expected_revision=1").status_code == 403
        assert admin.delete(_view_url(saved_view) + "&expected_revision=1").status_code == 404
        assert admin.delete(_view_url(shared_view) + "&expected_revision=1").status_code == 200
        shared_view.refresh_from_db()
        assert shared_view.deleted

    def test_default_workspace_admin_is_checked_on_record(self, workspace, admin_d, client_for, project, user):
        # Modern schema permits one default workspace. Simulate the legacy
        # request's default flag while keeping the database constraint intact.
        workspace.is_default = False
        workspace.save()
        sibling = Workspace.no_workspace_objects.create(
            name="Sibling", organization=workspace.organization, is_default=True, created_by=user,
        )
        project.workspace = sibling
        project.save()
        record = SavedView.no_workspace_objects.create(project=project, workspace=sibling,
            created_by=user, name="Sibling shared", tab_type="traces", visibility="project")
        original_from_db = Workspace.from_db
        def legacy_from_db(*args, **kwargs):
            instance = original_from_db(*args, **kwargs)
            if instance.id == workspace.id:
                instance.is_default = True
            return instance
        with patch.object(Workspace, "from_db", side_effect=legacy_from_db):
            response = client_for(admin_d).delete(_view_url(record) + "&expected_revision=1")
        assert response.status_code == 403

    @pytest.mark.parametrize("actor,expected", [
        ("user", (True, True, True)), ("writer_b", (False, False, False)),
        ("viewer_c", (False, False, False)), ("admin_d", (False, False, True)),
    ])
    def test_policy_matrix(self, request, client_for, shared_view, actor, expected):
        client = client_for(request.getfixturevalue(actor))
        for result in (client.get(_view_url(shared_view)).json()["result"],
                       client.get(f"{BASE_URL}/?project_id={shared_view.project_id}").json()["result"]["custom_views"][0]):
            assert tuple(result[k] for k in ("is_owner", "can_edit", "can_delete")) == expected

    @pytest.mark.parametrize("visibility", ["personal", "project"])
    def test_config_save_preserves_visibility(self, auth_client, saved_view, visibility):
        saved_view.visibility = visibility
        saved_view.save()
        response = auth_client.put(_view_url(saved_view), {
            "expected_revision": 1, "config": {"filters": [], "display": {"errors": False}},
        }, format="json")
        assert response.status_code == 200
        assert response.json()["result"]["visibility"] == visibility
        assert response.json()["result"]["revision"] == 2
        saved_view.refresh_from_db()
        assert not hasattr(saved_view, "expected_revision")

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_missing_revision(self, auth_client, saved_view, method):
        response = getattr(auth_client, method)(_view_url(saved_view), {}, format="json")
        assert response.status_code == 428
        assert response.json()["code"] == "revision_required"
        assert "Refresh" in str(response.json())

    @pytest.mark.parametrize("payload", [{"name": "Renamed"}, {"visibility": "project"},
        {"config": {"filters": []}}, {"name": ""}])
    def test_stale_write_before_body_validation(self, auth_client, saved_view, payload):
        first = auth_client.patch(_view_url(saved_view), {"expected_revision": 1,
            "config": {"display": {"rowHeight": "tall"}}}, format="json")
        assert first.status_code == 200
        second = auth_client.patch(_view_url(saved_view), {**payload, "expected_revision": 1}, format="json")
        assert second.status_code == 409
        assert second.json()["code"] == "revision_conflict"
        assert second.json()["result"]["current"] == first.json()["result"]
        assert auth_client.delete(_view_url(saved_view) + "&expected_revision=1").status_code == 409

    def test_delete_then_update_is_not_found(self, auth_client, saved_view):
        assert auth_client.delete(_view_url(saved_view) + "&expected_revision=1").status_code == 200
        assert auth_client.put(_view_url(saved_view), {"expected_revision": 1}, format="json").status_code == 404

    def test_create_retry_does_not_duplicate(self, auth_client, project):
        payload = {"project_id": str(project.id), "name": "Retry", "tab_type": "traces"}
        assert auth_client.post(f"{BASE_URL}/", payload, format="json").status_code == 200
        assert auth_client.post(f"{BASE_URL}/", payload, format="json").status_code == 400
        assert SavedView.objects.filter(project=project, name="Retry").count() == 1

    def test_update_retry_conflicts(self, auth_client, saved_view):
        payload = {"expected_revision": 1, "name": "Retry"}
        assert auth_client.put(_view_url(saved_view), payload, format="json").status_code == 200
        assert auth_client.put(_view_url(saved_view), payload, format="json").status_code == 409
        saved_view.refresh_from_db()
        assert saved_view.revision == 2

    def test_primary_and_replica_list_branches(self, auth_client, saved_view):
        from django.db.models.query import QuerySet
        original = QuerySet.using
        aliases = []
        def using(queryset, alias):
            if queryset.model in (SavedView, order_model()):
                aliases.append(alias)
                return original(queryset, "default")
            return original(queryset, alias)
        with patch("tracer.views.saved_view.DATABASE_FOR_SAVED_VIEW_LIST", "replica"), patch.object(QuerySet, "using", using):
            assert auth_client.get(f"{BASE_URL}/?project_id={saved_view.project_id}").status_code == 200
            assert aliases == ["replica", "replica"]
            aliases.clear()
            assert auth_client.get(f"{BASE_URL}/?project_id={saved_view.project_id}&consistency=primary").status_code == 200
            assert aliases == ["default", "default"]


@pytest.mark.django_db
class TestSavedViewTabOrder:
    def test_required_and_create(self, auth_client, project, saved_view):
        assert reorder(auth_client, project, [saved_view.id], None).status_code == 428
        response = reorder(auth_client, project, [saved_view.id])
        assert response.status_code == 200
        assert response.json()["result"]["tab_order"] == {"revision": 1, "order": [str(saved_view.id)]}

    def test_personal_order_and_conflict(self, auth_client, client_for, writer_b, project, saved_view, shared_view):
        positions = {v.id: v.position for v in (saved_view, shared_view)}
        ids = [str(shared_view.id), str(saved_view.id)]
        assert reorder(auth_client, project, ids).status_code == 200
        listing = auth_client.get(f"{BASE_URL}/?project_id={project.id}").json()["result"]
        assert [v["id"] for v in listing["custom_views"]] == ids
        teammate = client_for(writer_b).get(f"{BASE_URL}/?project_id={project.id}").json()["result"]
        assert teammate["tab_order"] == {"revision": 0, "order": [str(shared_view.id)]}
        assert reorder(auth_client, project, ids[::-1], 1).status_code == 200
        conflict = reorder(auth_client, project, ids, 1)
        assert conflict.status_code == 409
        assert conflict.json()["result"]["current"] == {"revision": 2, "order": ids[::-1]}
        assert dict(SavedView.objects.values_list("id", "position")) == positions

    def test_list_never_prunes_order(self, auth_client, project, saved_view, shared_view):
        ids = [str(shared_view.id), str(saved_view.id)]
        assert reorder(auth_client, project, ids).status_code == 200
        shared_view.delete()
        data = auth_client.get(f"{BASE_URL}/?project_id={project.id}").json()["result"]
        assert data["tab_order"]["order"] == [str(saved_view.id)]
        assert order_model().objects.get(project=project).order == ids
        assert reorder(auth_client, project, [saved_view.id], 1).status_code == 200
        assert order_model().objects.get(project=project).order == [str(saved_view.id)]

    def test_mixed_order_is_atomic_and_non_enumerating(self, auth_client, project, saved_view):
        foreign = str(uuid.uuid4())
        response = reorder(auth_client, project, [saved_view.id, foreign])
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_order"
        assert foreign not in str(response.json()) and str(saved_view.id) not in str(response.json())
        assert not order_model().objects.exists()

    def test_workspace_bucket_and_clean(self, auth_client, workspace, user, project):
        view = SavedView.objects.create(workspace=workspace, created_by=user, name="Users", tab_type="users")
        assert reorder(auth_client, None, [view.id], tab_type="users").status_code == 200
        data = auth_client.get(f"{BASE_URL}/?tab_type=users").json()["result"]
        assert data["tab_order"] == {"revision": 1, "order": [str(view.id)]}
        assert auth_client.get(f"{BASE_URL}/?tab_type=sessions").json()["result"]["tab_order"]["revision"] == 0
        for kwargs in ({"project": project, "tab_type": "users"}, {"project": None, "tab_type": None}):
            with pytest.raises(ValidationError):
                order_model()(workspace=workspace, user=user, **kwargs).clean()


@pytest.mark.django_db(transaction=True)
def test_saved_view_concurrent_revision(auth_client, saved_view, user, workspace):
    # Two independent DB connections must contend on the actual row lock.
    auth_client.get(_view_url(saved_view))  # finish lazy URL imports before threading
    barrier = Barrier(2)
    def write(name):
        close_old_connections()
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(workspace)
        try:
            barrier.wait(timeout=10)
            return client.patch(_view_url(saved_view), {"name": name, "expected_revision": 1}, format="json").status_code
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["Browser A", "Browser B"]))
    assert sorted(results) == [200, 409]
    saved_view.refresh_from_db()
    assert saved_view.revision == 2


@pytest.mark.django_db(transaction=True)
def test_saved_view_migration_preserves_records_and_reverses(project, workspace, user):
    before = ("tracer", "0110_expand_investigation_requirement_id")
    after = ("tracer", "0111_savedview_revision_savedviewtaborder")
    executor = MigrationExecutor(connection)
    executor.migrate([before])
    try:
        old_model = executor.loader.project_state([before]).apps.get_model("tracer", "SavedView")
        ids = []
        for name, visibility, project_id in [("Personal", "personal", project.id),
                ("Shared", "project", project.id), ("Workspace", "personal", None)]:
            ids.append(old_model.objects.create(name=name, visibility=visibility, project_id=project_id,
                workspace_id=workspace.id, created_by_id=user.id, tab_type="traces", config={"columns": []}).id)
        executor = MigrationExecutor(connection)
        executor.migrate([after])
        new_model = executor.loader.project_state([after]).apps.get_model("tracer", "SavedView")
        assert list(new_model.objects.filter(id__in=ids).values_list("revision", flat=True)) == [1, 1, 1]
        assert all(v.config == {"columns": []} and v.created_by_id == user.id for v in new_model.objects.filter(id__in=ids))
        MigrationExecutor(connection).migrate([before])
        assert old_model.objects.filter(id__in=ids).count() == 3
        assert "tracer_saved_view_tab_order" not in connection.introspection.table_names()
    finally:
        MigrationExecutor(connection).migrate([after])

@pytest.fixture
def real_client_for(workspace):
    """Use real token authentication: force_authenticate skips workspace write checks."""
    from rest_framework.test import APIClient
    def make(user):
        client = APIClient()
        login = client.post("/accounts/token/", {"email": user.email, "password": "testpassword123"}, format="json")
        assert login.status_code == 200, login.data
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
            HTTP_X_WORKSPACE_ID=str(workspace.id), HTTP_X_ORGANIZATION_ID=str(workspace.organization_id))
        return client
    return make


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["create", "put", "patch", "delete", "duplicate", "reorder"])
def test_saved_view_viewer_cannot_mutate(real_client_for, viewer_c, shared_view, project, operation):
    client = real_client_for(viewer_c)
    assert client.get(f"{BASE_URL}/?project_id={project.id}").status_code == 200
    if operation == "create":
        response = client.post(f"{BASE_URL}/", {"name": "Denied", "tab_type": "traces", "project_id": str(project.id)}, format="json")
    elif operation == "reorder":
        response = reorder(client, project, [shared_view.id])
    elif operation == "duplicate":
        response = client.post(_view_url(shared_view, "duplicate/"), {}, format="json")
    else:
        response = getattr(client, operation)(_view_url(shared_view) + "&expected_revision=1", {"expected_revision": 1}, format="json")
    assert response.status_code == 403
    shared_view.refresh_from_db()
    assert shared_view.revision == 1 and not shared_view.deleted


@pytest.mark.django_db
@pytest.mark.parametrize("method,action", [("get", ""), ("put", ""), ("patch", ""), ("delete", ""), ("post", "duplicate/")])
def test_saved_view_wrong_project_is_exact_not_found(auth_client, saved_view, method, action):
    url = f"{BASE_URL}/{saved_view.id}/{action}?project_id={uuid.uuid4()}"
    response = getattr(auth_client, method)(url, {}, format="json") if method != "get" else auth_client.get(url)
    assert response.status_code == 404
    assert saved_view.name not in str(response.data)
    saved_view.refresh_from_db()
    assert saved_view.revision == 1


@pytest.mark.django_db
def test_saved_view_revoked_membership_rejected(real_client_for, writer_b, workspace, shared_view):
    workspace.is_default = False
    workspace.save()
    # The default-workspace fixture auto-creates this user's membership, so the
    # explicit create in `member()` collides; deactivate whichever row exists.
    WorkspaceMembership.no_workspace_objects.filter(user=writer_b, workspace=workspace).update(is_active=False)
    client = real_client_for(writer_b)
    response = client.get(_view_url(shared_view))
    assert response.status_code == 404
    assert shared_view.name not in str(response.data)


def test_saved_view_contract_statuses_declared():
    from tracer.views.saved_view import SavedViewViewSet
    for action in ("update", "partial_update", "destroy", "reorder"):
        schema = getattr(SavedViewViewSet, action)._swagger_auto_schema
        # read-query POST endpoints store a per-method dict; others store the
        # options dict directly.
        if "responses" not in schema:
            schema = schema.get("post") or schema.get("put") or next(iter(schema.values()))
        responses = schema["responses"]
        assert {200, 400, 403, 404, 409, 428} <= set(responses)
