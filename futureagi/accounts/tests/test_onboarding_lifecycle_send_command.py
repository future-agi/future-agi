from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from accounts.models import (
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendAllowlist,
    OnboardingLifecycleSendLog,
)
from accounts.models.workspace import Workspace
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key


def _flags(**overrides):
    flags = {
        "onboarding_activation_state_api": True,
        "onboarding_goal_picker": True,
        "onboarding_path_cards": True,
        "onboarding_sample_project": False,
        "onboarding_daily_quality_home": False,
        "onboarding_lifecycle_email_dry_run": True,
        "onboarding_email_welcome_enabled": True,
        "onboarding_email_first_action_recovery_enabled": True,
        "onboarding_email_first_signal_enabled": True,
        "onboarding_email_next_loop_enabled": True,
        "onboarding_email_sample_bridge_enabled": False,
        "onboarding_email_daily_digest_enabled": False,
        "onboarding_lifecycle_send_enabled": True,
    }
    flags.update(overrides)
    return flags


def _eligible_log(user, organization, workspace):
    now = timezone.now()
    Workspace.no_workspace_objects.filter(id=workspace.id).update(
        created_at=now - timedelta(minutes=30)
    )
    campaign = lifecycle_campaign_by_key("welcome_choose_goal")
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000314",
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        activation_stage="choose_goal",
        target_success_event=campaign["target_success_event"],
        target_url="/dashboard/home?onboarding=choose-goal",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=now - timedelta(minutes=15),
        evaluated_at=now - timedelta(minutes=1),
        registry_snapshot=campaign,
    )


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_dry_run_writes_no_send_logs(organization, workspace, user):
    _eligible_log(user, organization, workspace)
    output = StringIO()

    call_command(
        "run_onboarding_lifecycle_send",
        "--cohort",
        "internal",
        "--limit",
        "10",
        "--dry-run",
        stdout=output,
    )

    assert "evaluated=1" in output.getvalue()
    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_respects_limit_and_sends_allowlisted(
    organization,
    workspace,
    user,
):
    _eligible_log(user, organization, workspace)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
    )
    output = StringIO()

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            stdout=output,
        )

    assert "sent=1" in output.getvalue()
    assert OnboardingLifecycleSendLog.no_workspace_objects.filter(
        status=OnboardingLifecycleSendLog.STATUS_SENT
    ).exists()
