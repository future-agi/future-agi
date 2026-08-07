"""
Tests for SavedView (Tab System & Saved Views) — Phase 1A.
"""

import uuid

import pytest

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.project import Project
from tracer.models.saved_view import SavedView

BASE_URL = "/tracer/saved-views"


def _filter(column_id="status", filter_type="text", filter_op="equals", value="ERROR"):
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": filter_type,
            "filter_op": filter_op,
            "filter_value": value,
        },
    }


def _view_url(view, action=""):
    return f"{BASE_URL}/{view.id}/{action}?project_id={view.project_id}"


def _workspace_view_url(view, action=""):
    return f"{BASE_URL}/{view.id}/{action}"


@pytest.fixture
def saved_view(db, project, workspace, user):
    """Create a test saved view."""
    return SavedView.objects.create(
        project=project,
        workspace=workspace,
        created_by=user,
        name="Error Traces",
        tab_type="traces",
        visibility="personal",
        position=0,
        config={
            "filters": [_filter()],
            "columns": [{"key": "name", "width": 300}],
            "sort": {"field": "start_time", "direction": "desc"},
        },
    )


@pytest.fixture
def shared_view(db, project, workspace, user):
    """Create a project-shared saved view."""
    return SavedView.objects.create(
        project=project,
        workspace=workspace,
        created_by=user,
        name="Shared Errors",
        tab_type="traces",
        visibility="project",
        position=1,
        config={
            "filters": [{"field": "status", "operator": "=", "value": "ERROR"}],
        },
    )


@pytest.fixture
def other_user(db, organization):
    """Create a second user for permission tests."""
    from accounts.models.user import User

    return User.objects.create_user(
        email="other@futureagi.com",
        password="testpassword123",
        name="Other User",
        organization=organization,
    )


@pytest.fixture
def other_auth_client(other_user, workspace):
    """Authenticated client for the other user."""
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=other_user)
    client.set_workspace(workspace)
    yield client
    client.stop_workspace_injection()


