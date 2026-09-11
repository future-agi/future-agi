import uuid

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.constants.roles import OrganizationRoles
from tracer.models.imagine_analysis import ImagineAnalysis
from tracer.models.project import Project
from tracer.models.saved_view import SavedView


IMAGINE_ANALYSIS_PATH = "/tracer/imagine-analysis/"


def result(response):
    body = response.json()
    return body.get("result", body)


def create_imagine_saved_view(project, workspace, user, name="Imagine View"):
    return SavedView.no_workspace_objects.create(
        project=project,
        workspace=workspace,
        created_by=user,
        name=f"{name} {uuid.uuid4().hex[:8]}",
        tab_type="imagine",
        visibility="personal",
        position=0,
        config={"widgets": [{"id": "summary", "dynamicAnalysis": True}]},
    )


@pytest.mark.django_db
def test_imagine_analysis_trigger_poll_and_cached_result(
    auth_client,
    observe_project,
    workspace,
    user,
    organization,
    monkeypatch,
):
    saved_view = create_imagine_saved_view(observe_project, workspace, user)
    trace_id = str(uuid.uuid4())
    workflow_calls = []

    def fake_start_imagine_analysis(**kwargs):
        workflow_calls.append(kwargs)
        return "workflow-imagine-test"

    monkeypatch.setattr(
        "tfc.temporal.imagine.client.start_imagine_analysis",
        fake_start_imagine_analysis,
    )

    response = auth_client.post(
        IMAGINE_ANALYSIS_PATH,
        data={
            "saved_view_id": str(saved_view.id),
            "trace_id": trace_id,
            "project_id": str(observe_project.id),
            "widgets": [
                {"widget_id": "summary", "prompt": "Summarize this trace."}
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    payload = result(response)
    assert len(payload["analyses"]) == 1
    assert payload["analyses"][0]["widget_id"] == "summary"
    assert payload["analyses"][0]["status"] == "running"

    analysis = ImagineAnalysis.no_workspace_objects.get(
        saved_view=saved_view,
        widget_id="summary",
        trace_id=trace_id,
    )
    assert analysis.project_id == observe_project.id
    assert analysis.organization_id == organization.id
    assert analysis.status == "running"
    assert analysis.workflow_id == "workflow-imagine-test"
    assert workflow_calls == [
        {
            "analysis_id": str(analysis.id),
            "trace_id": trace_id,
            "org_id": str(organization.id),
            "prompt": "Summarize this trace.",
        }
    ]

    poll_response = auth_client.get(
        IMAGINE_ANALYSIS_PATH,
        data={"saved_view_id": str(saved_view.id), "trace_id": trace_id},
    )
    assert poll_response.status_code == status.HTTP_200_OK
    poll_payload = result(poll_response)
    assert poll_payload["analyses"][0]["id"] == str(analysis.id)
    assert poll_payload["analyses"][0]["status"] == "running"

    analysis.status = "completed"
    analysis.content = "Trace summary content"
    analysis.save(update_fields=["status", "content", "updated_at"])
    workflow_calls.clear()

    cached_response = auth_client.post(
        IMAGINE_ANALYSIS_PATH,
        data={
            "saved_view_id": str(saved_view.id),
            "trace_id": trace_id,
            "project_id": str(observe_project.id),
            "widgets": [
                {"widget_id": "summary", "prompt": "Summarize this trace again."}
            ],
        },
        format="json",
    )
    assert cached_response.status_code == status.HTTP_200_OK
    cached_payload = result(cached_response)
    assert cached_payload["analyses"][0]["status"] == "completed"
    assert cached_payload["analyses"][0]["content"] == "Trace summary content"
    assert workflow_calls == []


@pytest.mark.django_db
def test_imagine_analysis_trigger_failure_persists_failed_status(
    auth_client,
    observe_project,
    workspace,
    user,
    monkeypatch,
):
    saved_view = create_imagine_saved_view(observe_project, workspace, user)
    trace_id = str(uuid.uuid4())

    def fail_start_imagine_analysis(**_kwargs):
        raise RuntimeError("temporal unavailable in test")

    monkeypatch.setattr(
        "tfc.temporal.imagine.client.start_imagine_analysis",
        fail_start_imagine_analysis,
    )

    response = auth_client.post(
        IMAGINE_ANALYSIS_PATH,
        data={
            "saved_view_id": str(saved_view.id),
            "trace_id": trace_id,
            "project_id": str(observe_project.id),
            "widgets": [{"widget_id": "risk", "prompt": "Find risks."}],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    payload = result(response)
    assert payload["analyses"][0]["status"] == "failed"
    analysis = ImagineAnalysis.no_workspace_objects.get(
        saved_view=saved_view,
        widget_id="risk",
        trace_id=trace_id,
    )
    assert analysis.status == "failed"
    assert "temporal unavailable in test" in analysis.error


@pytest.mark.django_db
def test_imagine_analysis_routes_hide_out_of_scope_saved_views_and_results(
    auth_client,
    organization,
    user,
    workspace,
):
    trace_id = str(uuid.uuid4())
    other_workspace = Workspace.no_workspace_objects.create(
        name="Imagine Other Workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    other_workspace_project = Project.no_workspace_objects.create(
        name="Imagine Other Workspace Project",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    other_workspace_saved_view = create_imagine_saved_view(
        other_workspace_project,
        other_workspace,
        user,
        name="Other Workspace Imagine",
    )

    other_organization = Organization.objects.create(name="Imagine Other Org")
    other_user = User.objects.create_user(
        email="imagine-other@example.com",
        password="testpassword123",
        name="Imagine Other User",
        organization=other_organization,
        organization_role=OrganizationRoles.OWNER,
    )
    other_org_workspace = Workspace.no_workspace_objects.create(
        name="Imagine Other Org Workspace",
        organization=other_organization,
        is_default=True,
        is_active=True,
        created_by=other_user,
    )
    other_org_project = Project.no_workspace_objects.create(
        name="Imagine Other Org Project",
        organization=other_organization,
        workspace=other_org_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    other_org_saved_view = create_imagine_saved_view(
        other_org_project,
        other_org_workspace,
        other_user,
        name="Other Org Imagine",
    )

    for saved_view, project, org in (
        (other_workspace_saved_view, other_workspace_project, organization),
        (other_org_saved_view, other_org_project, other_organization),
    ):
        analysis = ImagineAnalysis.no_workspace_objects.create(
            saved_view=saved_view,
            project=project,
            organization=org,
            widget_id="summary",
            trace_id=trace_id,
            prompt="hidden prompt",
            content="hidden content",
            status="completed",
        )

        poll_response = auth_client.get(
            IMAGINE_ANALYSIS_PATH,
            data={"saved_view_id": str(saved_view.id), "trace_id": trace_id},
        )
        assert poll_response.status_code == status.HTTP_404_NOT_FOUND

        trigger_response = auth_client.post(
            IMAGINE_ANALYSIS_PATH,
            data={
                "saved_view_id": str(saved_view.id),
                "trace_id": trace_id,
                "project_id": str(project.id),
                "widgets": [{"widget_id": "summary", "prompt": "try rerun"}],
            },
            format="json",
        )
        assert trigger_response.status_code == status.HTTP_404_NOT_FOUND

        analysis.refresh_from_db()
        assert analysis.status == "completed"
        assert analysis.content == "hidden content"
