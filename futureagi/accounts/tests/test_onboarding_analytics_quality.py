import io
import json
import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from accounts.models import (
    OnboardingActivationEvent,
    OnboardingLifecycleEvaluationLog,
)
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.analytics_quality import (
    check_onboarding_analytics_quality,
)


@pytest.mark.django_db
def test_analytics_quality_passes_for_valid_activation_events(workspace, user):
    record_event(
        user=user,
        organization=workspace.organization,
        workspace=workspace,
        event_name="trace_ingested",
        source="observe",
        product_path="observe",
        activation_stage="waiting_for_first_trace",
        metadata={"route": "/dashboard/observe"},
    )

    result = check_onboarding_analytics_quality(
        since=timezone.now() - timedelta(minutes=1),
    )

    assert result.status == "pass"
    assert result.events_checked == 1
    assert result.to_payload()["status"] == "pass"
    event = OnboardingActivationEvent.no_workspace_objects.get()
    assert event.event_name == "trace_received"


@pytest.mark.django_db
def test_analytics_quality_flags_event_drift(workspace, user):
    OnboardingActivationEvent.no_workspace_objects.create(
        organization=workspace.organization,
        workspace=workspace,
        user=user,
        event_name="trace_ingested",
        product_path="sample",
        activation_stage="unknown_stage",
        source="",
        is_sample=True,
        metadata={
            "new_goal": "unknown_goal",
            "prompt": "private",
            "recommended_action_id": "unknown_action",
        },
    )
    OnboardingActivationEvent.no_workspace_objects.create(
        organization=workspace.organization,
        workspace=workspace,
        user=user,
        event_name="first_quality_loop_completed",
        product_path="sample",
        activation_stage="activated",
        source="sample",
        is_sample=True,
    )

    result = check_onboarding_analytics_quality(
        since=timezone.now() - timedelta(minutes=1),
    )
    payload = result.to_payload()

    assert payload["status"] == "fail"
    assert payload["alias_leakage"] == 1
    assert payload["unknown_event"] == 1
    assert payload["unknown_stage"] == 1
    assert payload["unknown_goal"] == 1
    assert payload["unknown_recommendation"] == 1
    assert payload["sensitive_metadata_events"] == 1
    assert payload["sample_activation"] == 1
    assert payload["missing_source"] == 1


@pytest.mark.django_db
def test_analytics_quality_flags_lifecycle_false_send_risk(workspace, user):
    record_event(
        user=user,
        organization=workspace.organization,
        workspace=workspace,
        event_name="observe_project_created",
        source="observe",
        product_path="observe",
    )
    OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id=uuid.uuid4(),
        user=user,
        organization=workspace.organization,
        workspace=workspace,
        campaign_key="observe_connect_first",
        campaign_group="recovery",
        activation_stage="connect_observability",
        primary_path="observe",
        target_success_event="observe_project_created",
        target_url="/dashboard/observe?source=onboarding",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        evaluated_at=timezone.now(),
        registry_snapshot={"sample_policy": "real_only"},
    )
    OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id=uuid.uuid4(),
        user=user,
        organization=workspace.organization,
        workspace=workspace,
        campaign_key="broken_route",
        campaign_group="recovery",
        activation_stage="connect_observability",
        primary_path="observe",
        target_success_event="trace_received",
        target_url="",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        evaluated_at=timezone.now(),
        registry_snapshot={"sample_policy": "real_only"},
    )

    payload = check_onboarding_analytics_quality(
        since=timezone.now() - timedelta(minutes=1),
    ).to_payload()

    assert payload["status"] == "fail"
    assert payload["eligible_completed_target"] == 1
    assert payload["eligible_missing_target_url"] == 1


@pytest.mark.django_db
def test_analytics_quality_flags_lifecycle_guardrail_drift(workspace, user):
    now = timezone.now()
    for offset in (0, 1):
        OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
            run_id=uuid.uuid4(),
            user=user,
            organization=workspace.organization,
            workspace=workspace,
            campaign_key="observe_waiting_for_first_trace",
            campaign_group="recovery",
            activation_stage="waiting_for_first_trace",
            primary_path="observe",
            recommendation_id="send_first_trace",
            target_action_id="send_first_trace",
            target_success_event="trace_received",
            target_url="/dashboard/observe?source=onboarding",
            status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
            eligible_at=now - timedelta(minutes=offset + 1),
            evaluated_at=now - timedelta(minutes=offset),
            registry_snapshot={"sample_policy": "real_only"},
        )
    OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id=uuid.uuid4(),
        user=user,
        organization=workspace.organization,
        workspace=workspace,
        campaign_key="broken_candidate",
        campaign_group="recovery",
        activation_stage="unknown_stage",
        primary_path="unknown_path",
        recommendation_id="unknown_action",
        target_action_id="unknown_action",
        target_success_event="unknown_event",
        target_url="/dashboard/home",
        status=OnboardingLifecycleEvaluationLog.STATUS_ERROR,
        suppression_reason="route_unavailable",
        eligible_at=now + timedelta(minutes=1),
        evaluated_at=now,
        registry_snapshot={"sample_policy": "real_only"},
    )

    payload = check_onboarding_analytics_quality(
        since=timezone.now() - timedelta(minutes=5),
    ).to_payload()

    assert payload["status"] == "fail"
    assert payload["duplicate_lifecycle_candidate"] == 1
    assert payload["lifecycle_error"] == 1
    assert payload["negative_lifecycle_duration"] == 1
    assert payload["route_unavailable_primary"] == 1
    assert payload["unknown_path"] == 1
    assert payload["unknown_recommendation"] == 1
    assert payload["unknown_stage"] == 1
    assert payload["unknown_target_event"] == 1


@pytest.mark.django_db
def test_check_onboarding_analytics_quality_command_outputs_json(workspace, user):
    record_event(
        user=user,
        organization=workspace.organization,
        workspace=workspace,
        event_name="onboarding_home_viewed",
        source="home",
    )
    stdout = io.StringIO()

    call_command(
        "check_onboarding_analytics_quality",
        "--since",
        timezone.now().date().isoformat(),
        "--format",
        "json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["status"] == "pass"
    assert payload["events_checked"] == 1


@pytest.mark.django_db
def test_check_onboarding_analytics_quality_command_can_fail_ci(workspace, user):
    OnboardingActivationEvent.no_workspace_objects.create(
        organization=workspace.organization,
        workspace=workspace,
        user=user,
        event_name="first_quality_loop_completed",
        product_path="sample",
        activation_stage="activated",
        source="sample",
        is_sample=True,
    )

    with pytest.raises(CommandError):
        call_command(
            "check_onboarding_analytics_quality",
            "--since",
            timezone.now().date().isoformat(),
            "--fail-on-error",
            stdout=io.StringIO(),
        )
