import json
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from accounts.models import (
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendAllowlist,
    OnboardingLifecycleSendLog,
    User,
)
from accounts.models.workspace import Workspace
from accounts.services.onboarding.lifecycle_launch_packets import (
    LAUNCH_PACKET_METADATA_KEY,
    LAUNCH_PACKET_READY_STATUS,
    LAUNCH_PACKET_SCHEMA_VERSION,
    LAUNCH_PACKET_SOURCE,
    write_lifecycle_launch_packet,
)
from accounts.services.onboarding.lifecycle_preview_approval import (
    APPROVAL_METADATA_KEY,
    write_lifecycle_preview_approval_record,
)
from accounts.services.onboarding.lifecycle_preview_snapshots import (
    write_lifecycle_preview_snapshots,
)
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key
from accounts.services.onboarding.lifecycle_send_reports import (
    DRY_RUN_REPORT_METADATA_KEY,
    DRY_RUN_REPORT_SCHEMA_VERSION,
    DRY_RUN_REPORT_SOURCE,
    write_lifecycle_send_dry_run_report_review_record,
)


@pytest.fixture(autouse=True)
def _cloud_lifecycle_delivery_enabled():
    with patch(
        "accounts.services.onboarding.lifecycle_sender._cloud_lifecycle_delivery_enabled",
        return_value=True,
    ):
        yield


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
    campaign = lifecycle_campaign_by_key("welcome_resume_goal")
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id="00000000-0000-0000-0000-000000000314",
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        activation_stage=campaign["entry_stages"][0],
        target_success_event=campaign["target_success_event"],
        target_url="/dashboard/home?source=test",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=now - timedelta(minutes=15),
        evaluated_at=now - timedelta(minutes=1),
        registry_snapshot=campaign,
    )


def _eligible_campaign_log(user, organization, workspace, campaign_key):
    now = timezone.now()
    Workspace.no_workspace_objects.filter(id=workspace.id).update(
        created_at=now - timedelta(minutes=30)
    )
    campaign = lifecycle_campaign_by_key(campaign_key)
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id=f"00000000-0000-0000-0000-000000000{len(campaign_key):03d}",
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign["campaign_group"],
        template_key=campaign["template_key"],
        template_version=campaign["template_version"],
        activation_stage=campaign["entry_stages"][0],
        target_success_event=campaign["target_success_event"],
        target_url="/dashboard/home?source=test",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=now - timedelta(minutes=15),
        evaluated_at=now - timedelta(minutes=1),
        registry_snapshot=campaign,
    )


def _approval_manifest_path(tmp_path, campaign_key="welcome_resume_goal"):
    output_dir = tmp_path / (campaign_key or "all")
    write_lifecycle_preview_snapshots(
        output_dir=output_dir,
        campaign_key=campaign_key,
        now=timezone.now(),
    )
    return output_dir / "manifest.json"


def _approval_paths(
    tmp_path,
    *,
    campaign_key="welcome_resume_goal",
    approved_campaign_keys=None,
):
    manifest_path = _approval_manifest_path(tmp_path, campaign_key)
    record_path = manifest_path.parent / "approval-record.json"
    write_lifecycle_preview_approval_record(
        manifest_path=manifest_path,
        output_path=record_path,
        approved_by="Lifecycle reviewer <reviewer@example.com>",
        approved_at=timezone.now(),
        campaign_keys=approved_campaign_keys,
    )
    return manifest_path, record_path


def _reviewed_lifecycle_send_report_paths(
    tmp_path,
    *,
    approval_manifest,
    approval_record,
    cohort="internal",
    limit=1,
    campaign_group=None,
    user_id=None,
    workspace_id=None,
):
    report_path = tmp_path / "lifecycle-send-dry-run-report.json"
    review_record_path = tmp_path / "lifecycle-send-dry-run-report-review.json"
    args = [
        "--cohort",
        cohort,
        "--limit",
        str(limit),
        "--dry-run",
        "--approval-manifest",
        str(approval_manifest),
        "--approval-record",
        str(approval_record),
        "--report-output",
        str(report_path),
    ]
    if campaign_group:
        args.extend(["--campaign-family", campaign_group])
    if user_id:
        args.extend(["--user-id", str(user_id)])
    if workspace_id:
        args.extend(["--workspace-id", str(workspace_id)])
    call_command("run_onboarding_lifecycle_send", *args, stdout=StringIO())
    write_lifecycle_send_dry_run_report_review_record(
        report_path=report_path,
        output_path=review_record_path,
        reviewed_by="Lifecycle reviewer <reviewer@example.com>",
        reviewed_at=timezone.now(),
    )
    return report_path, review_record_path