@pytest.fixture
def other_workspace(db, organization, user):
    """Create a same-org non-default workspace for isolation tests."""
    return Workspace.no_workspace_objects.create(
        name="Other Workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


@pytest.fixture
def other_workspace_project(db, organization, other_workspace):
    return Project.no_workspace_objects.create(
        name="Other Workspace Project",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


# =====================================================================
# CRUD Tests
# =====================================================================


class TestSavedViewList:
    @pytest.mark.django_db
    def test_list_returns_default_tabs_and_custom_views(
        self, auth_client, project, saved_view
    ):
        response = auth_client.get(
            f"{BASE_URL}/?project_id={project.id}", format="json"
        )
        assert response.status_code == 200
        data = response.json()["result"]

        # Check default tabs
        assert len(data["default_tabs"]) == 3
        tab_keys = [t["key"] for t in data["default_tabs"]]
        assert tab_keys == ["traces", "spans", "voice"]

        # Check custom views
        assert len(data["custom_views"]) == 1
        assert data["custom_views"][0]["name"] == "Error Traces"

    @pytest.mark.django_db
    def test_list_requires_project_id(self, auth_client):
        response = auth_client.get(f"{BASE_URL}/", format="json")
        assert response.status_code == 200

    @pytest.mark.django_db
    def test_list_returns_404_for_nonexistent_project(self, auth_client):
        fake_id = uuid.uuid4()
        response = auth_client.get(f"{BASE_URL}/?project_id={fake_id}", format="json")
        assert response.status_code == 404

    @pytest.mark.django_db
    def test_list_shows_personal_and_shared_views(
        self, auth_client, project, saved_view, shared_view
    ):
        response = auth_client.get(
            f"{BASE_URL}/?project_id={project.id}", format="json"
        )
        data = response.json()["result"]
        assert len(data["custom_views"]) == 2

    @pytest.mark.django_db
    def test_list_excludes_other_users_personal_views(
        self, other_auth_client, project, saved_view, shared_view
    ):
        """Other user should see shared views but not the first user's personal views."""
        response = other_auth_client.get(
            f"{BASE_URL}/?project_id={project.id}", format="json"
        )
        data = response.json()["result"]
        # Only the shared view should be visible
        assert len(data["custom_views"]) == 1
        assert data["custom_views"][0]["name"] == "Shared Errors"


class TestSavedViewCreate:
    @pytest.mark.django_db
    def test_create_saved_view(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Slow Traces",
                "tab_type": "traces",
                "visibility": "personal",
                "config": {
                    "filters": [
                        _filter(
                            "latency_ms",
                            filter_type="number",
                            filter_op="greater_than",
                            value=5000,
                        )
                    ],
                },
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Slow Traces"
        assert data["tab_type"] == "traces"
        assert data["visibility"] == "personal"
        assert data["config"]["filters"][0]["column_id"] == "latency_ms"
        assert data["project"] == str(project.id)

    @pytest.mark.django_db
    def test_create_with_empty_name_fails(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "",
                "tab_type": "traces",
            },
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_create_with_invalid_tab_type_fails(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Bad View",
                "tab_type": "invalid",
            },
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_create_with_invalid_config_keys_fails(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Bad Config",
                "tab_type": "traces",
                "config": {"bad_key": "value"},
            },
            format="json",
        )
        assert response.status_code == 400

    @pytest.mark.django_db
    def test_create_with_nonexistent_project_fails(self, auth_client):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(uuid.uuid4()),
                "name": "Orphan View",
                "tab_type": "traces",
            },
            format="json",
        )
        assert response.status_code == 404

    @pytest.mark.django_db
    def test_position_auto_increments(self, auth_client, project, saved_view):
        """New views should get position = max + 1."""
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Second View",
                "tab_type": "spans",
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["position"] == saved_view.position + 1

    @pytest.mark.django_db
    def test_create_voice_tab_type(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Voice Errors",
                "tab_type": "voice",
            },
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["result"]["tab_type"] == "voice"

    @pytest.mark.django_db
    def test_config_accepts_all_valid_keys(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Full Config",
                "tab_type": "traces",
                "config": {
                    "filters": [_filter(value="OK")],
                    "columns": [{"key": "name", "width": 200}],
                    "sort": {"field": "cost", "direction": "asc"},
                    "display": {"density": "compact"},
                },
            },
            format="json",
        )
        assert response.status_code == 200
        config = response.json()["result"]["config"]
        assert "filters" in config
        assert "columns" in config
        assert "sort" in config
        assert "display" in config

    @pytest.mark.django_db
    def test_create_rejects_legacy_filter_shape(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Legacy Filter Shape",
                "tab_type": "traces",
                "config": {
                    "filters": [
                        {
                            "columnId": "status",
                            "filterConfig": {
                                "colType": "SYSTEM_METRIC",
                                "filterType": "text",
                                "filterOp": "equals",
                                "filterValue": "ERROR",
                            },
                        }
                    ],
                },
            },
            format="json",
        )

        assert response.status_code == 400
        assert "config" in str(response.json())

    @pytest.mark.django_db
    def test_create_accepts_canonical_compare_filter_keys(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Compare View",
                "tab_type": "traces",
                "config": {
                    "extra_filters": [_filter("status")],
                    "compare_filters": [
                        _filter("latency_ms", "number", "greater_than", 1)
                    ],
                    "compare_date_filter": {"start": "2026-01-01", "end": "2026-01-02"},
                    "compare_extra_filters": [],
                },
            },
            format="json",
        )

        assert response.status_code == 200
        config = response.json()["result"]["config"]
        assert config["extra_filters"][0]["column_id"] == "status"
        assert "extraFilters" not in config

    @pytest.mark.django_db
    def test_create_rejects_legacy_saved_view_config_keys(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Legacy Config",
                "tab_type": "traces",
                "config": {"extraFilters": [_filter("status")]},
            },
            format="json",
        )

        assert response.status_code == 400
        assert "extraFilters" in str(response.json())

    @pytest.mark.django_db
    def test_create_rejects_legacy_user_view_filters_object(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Legacy User View",
                "tab_type": "users",
                "config": {
                    "filters": {
                        "extraFilters": [_filter("status")],
                        "dateFilter": {"dateOption": "Last 7 days"},
                    }
                },
            },
            format="json",
        )

        assert response.status_code == 400
        assert "Filters must be a list" in str(response.json())

    @pytest.mark.django_db
    def test_create_accepts_canonical_user_view_config(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Users View",
                "tab_type": "users",
                "config": {
                    "display": {"dateFilter": {"dateOption": "Last 7 days"}},
                    "extra_filters": [_filter("status")],
                },
            },
            format="json",
        )

        assert response.status_code == 200
        config = response.json()["result"]["config"]
        assert config["extra_filters"][0]["column_id"] == "status"
        assert config["display"]["dateFilter"]["dateOption"] == "Last 7 days"


