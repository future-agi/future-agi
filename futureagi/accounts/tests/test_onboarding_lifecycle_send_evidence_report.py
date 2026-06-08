import json
import uuid
from datetime import timedelta
from io import StringIO

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from accounts.models import (
    NotificationDeliveryLog,
    NotificationPreference,
    OnboardingActivationFactReceipt,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecyclePreference,
    OnboardingLifecycleSendLog,
)
from accounts.services.onboarding.activation_exporter import (
    ACTIVATION_EXPORT_SCHEMA_VERSION,
)
from accounts.services.onboarding.lifecycle_launch_packets import (
    LAUNCH_PACKET_METADATA_KEY,
)
from accounts.services.onboarding.lifecycle_preview_approval import (
    APPROVAL_METADATA_KEY,
)
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key
from accounts.services.onboarding.lifecycle_send_evidence import (
    SEND_EVIDENCE_REPORT_PASSED_STATUS,
    SEND_EVIDENCE_REPORT_SCHEMA_VERSION,
    SEND_EVIDENCE_REPORT_SOURCE,
    load_lifecycle_send_evidence_report,
)
from accounts.services.onboarding.lifecycle_send_reports import (
    DRY_RUN_REPORT_METADATA_KEY,
)


def _receipt(organization, workspace, user):
    now = timezone.now()
    return OnboardingActivationFactReceipt.no_workspace_objects.create(
        export_log_id=uuid.uuid4(),
        idempotency_key=f"{workspace.id}:evidence:{uuid.uuid4()}",
        schema_version=ACTIVATION_EXPORT_SCHEMA_VERSION,
        event_cursor=now.isoformat(),
        organization_id_value=organization.id,
        workspace_id_value=workspace.id,
        user_id_value=user.id,
        deployment_mode="cloud",
        deployment_region="us",
        plan_tier="payg",
        activation_stage="waiting_for_first_trace",
        primary_path="observe",
        is_activated=False,
        lifecycle_campaign_key="welcome_resume_goal",
        lifecycle_template_key="welcome_resume_goal_v1",
        lifecycle_status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        email_next_key="welcome_resume_goal_v1",
        email_eligible=True,
        email_suppressed=False,
        journey_config_schema_version="onboarding-activation-export-config-2026-05-30.v1",
        primary_cohort_key="observe_waiting_first_trace",
        cohort_keys=["observe_waiting_first_trace"],
        journey_cohorts=[],
        payload_hash="d" * 64,
        payload={},
        evaluated_at=now,
        metadata={"source": "activation_fact_receiver"},
    )


def _metadata(receipt=None):
    metadata = {
        APPROVAL_METADATA_KEY: {
            "approval_record_sha256": "a" * 64,
        },
        DRY_RUN_REPORT_METADATA_KEY: {
            "sha256": "b" * 64,
        },
        LAUNCH_PACKET_METADATA_KEY: {
            "sha256": "c" * 64,
            "status": "ready_for_send",
            "command": "run_onboarding_lifecycle_send",
        },
    }
    if receipt:
        metadata.update(
            {
                "source": "activation_fact_receipt",
                "receipt_id": str(receipt.id),
                "idempotency_key": receipt.idempotency_key,
                "export_log_id": str(receipt.export_log_id),
                "payload_hash": receipt.payload_hash,
                "deployment_mode": receipt.deployment_mode,
                "deployment_region": receipt.deployment_region,
                "plan_tier": receipt.plan_tier,
                "primary_cohort_key": receipt.primary_cohort_key,
                "cohort_keys": receipt.cohort_keys,
                "journey_config_schema_version": (
                    receipt.journey_config_schema_version
                ),
                "receipt_template_key": receipt.lifecycle_template_key,
            }
        )
    return metadata


