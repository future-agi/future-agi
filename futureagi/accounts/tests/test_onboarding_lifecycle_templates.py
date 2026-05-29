from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from accounts.models import (
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendLog,
)
from accounts.services.onboarding.lifecycle_registry import (
    lifecycle_campaign_by_key,
    lifecycle_campaigns,
)
from accounts.services.onboarding.lifecycle_template_context import (
    render_lifecycle_email_preview,
)
from accounts.services.onboarding.lifecycle_template_contract import (
    required_context_keys_for_template,
)

CAMPAIGN_KEYS = tuple(campaign["campaign_key"] for campaign in lifecycle_campaigns())


def _digest_preview(workspace, now):
    return {
        "kind": "daily_quality_open_actions",
        "campaign_key": "daily_quality_open_actions",
        "template_key": "daily_quality_open_actions_v1",
        "generated_at": now.isoformat(),
        "workspace_id": str(workspace.id),
        "action_count": 1,
        "omitted_count": 0,
        "actions": [
            {
                "action_id": "trace-action-1",
                "label": "Review trace regression",
                "route": "/dashboard/home?mode=daily-quality",
                "fallback_route": "/dashboard/home",
                "source_type": "trace",
                "source_id": "trace-123",
                "primary_path": "observe",
                "status": "open",
                "age_minutes": 30,
                "last_event_at": (now - timedelta(minutes=30)).isoformat(),
                "is_overdue": False,
                "body": "Sensitive debugging notes must not leak.",
                "metadata": {"api_token": "secret-value"},
            }
        ],
    }


def _send_log_for_campaign(campaign, organization, workspace, user, now):
    target_route = "/dashboard/home?source=lifecycle-template-test"
    metadata = {"source": "template_test"}
    if campaign.get("requires_digest_preview"):
        metadata["digest_preview"] = _digest_preview(workspace, now)
    evaluation_log = OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id=f"00000000-0000-0000-0000-{len(campaign['campaign_key']):012d}",
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        activation_stage=campaign["entry_stages"][0],
        primary_path=campaign["primary_path"],
        recommendation_id=campaign["target_action_id"],
        target_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
        target_url=target_route,
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=now - timedelta(minutes=15),
        evaluated_at=now - timedelta(minutes=1),
        activation_state_snapshot={
            "stage": campaign["entry_stages"][0],
            "primary_path": campaign["primary_path"],
            "recommended_action_id": campaign["target_action_id"],
        },
        registry_snapshot=campaign,
        metadata=metadata,
    )
    return OnboardingLifecycleSendLog.no_workspace_objects.create(
        evaluation_log=evaluation_log,
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        primary_path=campaign["primary_path"],
        activation_stage=campaign["entry_stages"][0],
        recommended_action_id=campaign["target_action_id"],
        target_success_event=campaign["target_success_event"],
        target_route=target_route,
        status=OnboardingLifecycleSendLog.STATUS_QUEUED,
        queued_at=now,
        metadata=metadata,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("campaign_key", CAMPAIGN_KEYS)
def test_lifecycle_email_template_renders_for_campaign(
    campaign_key,
    organization,
    workspace,
    user,
):
    now = timezone.now()
    campaign = lifecycle_campaign_by_key(campaign_key)
    send_log = _send_log_for_campaign(campaign, organization, workspace, user, now)

    preview = render_lifecycle_email_preview(
        send_log=send_log,
        campaign=campaign,
        target_route=send_log.target_route,
        now=now,
    )

    assert preview["subject"]
    assert (
        preview["template"] == f"onboarding_lifecycle/{campaign['template_key']}.html"
    )
    assert required_context_keys_for_template(campaign["template_key"]) <= set(
        preview["context"]
    )
    assert "FutureAGI onboarding" in preview["html"]
    assert preview["context"]["primary_action_url"] in preview["html"]
    assert "snooze onboarding emails for 7 days" in preview["html"]
    assert "turn off onboarding lifecycle emails" in preview["html"]
    assert "token=" in preview["html"]
    assert "{{" not in preview["html"]
    assert "{%" not in preview["html"]
    assert "api_token" not in preview["html"]
    assert "secret-value" not in preview["html"]
    assert "Sensitive debugging notes" not in preview["html"]
    assert preview["text"]

    if campaign.get("requires_digest_preview"):
        assert "Review trace regression" in preview["html"]


def test_lifecycle_preview_command_writes_no_send_snapshot(tmp_path):
    output = StringIO()

    call_command(
        "generate_onboarding_lifecycle_previews",
        "--output-dir",
        str(tmp_path),
        "--campaign-key",
        "welcome_resume_goal",
        "--now",
        "2026-05-29T10:00:00Z",
        stdout=output,
    )

    assert "count=1" in output.getvalue()
    html = (tmp_path / "welcome_resume_goal.html").read_text()
    text = (tmp_path / "welcome_resume_goal.txt").read_text()
    index = (tmp_path / "index.md").read_text()

    assert "FutureAGI onboarding" in html
    assert "Connect the first observe project" in html
    assert "snooze onboarding emails for 7 days" in html
    assert "Connect the first observe project" in text
    assert "welcome_resume_goal" in index
    assert "These previews are generated without sending email." in index


def test_lifecycle_preview_command_writes_all_campaign_snapshots(tmp_path):
    output = StringIO()

    call_command(
        "generate_onboarding_lifecycle_previews",
        "--output-dir",
        str(tmp_path),
        "--now",
        "2026-05-29T10:00:00Z",
        stdout=output,
    )

    assert f"count={len(CAMPAIGN_KEYS)}" in output.getvalue()
    index = (tmp_path / "index.md").read_text()
    assert "daily_quality_open_actions" in index
    assert (tmp_path / "daily_quality_open_actions.html").is_file()
    digest_html = (tmp_path / "daily_quality_open_actions.html").read_text()
    assert "Review trace regression" in digest_html
    assert "Internal note intentionally omitted" not in digest_html
    assert "api_token" not in digest_html
    assert "redacted-preview-token" not in digest_html


def test_lifecycle_preview_command_rejects_unknown_campaign(tmp_path):
    output = StringIO()

    with pytest.raises(CommandError, match="Unknown onboarding lifecycle campaign"):
        call_command(
            "generate_onboarding_lifecycle_previews",
            "--output-dir",
            str(tmp_path),
            "--campaign-key",
            "missing_campaign",
            stdout=output,
        )


def test_lifecycle_preview_command_requires_force_for_existing_files(tmp_path):
    output = StringIO()
    call_command(
        "generate_onboarding_lifecycle_previews",
        "--output-dir",
        str(tmp_path),
        "--campaign-key",
        "welcome_resume_goal",
        stdout=output,
    )

    with pytest.raises(CommandError, match="Use --force to overwrite previews"):
        call_command(
            "generate_onboarding_lifecycle_previews",
            "--output-dir",
            str(tmp_path),
            "--campaign-key",
            "welcome_resume_goal",
            stdout=output,
        )