def _reviewed_welcome_send_report_paths(
    tmp_path,
    *,
    approval_manifest,
    approval_record,
    cohort="beta",
    limit=1,
    user_id=None,
    workspace_id=None,
):
    report_path = tmp_path / "welcome-send-dry-run-report.json"
    review_record_path = tmp_path / "welcome-send-dry-run-report-review.json"
    args = [
        "--cohort",
        cohort,
        "--limit",
        str(limit),
        "--approval-manifest",
        str(approval_manifest),
        "--approval-record",
        str(approval_record),
        "--report-output",
        str(report_path),
    ]
    if user_id:
        args.extend(["--user-id", str(user_id)])
    if workspace_id:
        args.extend(["--workspace-id", str(workspace_id)])
    call_command("run_onboarding_welcome_email_beta", *args, stdout=StringIO())
    write_lifecycle_send_dry_run_report_review_record(
        report_path=report_path,
        output_path=review_record_path,
        reviewed_by="Lifecycle reviewer <reviewer@example.com>",
        reviewed_at=timezone.now(),
    )
    return report_path, review_record_path


def _launch_packet_path(
    tmp_path,
    *,
    approval_manifest,
    approval_record,
    dry_run_report,
    dry_run_report_review,
):
    packet_path = tmp_path / f"{dry_run_report.stem}-launch-packet.json"
    write_lifecycle_launch_packet(
        output_path=packet_path,
        approval_manifest_path=approval_manifest,
        approval_record_path=approval_record,
        dry_run_report_path=dry_run_report,
        dry_run_report_review_record_path=dry_run_report_review,
        require_sendable_candidate=True,
    )
    return packet_path


def _batch_payload(**overrides):
    payload = {
        "approval_manifest_sha256": None,
        "approval_record_sha256": None,
        "dry_run_report_sha256": None,
        "dry_run_report_review_record_sha256": None,
        "launch_packet_sha256": None,
        "run_id": "00000000-0000-0000-0000-000000000999",
        "evaluated": 0,
        "sent": 0,
        "suppressed": 0,
        "failed": 0,
        "skipped": 0,
        "status_counts": {},
        "suppression_counts": {},
    }
    payload.update(overrides)
    return payload


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


def test_send_command_rejects_include_receipt_backed_for_dry_run():
    with pytest.raises(
        CommandError,
        match="--include-receipt-backed is only supported for sends",
    ):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--dry-run",
            "--include-receipt-backed",
            stdout=StringIO(),
        )


