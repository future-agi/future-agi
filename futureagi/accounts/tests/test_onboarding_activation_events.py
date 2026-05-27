from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts.models import OnboardingActivationEvent, Organization, User, Workspace
from accounts.services.onboarding.activation_events import (
    build_idempotency_key,
    events_for_workspace,
    first_quality_loop_completed,
    has_event,
    latest_event,
    record_event,
)


@pytest.fixture
def organization():
    return Organization.objects.create(name="Activation Org")


@pytest.fixture
def user(organization):
    return User.objects.create_user(
        email="activation@example.com",
        name="Activation User",
        organization=organization,
    )


@pytest.fixture
def workspace(organization, user):
    return Workspace.objects.create(
        name="Activation Workspace",
        organization=organization,
        created_by=user,
    )


@pytest.fixture
def other_workspace(organization, user):
    return Workspace.objects.create(
        name="Other Activation Workspace",
        organization=organization,
        created_by=user,
    )


@pytest.mark.django_db
def test_valid_event_records_workspace_scoped_row(organization, workspace, user):
    event = record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_goal_selected",
        source="goal_picker",
        product_path="observe",
        activation_stage="connect_observability",
        metadata={"action_id": "create_observe_project"},
        idempotency_key="goal:selected",
    )

    assert event.organization == organization
    assert event.workspace == workspace
    assert event.user == user
    assert event.event_name == "onboarding_goal_selected"
    assert event.product_path == "observe"
    assert event.activation_stage == "connect_observability"
    assert event.source == "goal_picker"
    assert event.metadata == {"action_id": "create_observe_project"}
    assert event.occurred_at is not None


@pytest.mark.django_db
def test_event_defaults_timestamp_when_omitted(organization, workspace, user):
    before = timezone.now() - timedelta(seconds=1)
    event = record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_home_viewed",
        source="home",
    )
    after = timezone.now() + timedelta(seconds=1)

    assert before <= event.occurred_at <= after


@pytest.mark.django_db
def test_unknown_event_rejected(organization, workspace, user):
    with pytest.raises(ValidationError):
        record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name="unknown_event",
            source="home",
        )


@pytest.mark.django_db
def test_unknown_product_path_rejected(organization, workspace, user):
    with pytest.raises(ValidationError):
        record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name="onboarding_home_viewed",
            source="home",
            product_path="unknown_path",
        )


@pytest.mark.django_db
def test_unknown_stage_rejected(organization, workspace, user):
    with pytest.raises(ValidationError):
        record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name="onboarding_home_viewed",
            source="home",
            activation_stage="unknown_stage",
        )


@pytest.mark.django_db
def test_workspace_org_mismatch_rejected(workspace, user):
    other_org = Organization.objects.create(name="Other Org")

    with pytest.raises(ValidationError):
        record_event(
            user=user,
            organization=other_org,
            workspace=workspace,
            event_name="onboarding_home_viewed",
            source="home",
        )


@pytest.mark.django_db
def test_duplicate_idempotency_key_returns_existing_event(
    organization, workspace, user
):
    first = record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_goal_selected",
        source="goal_picker",
        product_path="observe",
        idempotency_key=build_idempotency_key(
            ["goal", workspace.id, user.id, "observe"]
        ),
    )
    second = record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_goal_selected",
        source="goal_picker",
        product_path="observe",
        idempotency_key=build_idempotency_key(
            ["goal", workspace.id, user.id, "observe"]
        ),
    )

    assert second.id == first.id
    assert OnboardingActivationEvent.no_workspace_objects.count() == 1


@pytest.mark.django_db
def test_repeated_events_without_idempotency_key_can_create_rows(
    organization,
    workspace,
    user,
):
    for _index in range(2):
        record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name="onboarding_home_viewed",
            source="home",
        )

    assert OnboardingActivationEvent.no_workspace_objects.count() == 2


@pytest.mark.django_db
def test_sample_event_does_not_satisfy_real_event_query(organization, workspace, user):
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        source="trace_detail",
        product_path="observe",
        is_sample=True,
    )

    assert (
        has_event(
            organization=organization,
            workspace=workspace,
            event_name="trace_reviewed",
            is_sample=False,
        )
        is False
    )
    assert (
        has_event(
            organization=organization,
            workspace=workspace,
            event_name="trace_reviewed",
            is_sample=True,
        )
        is True
    )


@pytest.mark.django_db
def test_real_event_found(organization, workspace, user):
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="trace_reviewed",
        source="trace_detail",
        product_path="observe",
    )

    assert (
        has_event(
            organization=organization,
            workspace=workspace,
            event_name="trace_reviewed",
        )
        is True
    )


@pytest.mark.django_db
def test_latest_event_orders_by_occurred_at(organization, workspace, user):
    older = timezone.now() - timedelta(days=1)
    newer = timezone.now()
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_home_viewed",
        source="home",
        occurred_at=older,
    )
    expected = record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_goal_selected",
        source="goal_picker",
        occurred_at=newer,
    )

    assert latest_event(organization=organization, workspace=workspace) == expected


@pytest.mark.django_db
def test_workspace_isolation(organization, workspace, other_workspace, user):
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="onboarding_home_viewed",
        source="home",
    )

    assert (
        events_for_workspace(
            organization=organization,
            workspace=other_workspace,
        )
        == []
    )


@pytest.mark.django_db
def test_metadata_sensitive_key_rejected(organization, workspace, user):
    with pytest.raises(ValidationError):
        record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name="onboarding_home_viewed",
            source="home",
            metadata={"nested": {"api_key": "hidden"}},
        )

    assert OnboardingActivationEvent.no_workspace_objects.count() == 0


@pytest.mark.django_db
def test_metadata_size_bounded(organization, workspace, user):
    with pytest.raises(ValidationError):
        record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name="onboarding_home_viewed",
            source="home",
            metadata={"route": "x" * 9000},
        )


@pytest.mark.django_db
def test_metadata_must_be_json_safe(organization, workspace, user):
    with pytest.raises(ValidationError):
        record_event(
            user=user,
            organization=organization,
            workspace=workspace,
            event_name="onboarding_home_viewed",
            source="home",
            metadata={"workspace": workspace},
        )


@pytest.mark.django_db
def test_first_quality_loop_ignores_sample(organization, workspace, user):
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="first_quality_loop_completed",
        source="sample_project",
        product_path="observe",
        is_sample=True,
    )

    assert (
        first_quality_loop_completed(
            organization=organization,
            workspace=workspace,
            product_path="observe",
        )
        is False
    )

    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="first_quality_loop_completed",
        source="activation_resolver",
        product_path="observe",
    )

    assert (
        first_quality_loop_completed(
            organization=organization,
            workspace=workspace,
            product_path="observe",
        )
        is True
    )
