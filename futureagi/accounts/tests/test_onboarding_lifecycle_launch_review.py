import json
from datetime import timedelta
from hashlib import sha256
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from accounts.models import (
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendAllowlist,
)
from accounts.models.workspace import Workspace
from accounts.services.onboarding.lifecycle_launch_packets import (
    write_lifecycle_launch_packet,
)
from accounts.services.onboarding.lifecycle_preview_approval import (
    write_lifecycle_preview_approval_record,
)
from accounts.services.onboarding.lifecycle_preview_snapshots import (
    write_lifecycle_preview_snapshots,
)
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key
from accounts.services.onboarding.lifecycle_send_evidence import (
    REQUIREMENT_KEYS,
    SEND_EVIDENCE_REPORT_PASSED_STATUS,
    SEND_EVIDENCE_REPORT_SCHEMA_VERSION,
    SEND_EVIDENCE_REPORT_SOURCE,
)
from accounts.services.onboarding.lifecycle_send_reports import (
    write_lifecycle_send_dry_run_report_review_record,
)


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


def _eligible_welcome_log(user, organization, workspace):
    now = timezone.now()
    Workspace.no_workspace_objects.filter(id=workspace.id).update(
        created_at=now - timedelta(minutes=30)
    )
    campaign = lifecycle_campaign_by_key("welcome_resume_goal")
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000911",
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
        target_url="/dashboard/home?source=onboarding",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=now - timedelta(minutes=15),
        evaluated_at=now - timedelta(minutes=1),
        registry_snapshot=campaign,
    )


def _launch_artifacts(tmp_path, user, organization, workspace):
    _eligible_welcome_log(user, organization, workspace)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
        campaign_group="welcome",
    )
    preview_dir = tmp_path / "previews"
    write_lifecycle_preview_snapshots(
        output_dir=preview_dir,
        campaign_key="welcome_resume_goal",
        now=timezone.now(),
    )
    approval_manifest = preview_dir / "manifest.json"
    approval_record = preview_dir / "approval-record.json"
    write_lifecycle_preview_approval_record(
        manifest_path=approval_manifest,
        output_path=approval_record,
        approved_by="Lifecycle reviewer <reviewer@example.com>",
        approved_at=timezone.now(),
    )
    dry_run_report = tmp_path / "welcome-dry-run-report.json"
    call_command(
        "run_onboarding_welcome_email_beta",
        "--limit",
        "1",
        "--approval-manifest",
        str(approval_manifest),
        "--approval-record",
        str(approval_record),
        "--report-output",
        str(dry_run_report),
        stdout=StringIO(),
    )
    dry_run_review = tmp_path / "welcome-dry-run-report-review.json"
    write_lifecycle_send_dry_run_report_review_record(
        report_path=dry_run_report,
        output_path=dry_run_review,
        reviewed_by="Lifecycle reviewer <reviewer@example.com>",
        reviewed_at=timezone.now(),
    )
    launch_packet = tmp_path / "launch-packet.json"
    write_lifecycle_launch_packet(
        output_path=launch_packet,
        approval_manifest_path=approval_manifest,
        approval_record_path=approval_record,
        dry_run_report_path=dry_run_report,
        dry_run_report_review_record_path=dry_run_review,
        require_sendable_candidate=True,
    )
    return {
        "approval_manifest": approval_manifest,
        "approval_record": approval_record,
        "dry_run_report": dry_run_report,
        "dry_run_review": dry_run_review,
        "launch_packet": launch_packet,
    }


def _send_evidence_report(path, *, launch_packet_hash):
    requirements = dict.fromkeys(REQUIREMENT_KEYS, True)
    aggregate_evidence = dict.fromkeys(REQUIREMENT_KEYS, True)
    payload = {
        "schema_version": SEND_EVIDENCE_REPORT_SCHEMA_VERSION,
        "source": SEND_EVIDENCE_REPORT_SOURCE,
        "generated_at": "2026-05-30T11:45:00+00:00",
        "status": SEND_EVIDENCE_REPORT_PASSED_STATUS,
        "requirements": requirements,
        "missing_requirements": [],
        "aggregate_evidence": aggregate_evidence,
        "send_log_count": 1,
        "send_logs": [
            {
                "send_log_id": "00000000-0000-0000-0000-000000000001",
                "campaign_key": "welcome_resume_goal",
                "status": "completed",
                "artifact_hashes": {
                    "launch_packet": launch_packet_hash,
                    "preview_approval": "a" * 64,
                    "dry_run_report": "b" * 64,
                },
            }
        ],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_launch_review_command_writes_passed_manifest(
    organization,
    workspace,
    user,
    tmp_path,
):
    artifacts = _launch_artifacts(tmp_path, user, organization, workspace)
    launch_packet_hash = sha256(
        artifacts["launch_packet"].read_text().encode("utf-8")
    ).hexdigest()
    send_evidence = tmp_path / "send-evidence-report.json"
    _send_evidence_report(send_evidence, launch_packet_hash=launch_packet_hash)
    output_path = tmp_path / "launch-review.json"
    output = StringIO()

    call_command(
        "generate_onboarding_lifecycle_launch_review",
        "--approval-manifest",
        str(artifacts["approval_manifest"]),
        "--approval-record",
        str(artifacts["approval_record"]),
        "--dry-run-report",
        str(artifacts["dry_run_report"]),
        "--dry-run-report-review-record",
        str(artifacts["dry_run_review"]),
        "--launch-packet",
        str(artifacts["launch_packet"]),
        "--send-evidence-report",
        str(send_evidence),
        "--output",
        str(output_path),
        "--now",
        "2026-05-30T12:00:00Z",
        stdout=output,
    )

    review = json.loads(output_path.read_text())
    value = output.getvalue()
    assert f"output_path={output_path}" in value
    assert "review_sha256=" in value
    assert "status=passed" in value
    assert (
        review["schema_version"] == "onboarding-lifecycle-launch-review-2026-05-30.v1"
    )
    assert review["source"] == "onboarding_lifecycle_launch_review"
    assert review["generated_at"] == "2026-05-30T12:00:00+00:00"
    assert review["status"] == "passed"
    assert review["missing_checks"] == []
    assert review["artifacts"]["launch_packet"]["sha256"] == launch_packet_hash
    assert review["checks"]["send_evidence_references_launch_packet"] is True
    assert all(review["evidence"]["requirements"].values())
    assert all(review["evidence"]["aggregate_evidence"].values())


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_launch_review_rejects_evidence_for_different_launch_packet(
    organization,
    workspace,
    user,
    tmp_path,
):
    artifacts = _launch_artifacts(tmp_path, user, organization, workspace)
    send_evidence = tmp_path / "send-evidence-report.json"
    _send_evidence_report(send_evidence, launch_packet_hash="0" * 64)
    output_path = tmp_path / "launch-review.json"

    with pytest.raises(CommandError, match="send_evidence_references_launch_packet"):
        call_command(
            "generate_onboarding_lifecycle_launch_review",
            "--approval-manifest",
            str(artifacts["approval_manifest"]),
            "--approval-record",
            str(artifacts["approval_record"]),
            "--dry-run-report",
            str(artifacts["dry_run_report"]),
            "--dry-run-report-review-record",
            str(artifacts["dry_run_review"]),
            "--launch-packet",
            str(artifacts["launch_packet"]),
            "--send-evidence-report",
            str(send_evidence),
            "--output",
            str(output_path),
            stdout=StringIO(),
        )

    review = json.loads(output_path.read_text())
    assert review["status"] == "incomplete"
    assert review["missing_checks"] == ["send_evidence_references_launch_packet"]
