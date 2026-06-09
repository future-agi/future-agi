from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import (
    AnnotationTypeChoices,
    QueueItemSourceType,
    ScoreSource,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace


def _make_second_org_workspace(user):
    organization = Organization.objects.create(
        name=f"Second Org {uuid.uuid4().hex[:8]}"
    )
    organization_membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    workspace = Workspace.no_workspace_objects.create(
        name=f"Second Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role="Workspace Owner",
        level=Level.OWNER,
        is_active=True,
        organization_membership=organization_membership,
    )
    return organization, workspace


def _make_project_version_with_score(user, organization, workspace):
    project = Project.no_workspace_objects.create(
        name=f"Multi-org rollup {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="experiment",
        metadata={},
    )
    project_version = ProjectVersion.no_workspace_objects.create(
        project=project,
        name="Run",
        version="v1",
        metadata={},
        avg_eval_score=0.75,
    )
    trace = Trace.no_workspace_objects.create(
        project=project,
        project_version=project_version,
        name="Trace",
        metadata={},
        input={"prompt": "hello"},
        output={"response": "world"},
    )
    span = ObservationSpan.no_workspace_objects.create(
        id=f"span-{uuid.uuid4().hex}",
        project=project,
        project_version=project_version,
        trace=trace,
        name="Root span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=2),
        end_time=timezone.now(),
        input={"prompt": "hello"},
        output={"response": "world"},
        latency_ms=120,
        cost=0.01,
        status="OK",
    )
    label = AnnotationsLabels.no_workspace_objects.create(
        name="Quality score",
        type=AnnotationTypeChoices.NUMERIC.value,
        settings={
            "min": 0,
            "max": 10,
            "step_size": 1,
            "display_type": "slider",
        },
        organization=organization,
        workspace=workspace,
        project=project,
    )
    Score.no_workspace_objects.create(
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span=span,
        label=label,
        value={"value": 7},
        annotator=user,
        score_source=ScoreSource.HUMAN.value,
        organization=organization,
        workspace=workspace,
    )
    return project, project_version, label


@pytest.mark.django_db
def test_list_runs_annotation_rollups_use_active_request_organization(
    auth_client, user
):
    organization, workspace = _make_second_org_workspace(user)
    project, _project_version, label = _make_project_version_with_score(
        user, organization, workspace
    )
    assert user.organization_id != organization.id

    auth_client.set_workspace(workspace)
    response = auth_client.get(
        "/tracer/project-version/list_runs/",
        {"project_id": str(project.id)},
    )

    assert response.status_code == 200
    table = response.data["result"]["table"]
    assert len(table) == 1
    assert table[0][str(label.id)] == 7.0


@pytest.mark.django_db
def test_export_data_annotation_rollups_use_active_request_organization(
    auth_client, user
):
    organization, workspace = _make_second_org_workspace(user)
    project, project_version, label = _make_project_version_with_score(
        user, organization, workspace
    )
    assert user.organization_id != organization.id

    auth_client.set_workspace(workspace)
    response = auth_client.post(
        "/tracer/project-version/get_export_data/",
        {
            "project_id": str(project.id),
            "runs_ids": [str(project_version.id)],
        },
        format="json",
    )

    assert response.status_code == 200
    csv_body = b"".join(response.streaming_content).decode("utf-8")
    assert label.name in csv_body
    assert ",7.0" in csv_body or ",7" in csv_body