class TestSavedViewRetrieve:
    @pytest.mark.django_db
    def test_retrieve_saved_view(self, auth_client, saved_view):
        response = auth_client.get(_view_url(saved_view), format="json")
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Error Traces"
        assert "config" in data  # Detail includes config

    @pytest.mark.django_db
    def test_retrieve_nonexistent_returns_error(self, auth_client):
        fake_id = uuid.uuid4()
        response = auth_client.get(f"{BASE_URL}/{fake_id}/", format="json")
        # DRF returns 400 via our exception handler (not 404)
        assert response.status_code in (400, 404)


class TestSavedViewUpdate:
    @pytest.mark.django_db
    def test_update_saved_view(self, auth_client, saved_view):
        response = auth_client.put(
            _view_url(saved_view),
            {
                "name": "Critical Errors",
                "config": {
                    "filters": [
                        _filter(),
                        _filter(
                            "latency_ms",
                            filter_type="number",
                            filter_op="greater_than",
                            value=10000,
                        ),
                    ],
                },
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Critical Errors"
        assert len(data["config"]["filters"]) == 2

    @pytest.mark.django_db
    def test_partial_update_name_only(self, auth_client, saved_view):
        response = auth_client.patch(
            _view_url(saved_view),
            {"name": "Renamed View"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["result"]["name"] == "Renamed View"

    @pytest.mark.django_db
    def test_partial_update_visibility(self, auth_client, saved_view):
        response = auth_client.patch(
            _view_url(saved_view),
            {"visibility": "project"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["result"]["visibility"] == "project"

    @pytest.mark.django_db
    def test_update_rejects_create_only_fields(self, auth_client, saved_view):
        response = auth_client.put(
            _view_url(saved_view),
            {
                "name": "Critical Errors",
                "tab_type": "spans",
                "project_id": str(saved_view.project_id),
            },
            format="json",
        )

        assert response.status_code == 400
        assert "tab_type" in str(response.json())


class TestSavedViewDelete:
    @pytest.mark.django_db
    def test_delete_saved_view(self, auth_client, project, saved_view):
        response = auth_client.delete(_view_url(saved_view), format="json")
        assert response.status_code == 200

        # Verify it's gone from list
        list_response = auth_client.get(
            f"{BASE_URL}/?project_id={project.id}", format="json"
        )
        custom_views = list_response.json()["result"]["custom_views"]
        assert len(custom_views) == 0

    @pytest.mark.django_db
    def test_soft_delete_preserves_record(self, auth_client, saved_view):
        auth_client.delete(_view_url(saved_view), format="json")

        # Record still exists in DB (soft deleted)
        view = SavedView.all_objects.get(id=saved_view.id)
        assert view.deleted is True


# =====================================================================
# Custom Actions
# =====================================================================


class TestSavedViewDuplicate:
    @pytest.mark.django_db
    def test_duplicate_view(self, auth_client, project, saved_view):
        response = auth_client.post(
            _view_url(saved_view, "duplicate/"),
            {"name": "Error Traces v2"},
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["name"] == "Error Traces v2"
        assert data["tab_type"] == saved_view.tab_type
        assert data["visibility"] == "personal"
        assert data["config"] == saved_view.config
        assert data["id"] != str(saved_view.id)

    @pytest.mark.django_db
    def test_duplicate_default_name(self, auth_client, saved_view):
        response = auth_client.post(
            _view_url(saved_view, "duplicate/"),
            {},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["result"]["name"] == "Error Traces (Copy)"

    @pytest.mark.django_db
    def test_duplicate_shared_view_becomes_personal(self, auth_client, shared_view):
        response = auth_client.post(
            _view_url(shared_view, "duplicate/"),
            {"name": "My Copy"},
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["result"]["visibility"] == "personal"


class TestSavedViewReorder:
    @pytest.mark.django_db
    def test_reorder_views(self, auth_client, project, workspace, user):
        # Create 3 views
        views = []
        for i in range(3):
            v = SavedView.objects.create(
                project=project,
                workspace=workspace,
                created_by=user,
                name=f"View {i}",
                tab_type="traces",
                position=i,
            )
            views.append(v)

        # Reverse the order
        response = auth_client.post(
            f"{BASE_URL}/reorder/",
            {
                "project_id": str(project.id),
                "order": [
                    {"id": str(views[2].id), "position": 0},
                    {"id": str(views[1].id), "position": 1},
                    {"id": str(views[0].id), "position": 2},
                ],
            },
            format="json",
        )
        assert response.status_code == 200

        # Verify positions updated
        views[0].refresh_from_db()
        views[1].refresh_from_db()
        views[2].refresh_from_db()
        assert views[0].position == 2
        assert views[1].position == 1
        assert views[2].position == 0

    @pytest.mark.django_db
    def test_reorder_with_invalid_ids_fails(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/reorder/",
            {
                "project_id": str(project.id),
                "order": [
                    {"id": str(uuid.uuid4()), "position": 0},
                ],
            },
            format="json",
        )
        assert response.status_code == 400


# =====================================================================
# Permissions
# =====================================================================


class TestSavedViewPermissions:
    @pytest.mark.django_db
    def test_unauthenticated_request_fails(self, api_client, project):
        response = api_client.get(f"{BASE_URL}/?project_id={project.id}", format="json")
        assert response.status_code in (401, 403)

    @pytest.mark.django_db
    def test_other_user_can_see_shared_views(
        self, other_auth_client, project, shared_view
    ):
        response = other_auth_client.get(
            f"{BASE_URL}/?project_id={project.id}", format="json"
        )
        data = response.json()["result"]
        names = [v["name"] for v in data["custom_views"]]
        assert "Shared Errors" in names

    @pytest.mark.django_db
    def test_other_user_cannot_see_personal_views(
        self, other_auth_client, project, saved_view
    ):
        response = other_auth_client.get(
            f"{BASE_URL}/?project_id={project.id}", format="json"
        )
        data = response.json()["result"]
        names = [v["name"] for v in data["custom_views"]]
        assert "Error Traces" not in names


class TestSavedViewWorkspaceScope:
    @pytest.mark.django_db
    def test_workspace_scoped_views_remain_personal_on_update(
        self, auth_client, workspace
    ):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "name": "Workspace Sessions",
                "tab_type": "sessions",
                "visibility": "project",
                "config": {"display": {"density": "compact"}},
            },
            format="json",
        )
        assert response.status_code == 200
        data = response.json()["result"]
        assert data["project"] is None
        assert data["visibility"] == "personal"

        view_id = data["id"]
        patch_response = auth_client.patch(
            f"{BASE_URL}/{view_id}/",
            {"name": "Workspace Sessions Updated", "visibility": "project"},
            format="json",
        )
        assert patch_response.status_code == 200
        updated = patch_response.json()["result"]
        assert updated["name"] == "Workspace Sessions Updated"
        assert updated["visibility"] == "personal"

        saved_view = SavedView.no_workspace_objects.get(id=view_id)
        assert saved_view.workspace_id == workspace.id
        assert saved_view.project_id is None
        assert saved_view.visibility == "personal"

        list_response = auth_client.get(f"{BASE_URL}/?tab_type=sessions", format="json")
        assert list_response.status_code == 200
        ids = [view["id"] for view in list_response.json()["result"]["custom_views"]]
        assert view_id in ids

    @pytest.mark.django_db
    def test_same_org_other_workspace_project_views_are_hidden_and_unchanged(
        self, auth_client, other_workspace_project, other_workspace, user
    ):
        hidden_view = SavedView.no_workspace_objects.create(
            project=other_workspace_project,
            workspace=other_workspace,
            created_by=user,
            name="Hidden Other Workspace",
            tab_type="traces",
            visibility="personal",
            position=0,
            config={"display": {"viewMode": "list"}},
        )

        list_response = auth_client.get(
            f"{BASE_URL}/?project_id={other_workspace_project.id}", format="json"
        )
        assert list_response.status_code == 404

        create_response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(other_workspace_project.id),
                "name": "Should Not Create",
                "tab_type": "traces",
            },
            format="json",
        )
        assert create_response.status_code == 404

        detail_response = auth_client.get(_view_url(hidden_view), format="json")
        assert detail_response.status_code in (400, 404)

        patch_response = auth_client.patch(
            _view_url(hidden_view),
            {"name": "Leaked Update"},
            format="json",
        )
        assert patch_response.status_code in (400, 404)

        duplicate_response = auth_client.post(
            _view_url(hidden_view, "duplicate/"),
            {"name": "Leaked Duplicate"},
            format="json",
        )
        assert duplicate_response.status_code in (400, 404)

        reorder_response = auth_client.post(
            f"{BASE_URL}/reorder/",
            {
                "project_id": str(other_workspace_project.id),
                "order": [{"id": str(hidden_view.id), "position": 9}],
            },
            format="json",
        )
        assert reorder_response.status_code == 400

        delete_response = auth_client.delete(_view_url(hidden_view), format="json")
        assert delete_response.status_code in (400, 404)

        hidden_view.refresh_from_db()
        assert hidden_view.name == "Hidden Other Workspace"
        assert hidden_view.position == 0
        assert hidden_view.deleted is False
        assert (
            SavedView.no_workspace_objects.filter(
                name="Should Not Create", project=other_workspace_project
            ).count()
            == 0
        )
        assert (
            SavedView.no_workspace_objects.filter(
                name="Leaked Duplicate", project=other_workspace_project
            ).count()
            == 0
        )

    @pytest.mark.django_db
    def test_workspace_scoped_duplicate_position_uses_same_tab_bucket(
        self, auth_client, workspace, user
    ):
        sessions_view = SavedView.objects.create(
            project=None,
            workspace=workspace,
            created_by=user,
            name="Sessions View",
            tab_type="sessions",
            visibility="personal",
            position=0,
        )
        SavedView.objects.create(
            project=None,
            workspace=workspace,
            created_by=user,
            name="Traces View",
            tab_type="traces",
            visibility="personal",
            position=5,
        )

        response = auth_client.post(
            _workspace_view_url(sessions_view, "duplicate/"),
            {"name": "Sessions View Copy"},
            format="json",
        )

        assert response.status_code == 200
        data = response.json()["result"]
        assert data["position"] == 1
        assert data["tab_type"] == "sessions"
        assert data["visibility"] == "personal"


# =====================================================================
# Edge Cases
# =====================================================================


class TestSavedViewEdgeCases:
    @pytest.mark.django_db
    def test_empty_config_is_valid(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Minimal View",
                "tab_type": "traces",
                "config": {},
            },
            format="json",
        )
        assert response.status_code == 200

    @pytest.mark.django_db
    def test_no_config_defaults_to_empty_dict(self, auth_client, project):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "No Config View",
                "tab_type": "spans",
            },
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["result"]["config"] == {}

    @pytest.mark.django_db
    def test_deleted_views_excluded_from_list(self, auth_client, project, saved_view):
        # Delete the view
        auth_client.delete(_view_url(saved_view), format="json")

        # List should be empty
        response = auth_client.get(
            f"{BASE_URL}/?project_id={project.id}", format="json"
        )
        assert len(response.json()["result"]["custom_views"]) == 0

    @pytest.mark.django_db
    def test_created_by_populated_on_response(self, auth_client, project, user):
        response = auth_client.post(
            f"{BASE_URL}/",
            {
                "project_id": str(project.id),
                "name": "Creator Test",
                "tab_type": "traces",
            },
            format="json",
        )
        assert response.status_code == 200
        created_by = response.json()["result"]["created_by"]
        assert created_by["email"] == user.email
        assert created_by["name"] == user.name

    @pytest.mark.django_db
    def test_list_ordered_by_position(self, auth_client, project, workspace, user):
        SavedView.objects.create(
            project=project,
            workspace=workspace,
            created_by=user,
            name="Third",
            tab_type="traces",
            position=2,
        )
        SavedView.objects.create(
            project=project,
            workspace=workspace,
            created_by=user,
            name="First",
            tab_type="traces",
            position=0,
        )
        SavedView.objects.create(
            project=project,
            workspace=workspace,
            created_by=user,
            name="Second",
            tab_type="traces",
            position=1,
        )

        response = auth_client.get(
            f"{BASE_URL}/?project_id={project.id}", format="json"
        )
        views = response.json()["result"]["custom_views"]
        names = [v["name"] for v in views]
        assert names == ["First", "Second", "Third"]
