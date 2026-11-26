"""
Test cross-organization data isolation across all major API endpoints.

Creates 2 orgs with separate data, then verifies that switching between
orgs returns ONLY the data belonging to the selected org.
"""

import uuid

import pytest
from django.test import override_settings
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace
from conftest import WorkspaceAwareAPIClient
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)


@pytest.fixture
def org_a(db):
    return Organization.objects.create(name="Org Alpha", display_name="Org Alpha")


@pytest.fixture
def org_b(db):
    return Organization.objects.create(name="Org Beta", display_name="Org Beta")


@pytest.fixture
def multi_org_user(db, org_a, org_b):
    """Create a user who belongs to BOTH organizations."""
    clear_workspace_context()
    set_workspace_context(organization=org_a)

    user = User.objects.create_user(
        email="multiorg@futureagi.com",
        password="testpass123",
        name="Multi Org User",
        organization=org_a,  # Primary org FK
        organization_role=OrganizationRoles.OWNER,
    )

    # Create workspaces for each org
    ws_a = Workspace.objects.create(
        name="Alpha Workspace",
        organization=org_a,
        is_default=True,
        is_active=True,
        created_by=user,
    )

    clear_workspace_context()
    set_workspace_context(organization=org_b)

    ws_b = Workspace.objects.create(
        name="Beta Workspace",
        organization=org_b,
        is_default=True,
        is_active=True,
        created_by=user,
    )

    # Create memberships for both orgs
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org_a,
        defaults={"role": OrganizationRoles.OWNER, "is_active": True},
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org_b,
        defaults={"role": OrganizationRoles.OWNER, "is_active": True},
    )

    clear_workspace_context()
    return user, ws_a, ws_b


@pytest.fixture
def seed_data(multi_org_user, org_a, org_b):
    """Create distinct data in each org."""
    user, ws_a, ws_b = multi_org_user

    # Import models
    from model_hub.models.develop_dataset import Dataset
    from model_hub.models.run_prompt import PromptTemplate

    # Seed Org A data
    clear_workspace_context()
    set_workspace_context(workspace=ws_a, organization=org_a, user=user)

    dataset_a = Dataset.objects.create(
        name="Alpha Dataset",
        organization=org_a,
        workspace=ws_a,
        user=user,
    )
    prompt_a = PromptTemplate.objects.create(
        name="Alpha Prompt",
        organization=org_a,
        workspace=ws_a,
    )

    # Seed Org B data
    clear_workspace_context()
    set_workspace_context(workspace=ws_b, organization=org_b, user=user)

    dataset_b = Dataset.objects.create(
        name="Beta Dataset",
        organization=org_b,
        workspace=ws_b,
        user=user,
    )
    prompt_b = PromptTemplate.objects.create(
        name="Beta Prompt",
        organization=org_b,
        workspace=ws_b,
    )

    clear_workspace_context()

    return {
        "org_a": org_a,
        "org_b": org_b,
        "ws_a": ws_a,
        "ws_b": ws_b,
        "dataset_a": dataset_a,
        "dataset_b": dataset_b,
        "prompt_a": prompt_a,
        "prompt_b": prompt_b,
    }


def _make_client_for_org(user, workspace):
    """Create an authenticated client targeting a specific org/workspace."""
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return client