def test_send_command_passes_include_receipt_backed_to_batch():
    preview_approval = SimpleNamespace(
        manifest_sha256="a" * 64,
        approval_record_sha256="b" * 64,
    )
    dry_run_report_review = SimpleNamespace(
        report=SimpleNamespace(sha256="c" * 64),
        review_record_sha256="d" * 64,
    )
    launch_packet = SimpleNamespace(sha256="e" * 64)
    batch_result = SimpleNamespace(
        to_payload=lambda: _batch_payload(
            approval_manifest_sha256=preview_approval.manifest_sha256,
            approval_record_sha256=preview_approval.approval_record_sha256,
            dry_run_report_sha256=dry_run_report_review.report.sha256,
            dry_run_report_review_record_sha256=(
                dry_run_report_review.review_record_sha256
            ),
            launch_packet_sha256=launch_packet.sha256,
        )
    )

    with (
        patch(
            "accounts.management.commands.run_onboarding_lifecycle_send.load_lifecycle_preview_approval",
            return_value=preview_approval,
        ),
        patch(
            "accounts.management.commands.run_onboarding_lifecycle_send.load_lifecycle_send_dry_run_report_review",
            return_value=dry_run_report_review,
        ),
        patch(
            "accounts.management.commands.run_onboarding_lifecycle_send.load_lifecycle_launch_packet",
            return_value=launch_packet,
        ),
        patch(
            "accounts.management.commands.run_onboarding_lifecycle_send.send_limited_onboarding_lifecycle_batch",
            return_value=batch_result,
        ) as send_batch,
    ):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            "/tmp/approval-manifest.json",
            "--approval-record",
            "/tmp/approval-record.json",
            "--dry-run-report",
            "/tmp/lifecycle-send-dry-run-report.json",
            "--dry-run-report-review-record",
            "/tmp/lifecycle-send-dry-run-report-review.json",
            "--launch-packet",
            "/tmp/lifecycle-launch-packet.json",
            "--include-receipt-backed",
            stdout=StringIO(),
        )

    assert send_batch.call_args.kwargs["include_receipt_backed"] is True


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_dry_run_writes_review_report(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    report_path = tmp_path / "send-dry-run-report.json"
    output = StringIO()

    call_command(
        "run_onboarding_lifecycle_send",
        "--cohort",
        "internal",
        "--limit",
        "10",
        "--dry-run",
        "--report-output",
        str(report_path),
        stdout=output,
    )

    report_text = report_path.read_text()
    report = json.loads(report_text)
    assert f"report_output={report_path}" in output.getvalue()
    assert report["schema_version"] == DRY_RUN_REPORT_SCHEMA_VERSION
    assert report["source"] == DRY_RUN_REPORT_SOURCE
    assert report["command"] == "run_onboarding_lifecycle_send"
    assert report["parameters"] == {
        "cohort": "internal",
        "limit": 10,
        "campaign_group": None,
        "user_id": None,
        "workspace_id": None,
        "require_campaign_group_allowlist": False,
    }
    assert report["approval"] == {
        "manifest_sha256": None,
        "record_sha256": None,
    }
    assert report["summary"]["evaluated"] == 1
    assert report["summary"]["status_counts"] == {"would_suppress": 1}
    assert report["summary"]["suppression_counts"] == {"not_in_send_cohort": 1}
    assert len(report["candidates"]) == 1
    candidate = report["candidates"][0]
    assert candidate["campaign_key"] == "welcome_resume_goal"
    assert candidate["status"] == "would_suppress"
    assert candidate["suppression_reason"] == "not_in_send_cohort"
    assert candidate["approval_status"] == "not_supplied"
    assert candidate["target_success_event"] == "observe_project_created"
    assert candidate["target_route"].startswith("/dashboard/")
    assert str(user.id) in report_text
    assert user.email not in report_text
    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_approve_send_dry_run_report_command_writes_review_record(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    report_path = tmp_path / "send-dry-run-report.json"
    review_path = tmp_path / "send-dry-run-report-review.json"
    call_command(
        "run_onboarding_lifecycle_send",
        "--cohort",
        "internal",
        "--limit",
        "10",
        "--dry-run",
        "--report-output",
        str(report_path),
        stdout=StringIO(),
    )
    output = StringIO()

    call_command(
        "approve_onboarding_lifecycle_send_dry_run_report",
        "--report",
        str(report_path),
        "--output",
        str(review_path),
        "--reviewed-by",
        "Lifecycle reviewer <reviewer@example.com>",
        "--reviewed-at",
        "2026-05-29T10:05:00Z",
        stdout=output,
    )

    record_text = review_path.read_text()
    record = json.loads(record_text)
    value = output.getvalue()
    assert f"output_path={review_path}" in value
    assert "report_sha256=" in value
    assert "review_record_sha256=" in value
    assert record["decision"] == "approved"
    assert record["reviewed_by"] == "Lifecycle reviewer <reviewer@example.com>"
    assert record["command"] == "run_onboarding_lifecycle_send"
    assert record["candidate_count"] == 1
    assert user.email not in record_text


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_lifecycle_launch_packet_command_writes_reviewable_packet(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
        campaign_group="welcome",
    )
    dry_run_report, dry_run_report_review = _reviewed_welcome_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    packet_path = tmp_path / "welcome-launch-packet.json"
    output = StringIO()

    call_command(
        "generate_onboarding_lifecycle_launch_packet",
        "--approval-manifest",
        str(approval_manifest),
        "--approval-record",
        str(approval_record),
        "--dry-run-report",
        str(dry_run_report),
        "--dry-run-report-review-record",
        str(dry_run_report_review),
        "--output",
        str(packet_path),
        "--require-sendable-candidate",
        "--now",
        "2026-05-30T10:15:00Z",
        stdout=output,
    )

    packet_text = packet_path.read_text()
    packet = json.loads(packet_text)
    value = output.getvalue()
    assert f"output_path={packet_path}" in value
    assert "packet_sha256=" in value
    assert "sendable_candidate_count=1" in value
    assert packet["schema_version"] == LAUNCH_PACKET_SCHEMA_VERSION
    assert packet["source"] == LAUNCH_PACKET_SOURCE
    assert packet["generated_at"] == "2026-05-30T10:15:00+00:00"
    assert packet["status"] == LAUNCH_PACKET_READY_STATUS
    assert packet["command"]["name"] == "run_onboarding_welcome_email_beta"
    assert packet["command"]["argv"] == [
        "run_onboarding_welcome_email_beta",
        "--send",
        "--cohort",
        "beta",
        "--limit",
        "1",
        "--approval-manifest",
        str(approval_manifest),
        "--approval-record",
        str(approval_record),
        "--dry-run-report",
        str(dry_run_report),
        "--dry-run-report-review-record",
        str(dry_run_report_review),
    ]
    assert packet["send_parameters"] == {
        "cohort": "beta",
        "limit": 1,
        "campaign_group": "welcome",
        "user_id": None,
        "workspace_id": None,
        "require_campaign_group_allowlist": True,
    }
    assert (
        packet["preview"]["manifest_sha256"]
        == packet["dry_run"]["approval_manifest_sha256"]
    )
    assert (
        packet["preview"]["approval_record_sha256"]
        == packet["dry_run"]["approval_record_sha256"]
    )
    assert len(packet["preview"]["manifest_sha256"]) == 64
    assert len(packet["preview"]["approval_record_sha256"]) == 64
    assert packet["preview"]["approved_campaign_keys"] == ["welcome_resume_goal"]
    assert packet["dry_run"]["sendable_candidate_count"] == 1
    assert packet["dry_run"]["status_counts"] == {"would_send": 1}
    assert packet["checks"] == {
        "preview_approval_record_present": True,
        "dry_run_review_record_present": True,
        "dry_run_approval_matches_preview": True,
        "sendable_candidate_required": True,
    }
    assert user.email not in packet_text


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_lifecycle_launch_packet_command_supports_lifecycle_send(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
    )
    dry_run_report, dry_run_report_review = _reviewed_lifecycle_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
        campaign_group="welcome",
    )
    packet_path = tmp_path / "lifecycle-launch-packet.json"

    call_command(
        "generate_onboarding_lifecycle_launch_packet",
        "--approval-manifest",
        str(approval_manifest),
        "--approval-record",
        str(approval_record),
        "--dry-run-report",
        str(dry_run_report),
        "--dry-run-report-review-record",
        str(dry_run_report_review),
        "--output",
        str(packet_path),
        "--require-sendable-candidate",
        stdout=StringIO(),
    )

    packet = json.loads(packet_path.read_text())
    assert packet["status"] == LAUNCH_PACKET_READY_STATUS
    assert packet["command"]["name"] == "run_onboarding_lifecycle_send"
    assert packet["command"]["argv"] == [
        "run_onboarding_lifecycle_send",
        "--cohort",
        "internal",
        "--limit",
        "1",
        "--campaign-family",
        "welcome",
        "--approval-manifest",
        str(approval_manifest),
        "--approval-record",
        str(approval_record),
        "--dry-run-report",
        str(dry_run_report),
        "--dry-run-report-review-record",
        str(dry_run_report_review),
    ]
    assert packet["send_parameters"] == {
        "cohort": "internal",
        "limit": 1,
        "campaign_group": "welcome",
        "user_id": None,
        "workspace_id": None,
        "require_campaign_group_allowlist": False,
    }


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_lifecycle_launch_packet_rejects_mismatched_preview_approval(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
        campaign_group="welcome",
    )
    dry_run_report, dry_run_report_review = _reviewed_welcome_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    other_manifest, other_record = _approval_paths(
        tmp_path,
        campaign_key="prompt_create_first",
    )
    packet_path = tmp_path / "mismatched-launch-packet.json"

    with pytest.raises(CommandError, match="approval manifest does not match"):
        call_command(
            "generate_onboarding_lifecycle_launch_packet",
            "--approval-manifest",
            str(other_manifest),
            "--approval-record",
            str(other_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            "--output",
            str(packet_path),
            stdout=StringIO(),
        )

    assert not packet_path.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_lifecycle_launch_packet_can_require_sendable_candidates(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    approval_manifest, approval_record = _approval_paths(tmp_path)
    dry_run_report, dry_run_report_review = _reviewed_welcome_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    packet_path = tmp_path / "empty-launch-packet.json"

    with pytest.raises(CommandError, match="no sendable candidates"):
        call_command(
            "generate_onboarding_lifecycle_launch_packet",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            "--output",
            str(packet_path),
            "--require-sendable-candidate",
            stdout=StringIO(),
        )

    assert not packet_path.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_respects_limit_and_sends_allowlisted(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
    )
    dry_run_report, dry_run_report_review = _reviewed_lifecycle_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    launch_packet = _launch_packet_path(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
        dry_run_report=dry_run_report,
        dry_run_report_review=dry_run_report_review,
    )
    output = StringIO()

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            "--launch-packet",
            str(launch_packet),
            stdout=output,
        )

    value = output.getvalue()
    assert "approval_manifest_sha256=" in value
    assert "approval_record_sha256=" in value
    assert "dry_run_report_sha256=" in value
    assert "dry_run_report_review_record_sha256=" in value
    assert "launch_packet_sha256=" in value
    assert "sent=1" in value
    send_log = OnboardingLifecycleSendLog.no_workspace_objects.get(
        status=OnboardingLifecycleSendLog.STATUS_SENT
    )
    assert APPROVAL_METADATA_KEY in send_log.metadata
    assert send_log.metadata[APPROVAL_METADATA_KEY]["campaign_key"] == (
        "welcome_resume_goal"
    )
    assert len(send_log.metadata[APPROVAL_METADATA_KEY]["manifest_sha256"]) == 64
    assert len(send_log.metadata[APPROVAL_METADATA_KEY]["approval_record_sha256"]) == 64
    assert (
        send_log.metadata[APPROVAL_METADATA_KEY]["approved_by"]
        == "Lifecycle reviewer <reviewer@example.com>"
    )
    assert DRY_RUN_REPORT_METADATA_KEY in send_log.metadata
    assert len(send_log.metadata[DRY_RUN_REPORT_METADATA_KEY]["sha256"]) == 64
    assert (
        len(send_log.metadata[DRY_RUN_REPORT_METADATA_KEY]["review_record_sha256"])
        == 64
    )
    assert send_log.metadata[LAUNCH_PACKET_METADATA_KEY]["path"] == str(launch_packet)


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_accepts_launch_packet_and_stamps_metadata(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
    )
    dry_run_report, dry_run_report_review = _reviewed_lifecycle_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    launch_packet = _launch_packet_path(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
        dry_run_report=dry_run_report,
        dry_run_report_review=dry_run_report_review,
    )
    output = StringIO()

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            "--launch-packet",
            str(launch_packet),
            stdout=output,
        )

    value = output.getvalue()
    assert "launch_packet_sha256=" in value
    assert "sent=1" in value
    send_log = OnboardingLifecycleSendLog.no_workspace_objects.get(
        status=OnboardingLifecycleSendLog.STATUS_SENT
    )
    packet_metadata = send_log.metadata[LAUNCH_PACKET_METADATA_KEY]
    assert packet_metadata["path"] == str(launch_packet)
    assert len(packet_metadata["sha256"]) == 64
    assert packet_metadata["status"] == LAUNCH_PACKET_READY_STATUS
    assert packet_metadata["command"] == "run_onboarding_lifecycle_send"
    assert packet_metadata["sendable_candidate_count"] == 1


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_rejects_mismatched_launch_packet(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
    )
    dry_run_report, dry_run_report_review = _reviewed_lifecycle_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    launch_packet = _launch_packet_path(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
        dry_run_report=dry_run_report,
        dry_run_report_review=dry_run_report_review,
    )
    packet = json.loads(launch_packet.read_text())
    packet["send_parameters"]["cohort"] = "beta"
    launch_packet.write_text(json.dumps(packet), encoding="utf-8")

    with pytest.raises(CommandError, match="send_parameters.cohort"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            "--launch-packet",
            str(launch_packet),
            stdout=StringIO(),
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_requires_approval_manifest_for_real_send(
    organization,
    workspace,
    user,
):
    _eligible_log(user, organization, workspace)
    output = StringIO()

    with pytest.raises(CommandError, match="--approval-manifest is required"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            stdout=output,
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_requires_approval_record_for_real_send(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest = _approval_manifest_path(tmp_path)
    output = StringIO()

    with pytest.raises(CommandError, match="--approval-record is required"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            stdout=output,
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_requires_dry_run_report_for_real_send(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    output = StringIO()

    with pytest.raises(CommandError, match="--dry-run-report is required"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            stdout=output,
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_requires_dry_run_report_review_for_real_send(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
    )
    dry_run_report, _dry_run_report_review = _reviewed_lifecycle_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    output = StringIO()

    with pytest.raises(
        CommandError,
        match="--dry-run-report-review-record is required",
    ):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            stdout=output,
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


def test_lifecycle_send_command_requires_launch_packet_before_report_load(
    monkeypatch,
):
    from accounts.management.commands import (
        run_onboarding_lifecycle_send as lifecycle_send_command,
    )

    monkeypatch.setattr(
        lifecycle_send_command,
        "load_lifecycle_preview_approval",
        lambda *args, **kwargs: SimpleNamespace(
            manifest_sha256="a" * 64,
            approval_record_sha256="b" * 64,
        ),
    )
    monkeypatch.setattr(
        lifecycle_send_command,
        "load_lifecycle_send_dry_run_report_review",
        lambda **kwargs: pytest.fail("dry-run report should not load"),
    )

    with pytest.raises(CommandError, match="--launch-packet is required"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            "/tmp/manifest.json",
            "--approval-record",
            "/tmp/approval-record.json",
            "--dry-run-report",
            "/tmp/dry-run.json",
            "--dry-run-report-review-record",
            "/tmp/dry-run-review.json",
            stdout=StringIO(),
        )


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_requires_launch_packet_for_real_send(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
    )
    dry_run_report, dry_run_report_review = _reviewed_lifecycle_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )

    with pytest.raises(CommandError, match="--launch-packet is required"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            stdout=StringIO(),
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_rejects_report_output_for_real_send(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    report_path = tmp_path / "send-report.json"
    output = StringIO()

    with pytest.raises(CommandError, match="--report-output requires --dry-run"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--report-output",
            str(report_path),
            stdout=output,
        )

    assert not report_path.exists()
    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_rejects_stale_approval_manifest(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    manifest = json.loads(approval_manifest.read_text())
    manifest["campaigns"][0]["subject"] = "Stale subject"
    approval_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    output = StringIO()

    with pytest.raises(CommandError, match="does not match current preview"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            stdout=output,
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_rejects_stale_approval_record(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    record = json.loads(approval_record.read_text())
    record["manifest_sha256"] = "0" * 64
    approval_record.write_text(json.dumps(record), encoding="utf-8")
    output = StringIO()

    with pytest.raises(CommandError, match="manifest_sha256 does not match"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            stdout=output,
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_rejects_mismatched_dry_run_report(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
    )
    dry_run_report, dry_run_report_review = _reviewed_lifecycle_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    launch_packet = _launch_packet_path(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
        dry_run_report=dry_run_report,
        dry_run_report_review=dry_run_report_review,
    )
    report = json.loads(dry_run_report.read_text())
    report["parameters"]["cohort"] = "beta"
    dry_run_report.write_text(json.dumps(report), encoding="utf-8")
    output = StringIO()

    with pytest.raises(CommandError, match="parameters.cohort does not match"):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            "--launch-packet",
            str(launch_packet),
            stdout=output,
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_launch_packet_rejects_campaign_missing_from_approval_record(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest = _approval_manifest_path(tmp_path, None)
    approval_record = approval_manifest.parent / "approval-record.json"
    write_lifecycle_preview_approval_record(
        manifest_path=approval_manifest,
        output_path=approval_record,
        approved_by="Lifecycle reviewer <reviewer@example.com>",
        approved_at=timezone.now(),
        campaign_keys=["prompt_create_first"],
    )
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
    )
    dry_run_report, dry_run_report_review = _reviewed_lifecycle_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    packet_path = tmp_path / "missing-approval-launch-packet.json"

    with pytest.raises(CommandError, match="dry-run report has no sendable candidates"):
        call_command(
            "generate_onboarding_lifecycle_launch_packet",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            "--output",
            str(packet_path),
            "--require-sendable-candidate",
            stdout=StringIO(),
        )

    assert not packet_path.exists()
    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_send_command_suppresses_real_send_when_not_cloud(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_log(user, organization, workspace)
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
    )
    dry_run_report, dry_run_report_review = _reviewed_lifecycle_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    launch_packet = _launch_packet_path(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
        dry_run_report=dry_run_report,
        dry_run_report_review=dry_run_report_review,
    )
    output = StringIO()

    with (
        patch(
            "accounts.services.onboarding.lifecycle_sender._cloud_lifecycle_delivery_enabled",
            return_value=False,
        ),
        patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper,
    ):
        call_command(
            "run_onboarding_lifecycle_send",
            "--cohort",
            "internal",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            "--launch-packet",
            str(launch_packet),
            stdout=output,
        )

    value = output.getvalue()
    assert "sent=0" in value
    assert "suppressed=1" in value
    assert "cloud_deployment_required" in value
    helper.assert_not_called()
    assert OnboardingLifecycleSendLog.no_workspace_objects.filter(
        status=OnboardingLifecycleSendLog.STATUS_SUPPRESSED,
        suppression_reason="cloud_deployment_required",
    ).exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_welcome_email_beta_defaults_to_dry_run_and_welcome_group(
    organization,
    workspace,
    user,
):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    _eligible_campaign_log(user, organization, workspace, "prompt_create_first")
    output = StringIO()

    call_command(
        "run_onboarding_welcome_email_beta",
        "--limit",
        "10",
        stdout=output,
    )

    value = output.getvalue()
    assert "mode=dry_run" in value
    assert "campaign_group=welcome" in value
    assert "cohort=beta" in value
    assert "evaluated=1" in value
    assert "sent=0" in value
    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_welcome_email_beta_dry_run_writes_review_report(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    _eligible_campaign_log(user, organization, workspace, "prompt_create_first")
    report_path = tmp_path / "welcome-beta-report.json"
    output = StringIO()

    call_command(
        "run_onboarding_welcome_email_beta",
        "--limit",
        "10",
        "--report-output",
        str(report_path),
        stdout=output,
    )

    report_text = report_path.read_text()
    report = json.loads(report_text)
    assert f"report_output={report_path}" in output.getvalue()
    assert report["command"] == "run_onboarding_welcome_email_beta"
    assert report["parameters"] == {
        "cohort": "beta",
        "limit": 10,
        "campaign_group": "welcome",
        "user_id": None,
        "workspace_id": None,
        "require_campaign_group_allowlist": True,
    }
    assert report["summary"]["evaluated"] == 1
    assert len(report["candidates"]) == 1
    assert report["candidates"][0]["campaign_group"] == "welcome"
    assert report["candidates"][0]["campaign_key"] == "welcome_resume_goal"
    assert "prompt_create_first" not in report_text
    assert user.email not in report_text
    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_welcome_email_beta_send_requires_dry_run_report(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    approval_manifest, approval_record = _approval_paths(tmp_path)
    output = StringIO()

    with pytest.raises(CommandError, match="--dry-run-report is required"):
        call_command(
            "run_onboarding_welcome_email_beta",
            "--send",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            stdout=output,
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_welcome_email_beta_send_requires_dry_run_report_review(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
        campaign_group="welcome",
    )
    dry_run_report, _dry_run_report_review = _reviewed_welcome_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    output = StringIO()

    with pytest.raises(
        CommandError,
        match="--dry-run-report-review-record is required",
    ):
        call_command(
            "run_onboarding_welcome_email_beta",
            "--send",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            stdout=output,
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


def test_welcome_email_beta_requires_launch_packet_before_report_load(monkeypatch):
    from accounts.management.commands import (
        run_onboarding_welcome_email_beta as welcome_command,
    )

    monkeypatch.setattr(
        welcome_command,
        "load_lifecycle_preview_approval",
        lambda *args, **kwargs: SimpleNamespace(
            manifest_sha256="a" * 64,
            approval_record_sha256="b" * 64,
        ),
    )
    monkeypatch.setattr(
        welcome_command,
        "load_lifecycle_send_dry_run_report_review",
        lambda **kwargs: pytest.fail("dry-run report should not load"),
    )

    with pytest.raises(CommandError, match="--launch-packet is required"):
        call_command(
            "run_onboarding_welcome_email_beta",
            "--send",
            "--limit",
            "1",
            "--approval-manifest",
            "/tmp/manifest.json",
            "--approval-record",
            "/tmp/approval-record.json",
            "--dry-run-report",
            "/tmp/dry-run.json",
            "--dry-run-report-review-record",
            "/tmp/dry-run-review.json",
            stdout=StringIO(),
        )


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_welcome_email_beta_send_requires_launch_packet(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
        campaign_group="welcome",
    )
    dry_run_report, dry_run_report_review = _reviewed_welcome_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )

    with pytest.raises(CommandError, match="--launch-packet is required"):
        call_command(
            "run_onboarding_welcome_email_beta",
            "--send",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            stdout=StringIO(),
        )

    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_welcome_email_beta_send_requires_explicit_flag_and_allowlist(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
        campaign_group="welcome",
    )
    dry_run_report, dry_run_report_review = _reviewed_welcome_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    launch_packet = _launch_packet_path(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
        dry_run_report=dry_run_report,
        dry_run_report_review=dry_run_report_review,
    )
    output = StringIO()

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper"):
        call_command(
            "run_onboarding_welcome_email_beta",
            "--send",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            "--launch-packet",
            str(launch_packet),
            stdout=output,
        )

    value = output.getvalue()
    assert "mode=send" in value
    assert "cohort=beta" in value
    assert "dry_run_report_sha256=" in value
    assert "dry_run_report_review_record_sha256=" in value
    assert "launch_packet_sha256=" in value
    assert "sent=1" in value
    send_log = OnboardingLifecycleSendLog.no_workspace_objects.get(
        campaign_group="welcome",
        campaign_key="welcome_resume_goal",
        status=OnboardingLifecycleSendLog.STATUS_SENT,
    )
    assert send_log.metadata["cohort"] == "beta"


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_welcome_email_beta_send_accepts_launch_packet(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    approval_manifest, approval_record = _approval_paths(tmp_path)
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
        campaign_group="welcome",
    )
    dry_run_report, dry_run_report_review = _reviewed_welcome_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    launch_packet = _launch_packet_path(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
        dry_run_report=dry_run_report,
        dry_run_report_review=dry_run_report_review,
    )
    output = StringIO()

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper"):
        call_command(
            "run_onboarding_welcome_email_beta",
            "--send",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            "--launch-packet",
            str(launch_packet),
            stdout=output,
        )

    value = output.getvalue()
    assert "launch_packet_sha256=" in value
    assert "sent=1" in value
    send_log = OnboardingLifecycleSendLog.no_workspace_objects.get(
        campaign_group="welcome",
        campaign_key="welcome_resume_goal",
        status=OnboardingLifecycleSendLog.STATUS_SENT,
    )
    assert send_log.metadata[LAUNCH_PACKET_METADATA_KEY]["path"] == str(launch_packet)
    assert send_log.metadata[LAUNCH_PACKET_METADATA_KEY]["command"] == (
        "run_onboarding_welcome_email_beta"
    )


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_welcome_email_beta_requires_welcome_specific_allowlist(
    organization,
    workspace,
    user,
    tmp_path,
):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    approval_manifest, approval_record = _approval_paths(tmp_path)
    allowlist = OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
        campaign_group="welcome",
    )
    dry_run_report, dry_run_report_review = _reviewed_welcome_send_report_paths(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
    )
    launch_packet = _launch_packet_path(
        tmp_path,
        approval_manifest=approval_manifest,
        approval_record=approval_record,
        dry_run_report=dry_run_report,
        dry_run_report_review=dry_run_report_review,
    )
    allowlist.delete()
    OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
    )
    output = StringIO()

    with patch("accounts.services.onboarding.lifecycle_sender.email_helper"):
        call_command(
            "run_onboarding_welcome_email_beta",
            "--send",
            "--limit",
            "1",
            "--approval-manifest",
            str(approval_manifest),
            "--approval-record",
            str(approval_record),
            "--dry-run-report",
            str(dry_run_report),
            "--dry-run-report-review-record",
            str(dry_run_report_review),
            "--launch-packet",
            str(launch_packet),
            stdout=output,
        )

    value = output.getvalue()
    assert "mode=send" in value
    assert "sent=0" in value
    assert "suppressed=1" in value
    assert "not_in_send_cohort" in value
    assert OnboardingLifecycleSendLog.no_workspace_objects.filter(
        campaign_group="welcome",
        status=OnboardingLifecycleSendLog.STATUS_SUPPRESSED,
        suppression_reason="not_in_send_cohort",
    ).exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_welcome_email_beta_filters_by_user_id(organization, workspace, user):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    other_user = User.objects.create_user(
        email="welcome-filter-other@example.com",
        name="Welcome Filter Other",
        organization=organization,
    )
    _eligible_campaign_log(
        other_user,
        organization,
        workspace,
        "welcome_resume_goal",
    )
    output = StringIO()

    call_command(
        "run_onboarding_welcome_email_beta",
        "--user-id",
        str(user.id),
        "--limit",
        "10",
        stdout=output,
    )

    value = output.getvalue()
    assert "mode=dry_run" in value
    assert "campaign_group=welcome" in value
    assert "evaluated=1" in value
    assert "sent=0" in value
    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_flags())
def test_welcome_email_beta_filters_by_workspace_id(organization, workspace, user):
    _eligible_campaign_log(user, organization, workspace, "welcome_resume_goal")
    other_workspace = Workspace.no_workspace_objects.create(
        name="Welcome Filter Other Workspace",
        organization=organization,
        created_by=user,
    )
    _eligible_campaign_log(
        user,
        organization,
        other_workspace,
        "welcome_resume_goal",
    )
    output = StringIO()

    call_command(
        "run_onboarding_welcome_email_beta",
        "--workspace-id",
        str(workspace.id),
        "--limit",
        "10",
        stdout=output,
    )

    value = output.getvalue()
    assert "mode=dry_run" in value
    assert "campaign_group=welcome" in value
    assert "evaluated=1" in value
    assert "sent=0" in value
    assert not OnboardingLifecycleSendLog.no_workspace_objects.exists()


@pytest.mark.django_db
def test_welcome_email_beta_rejects_unbounded_limit():
    output = StringIO()

    with pytest.raises(CommandError, match="--limit must be 100 or lower"):
        call_command(
            "run_onboarding_welcome_email_beta",
            "--limit",
            "101",
            stdout=output,
        )


def test_welcome_email_beta_rejects_invalid_now():
    output = StringIO()

    with pytest.raises(CommandError, match="--now must be an ISO datetime"):
        call_command(
            "run_onboarding_welcome_email_beta",
            "--limit",
            "1",
            "--now",
            "not-a-date",
            stdout=output,
        )
