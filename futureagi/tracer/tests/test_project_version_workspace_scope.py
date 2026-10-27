import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion, ProjectVersionWinner
from tracer.models.trace import Trace


def make_same_org_other_workspace_run(organization, user):
    suffix = uuid.uuid4().hex[:8]
    other_workspace = Workspace.objects.create(
        name=f"Other Project Version Workspace {suffix}",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    other_project = Project.objects.create(
        name=f"Other Project Version Project {suffix}",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="experiment",
        metadata={},
    )
    other_project_version = ProjectVersion.objects.create(
        project=other_project,
        name=f"Other Run {suffix}",
        version="v1",
        metadata={},
        config=[],
    )
    other_trace = Trace.objects.create(
        project=other_project,
        project_version=other_project_version,
        name=f"Other Trace {suffix}",
        input={"prompt": "hidden"},
        output={"response": "hidden"},
    )
    other_span = ObservationSpan.objects.create(
        id=f"other_project_version_span_{suffix}",
        project=other_project,
        project_version=other_project_version,
        trace=other_trace,
        name="Other Project Version Span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=3),
        end_time=timezone.now(),
        latency_ms=250,
        cost=0.02,
        status="OK",
    )
    return other_workspace, other_project, other_project_version, other_trace, other_span


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestProjectVersionWorkspaceScopeAPI:
    def test_create_rejects_same_org_other_workspace_project(
        self, auth_client, organization, user
    ):
        _, other_project, _, _, _ = make_same_org_other_workspace_run(
            organization, user
        )

        response = auth_client.post(
            "/tracer/project-version/",
            {
                "project": str(other_project.id),
                "name": "Cross Workspace Run",
                "metadata": {},
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not ProjectVersion.no_workspace_objects.filter(
            project=other_project,
            name="Cross Workspace Run",
        ).exists()

    def test_patch_rejects_same_org_other_workspace_project(
        self, auth_client, organization, user, project_version
    ):
        _, other_project, _, _, _ = make_same_org_other_workspace_run(
            organization, user
        )

        response = auth_client.patch(
            f"/tracer/project-version/{project_version.id}/",
            {"project": str(other_project.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        project_version.refresh_from_db()
        assert project_version.project_id != other_project.id

    def test_custom_actions_reject_same_org_other_workspace_project_or_run(
        self, auth_client, organization, user
    ):
        _, other_project, other_project_version, _, _ = (
            make_same_org_other_workspace_run(organization, user)
        )

        list_response = auth_client.get(
            "/tracer/project-version/list_runs/",
            {"project_id": str(other_project.id)},
        )
        export_response = auth_client.post(
            "/tracer/project-version/get_export_data/",
            {
                "project_id": str(other_project.id),
                "runs_ids": [str(other_project_version.id)],
            },
            format="json",
        )
        winner_response = auth_client.post(
            "/tracer/project-version/project_version_winner/",
            {
                "project_id": str(other_project.id),
                "config": {"avg_latency_ms": 1},
            },
            format="json",
        )
        insight_response = auth_client.get(
            "/tracer/project-version/get_run_insights/",
            {"project_version_id": str(other_project_version.id)},
        )
        config_response = auth_client.post(
            "/tracer/project-version/update_project_version_config/",
            {
                "project_version_id": str(other_project_version.id),
                "visibility": {"latency": False},
            },
            format="json",
        )
        annotation_response = auth_client.post(
            "/tracer/project-version/add_annotations/",
            {
                "project_version_id": str(other_project_version.id),
                "annotation_values": {},
            },
            format="json",
        )

        assert list_response.status_code == status.HTTP_400_BAD_REQUEST
        assert export_response.status_code == status.HTTP_400_BAD_REQUEST
        assert winner_response.status_code == status.HTTP_400_BAD_REQUEST
        assert insight_response.status_code == status.HTTP_400_BAD_REQUEST
        assert config_response.status_code == status.HTTP_400_BAD_REQUEST
        assert annotation_response.status_code == status.HTTP_400_BAD_REQUEST
        assert not ProjectVersionWinner.no_workspace_objects.filter(
            project=other_project
        ).exists()

    def test_delete_runs_does_not_delete_same_org_other_workspace_run(
        self, auth_client, organization, user
    ):
        _, _, other_project_version, other_trace, other_span = (
            make_same_org_other_workspace_run(organization, user)
        )

        response = auth_client.post(
            "/tracer/project-version/delete_runs/",
            {"ids": [str(other_project_version.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["deleted_ids"] == []
        other_project_version.refresh_from_db()
        other_trace.refresh_from_db()
        other_span.refresh_from_db()
        assert other_project_version.deleted is False
        assert other_trace.deleted is False
        assert other_span.deleted is False

    def test_destroy_cascades_visible_run_traces_and_spans(
        self, auth_client, project_version, trace, observation_span
    ):
        observation_span.project_version = project_version
        observation_span.save(update_fields=["project_version"])

        response = auth_client.delete(f"/tracer/project-version/{project_version.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert ProjectVersion.all_objects.get(id=project_version.id).deleted is True
        assert Trace.all_objects.get(id=trace.id).deleted is True
        assert ObservationSpan.all_objects.get(id=observation_span.id).deleted is True
