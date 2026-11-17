"""Get started counts the Observe project that a first trace creates.

The collector creates that project at ingest without a user on it, so the
observability step counts the organization's Observe projects. The demo
project seeded for every new organization, other organizations and other
workspaces still do not count.
"""

import pytest
from rest_framework import status

from accounts.models import Organization
from accounts.models.workspace import Workspace
from accounts.views.user import get_user_checks
from model_hub.models.ai_model import AIModel
from tracer.models.project import Project, ProjectSourceChoices


def _observe_project(name, organization, workspace, **fields):
    return Project.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        **fields,
    )


def _first_checks_observe(auth_client):
    response = auth_client.get("/accounts/first-checks/")
    assert response.status_code == status.HTTP_200_OK
    return response.json().get("result", {})["observe"]


@pytest.mark.integration
@pytest.mark.api
class TestObserveCheckCountsIngestProjects:
    def test_project_created_at_ingest_completes_the_step(
        self, auth_client, organization, workspace
    ):
        assert _first_checks_observe(auth_client) is False

        # The row the collector writes for the first trace: no user on it.
        _observe_project("FUTURE_AGI", organization, workspace, user=None)

        assert _first_checks_observe(auth_client) is True

    def test_sidebar_check_counts_project_created_at_ingest(
        self, user, organization, workspace
    ):
        # get_user_info builds get_started_completed (the sidebar's "Get
        # started" placement) from this call, with no workspace.
        _observe_project("FUTURE_AGI", organization, workspace, user=None)

        assert get_user_checks(user)["observe"] is True

    def test_demo_project_does_not_complete_the_step(
        self, auth_client, user, organization, workspace
    ):
        _observe_project(
            "Demo SQL Agent (Observe)",
            organization,
            workspace,
            source=ProjectSourceChoices.DEMO.value,
        )

        assert _first_checks_observe(auth_client) is False
        assert get_user_checks(user)["observe"] is False

    def test_other_organization_project_does_not_complete_the_step(
        self, auth_client, user
    ):
        other_organization = Organization.objects.create(name="Other Organization")
        _observe_project("FUTURE_AGI", other_organization, None)

        assert _first_checks_observe(auth_client) is False
        assert get_user_checks(user)["observe"] is False

    def test_other_workspace_project_does_not_complete_the_step(
        self, auth_client, user, organization
    ):
        other_workspace = Workspace.objects.create(
            name="Observe Other Workspace",
            organization=organization,
            is_active=True,
            created_by=user,
        )
        _observe_project("FUTURE_AGI", organization, other_workspace)

        assert _first_checks_observe(auth_client) is False
