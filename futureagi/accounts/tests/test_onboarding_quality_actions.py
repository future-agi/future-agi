from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from accounts.models import OnboardingQualityAction, Organization, User, Workspace
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.quality_actions import (
    open_quality_actions_for_context,
)


@pytest.fixture
def organization():
    return Organization.objects.create(name="Quality Action Org")


@pytest.fixture
def user(organization):
    return User.objects.create_user(
        email="quality-action@example.com",
        name="Quality Action User",
        organization=organization,
    )


@pytest.fixture
def workspace(organization, user):
    return Workspace.objects.create(
        name="Quality Action Workspace",
        organization=organization,
        created_by=user,
    )


def _context(organization, workspace, primary_path="observe"):
    return SimpleNamespace(
        organization=organization,
        workspace=workspace,
        primary_path=primary_path,
    )


@pytest.mark.django_db
def test_quality_action_created_event_creates_safe_durable_action(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    due_at = now + timedelta(hours=4)

    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="daily_quality_action_created",
        source="test",
        product_path="observe",
        metadata={
            "action_id": "trace-owner",
            "label": "Assign trace owner",
            "body": "Pick an owner for the recurring trace failure.",
            "route": "https://example.com/bad",
            "fallback_route": "//example.com/bad",
            "source_type": "project",
            "source_id": "observe-1",
            "assigned_to_user_id": str(user.id),
            "due_at": due_at.isoformat(),
        },
        occurred_at=now,
    )

    action = OnboardingQualityAction.no_workspace_objects.get(
        organization=organization,
        workspace=workspace,
        product_path="observe",
        action_key="trace-owner",
    )

    assert action.status == OnboardingQualityAction.STATUS_OPEN
    assert action.created_by == user
    assert action.label == "Assign trace owner"
    assert action.body == "Pick an owner for the recurring trace failure."
    assert action.route == "/dashboard/home"
    assert action.fallback_route == "/dashboard/get-started"
    assert action.source_type == "project"
    assert action.source_id == "observe-1"
    assert action.assigned_to == user
    assert action.due_at == due_at
    assert action.last_event_at == now


@pytest.mark.django_db
def test_quality_action_open_event_preserves_prior_safe_metadata(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="daily_quality_action_created",
        source="test",
        product_path="observe",
        metadata={
            "action_id": "trace-owner",
            "label": "Assign trace owner",
            "body": "Pick an owner for the recurring trace failure.",
            "route": "/dashboard/observe/observe-1",
            "source_type": "project",
            "source_id": "observe-1",
        },
        occurred_at=now - timedelta(minutes=10),
    )

    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="daily_quality_action_opened",
        source="test",
        product_path="observe",
        metadata={"action_id": "trace-owner"},
        occurred_at=now,
    )

    action = OnboardingQualityAction.no_workspace_objects.get(
        organization=organization,
        workspace=workspace,
        product_path="observe",
        action_key="trace-owner",
    )

    assert action.status == OnboardingQualityAction.STATUS_OPEN
    assert action.label == "Assign trace owner"
    assert action.route == "/dashboard/observe/observe-1"
    assert action.source_type == "project"
    assert action.source_id == "observe-1"
    assert action.last_event_at == now

    open_actions = open_quality_actions_for_context(
        _context(organization, workspace),
        now + timedelta(minutes=1),
    )
    assert [item["id"] for item in open_actions] == ["trace-owner"]
    assert open_actions[0]["label"] == "Assign trace owner"


@pytest.mark.django_db
def test_quality_action_assigned_event_updates_owner_due_date_and_payload(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    due_at = now - timedelta(minutes=5)
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="daily_quality_action_created",
        source="test",
        product_path="observe",
        metadata={
            "action_id": "trace-owner",
            "label": "Assign trace owner",
            "route": "/dashboard/observe/observe-1",
            "source_type": "project",
            "source_id": "observe-1",
        },
        occurred_at=now - timedelta(minutes=20),
    )

    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="daily_quality_action_assigned",
        source="test",
        product_path="observe",
        metadata={
            "action_id": "trace-owner",
            "assigned_to_user_id": str(user.id),
            "due_at": due_at.isoformat(),
        },
        occurred_at=now - timedelta(minutes=10),
    )

    action = OnboardingQualityAction.no_workspace_objects.get(
        organization=organization,
        workspace=workspace,
        product_path="observe",
        action_key="trace-owner",
    )
    assert action.assigned_to == user
    assert action.assigned_at == now - timedelta(minutes=10)
    assert action.due_at == due_at

    open_actions = open_quality_actions_for_context(
        _context(organization, workspace),
        now,
    )
    assert open_actions[0]["assigned_to_user_id"] == str(user.id)
    assert open_actions[0]["assigned_to_name"] == "Quality Action User"
    assert open_actions[0]["assigned_at"] == now - timedelta(minutes=10)
    assert open_actions[0]["due_at"] == due_at
    assert open_actions[0]["is_overdue"] is True


@pytest.mark.django_db
def test_quality_action_completed_event_closes_durable_action(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="daily_quality_action_created",
        source="test",
        product_path="observe",
        metadata={
            "action_id": "trace-owner",
            "label": "Assign trace owner",
            "route": "/dashboard/observe/observe-1",
        },
        occurred_at=now - timedelta(minutes=10),
    )

    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="daily_quality_action_completed",
        source="test",
        product_path="observe",
        metadata={"action_id": "trace-owner", "resolution": "completed"},
        occurred_at=now,
    )

    action = OnboardingQualityAction.no_workspace_objects.get(
        organization=organization,
        workspace=workspace,
        product_path="observe",
        action_key="trace-owner",
    )

    assert action.status == OnboardingQualityAction.STATUS_COMPLETED
    assert action.completed_at == now
    assert (
        open_quality_actions_for_context(
            _context(organization, workspace),
            now + timedelta(minutes=1),
        )
        == []
    )
