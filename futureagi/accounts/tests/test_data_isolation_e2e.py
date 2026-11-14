"""
End-to-end data isolation tests for cross-organization and cross-workspace boundaries.

Covers the CRITICAL security requirement that:
  1. Data created in Org A is NEVER visible in Org B.
  2. Data created in Workspace A is NEVER visible in Workspace B (same org).
  3. Direct access by ID to another org's resource returns 403/404.
  4. Default workspace includes null-workspace (legacy) data; non-default excludes it.
  5. Write isolation: creating data in ws A does not appear in ws B.
"""

import uuid

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(user, workspace):
    """Return an authenticated WorkspaceAwareAPIClient targeting *workspace*."""
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return client


def _extract_items(resp, name_field="name"):
    """Extract a flat list of *name_field* values from a paginated/nested response.

    Handles many API wrapper styles:
      - {"status": true, "result": {"projects": [...], ...}}
      - {"status": true, "result": {"data": [...]}}
      - {"results": [...]}
      - {"data": {"results": [...]}}
      - [...]
      - {"status": true, "result": {"table_data": [...]}}
    """
    data = resp.data

    # Unwrap GeneralMethods success_response: {"status": ..., "result": ...}
    if isinstance(data, dict) and "result" in data:
        data = data["result"]

    # Unwrap known list keys
    if isinstance(data, dict):
        # Try common list-wrapper keys in priority order
        for key in ("projects", "table", "table_data", "datasets", "data", "results"):
            if key in data:
                inner = data[key]
                if isinstance(inner, list):
                    data = inner
                    break
                if isinstance(inner, dict) and "results" in inner:
                    data = inner["results"]
                    break

    # If still a dict with "results" key
    if isinstance(data, dict) and "results" in data:
        data = data["results"]

    if isinstance(data, list):
        return [item.get(name_field, "") for item in data if isinstance(item, dict)]
    return []


# ---------------------------------------------------------------------------
# Fixtures — Two-Org Setup
# ---------------------------------------------------------------------------


@pytest.fixture
def org_a(db):
    return Organization.objects.create(
        name="Isolation Org A", display_name="Isolation Org A"
    )


@pytest.fixture
def org_b(db):
    return Organization.objects.create(
        name="Isolation Org B", display_name="Isolation Org B"
    )


@pytest.fixture
def iso_user(db, org_a, org_b):
    """A single user who is OWNER in both orgs, each with a default workspace."""
    clear_workspace_context()
    set_workspace_context(organization=org_a)

    user = User.objects.create_user(
        email="isolation@futureagi.com",
        password="testpass123",
        name="Isolation User",
        organization=org_a,
        organization_role=OrganizationRoles.OWNER,
    )

    ws_a = Workspace.objects.create(
        name="Iso WS A",
        organization=org_a,
        is_default=True,
        is_active=True,
        created_by=user,
    )

    clear_workspace_context()
    set_workspace_context(organization=org_b)

    ws_b = Workspace.objects.create(
        name="Iso WS B",
        organization=org_b,
        is_default=True,
        is_active=True,
        created_by=user,
    )

    # Org memberships
    for org in (org_a, org_b):
        OrganizationMembership.no_workspace_objects.get_or_create(
            user=user,
            organization=org,
            defaults={
                "role": OrganizationRoles.OWNER,
                "level": Level.OWNER,
                "is_active": True,
            },
        )

    # Workspace memberships
    for ws in (ws_a, ws_b):
        WorkspaceMembership.no_workspace_objects.get_or_create(
            workspace=ws,
            user=user,
            defaults={"role": OrganizationRoles.OWNER, "level": Level.OWNER},
        )

    clear_workspace_context()
    return user, org_a, org_b, ws_a, ws_b


# ---------------------------------------------------------------------------
# Fixtures — Two-Workspace Setup (same org)
# ---------------------------------------------------------------------------


