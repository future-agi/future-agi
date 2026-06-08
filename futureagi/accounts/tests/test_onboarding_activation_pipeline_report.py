import io
import json
import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from accounts.models import (
    NotificationDeliveryLog,
    NotificationPreference,
    OnboardingActivationFactReceipt,
    OnboardingActivationFactReceiptRejection,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendLog,
    OnboardingPaidCloudActivationExportLog,
)
from accounts.services.onboarding.activation_exporter import (
    ACTIVATION_EXPORT_SCHEMA_VERSION,
)
from accounts.services.onboarding.activation_pipeline_report import (
    activation_pipeline_report,
)


def _export_log(organization, workspace, user, *, status, suppression_reason=None):
    return OnboardingPaidCloudActivationExportLog.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        deployment_mode="cloud",
        run_id=uuid.uuid4(),
        region="us",
        plan_tier="payg",
        schema_version=ACTIVATION_EXPORT_SCHEMA_VERSION,
        event_cursor=timezone.now().isoformat(),
        idempotency_key=f"pipeline-report:{status}:{uuid.uuid4()}",
        status=status,
        suppression_reason=suppression_reason,
        fact_payload={
            "schema_version": ACTIVATION_EXPORT_SCHEMA_VERSION,
            "workspace": {"id": str(workspace.id)},
        },
        evaluated_at=timezone.now(),
        metadata={"source": "pipeline_report_test"},
    )


def _receipt(organization, workspace, user, **overrides):
    now = timezone.now()
    fields = {
        "export_log_id": uuid.uuid4(),
        "idempotency_key": f"pipeline-report:receipt:{uuid.uuid4()}",
        "schema_version": ACTIVATION_EXPORT_SCHEMA_VERSION,
        "event_cursor": now.isoformat(),
        "organization_id_value": organization.id,
        "workspace_id_value": workspace.id,
        "user_id_value": user.id,
        "deployment_mode": "cloud",
        "deployment_region": "us",
        "plan_tier": "payg",
        "activation_stage": "waiting_for_first_trace",
        "primary_path": "observe",
        "is_activated": False,
        "lifecycle_campaign_key": "observe_waiting_for_first_trace",
        "lifecycle_template_key": "observe_waiting_for_first_trace_v1",
        "lifecycle_status": OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        "email_next_key": "observe_waiting_for_first_trace_v1",
        "email_eligible": True,
        "email_suppressed": False,
        "journey_config_schema_version": (
            "onboarding-activation-export-config-2026-05-30.v1"
        ),
        "primary_cohort_key": "observe_waiting_first_trace",
        "cohort_keys": ["observe_waiting_first_trace"],
        "journey_cohorts": [],
        "payload_hash": "a" * 64,
        "payload": {"fact": {"activation": {"primary_path": "observe"}}},
        "evaluated_at": now,
        "received_at": now,
        "metadata": {"source": "pipeline_report_test"},
    }
    fields.update(overrides)
    return OnboardingActivationFactReceipt.no_workspace_objects.create(**fields)


def _evaluation_log(
    organization,
    workspace,
    user,
    *,
    status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
    source_receipt=None,
):
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id=uuid.uuid4(),
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key="observe_waiting_for_first_trace",
        campaign_group="recovery",
        template_key="observe_waiting_for_first_trace_v1",
        template_version="2026-05-30.1",
        activation_stage="waiting_for_first_trace",
        primary_path="observe",
        recommendation_id="send_first_trace",
        target_action_id="send_first_trace",
        target_success_event="trace_received",
        target_url="/dashboard/observe?source=onboarding",
        status=status,
        suppression_reason=(
            "activation_state_error"
            if status == OnboardingLifecycleEvaluationLog.STATUS_ERROR
            else None
        ),
        eligible_at=timezone.now(),
        evaluated_at=timezone.now(),
        activation_state_snapshot={"stage": "waiting_for_first_trace"},
        registry_snapshot={"sample_policy": "real_only"},
        metadata={"source": "pipeline_report_test"},
        source_receipt=source_receipt,
    )


def _send_log(organization, workspace, user, evaluation_log, *, status):
    return OnboardingLifecycleSendLog.no_workspace_objects.create(
        evaluation_log=evaluation_log,
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key="observe_waiting_for_first_trace",
        campaign_group="recovery",
        template_key="observe_waiting_for_first_trace_v1",
        template_version="2026-05-30.1",
        primary_path="observe",
        activation_stage="waiting_for_first_trace",
        recommended_action_id="send_first_trace",
        target_success_event="trace_received",
        target_route="/dashboard/observe?source=onboarding",
        status=status,
        sent_at=timezone.now()
        if status == OnboardingLifecycleSendLog.STATUS_SENT
        else None,
        failure_reason="provider error"
        if status == OnboardingLifecycleSendLog.STATUS_FAILED
        else None,
        metadata={"source": "pipeline_report_test"},
    )


