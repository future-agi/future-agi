import json
from datetime import timedelta
from hashlib import sha256
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from accounts.models import (
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendLog,
)
from accounts.services.onboarding.lifecycle_preview_approval import (
    APPROVAL_RECORD_SCHEMA_VERSION,
    APPROVAL_RECORD_SOURCE,
)
from accounts.services.onboarding.lifecycle_registry import (
    lifecycle_campaign_by_key,
    lifecycle_campaigns,
)
from accounts.services.onboarding.lifecycle_template_context import (
    render_lifecycle_email_preview,
)
from accounts.services.onboarding.lifecycle_template_contract import (
    lifecycle_email_copy_for_campaign,
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
    email_copy = lifecycle_email_copy_for_campaign(campaign)

    assert preview["subject"] == email_copy["subject"]
    assert preview["preheader"] == email_copy["preheader"]
    assert preview["context"]["email_subject"] == email_copy["subject"]
    assert preview["context"]["preheader_text"] == email_copy["preheader"]
    assert (
        preview["template"] == f"onboarding_lifecycle/{campaign['template_key']}.html"
    )
    assert required_context_keys_for_template(campaign["template_key"]) <= set(
        preview["context"]
    )
    assert "FutureAGI setup" in preview["html"]
    assert email_copy["preheader"] in preview["html"]
    assert preview["context"]["primary_action_url"] in preview["html"]
    assert "snooze setup emails for 7 days" in preview["html"]
    assert "turn off setup emails" in preview["html"]
    assert "token=" in preview["html"]
    assert "{{" not in preview["html"]
    assert "{%" not in preview["html"]
    assert "api_token" not in preview["html"]
    assert "secret-value" not in preview["html"]
    assert "Sensitive debugging notes" not in preview["html"]
    assert preview["text"]
    assert email_copy["preheader"] in preview["text"]

    if campaign.get("requires_digest_preview"):
        assert "Review trace regression" in preview["html"]


@pytest.mark.django_db
def test_observe_waiting_template_uses_credential_ready_context(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    campaign = lifecycle_campaign_by_key("observe_waiting_for_first_trace")
    send_log = _send_log_for_campaign(campaign, organization, workspace, user, now)
    send_log.metadata = {
        **(send_log.metadata or {}),
        "observe_credentials_ready": True,
        "observe_credentials_ready_at": now.isoformat(),
    }
    send_log.save(update_fields=["metadata", "updated_at"])

    preview = render_lifecycle_email_preview(
        send_log=send_log,
        campaign=campaign,
        target_route=send_log.target_route,
        now=now,
    )

    assert preview["context"]["observe_credentials_ready"] is True
    assert "has credentials ready for the first observe trace" in preview["text"]
    assert "Paste the copied values into your SDK" in preview["text"]


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

    command_output = output.getvalue()
    assert "count=1" in command_output
    assert "manifest.json" in command_output
    html = (tmp_path / "welcome_resume_goal.html").read_text()
    text = (tmp_path / "welcome_resume_goal.txt").read_text()
    index = (tmp_path / "index.md").read_text()
    manifest = json.loads((tmp_path / "manifest.json").read_text())

    assert "FutureAGI setup" in html
    assert "Connect the first observe project" in html
    assert "snooze setup emails for 7 days" in html
    assert "Connect the first observe project" in text
    assert "welcome_resume_goal" in index
    assert (
        "| Campaign | Group | Template | Subject | Preheader | HTML | Text |" in index
    )
    assert "Create the project that will receive your first trace" in index
    assert "These previews are generated without sending email." in index
    assert set(manifest) == {
        "schema_version",
        "generated_at",
        "source",
        "count",
        "campaigns",
    }
    assert manifest["schema_version"] == (
        "onboarding-lifecycle-preview-manifest-2026-05-29.v1"
    )
    assert manifest["source"] == "lifecycle_preview_snapshot"
    assert manifest["generated_at"] == "2026-05-29T10:00:00+00:00"
    assert manifest["count"] == 1
    entry = manifest["campaigns"][0]
    assert set(entry) == {
        "campaign_key",
        "campaign_group",
        "template_key",
        "template_version",
        "primary_path",
        "activation_stage",
        "target_action_id",
        "target_success_event",
        "route_strategy",
        "subject",
        "preheader",
        "html_file",
        "text_file",
        "html_sha256",
        "text_sha256",
        "required_context_keys",
        "digest_preview_required",
        "generated_at",
    }
    assert entry["campaign_key"] == "welcome_resume_goal"
    assert entry["subject"] == "Continue with your first observe project"
    assert entry["preheader"] == (
        "Create the project that will receive your first trace "
        "and show the first quality signal."
    )
    assert entry["html_sha256"] == sha256(html.encode("utf-8")).hexdigest()
    assert entry["text_sha256"] == sha256(text.encode("utf-8")).hexdigest()
    assert entry["required_context_keys"] == sorted(
        required_context_keys_for_template("welcome_resume_goal_v1")
    )
    assert entry["digest_preview_required"] is False
    assert entry["generated_at"] == "2026-05-29T10:00:00+00:00"


def test_lifecycle_preview_approval_command_writes_review_record(tmp_path):
    manifest_output = StringIO()
    call_command(
        "generate_onboarding_lifecycle_previews",
        "--output-dir",
        str(tmp_path),
        "--campaign-key",
        "welcome_resume_goal",
        "--now",
        "2026-05-29T10:00:00Z",
        stdout=manifest_output,
    )
    output = StringIO()
    approval_path = tmp_path / "approval-record.json"

    call_command(
        "approve_onboarding_lifecycle_previews",
        "--manifest",
        str(tmp_path / "manifest.json"),
        "--output",
        str(approval_path),
        "--approved-by",
        "Lifecycle reviewer <reviewer@example.com>",
        "--approved-at",
        "2026-05-29T10:05:00Z",
        "--note",
        "Reviewed welcome copy and route target.",
        stdout=output,
    )

    command_output = output.getvalue()
    manifest_text = (tmp_path / "manifest.json").read_text()
    record_text = approval_path.read_text()
    record = json.loads(record_text)
    assert "approval_record_sha256=" in command_output
    assert "Lifecycle reviewer <reviewer@example.com>" in command_output
    assert record == {
        "schema_version": APPROVAL_RECORD_SCHEMA_VERSION,
        "source": APPROVAL_RECORD_SOURCE,
        "decision": "approved",
        "approved_by": "Lifecycle reviewer <reviewer@example.com>",
        "approved_at": "2026-05-29T10:05:00+00:00",
        "manifest_sha256": sha256(manifest_text.encode("utf-8")).hexdigest(),
        "manifest_generated_at": "2026-05-29T10:00:00+00:00",
        "campaign_count": 1,
        "campaigns": [
            {
                "campaign_key": "welcome_resume_goal",
                "html_sha256": sha256(
                    (tmp_path / "welcome_resume_goal.html").read_text().encode("utf-8")
                ).hexdigest(),
                "text_sha256": sha256(
                    (tmp_path / "welcome_resume_goal.txt").read_text().encode("utf-8")
                ).hexdigest(),
            }
        ],
        "note": "Reviewed welcome copy and route target.",
    }
    assert "FutureAGI setup" not in record_text
    assert "Connect the first observe project" not in record_text
    assert "reviewer@example.com" in record_text


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
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert "daily_quality_open_actions" in index
    assert "Review, assign, or dismiss the open item" in index
    assert manifest["count"] == len(CAMPAIGN_KEYS)
    digest_entry = next(
        campaign
        for campaign in manifest["campaigns"]
        if campaign["campaign_key"] == "daily_quality_open_actions"
    )
    assert digest_entry["digest_preview_required"] is True
    assert digest_entry["required_context_keys"] == sorted(
        required_context_keys_for_template("daily_quality_open_actions_v1")
    )
    manifest_text = json.dumps(manifest)
    assert "Review trace regression" not in manifest_text
    assert "preview-quality-action" not in manifest_text
    assert "preview-trace" not in manifest_text
    assert "Internal note intentionally omitted" not in manifest_text
    assert "api_token" not in manifest_text
    assert "redacted-preview-token" not in manifest_text
    assert "metadata" not in digest_entry
    assert "actions" not in digest_entry
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
