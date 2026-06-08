from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from accounts.models import OnboardingLifecycleEvaluationLog
from accounts.models.workspace import Workspace
from accounts.services.onboarding.lifecycle_jobs import (
    run_onboarding_lifecycle_dry_run,
)

FEATURE_FLAGS = {
    "onboarding_activation_state_api": True,
    "onboarding_goal_picker": True,
    "onboarding_path_cards": True,
    "onboarding_lifecycle_email_dry_run": True,
    "onboarding_email_welcome_enabled": True,
    "onboarding_email_first_action_recovery_enabled": True,
    "onboarding_email_first_signal_enabled": True,
    "onboarding_email_next_loop_enabled": True,
    "onboarding_email_sample_bridge_enabled": True,
    "onboarding_email_daily_digest_enabled": True,
}


def _set_workspace_created_at(workspace, value):
    Workspace.no_workspace_objects.filter(id=workspace.id).update(created_at=value)
    workspace.refresh_from_db()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=FEATURE_FLAGS)
def test_lifecycle_dry_run_writes_evaluation_log(workspace):
    now = timezone.now()
    _set_workspace_created_at(workspace, now - timedelta(minutes=20))

    result = run_onboarding_lifecycle_dry_run(limit=10, now=now)

    assert result.evaluated == 1
    assert result.written == 1
    assert result.status_counts["eligible"] == 1
    log = OnboardingLifecycleEvaluationLog.no_workspace_objects.get()
    assert log.campaign_key == "welcome_resume_goal"
    assert log.status == OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE
    assert log.metadata["send_enabled"] is False


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=FEATURE_FLAGS)
def test_lifecycle_dry_run_no_write_only_returns_summary(workspace):
    now = timezone.now()
    _set_workspace_created_at(workspace, now - timedelta(minutes=20))

    result = run_onboarding_lifecycle_dry_run(limit=10, now=now, write=False)

    assert result.evaluated == 1
    assert result.written == 0
    assert result.status_counts["eligible"] == 1
    assert not OnboardingLifecycleEvaluationLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=FEATURE_FLAGS)
def test_lifecycle_dry_run_campaign_filter_can_skip_non_matching_stage(workspace):
    now = timezone.now()
    _set_workspace_created_at(workspace, now - timedelta(minutes=20))

    result = run_onboarding_lifecycle_dry_run(
        limit=10,
        now=now,
        campaign_key="prompt_create_first",
    )

    assert result.evaluated == 1
    assert result.written == 1
    log = OnboardingLifecycleEvaluationLog.no_workspace_objects.get()
    assert log.campaign_key is None
    assert log.status == OnboardingLifecycleEvaluationLog.STATUS_SKIPPED
    assert log.suppression_reason == "no_matching_campaign"