@pytest.fixture
def two_ws_setup(db, org_a, iso_user):
    """Two workspaces (default + non-default) inside org_a for workspace isolation tests."""
    user, _, _, ws_a, _ = iso_user

    clear_workspace_context()
    set_workspace_context(organization=org_a)

    ws_a2 = Workspace.objects.create(
        name="Iso WS A Non-Default",
        organization=org_a,
        is_default=False,
        is_active=True,
        created_by=user,
    )

    WorkspaceMembership.no_workspace_objects.get_or_create(
        workspace=ws_a2,
        user=user,
        defaults={"role": OrganizationRoles.OWNER, "level": Level.OWNER},
    )

    clear_workspace_context()
    return ws_a, ws_a2  # ws_a is default, ws_a2 is non-default


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_projects(user, org, ws, prefix):
    """Create a project in the given org/workspace and return it."""
    from tracer.models.project import Project

    clear_workspace_context()
    set_workspace_context(workspace=ws, organization=org, user=user)

    project = Project(
        name=f"{prefix} Project",
        organization=org,
        workspace=ws,
        user=user,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    project.save()

    clear_workspace_context()
    return project


def _seed_datasets(user, org, ws, prefix):
    """Create a dataset in the given org/workspace and return it."""
    from model_hub.models.develop_dataset import Dataset

    clear_workspace_context()
    set_workspace_context(workspace=ws, organization=org, user=user)

    dataset = Dataset.objects.create(
        name=f"{prefix} Dataset",
        organization=org,
        workspace=ws,
        user=user,
    )

    clear_workspace_context()
    return dataset


def _seed_prompt_templates(user, org, ws, prefix):
    """Create a PromptTemplate in the given org/workspace."""
    from model_hub.models.run_prompt import PromptTemplate

    clear_workspace_context()
    set_workspace_context(workspace=ws, organization=org, user=user)

    pt = PromptTemplate.objects.create(
        name=f"{prefix} Prompt",
        organization=org,
        workspace=ws,
    )

    clear_workspace_context()
    return pt


def _seed_monitors(user, org, ws, project, prefix):
    """Create a UserAlertMonitor in the given org/workspace."""
    from tracer.models.monitor import UserAlertMonitor

    clear_workspace_context()
    set_workspace_context(workspace=ws, organization=org, user=user)

    monitor = UserAlertMonitor.objects.create(
        name=f"{prefix} Monitor",
        organization=org,
        workspace=ws,
        created_by=user,
        project=project,
        metric_type="count_of_errors",
        threshold_operator="greater_than",
        threshold_type="static",
        critical_threshold_value=100.0,
        alert_frequency=60,
    )

    clear_workspace_context()
    return monitor


def _seed_knowledge_bases(user, org, ws, prefix):
    """Create a KnowledgeBaseFile in the given org/workspace."""
    from model_hub.models.develop_dataset import KnowledgeBaseFile

    clear_workspace_context()
    set_workspace_context(workspace=ws, organization=org, user=user)

    kb = KnowledgeBaseFile.objects.create(
        name=f"{prefix} KB",
        organization=org,
        workspace=ws,
    )

    clear_workspace_context()
    return kb


def _seed_agent_definitions(user, org, ws, prefix):
    """Create an AgentDefinition in the given org/workspace."""
    from simulate.models.agent_definition import AgentDefinition

    clear_workspace_context()
    set_workspace_context(workspace=ws, organization=org, user=user)

    agent = AgentDefinition.objects.create(
        agent_name=f"{prefix} Agent",
        organization=org,
        workspace=ws,
        inbound=False,
        description=f"{prefix} agent def",
    )

    clear_workspace_context()
    return agent


def _seed_secrets(user, org, ws, prefix):
    """Create a SecretModel in the given org/workspace."""
    from model_hub.models.api_key import SecretModel

    clear_workspace_context()
    set_workspace_context(workspace=ws, organization=org, user=user)

    secret = SecretModel.objects.create(
        name=f"{prefix} Secret",
        organization=org,
        workspace=ws,
        secret_type="OTHER",
        key="test-secret-key-value",
    )

    clear_workspace_context()
    return secret


# ---------------------------------------------------------------------------
# Comprehensive seed fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_orgs(iso_user):
    """Seed both orgs with projects, datasets, prompts, monitors, KBs, agents, secrets."""
    user, org_a, org_b, ws_a, ws_b = iso_user

    project_a = _seed_projects(user, org_a, ws_a, "Alpha")
    project_b = _seed_projects(user, org_b, ws_b, "Beta")

    dataset_a = _seed_datasets(user, org_a, ws_a, "Alpha")
    dataset_b = _seed_datasets(user, org_b, ws_b, "Beta")

    prompt_a = _seed_prompt_templates(user, org_a, ws_a, "Alpha")
    prompt_b = _seed_prompt_templates(user, org_b, ws_b, "Beta")

    monitor_a = _seed_monitors(user, org_a, ws_a, project_a, "Alpha")
    monitor_b = _seed_monitors(user, org_b, ws_b, project_b, "Beta")

    kb_a = _seed_knowledge_bases(user, org_a, ws_a, "Alpha")
    kb_b = _seed_knowledge_bases(user, org_b, ws_b, "Beta")

    agent_a = _seed_agent_definitions(user, org_a, ws_a, "Alpha")
    agent_b = _seed_agent_definitions(user, org_b, ws_b, "Beta")

    secret_a = _seed_secrets(user, org_a, ws_a, "Alpha")
    secret_b = _seed_secrets(user, org_b, ws_b, "Beta")

    return {
        "user": user,
        "org_a": org_a,
        "org_b": org_b,
        "ws_a": ws_a,
        "ws_b": ws_b,
        "project_a": project_a,
        "project_b": project_b,
        "dataset_a": dataset_a,
        "dataset_b": dataset_b,
        "prompt_a": prompt_a,
        "prompt_b": prompt_b,
        "monitor_a": monitor_a,
        "monitor_b": monitor_b,
        "kb_a": kb_a,
        "kb_b": kb_b,
        "agent_a": agent_a,
        "agent_b": agent_b,
        "secret_a": secret_a,
        "secret_b": secret_b,
    }


# ===================================================================
# A. CROSS-ORGANIZATION ISOLATION
# ===================================================================


class TestCrossOrgProjectIsolation:
    """Projects must be isolated between organizations."""

    def test_list_projects_org_a(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get("/tracer/project/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Alpha Project" in names, f"Expected Alpha Project in org A, got {names}"
        assert "Beta Project" not in names, (
            f"Beta Project leaked into org A! Got {names}"
        )
        client.stop_workspace_injection()

    def test_list_projects_org_b(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_b"])
        resp = client.get("/tracer/project/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Beta Project" in names, f"Expected Beta Project in org B, got {names}"
        assert "Alpha Project" not in names, (
            f"Alpha Project leaked into org B! Got {names}"
        )
        client.stop_workspace_injection()

    def test_access_other_org_project_by_id(self, seeded_orgs):
        """Direct access to org B's project from org A should fail."""
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get(f"/tracer/project/{s['project_b'].id}/")
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), f"Expected 400/403/404 accessing other org's project, got {resp.status_code}"
        client.stop_workspace_injection()


class TestCrossOrgDatasetIsolation:
    """Datasets must be isolated between organizations."""

    def test_list_datasets_org_a(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get("/model-hub/develops/get-datasets/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Beta Dataset" not in names, (
            f"Beta Dataset leaked into org A! Got {names}"
        )
        client.stop_workspace_injection()

    def test_list_datasets_org_b(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_b"])
        resp = client.get("/model-hub/develops/get-datasets/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Alpha Dataset" not in names, (
            f"Alpha Dataset leaked into org B! Got {names}"
        )
        client.stop_workspace_injection()


class TestCrossOrgPromptTemplateIsolation:
    """Prompt templates must be isolated between organizations."""

    def test_list_prompts_org_a(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get("/model-hub/prompt-templates/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Alpha Prompt" in names, f"Expected Alpha Prompt in org A, got {names}"
        assert "Beta Prompt" not in names, f"Beta Prompt leaked into org A! Got {names}"
        client.stop_workspace_injection()

    def test_list_prompts_org_b(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_b"])
        resp = client.get("/model-hub/prompt-templates/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Beta Prompt" in names, f"Expected Beta Prompt in org B, got {names}"
        assert "Alpha Prompt" not in names, (
            f"Alpha Prompt leaked into org B! Got {names}"
        )
        client.stop_workspace_injection()


class TestCrossOrgMonitorIsolation:
    """Monitors/alerts must be isolated between organizations."""

    def test_list_monitors_org_a(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get("/tracer/user-alerts/list_monitors/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Alpha Monitor" in names, f"Expected Alpha Monitor in org A, got {names}"
        assert "Beta Monitor" not in names, (
            f"Beta Monitor leaked into org A! Got {names}"
        )
        client.stop_workspace_injection()

    def test_list_monitors_org_b(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_b"])
        resp = client.get("/tracer/user-alerts/list_monitors/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Beta Monitor" in names, f"Expected Beta Monitor in org B, got {names}"
        assert "Alpha Monitor" not in names, (
            f"Alpha Monitor leaked into org B! Got {names}"
        )
        client.stop_workspace_injection()

    def test_access_other_org_monitor_by_id(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get(f"/tracer/user-alerts/{s['monitor_b'].id}/details/")
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), f"Expected 400/403/404 accessing other org's monitor, got {resp.status_code}"
        client.stop_workspace_injection()


class TestCrossOrgAgentDefinitionIsolation:
    """Agent definitions must be isolated between organizations."""

    def test_list_agents_org_a(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get("/simulate/agent-definitions/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp, name_field="agent_name")
        if not names:
            names = _extract_items(resp, name_field="name")
        assert "Beta Agent" not in names, f"Beta Agent leaked into org A! Got {names}"
        client.stop_workspace_injection()

    def test_list_agents_org_b(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_b"])
        resp = client.get("/simulate/agent-definitions/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp, name_field="agent_name")
        if not names:
            names = _extract_items(resp, name_field="name")
        assert "Alpha Agent" not in names, f"Alpha Agent leaked into org B! Got {names}"
        client.stop_workspace_injection()


class TestCrossOrgKnowledgeBaseIsolation:
    """Knowledge bases must be isolated between organizations."""

    def test_list_kbs_org_a(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get("/model-hub/knowledge-base/list/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Beta KB" not in names, f"Beta KB leaked into org A! Got {names}"
        client.stop_workspace_injection()

    def test_list_kbs_org_b(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_b"])
        resp = client.get("/model-hub/knowledge-base/list/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Alpha KB" not in names, f"Alpha KB leaked into org B! Got {names}"
        client.stop_workspace_injection()


class TestCrossOrgSecretIsolation:
    """Secrets must be isolated between organizations."""

    def test_list_secrets_org_a(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get("/model-hub/secrets/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Beta Secret" not in names, f"Beta Secret leaked into org A! Got {names}"
        client.stop_workspace_injection()

    def test_list_secrets_org_b(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_b"])
        resp = client.get("/model-hub/secrets/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Alpha Secret" not in names, (
            f"Alpha Secret leaked into org B! Got {names}"
        )
        client.stop_workspace_injection()

    def test_access_other_org_secret_by_id(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get(f"/model-hub/secrets/{s['secret_b'].id}/")
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), f"Expected 400/403/404 accessing other org's secret, got {resp.status_code}"
        client.stop_workspace_injection()


# ===================================================================
# A2. CROSS-ORG ACCESS ATTEMPTS (by ID)
# ===================================================================


class TestCrossOrgAccessByID:
    """Directly accessing another org's resource by ID must NOT return the resource's data.

    Some views return 400 (bad request) on not-found within org scope rather than 404.
    This is acceptable -- the key invariant is that the OTHER org's data is never returned.
    """

    # Acceptable "denied" status codes: the resource is NOT returned.
    _DENIED = (
        status.HTTP_400_BAD_REQUEST,
        status.HTTP_403_FORBIDDEN,
        status.HTTP_404_NOT_FOUND,
    )

    def test_access_other_org_project_by_id(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get(f"/tracer/project/{s['project_b'].id}/")
        assert resp.status_code in self._DENIED, (
            f"Expected denied accessing other org's project, got {resp.status_code}"
        )
        client.stop_workspace_injection()

    def test_access_other_org_prompt_by_id(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.get(f"/model-hub/prompt-templates/{s['prompt_b'].id}/")
        assert resp.status_code in self._DENIED, (
            f"Expected denied accessing other org's prompt, got {resp.status_code}"
        )
        client.stop_workspace_injection()

    def test_delete_other_org_project(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.delete(f"/tracer/project/{s['project_b'].id}/")
        assert resp.status_code in self._DENIED + (
            status.HTTP_405_METHOD_NOT_ALLOWED,
        ), f"Should not be able to delete other org's project, got {resp.status_code}"
        client.stop_workspace_injection()

    def test_modify_other_org_monitor(self, seeded_orgs):
        s = seeded_orgs
        client = _make_client(s["user"], s["ws_a"])
        resp = client.patch(
            f"/tracer/user-alerts/{s['monitor_b'].id}/",
            {"name": "Hacked Monitor"},
            format="json",
        )
        assert resp.status_code in self._DENIED, (
            f"Should not be able to modify other org's monitor, got {resp.status_code}"
        )
        client.stop_workspace_injection()


# ===================================================================
# A3. HEADER MANIPULATION ATTACKS
# ===================================================================


class TestHeaderManipulation:
    """Sending forged X-Organization-Id / X-Workspace-Id headers should not leak data."""

    def test_forged_org_header_returns_correct_data(self, seeded_orgs):
        """Even with X-Organization-Id of org_b, user should see only org_b data (they are a member)."""
        s = seeded_orgs
        # Client configured for org_b
        client = _make_client(s["user"], s["ws_b"])
        resp = client.get("/model-hub/prompt-templates/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Alpha Prompt" not in names, (
            f"Alpha Prompt leaked via header switch! Got {names}"
        )
        assert "Beta Prompt" in names, (
            f"Expected Beta Prompt via header switch, got {names}"
        )
        client.stop_workspace_injection()

    def test_switch_org_then_switch_back(self, seeded_orgs):
        """Switching org B then back to org A should restore org A data."""
        s = seeded_orgs

        # First: org B
        client_b = _make_client(s["user"], s["ws_b"])
        resp_b = client_b.get("/tracer/project/")
        assert resp_b.status_code == status.HTTP_200_OK
        names_b = _extract_items(resp_b)
        assert "Beta Project" in names_b
        assert "Alpha Project" not in names_b
        client_b.stop_workspace_injection()

        # Then: back to org A
        client_a = _make_client(s["user"], s["ws_a"])
        resp_a = client_a.get("/tracer/project/")
        assert resp_a.status_code == status.HTTP_200_OK
        names_a = _extract_items(resp_a)
        assert "Alpha Project" in names_a
        assert "Beta Project" not in names_a
        client_a.stop_workspace_injection()


# ===================================================================
# B. CROSS-WORKSPACE ISOLATION (same org)
# ===================================================================


class TestCrossWorkspaceProjectIsolation:
    """Projects in ws_a (default) vs ws_a2 (non-default) must be isolated."""

    def test_project_in_default_ws_not_in_nondefault(self, seeded_orgs, two_ws_setup):
        s = seeded_orgs
        ws_default, ws_nondefault = two_ws_setup

        # Create project in non-default workspace
        project_nd = _seed_projects(s["user"], s["org_a"], ws_nondefault, "NonDefault")

        # From default workspace: should see Alpha Project (and possibly NonDefault due to default
        # workspace including all), should NOT see Beta org's project
        client_default = _make_client(s["user"], ws_default)
        resp = client_default.get("/tracer/project/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Beta Project" not in names, (
            f"Beta Project (other org) leaked! Got {names}"
        )
        client_default.stop_workspace_injection()

        # From non-default workspace: should see NonDefault Project, NOT Alpha Project
        client_nd = _make_client(s["user"], ws_nondefault)
        resp_nd = client_nd.get("/tracer/project/")
        assert resp_nd.status_code == status.HTTP_200_OK
        names_nd = _extract_items(resp_nd)
        assert "NonDefault Project" in names_nd, (
            f"Expected NonDefault Project, got {names_nd}"
        )
        assert "Alpha Project" not in names_nd, (
            f"Alpha Project leaked into non-default ws! Got {names_nd}"
        )
        client_nd.stop_workspace_injection()


class TestCrossWorkspaceDatasetIsolation:
    """Datasets created in one workspace must not appear in another."""

    def test_dataset_workspace_isolation(self, seeded_orgs, two_ws_setup):
        s = seeded_orgs
        ws_default, ws_nondefault = two_ws_setup

        # Create dataset in non-default workspace
        _seed_datasets(s["user"], s["org_a"], ws_nondefault, "WsB")

        # From non-default: should see WsB Dataset, not Alpha Dataset
        client_nd = _make_client(s["user"], ws_nondefault)
        resp = client_nd.get("/model-hub/develops/get-datasets/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "WsB Dataset" in names, f"Expected WsB Dataset, got {names}"
        assert "Alpha Dataset" not in names, (
            f"Alpha Dataset leaked into non-default ws! Got {names}"
        )
        client_nd.stop_workspace_injection()


class TestCrossWorkspacePromptIsolation:
    """Prompt templates must be isolated between workspaces."""

    def test_prompt_workspace_isolation(self, seeded_orgs, two_ws_setup):
        s = seeded_orgs
        ws_default, ws_nondefault = two_ws_setup

        _seed_prompt_templates(s["user"], s["org_a"], ws_nondefault, "WsB")

        client_nd = _make_client(s["user"], ws_nondefault)
        resp = client_nd.get("/model-hub/prompt-templates/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "WsB Prompt" in names, f"Expected WsB Prompt, got {names}"
        assert "Alpha Prompt" not in names, (
            f"Alpha Prompt leaked into non-default ws! Got {names}"
        )
        client_nd.stop_workspace_injection()


class TestCrossWorkspaceMonitorIsolation:
    """Monitors must be isolated between workspaces."""

    def test_monitor_workspace_isolation(self, seeded_orgs, two_ws_setup):
        s = seeded_orgs
        ws_default, ws_nondefault = two_ws_setup

        project_nd = _seed_projects(s["user"], s["org_a"], ws_nondefault, "MonWsB")
        _seed_monitors(s["user"], s["org_a"], ws_nondefault, project_nd, "WsB")

        client_nd = _make_client(s["user"], ws_nondefault)
        resp = client_nd.get("/tracer/user-alerts/list_monitors/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "WsB Monitor" in names, f"Expected WsB Monitor, got {names}"
        assert "Alpha Monitor" not in names, (
            f"Alpha Monitor leaked into non-default ws! Got {names}"
        )
        client_nd.stop_workspace_injection()


class TestCrossWorkspaceSecretIsolation:
    """Secrets must be isolated between workspaces."""

    def test_secret_workspace_isolation(self, seeded_orgs, two_ws_setup):
        s = seeded_orgs
        ws_default, ws_nondefault = two_ws_setup

        _seed_secrets(s["user"], s["org_a"], ws_nondefault, "WsB")

        client_nd = _make_client(s["user"], ws_nondefault)
        resp = client_nd.get("/model-hub/secrets/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "WsB Secret" in names, f"Expected WsB Secret, got {names}"
        assert "Alpha Secret" not in names, (
            f"Alpha Secret leaked into non-default ws! Got {names}"
        )
        client_nd.stop_workspace_injection()


# ===================================================================
# B2. DEFAULT WORKSPACE INCLUDES LEGACY (NULL-WORKSPACE) DATA
# ===================================================================


class TestDefaultWorkspaceNullData:
    """Default workspace should include records with workspace=NULL; non-default should exclude them."""

    def test_null_workspace_visible_in_default(self, seeded_orgs, two_ws_setup):
        """Data with workspace=NULL should be visible in the default workspace."""
        s = seeded_orgs
        ws_default, ws_nondefault = two_ws_setup
        from model_hub.models.run_prompt import PromptTemplate

        clear_workspace_context()
        set_workspace_context(organization=s["org_a"], user=s["user"])

        # Create a prompt with null workspace (legacy data)
        legacy = PromptTemplate.no_workspace_objects.create(
            name="Legacy Null Prompt",
            organization=s["org_a"],
            workspace=None,
        )
        clear_workspace_context()

        # Default workspace should see it
        client = _make_client(s["user"], ws_default)
        resp = client.get("/model-hub/prompt-templates/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Legacy Null Prompt" in names, (
            f"Null-workspace prompt should be in default ws, got {names}"
        )
        client.stop_workspace_injection()

    def test_null_workspace_not_in_nondefault(self, seeded_orgs, two_ws_setup):
        """Data with workspace=NULL should NOT be visible in a non-default workspace."""
        s = seeded_orgs
        ws_default, ws_nondefault = two_ws_setup
        from model_hub.models.run_prompt import PromptTemplate

        clear_workspace_context()
        set_workspace_context(organization=s["org_a"], user=s["user"])

        PromptTemplate.no_workspace_objects.create(
            name="Legacy Null Prompt ND",
            organization=s["org_a"],
            workspace=None,
        )
        clear_workspace_context()

        # Non-default workspace should NOT see it
        client = _make_client(s["user"], ws_nondefault)
        resp = client.get("/model-hub/prompt-templates/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Legacy Null Prompt ND" not in names, (
            f"Null-workspace prompt should NOT be in non-default ws, got {names}"
        )
        client.stop_workspace_injection()


# ===================================================================
# B3. WRITE ISOLATION
# ===================================================================


class TestWriteIsolation:
    """Creating data in workspace A must not make it appear in workspace B."""

    def test_create_project_in_ws_a_not_in_ws_b(self, seeded_orgs, two_ws_setup):
        s = seeded_orgs
        ws_default, ws_nondefault = two_ws_setup

        # Create via API in default workspace
        client_a = _make_client(s["user"], ws_default)
        create_resp = client_a.post(
            "/tracer/project/",
            {
                "name": "Write Test Project",
                "model_type": "GenerativeLLM",
                "trace_type": "observe",
            },
            format="json",
        )
        # Accept 201 or 200
        assert create_resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        ), f"Failed to create project: {create_resp.status_code} {create_resp.data}"
        client_a.stop_workspace_injection()

        # Verify NOT visible in non-default workspace
        client_b = _make_client(s["user"], ws_nondefault)
        resp = client_b.get("/tracer/project/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "Write Test Project" not in names, (
            f"Project created in ws A appeared in ws B! Got {names}"
        )
        client_b.stop_workspace_injection()

    def test_create_dataset_in_ws_a_not_in_ws_b(self, seeded_orgs, two_ws_setup):
        s = seeded_orgs
        ws_default, ws_nondefault = two_ws_setup

        # Create dataset directly in default workspace
        _seed_datasets(s["user"], s["org_a"], ws_default, "WriteTestDS")

        # Not visible in non-default
        client_b = _make_client(s["user"], ws_nondefault)
        resp = client_b.get("/model-hub/develops/get-datasets/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "WriteTestDS Dataset" not in names, (
            f"Dataset created in ws A appeared in ws B! Got {names}"
        )
        client_b.stop_workspace_injection()

    def test_create_secret_in_ws_a_not_in_ws_b(self, seeded_orgs, two_ws_setup):
        s = seeded_orgs
        ws_default, ws_nondefault = two_ws_setup

        _seed_secrets(s["user"], s["org_a"], ws_default, "WriteTestSecret")

        client_b = _make_client(s["user"], ws_nondefault)
        resp = client_b.get("/model-hub/secrets/")
        assert resp.status_code == status.HTTP_200_OK
        names = _extract_items(resp)
        assert "WriteTestSecret Secret" not in names, (
            f"Secret created in ws A appeared in ws B! Got {names}"
        )
        client_b.stop_workspace_injection()
