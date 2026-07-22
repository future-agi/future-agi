import uuid

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace


pytestmark = [pytest.mark.integration, pytest.mark.api]


def _make_same_org_other_workspace_project(organization, user, trace_type="experiment"):
    other_workspace = Workspace.no_workspace_objects.create(
        name=f"other-workspace-{uuid.uuid4().hex[:8]}",
        organization=organization,
        created_by=user,
    )
    project = Project.no_workspace_objects.create(
        name=f"Other Workspace Project {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type=trace_type,
        config=[{"id": "input", "name": "Input", "is_visible": True}],
        session_config=[
            {"id": "session_input", "name": "Session Input", "is_visible": True}
        ],
        tags=["hidden"],
    )
    project_version = ProjectVersion.no_workspace_objects.create(
        project=project,
        name="Other Workspace Run",
        version="v1",
    )
    trace = Trace.no_workspace_objects.create(
        project=project,
        project_version=project_version,
        name="Other Workspace Trace",
    )
    span = ObservationSpan.no_workspace_objects.create(
        id=f"other_ws_span_{uuid.uuid4().hex[:12]}",
        project=project,
        project_version=project_version,
        trace=trace,
        name="Other Workspace Span",
        observation_type="llm",
    )
    return other_workspace, project, project_version, trace, span


def _assert_not_mutated(project_id, *, name, config, session_config, tags):
    project = Project.no_workspace_objects.get(id=project_id)
    assert project.name == name
    assert project.config == config
    assert project.session_config == session_config
    assert project.tags == tags
    assert project.deleted is False


class TestProjectWorkspaceScope:
    def test_custom_project_mutations_reject_same_org_other_workspace_project(
        self, auth_client, organization, user
    ):
        _, other_project, *_ = _make_same_org_other_workspace_project(
            organization, user
        )
        original = {
            "name": other_project.name,
            "config": other_project.config,
            "session_config": other_project.session_config,
            "tags": other_project.tags,
        }

        rename = auth_client.post(
            "/tracer/project/update_project_name/",
            {"project_id": str(other_project.id), "name": "Leaked Name"},
            format="json",
        )
        assert rename.status_code == status.HTTP_400_BAD_REQUEST

        config = auth_client.post(
            "/tracer/project/update_project_config/",
            {
                "project_id": str(other_project.id),
                "visibility": {"input": False},
            },
            format="json",
        )
        assert config.status_code == status.HTTP_400_BAD_REQUEST

        session_config = auth_client.post(
            "/tracer/project/update_project_session_config/",
            {
                "project_id": str(other_project.id),
                "visibility": {"session_input": False},
            },
            format="json",
        )
        assert session_config.status_code == status.HTTP_400_BAD_REQUEST

        _assert_not_mutated(other_project.id, **original)

    def test_detail_update_patch_tags_reject_same_org_other_workspace_project(
        self, auth_client, organization, user
    ):
        _, other_project, *_ = _make_same_org_other_workspace_project(
            organization, user
        )
        original = {
            "name": other_project.name,
            "config": other_project.config,
            "session_config": other_project.session_config,
            "tags": other_project.tags,
        }

        put = auth_client.put(
            f"/tracer/project/{other_project.id}/",
            {
                "name": "Leaked Put",
                "model_type": "GenerativeLLM",
                "trace_type": "experiment",
                "metadata": {},
            },
            format="json",
        )
        assert put.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        )

        patch = auth_client.patch(
            f"/tracer/project/{other_project.id}/",
            {"name": "Leaked Patch"},
            format="json",
        )
        assert patch.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        )

        tags = auth_client.patch(
            f"/tracer/project/{other_project.id}/tags/",
            {"tags": ["leaked"]},
            format="json",
        )
        assert tags.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        )

        _assert_not_mutated(other_project.id, **original)

    def test_project_graph_routes_reject_same_org_other_workspace_project(
        self, auth_client, organization, user
    ):
        _, other_project, *_ = _make_same_org_other_workspace_project(
            organization, user
        )
        end_user_id = str(uuid.uuid4())

        graph = auth_client.get(
            "/tracer/project/get_graph_data/",
            {"project_id": str(other_project.id), "interval": "hour"},
        )
        assert graph.status_code == status.HTTP_400_BAD_REQUEST

        user_metrics = auth_client.post(
            "/tracer/project/get_user_metrics/",
            {
                "project_id": str(other_project.id),
                "end_user_id": end_user_id,
                "interval": "day",
                "filters": [],
            },
            format="json",
        )
        assert user_metrics.status_code == status.HTTP_400_BAD_REQUEST

        user_aggregate = auth_client.post(
            "/tracer/project/get_users_aggregate_graph_data/",
            {
                "project_id": str(other_project.id),
                "interval": "day",
                "filters": [],
                "property": "average",
                "req_data_config": {"type": "SYSTEM_METRIC", "id": "active_users"},
            },
            format="json",
        )
        assert user_aggregate.status_code == status.HTTP_400_BAD_REQUEST

        user_graph = auth_client.post(
            (
                "/tracer/project/get_user_graph_data/"
                f"?project_id={other_project.id}&end_user_id={end_user_id}"
            ),
            {"interval": "day", "filters": []},
            format="json",
        )
        assert user_graph.status_code == status.HTTP_400_BAD_REQUEST

    def test_bulk_delete_rejects_same_org_other_workspace_project_without_mutating(
        self, auth_client, organization, user
    ):
        _, other_project, other_run, other_trace, other_span = (
            _make_same_org_other_workspace_project(organization, user)
        )

        response = auth_client.delete(
            "/tracer/project/",
            {
                "project_ids": [str(other_project.id)],
                "project_type": "experiment",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Project.no_workspace_objects.get(id=other_project.id).deleted is False
        assert ProjectVersion.all_objects.get(id=other_run.id).deleted is False
        assert Trace.all_objects.get(id=other_trace.id).deleted is False
        assert ObservationSpan.all_objects.get(id=other_span.id).deleted is False

    def test_detail_delete_cascades_current_workspace_project_children(
        self, auth_client, project, project_version, trace, observation_span
    ):
        response = auth_client.delete(f"/tracer/project/{project.id}/")

        assert response.status_code == status.HTTP_200_OK
        assert Project.all_objects.get(id=project.id).deleted is True
        assert ProjectVersion.all_objects.get(id=project_version.id).deleted is True
        assert Trace.all_objects.get(id=trace.id).deleted is True
        assert ObservationSpan.all_objects.get(id=observation_span.id).deleted is True

    def test_detail_delete_rejects_same_org_other_workspace_project_without_mutating(
        self, auth_client, organization, user
    ):
        _, other_project, other_run, other_trace, other_span = (
            _make_same_org_other_workspace_project(organization, user)
        )

        response = auth_client.delete(f"/tracer/project/{other_project.id}/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Project.no_workspace_objects.get(id=other_project.id).deleted is False
        assert ProjectVersion.all_objects.get(id=other_run.id).deleted is False
        assert Trace.all_objects.get(id=other_trace.id).deleted is False
        assert ObservationSpan.all_objects.get(id=other_span.id).deleted is False