def _notification_log(organization, workspace, user, *, status, channel="email"):
    return NotificationDeliveryLog.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        user=user,
        family=NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
        source_type="onboarding_lifecycle",
        source_id=str(uuid.uuid4()),
        channel=channel,
        notification_key="observe_waiting_for_first_trace",
        idempotency_key=f"pipeline-report:notification:{uuid.uuid4()}",
        stage="waiting_for_first_trace",
        severity="info",
        status=status,
        route_url="/dashboard/observe?source=onboarding",
        sent_at=timezone.now()
        if status == NotificationDeliveryLog.STATUS_SENT
        else None,
        error="delivery failed"
        if status == NotificationDeliveryLog.STATUS_FAILED
        else None,
        metadata={"source": "pipeline_report_test"},
    )


@pytest.mark.django_db
def test_activation_pipeline_report_counts_backlogs_and_failures(
    organization,
    workspace,
    user,
):
    _export_log(
        organization,
        workspace,
        user,
        status=OnboardingPaidCloudActivationExportLog.STATUS_READY,
    )
    _export_log(
        organization,
        workspace,
        user,
        status=OnboardingPaidCloudActivationExportLog.STATUS_EXPORTED,
    )
    _export_log(
        organization,
        workspace,
        user,
        status=OnboardingPaidCloudActivationExportLog.STATUS_FAILED,
    )
    _export_log(
        organization,
        workspace,
        user,
        status=OnboardingPaidCloudActivationExportLog.STATUS_SUPPRESSED,
        suppression_reason="non_cloud_deployment",
    )

    imported_receipt = _receipt(organization, workspace, user)
    _receipt(organization, workspace, user)
    OnboardingActivationFactReceiptRejection.no_workspace_objects.create(
        reason="invalid_signature",
        message="bad signature",
        export_log_id=uuid.uuid4(),
        idempotency_key=f"pipeline-report:rejection:{uuid.uuid4()}",
        schema_version=ACTIVATION_EXPORT_SCHEMA_VERSION,
        payload_hash="b" * 64,
        metadata={"source": "pipeline_report_test"},
    )

    imported_evaluation = _evaluation_log(
        organization,
        workspace,
        user,
        source_receipt=imported_receipt,
    )
    failed_evaluation = _evaluation_log(
        organization,
        workspace,
        user,
        status=OnboardingLifecycleEvaluationLog.STATUS_ERROR,
    )
    _send_log(
        organization,
        workspace,
        user,
        imported_evaluation,
        status=OnboardingLifecycleSendLog.STATUS_SENT,
    )
    _send_log(
        organization,
        workspace,
        user,
        failed_evaluation,
        status=OnboardingLifecycleSendLog.STATUS_FAILED,
    )
    _notification_log(
        organization,
        workspace,
        user,
        status=NotificationDeliveryLog.STATUS_SENT,
    )
    _notification_log(
        organization,
        workspace,
        user,
        status=NotificationDeliveryLog.STATUS_FAILED,
        channel="slack",
    )

    result = activation_pipeline_report(since=timezone.now() - timedelta(hours=1))
    payload = result.to_payload()

    assert payload["status"] == "attention_required"
    assert payload["activation_exports"]["evaluated_count"] == 4
    assert payload["activation_exports"]["current_ready_backlog_count"] == 1
    assert payload["activation_exports"]["current_failed_backlog_count"] == 1
    assert payload["activation_receipts"]["accepted_count"] == 2
    assert payload["activation_receipts"]["imported_count"] == 1
    assert payload["activation_receipts"]["current_import_backlog_count"] == 1
    assert payload["activation_receipts"]["rejection_reason_counts"] == {
        "invalid_signature": 1
    }
    assert payload["lifecycle_evaluations"]["receipt_backed_count"] == 1
    assert payload["lifecycle_evaluations"]["error_count"] == 1
    assert payload["lifecycle_sends"]["failed_count"] == 1
    assert payload["notification_deliveries"]["failed_count"] == 1
    assert payload["risks"] == {
        "status": "attention_required",
        "export_ready_backlog_count": 1,
        "export_failed_backlog_count": 1,
        "receipt_import_backlog_count": 1,
        "receipt_rejection_count": 1,
        "lifecycle_error_count": 1,
        "send_failed_count": 1,
        "notification_failed_count": 1,
    }


@pytest.mark.django_db
def test_activation_pipeline_report_command_outputs_json_and_can_fail(
    organization,
    workspace,
    user,
):
    _export_log(
        organization,
        workspace,
        user,
        status=OnboardingPaidCloudActivationExportLog.STATUS_FAILED,
    )
    stdout = io.StringIO()

    call_command(
        "report_onboarding_activation_pipeline",
        "--since",
        timezone.now().date().isoformat(),
        "--format",
        "json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["schema_version"] == (
        "onboarding-activation-pipeline-report-2026-05-31.v1"
    )
    assert payload["status"] == "attention_required"
    assert payload["risks"]["export_failed_backlog_count"] == 1

    with pytest.raises(CommandError):
        call_command(
            "report_onboarding_activation_pipeline",
            "--since",
            timezone.now().date().isoformat(),
            "--fail-on-risk",
            stdout=io.StringIO(),
        )
