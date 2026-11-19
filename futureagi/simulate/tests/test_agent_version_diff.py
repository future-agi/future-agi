"""
Unit and API integration tests for Agent Version Diff and Changelog.

Tests cover:
- compute_prompt_diff (unified diff, structured line chunks, added/removed counts)
- compute_parameter_diff (modified, added, removed parameters)
- compute_performance_delta (score, pass_rate, test_count deltas)
- generate_change_summary (human-readable bullet points)
- compare_agent_versions (full comparison service)
- get_agent_changelog (full changelog generation across versions)
- AgentVersionDiffView API (GET /simulate/agent-definitions/{id}/versions/diff/)
- AgentVersionChangelogView API (GET /simulate/agent-definitions/{id}/versions/changelog/)
"""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from simulate.models import AgentDefinition, AgentVersion
from simulate.services.agent_version_diff import (
    compare_agent_versions,
    compute_parameter_diff,
    compute_performance_delta,
    compute_prompt_diff,
    generate_change_summary,
    get_agent_changelog,
)
from simulate.views.agent_version_diff import (
    AgentVersionChangelogView,
    AgentVersionDiffView,
)

# ============================================================================
# Unit Tests for Pure Service Functions
# ============================================================================


def test_compute_prompt_diff_no_changes():
    prompt = "You are a customer support agent. Be polite and helpful."
    result = compute_prompt_diff(prompt, prompt)

    assert result["has_changes"] is False
    assert result["added_lines"] == 0
    assert result["removed_lines"] == 0
    assert len(result["chunks"]) == 1
    assert result["chunks"][0]["type"] == "unchanged"


def test_compute_prompt_diff_with_modifications():
    prompt_a = "You are a support agent.\nNever give refunds."
    prompt_b = "You are a support agent.\nYou may issue refunds up to $50.\nAlways be polite."

    result = compute_prompt_diff(prompt_a, prompt_b)

    assert result["has_changes"] is True
    assert result["removed_lines"] == 1
    assert result["added_lines"] == 2
    assert "Never give refunds" in result["unified_diff"]
    assert "You may issue refunds up to $50" in result["unified_diff"]

    types = [chunk["type"] for chunk in result["chunks"]]
    assert "unchanged" in types
    assert "removed" in types
    assert "added" in types


def test_compute_prompt_diff_empty_inputs():
    result = compute_prompt_diff(None, "New prompt")
    assert result["has_changes"] is True
    assert result["added_lines"] == 1
    assert result["removed_lines"] == 0

    result2 = compute_prompt_diff("Old prompt", "")
    assert result2["has_changes"] is True
    assert result2["added_lines"] == 0
    assert result2["removed_lines"] == 1


def test_compute_parameter_diff():
    snapshot_a = {
        "model": "gpt-4o",
        "provider": "vapi",
        "language": "en",
        "inbound": True,
        "old_setting": "deprecated",
    }
    snapshot_b = {
        "model": "claude-3-5-sonnet",
        "provider": "vapi",
        "language": "es",
        "inbound": True,
        "new_setting": "enabled",
    }

    changes = compute_parameter_diff(snapshot_a, snapshot_b)
    change_map = {c["key"]: c for c in changes}

    assert "model" in change_map
    assert change_map["model"]["change_type"] == "modified"
    assert change_map["model"]["base_value"] == "gpt-4o"
    assert change_map["model"]["target_value"] == "claude-3-5-sonnet"

    assert "language" in change_map
    assert change_map["language"]["change_type"] == "modified"

    assert "new_setting" in change_map
    assert change_map["new_setting"]["change_type"] == "added"

    assert "old_setting" in change_map
    assert change_map["old_setting"]["change_type"] == "removed"

    # Inbound and provider were unchanged, so they should not appear in diff
    assert "inbound" not in change_map
    assert "provider" not in change_map


def test_compute_performance_delta():
    version_a = MagicMock()
    version_a.score = 7.5
    version_a.pass_rate = 75.0
    version_a.test_count = 10

    version_b = MagicMock()
    version_b.score = 8.8
    version_b.pass_rate = 88.0
    version_b.test_count = 25

    with patch("simulate.services.agent_version_diff._get_version_eval_summary", return_value=[]):
        deltas = compute_performance_delta(version_a, version_b, include_template_evals=False)

    assert deltas["score"]["base"] == 7.5
    assert deltas["score"]["target"] == 8.8
    assert deltas["score"]["delta"] == 1.3

    assert deltas["pass_rate"]["base"] == 75.0
    assert deltas["pass_rate"]["target"] == 88.0
    assert deltas["pass_rate"]["delta"] == 13.0

    assert deltas["test_count"]["base"] == 10
    assert deltas["test_count"]["target"] == 25
    assert deltas["test_count"]["delta"] == 15


