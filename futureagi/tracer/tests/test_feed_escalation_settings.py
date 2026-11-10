"""Workspace policy API, authorization, and optimistic concurrency contracts."""

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.levels import Level
from tracer.models.feed_escalation import FeedEscalationPolicy
from tracer.services.feed_escalation_settings import PolicyConflict, update_policy
from tracer.views.feed.escalation_settings import FeedEscalationSettingsView

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def request_settings(user, workspace, monkeypatch, *, method="get", body=None):
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *a, **kw: None)
    request = getattr(APIRequestFactory(), method)(
        "/tracer/feed/escalation-settings/", body or {}, format="json"
    )
    request.organization = workspace.organization
    request.workspace = workspace
    force_authenticate(request, user=user)
    return FeedEscalationSettingsView.as_view()(request)


def test_get_returns_versioned_defaults_scope_and_permissions(
    user, workspace, monkeypatch
):
    response = request_settings(user, workspace, monkeypatch)
    assert response.status_code == 200
    result = response.data["result"]
    assert result["values"]["min_findings"] == 20
    assert str(result["values"]["entry_multiplier"]) == "3.0"
    assert result["defaults"]["recovery_min"] == 5
    assert result["workspace_id"] == str(workspace.id)
    assert result["can_edit"] is True
    assert result["window_minutes"] == 60
    assert result["baseline_hours"] == 24
    assert result["cadence_minutes"] == 5
    assert result["processing_allowance_minutes"] == 10


def test_save_partial_merge_and_noop_revision(user, workspace, monkeypatch):
    response = request_settings(user, workspace, monkeypatch)
    revision = response.data["result"]["revision"]
    response = request_settings(
        user,
        workspace,
        monkeypatch,
        method="patch",
        body={"revision": revision, "min_findings": 25},
    )
    assert response.status_code == 200
    assert response.data["result"]["revision"] == revision + 1
    stored = FeedEscalationPolicy.no_workspace_objects.get(workspace=workspace)
    assert stored.min_findings == 25
    assert stored.entry_delta == 10
    assert stored.updated_by_id == user.id
    response = request_settings(
        user,
        workspace,
        monkeypatch,
        method="patch",
        body={"revision": revision + 1, "min_findings": 25},
    )
    assert response.status_code == 200
    assert response.data["result"]["revision"] == revision + 1


def test_stale_save_returns_conflict_without_partial_write(
    user, workspace, monkeypatch
):
    revision = request_settings(user, workspace, monkeypatch).data["result"]["revision"]
    update_policy(
        workspace=workspace,
        actor=user,
        expected_revision=revision,
        changes={"min_findings": 30},
    )
    response = request_settings(
        user,
        workspace,
        monkeypatch,
        method="patch",
        body={"revision": revision, "entry_delta": 99},
    )
    assert response.status_code == 409
    stored = FeedEscalationPolicy.no_workspace_objects.get(workspace=workspace)
    assert stored.min_findings == 30
    assert stored.entry_delta == 10
    assert stored.revision == revision + 1


@pytest.mark.parametrize(
    "body",
    [
        {"min_findings": True},
        {"min_findings": 2},
        {"entry_multiplier": "NaN"},
        {"entry_multiplier": "3.01"},
        {"recovery_delta": 10},
        {"entry_confirmation_minutes": 7},
        {"workspace_id": "another-workspace"},
        {"project_id": "another-project"},
        {"unknown": 20},
    ],
)
def test_invalid_body_has_no_write(user, workspace, monkeypatch, body):
    revision = request_settings(user, workspace, monkeypatch).data["result"]["revision"]
    response = request_settings(
        user,
        workspace,
        monkeypatch,
        method="patch",
        body={"revision": revision, **body},
    )
    assert response.status_code == 400
    policy = FeedEscalationPolicy.no_workspace_objects.filter(
        workspace=workspace
    ).first()
    assert policy is None or policy.revision == revision


def test_missing_revision_is_rejected(user, workspace, monkeypatch):
    response = request_settings(
        user, workspace, monkeypatch, method="patch", body={"min_findings": 25}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("level", [Level.MEMBER, Level.VIEWER])
def test_viewers_can_read_but_cannot_save(user, workspace, monkeypatch, level):
    OrganizationMembership.no_workspace_objects.filter(user=user).update(level=level)
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=level)
    response = request_settings(user, workspace, monkeypatch)
    assert response.status_code == 200
    assert response.data["result"]["can_edit"] is False
    response = request_settings(
        user,
        workspace,
        monkeypatch,
        method="patch",
        body={"revision": 0, "min_findings": 25},
    )
    assert response.status_code == 403


def test_workspace_admin_can_save(user, workspace, monkeypatch):
    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        level=Level.MEMBER
    )
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(level=Level.WORKSPACE_ADMIN)
    response = request_settings(
        user,
        workspace,
        monkeypatch,
        method="patch",
        body={"revision": 0, "min_findings": 25},
    )
    assert response.status_code == 200


def test_other_workspace_policy_is_unchanged(user, workspace, monkeypatch):
    other = Workspace.no_workspace_objects.create(
        name="Other", organization=workspace.organization, created_by=user
    )
    before = request_settings(user, other, monkeypatch).data["result"]
    request_settings(
        user,
        workspace,
        monkeypatch,
        method="patch",
        body={"revision": 0, "min_findings": 25},
    )
    after = request_settings(user, other, monkeypatch).data["result"]
    assert after["values"] == before["values"]
    assert after["revision"] == before["revision"]


def test_cross_organization_scope_is_rejected(
    user, workspace, organization, monkeypatch
):
    from accounts.models.organization import Organization

    foreign = Organization.objects.create(name="Foreign")
    workspace.organization = foreign
    response = request_settings(user, workspace, monkeypatch)
    assert response.status_code == 403


def test_first_write_conflict_preserves_winner(user, workspace):
    FeedEscalationPolicy.no_workspace_objects.filter(workspace=workspace).delete()
    update_policy(
        workspace=workspace,
        actor=user,
        expected_revision=0,
        changes={"min_findings": 25},
    )
    with pytest.raises(PolicyConflict):
        update_policy(
            workspace=workspace,
            actor=user,
            expected_revision=0,
            changes={"min_findings": 30},
        )
    assert (
        FeedEscalationPolicy.no_workspace_objects.get(workspace=workspace).min_findings
        == 25
    )