def _send_log(
    user,
    organization,
    workspace,
    *,
    status=OnboardingLifecycleSendLog.STATUS_SENT,
    suppression_reason=None,
    provider_status=None,
    sent_at=None,
    clicked_at=None,
    completed_at=None,
    unsubscribed_at=None,
    metadata=None,
    source_receipt=None,
):
    now = timezone.now()
    campaign = lifecycle_campaign_by_key("welcome_resume_goal")
    evaluation = OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id=uuid.uuid4(),
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
        source_receipt=source_receipt,
    )
    return OnboardingLifecycleSendLog.no_workspace_objects.create(
        evaluation_log=evaluation,
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
        target_route="/dashboard/home?source=onboarding",
        status=status,
        suppression_reason=suppression_reason,
        provider_status=provider_status,
        queued_at=now - timedelta(minutes=6),
        sent_at=sent_at,
        clicked_at=clicked_at,
        completed_at=completed_at,
        unsubscribed_at=unsubscribed_at,
        metadata=metadata or {},
    )


def _delivery_log(send_log, *, status, channel=None, suppressed_reason=None):
    return NotificationDeliveryLog.no_workspace_objects.create(
        organization=send_log.organization,
        workspace=send_log.workspace,
        user=send_log.user,
        family=NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
        source_type="onboarding_lifecycle",
        source_id=str(send_log.id),
        channel=channel or NotificationPreference.CHANNEL_EMAIL,
        recipient_type="user",
        recipient_identifier_masked="us***@example.com",
        notification_key=send_log.campaign_key,
        stage=send_log.activation_stage,
        status=status,
        suppressed_reason=suppressed_reason,
        route_url=send_log.target_route,
        sent_at=timezone.now()
        if status == NotificationDeliveryLog.STATUS_SENT
        else None,
    )


@pytest.mark.django_db
def test_lifecycle_send_evidence_report_command_writes_passed_report(
    organization,
    workspace,
    user,
    tmp_path,
):
    receipt = _receipt(organization, workspace, user)
    now = timezone.now()
    sent_log = _send_log(
        user,
        organization,
        workspace,
        status=OnboardingLifecycleSendLog.STATUS_COMPLETED,
        provider_status="accepted",
        sent_at=now - timedelta(minutes=5),
        clicked_at=now - timedelta(minutes=4),
        completed_at=now - timedelta(minutes=3),
        unsubscribed_at=now - timedelta(minutes=2),
        metadata=_metadata(receipt),
        source_receipt=receipt,
    )
    _delivery_log(sent_log, status=NotificationDeliveryLog.STATUS_SENT)
    OnboardingLifecyclePreference.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        onboarding_enabled=False,
        unsubscribed_at=now - timedelta(minutes=2),
        snoozed_until=now + timedelta(days=7),
    )
    frequency_capped = _send_log(
        user,
        organization,
        workspace,
        status=OnboardingLifecycleSendLog.STATUS_SUPPRESSED,
        suppression_reason="frequency_capped",
    )
    _delivery_log(
        frequency_capped,
        status=NotificationDeliveryLog.STATUS_SUPPRESSED,
        suppressed_reason="frequency_capped",
    )
    completion_suppressed = _send_log(
        user,
        organization,
        workspace,
        status=OnboardingLifecycleSendLog.STATUS_SUPPRESSED,
        suppression_reason="target_success_event_completed",
    )
    report_path = tmp_path / "send-evidence-report.json"
    output = StringIO()

    call_command(
        "generate_onboarding_lifecycle_send_evidence_report",
        "--send-log-id",
        str(sent_log.id),
        "--send-log-id",
        str(frequency_capped.id),
        "--send-log-id",
        str(completion_suppressed.id),
        "--output",
        str(report_path),
        "--require-launch-packet",
        "--require-provider-accepted",
        "--require-email-delivery",
        "--require-click",
        "--require-completion",
        "--require-unsubscribe",
        "--require-snooze",
        "--require-frequency-cap",
        "--require-completion-suppression",
        "--require-receipt-backed",
        "--now",
        "2026-05-30T11:30:00Z",
        stdout=output,
    )

    report_text = report_path.read_text()
    report = json.loads(report_text)
    value = output.getvalue()
    assert f"output_path={report_path}" in value
    assert "report_sha256=" in value
    assert "status=passed" in value
    assert report["schema_version"] == SEND_EVIDENCE_REPORT_SCHEMA_VERSION
    assert report["source"] == SEND_EVIDENCE_REPORT_SOURCE
    assert report["generated_at"] == "2026-05-30T11:30:00+00:00"
    assert report["status"] == SEND_EVIDENCE_REPORT_PASSED_STATUS
    assert report["missing_requirements"] == []
    assert report["send_log_count"] == 3
    for key, enabled in report["requirements"].items():
        assert enabled is True, key
        assert report["aggregate_evidence"][key] is True, key
    sent_payload = report["send_logs"][0]
    assert sent_payload["artifact_hashes"] == {
        "preview_approval": "a" * 64,
        "dry_run_report": "b" * 64,
        "launch_packet": "c" * 64,
    }
    assert sent_payload["delivery_counts"] == {"email:sent": 1}
    assert sent_payload["receipt"] == {
        "is_receipt_backed": True,
        "source_receipt_id": str(receipt.id),
        "receipt_id": str(receipt.id),
        "payload_hash": "d" * 64,
        "deployment_mode": "cloud",
        "deployment_region": "us",
        "plan_tier": "payg",
        "primary_cohort_key": "observe_waiting_first_trace",
    }
    assert user.email not in report_text