def test_generate_change_summary():
    prompt_diff = {"has_changes": True, "added_lines": 3, "removed_lines": 1}
    param_diff = [
        {"key": "model", "change_type": "modified", "base_value": "gpt-4o", "target_value": "claude-3-5-sonnet"}
    ]
    perf_delta = {
        "score": {"delta": 1.2},
        "pass_rate": {"delta": 8.5},
    }

    bullets = generate_change_summary(prompt_diff, param_diff, perf_delta)

    assert any("Prompt updated (+3, -1 lines)" in b for b in bullets)
    assert any("model changed from 'gpt-4o' to 'claude-3-5-sonnet'" in b for b in bullets)
    assert any("Overall score changed by +1.2" in b for b in bullets)
    assert any("Pass rate changed by +8.5%" in b for b in bullets)


# ============================================================================
# API View Integration Tests
# ============================================================================


@pytest.fixture
def org_and_user(db):
    org = Organization.objects.create(name="Test Org")
    user = User.objects.create_user(
        email="test_diff@futureagi.com",
        password="testpassword",
        organization=org,
    )
    workspace = Workspace.objects.create(name="Default", organization=org)
    return org, user, workspace


@pytest.fixture
def agent_with_two_versions(db, org_and_user):
    org, user, workspace = org_and_user
    agent = AgentDefinition.objects.create(
        agent_name="Support Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+18005550199",
        description="Version 1 prompt: You are a support assistant.",
        organization=org,
        workspace=workspace,
        model="gpt-4o",
        provider="vapi",
    )

    v1 = agent.create_version(
        description="Version 1 prompt: You are a support assistant.",
        commit_message="Initial release",
        status=AgentVersion.StatusChoices.ARCHIVED,
    )
    v1.score = 7.0
    v1.pass_rate = 70.0
    v1.test_count = 10
    v1.save()

    # Modify agent for version 2
    agent.description = "Version 2 prompt: You are a helpful support agent. Always be polite."
    agent.model = "claude-3-5-sonnet"
    agent.save()

    v2 = agent.create_version(
        description="Version 2 prompt: You are a helpful support agent. Always be polite.",
        commit_message="Upgraded model and refined prompt",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    v2.score = 8.5
    v2.pass_rate = 85.0
    v2.test_count = 20
    v2.save()

    return agent, v1, v2


@pytest.mark.django_db
def test_agent_version_diff_api_success(org_and_user, agent_with_two_versions):
    org, user, workspace = org_and_user
    agent, v1, v2 = agent_with_two_versions

    factory = APIRequestFactory()
    url = f"/simulate/agent-definitions/{agent.id}/versions/diff/?base_version_id={v1.id}&target_version_id={v2.id}"
    request = factory.get(url)
    force_authenticate(request, user=user)
    request.organization = org

    view = AgentVersionDiffView.as_view()
    response = view(request, agent_id=agent.id)

    assert response.status_code == status.HTTP_200_OK
    data = response.data["data"]

    # Verify base and target metadata
    assert data["base_version"]["version_number"] == 1
    assert data["target_version"]["version_number"] == 2

    # Verify performance deltas
    assert data["performance_delta"]["score"]["delta"] == 1.5
    assert data["performance_delta"]["pass_rate"]["delta"] == 15.0

    # Verify prompt diff
    assert data["prompt_diff"]["has_changes"] is True
    assert len(data["prompt_diff"]["chunks"]) > 0

    # Verify summary
    assert len(data["summary"]) > 0


@pytest.mark.django_db
def test_agent_version_diff_api_missing_params(org_and_user, agent_with_two_versions):
    org, user, _ = org_and_user
    agent, v1, _ = agent_with_two_versions

    factory = APIRequestFactory()
    url = f"/simulate/agent-definitions/{agent.id}/versions/diff/?base_version_id={v1.id}"
    request = factory.get(url)
    force_authenticate(request, user=user)
    request.organization = org

    view = AgentVersionDiffView.as_view()
    response = view(request, agent_id=agent.id)

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_agent_version_diff_api_not_found(org_and_user, agent_with_two_versions):
    org, user, _ = org_and_user
    agent, v1, _ = agent_with_two_versions
    import uuid

    factory = APIRequestFactory()
    url = f"/simulate/agent-definitions/{agent.id}/versions/diff/?base_version_id={v1.id}&target_version_id={uuid.uuid4()}"
    request = factory.get(url)
    force_authenticate(request, user=user)
    request.organization = org

    view = AgentVersionDiffView.as_view()
    response = view(request, agent_id=agent.id)

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_agent_version_changelog_api_success(org_and_user, agent_with_two_versions):
    org, user, _ = org_and_user
    agent, v1, v2 = agent_with_two_versions

    factory = APIRequestFactory()
    url = f"/simulate/agent-definitions/{agent.id}/versions/changelog/"
    request = factory.get(url)
    force_authenticate(request, user=user)
    request.organization = org

    view = AgentVersionChangelogView.as_view()
    response = view(request, agent_id=agent.id)

    assert response.status_code == status.HTTP_200_OK
    changelog = response.data["data"]

    assert len(changelog) == 2
    # Newest version first
    assert changelog[0]["version_number"] == 2
    assert changelog[0]["is_active"] is True
    assert changelog[1]["version_number"] == 1
    assert changelog[1]["changes_summary"] == ["Initial release"]