class TestCrossOrgDataIsolation:
    """Verify that switching orgs shows ONLY the correct org's data."""

    def test_prompt_templates_isolated(self, multi_org_user, seed_data):
        """PromptTemplateViewSet must only return prompts for the selected org."""
        user, ws_a, ws_b = multi_org_user

        # Client for Org A
        client_a = _make_client_for_org(user, ws_a)
        resp_a = client_a.get("/model-hub/prompt-templates/")
        assert resp_a.status_code == status.HTTP_200_OK
        names_a = [p["name"] for p in resp_a.data.get("results", resp_a.data)]
        assert "Alpha Prompt" in names_a, (
            f"Expected Alpha Prompt in Org A, got: {names_a}"
        )
        assert "Beta Prompt" not in names_a, (
            f"Beta Prompt leaked into Org A! Got: {names_a}"
        )
        client_a.stop_workspace_injection()

        # Client for Org B
        client_b = _make_client_for_org(user, ws_b)
        resp_b = client_b.get("/model-hub/prompt-templates/")
        assert resp_b.status_code == status.HTTP_200_OK
        names_b = [p["name"] for p in resp_b.data.get("results", resp_b.data)]
        assert "Beta Prompt" in names_b, (
            f"Expected Beta Prompt in Org B, got: {names_b}"
        )
        assert "Alpha Prompt" not in names_b, (
            f"Alpha Prompt leaked into Org B! Got: {names_b}"
        )
        client_b.stop_workspace_injection()

    def test_datasets_isolated(self, multi_org_user, seed_data):
        """GetDatasetsView must only return datasets for the selected org."""
        user, ws_a, ws_b = multi_org_user

        client_a = _make_client_for_org(user, ws_a)
        resp_a = client_a.get("/model-hub/develops/get-datasets/")
        assert resp_a.status_code == status.HTTP_200_OK
        data_a = resp_a.data
        # Response could be wrapped in "data" key
        if isinstance(data_a, dict) and "data" in data_a:
            items_a = data_a["data"]
            if isinstance(items_a, dict) and "results" in items_a:
                items_a = items_a["results"]
        elif isinstance(data_a, dict) and "results" in data_a:
            items_a = data_a["results"]
        else:
            items_a = data_a if isinstance(data_a, list) else []

        names_a = (
            [d.get("name", "") for d in items_a] if isinstance(items_a, list) else []
        )
        assert "Beta Dataset" not in names_a, (
            f"Beta Dataset leaked into Org A! Got: {names_a}"
        )
        client_a.stop_workspace_injection()

        client_b = _make_client_for_org(user, ws_b)
        resp_b = client_b.get("/model-hub/develops/get-datasets/")
        assert resp_b.status_code == status.HTTP_200_OK
        data_b = resp_b.data
        if isinstance(data_b, dict) and "data" in data_b:
            items_b = data_b["data"]
            if isinstance(items_b, dict) and "results" in items_b:
                items_b = items_b["results"]
        elif isinstance(data_b, dict) and "results" in data_b:
            items_b = data_b["results"]
        else:
            items_b = data_b if isinstance(data_b, list) else []

        names_b = (
            [d.get("name", "") for d in items_b] if isinstance(items_b, list) else []
        )
        assert "Alpha Dataset" not in names_b, (
            f"Alpha Dataset leaked into Org B! Got: {names_b}"
        )
        client_b.stop_workspace_injection()

    def test_agent_definitions_isolated(self, multi_org_user, seed_data):
        """AgentDefinitionView must only return agents for the selected org."""
        from simulate.models.agent_definition import AgentDefinition

        user, ws_a, ws_b = multi_org_user
        org_a = seed_data["org_a"]
        org_b = seed_data["org_b"]

        # Create agent definitions in each org
        clear_workspace_context()
        set_workspace_context(
            workspace=seed_data["ws_a"], organization=org_a, user=user
        )
        AgentDefinition.objects.create(
            agent_name="Alpha Agent",
            organization=org_a,
            workspace=seed_data["ws_a"],
            inbound=False,
            description="Alpha agent",
        )

        clear_workspace_context()
        set_workspace_context(
            workspace=seed_data["ws_b"], organization=org_b, user=user
        )
        AgentDefinition.objects.create(
            agent_name="Beta Agent",
            organization=org_b,
            workspace=seed_data["ws_b"],
            inbound=False,
            description="Beta agent",
        )
        clear_workspace_context()

        # Test Org A
        client_a = _make_client_for_org(user, seed_data["ws_a"])
        resp_a = client_a.get("/simulate/agent-definitions/")
        assert resp_a.status_code == status.HTTP_200_OK
        data_a = resp_a.data
        if isinstance(data_a, dict) and "data" in data_a:
            items_a = data_a["data"]
            if isinstance(items_a, dict) and "results" in items_a:
                items_a = items_a["results"]
        elif isinstance(data_a, dict) and "results" in data_a:
            items_a = data_a["results"]
        else:
            items_a = data_a if isinstance(data_a, list) else []
        names_a = (
            [a.get("agent_name", a.get("name", "")) for a in items_a]
            if isinstance(items_a, list)
            else []
        )
        assert "Beta Agent" not in names_a, (
            f"Beta Agent leaked into Org A! Got: {names_a}"
        )
        client_a.stop_workspace_injection()

        # Test Org B
        client_b = _make_client_for_org(user, seed_data["ws_b"])
        resp_b = client_b.get("/simulate/agent-definitions/")
        assert resp_b.status_code == status.HTTP_200_OK
        data_b = resp_b.data
        if isinstance(data_b, dict) and "data" in data_b:
            items_b = data_b["data"]
            if isinstance(items_b, dict) and "results" in items_b:
                items_b = items_b["results"]
        elif isinstance(data_b, dict) and "results" in data_b:
            items_b = data_b["results"]
        else:
            items_b = data_b if isinstance(data_b, list) else []
        names_b = (
            [a.get("agent_name", a.get("name", "")) for a in items_b]
            if isinstance(items_b, list)
            else []
        )
        assert "Alpha Agent" not in names_b, (
            f"Alpha Agent leaked into Org B! Got: {names_b}"
        )
        client_b.stop_workspace_injection()

    def test_org_switch_via_header(self, multi_org_user, seed_data):
        """Verify that X-Organization-Id header switches the org context correctly."""
        user, ws_a, ws_b = multi_org_user
        org_a = seed_data["org_a"]
        org_b = seed_data["org_b"]

        # User's primary org FK is org_a
        assert user.organization_id == org_a.id

        # But when sending X-Organization-Id for org_b, should see only org_b data
        client = WorkspaceAwareAPIClient()
        client.force_authenticate(user=user)
        client.set_workspace(
            ws_b
        )  # Sets X-Workspace-Id and X-Organization-Id for org_b

        resp = client.get("/model-hub/prompt-templates/")
        assert resp.status_code == status.HTTP_200_OK
        names = [p["name"] for p in resp.data.get("results", resp.data)]
        assert "Beta Prompt" in names, (
            f"Expected Beta Prompt via header switch, got: {names}"
        )
        assert "Alpha Prompt" not in names, (
            f"Alpha Prompt leaked despite header switch! Got: {names}"
        )
        client.stop_workspace_injection()