@pytest.mark.django_db
def test_lifecycle_send_evidence_report_requires_receipt_backed_provenance(
    organization,
    workspace,
    user,
    tmp_path,
):
    send_log = _send_log(
        user,
        organization,
        workspace,
        status=OnboardingLifecycleSendLog.STATUS_SENT,
        provider_status="accepted",
        sent_at=timezone.now(),
        metadata=_metadata(),
    )
    report_path = tmp_path / "missing-receipt-evidence-report.json"

    with pytest.raises(CommandError, match="receipt_backed"):
        call_command(
            "generate_onboarding_lifecycle_send_evidence_report",
            "--send-log-id",
            str(send_log.id),
            "--output",
            str(report_path),
            "--require-receipt-backed",
            stdout=StringIO(),
        )

    report = json.loads(report_path.read_text())
    assert report["status"] == "incomplete"
    assert report["missing_requirements"] == ["receipt_backed"]
    assert report["aggregate_evidence"]["receipt_backed"] is False
    assert report["send_logs"][0]["receipt"]["is_receipt_backed"] is False


@pytest.mark.django_db
def test_lifecycle_send_evidence_report_writes_incomplete_report_before_error(
    organization,
    workspace,
    user,
    tmp_path,
):
    send_log = _send_log(
        user,
        organization,
        workspace,
        status=OnboardingLifecycleSendLog.STATUS_SENT,
        provider_status="accepted",
        sent_at=timezone.now(),
    )
    report_path = tmp_path / "missing-evidence-report.json"

    with pytest.raises(CommandError, match="launch_packet"):
        call_command(
            "generate_onboarding_lifecycle_send_evidence_report",
            "--send-log-id",
            str(send_log.id),
            "--output",
            str(report_path),
            "--require-launch-packet",
            stdout=StringIO(),
        )

    report = json.loads(report_path.read_text())
    assert report["status"] == "incomplete"
    assert report["missing_requirements"] == ["launch_packet"]
    assert report["aggregate_evidence"]["launch_packet"] is False


@pytest.mark.django_db
def test_lifecycle_send_evidence_loader_rejects_inconsistent_status(
    organization,
    workspace,
    user,
    tmp_path,
):
    send_log = _send_log(
        user,
        organization,
        workspace,
        status=OnboardingLifecycleSendLog.STATUS_SENT,
        provider_status="accepted",
        sent_at=timezone.now(),
        metadata=_metadata(),
    )
    _delivery_log(send_log, status=NotificationDeliveryLog.STATUS_SENT)
    report_path = tmp_path / "tampered-evidence-report.json"
    call_command(
        "generate_onboarding_lifecycle_send_evidence_report",
        "--send-log-id",
        str(send_log.id),
        "--output",
        str(report_path),
        "--require-launch-packet",
        "--require-provider-accepted",
        "--require-email-delivery",
        stdout=StringIO(),
    )
    report = json.loads(report_path.read_text())
    report["aggregate_evidence"]["click"] = False
    report["requirements"]["click"] = True
    report["missing_requirements"] = []
    report["status"] = "passed"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(
        ImproperlyConfigured,
        match="missing_requirements does not match",
    ):
        load_lifecycle_send_evidence_report(report_path)
