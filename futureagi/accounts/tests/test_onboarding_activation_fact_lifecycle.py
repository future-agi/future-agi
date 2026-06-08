import io
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from accounts.models import (
    OnboardingActivationFactReceipt,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendAllowlist,
    OnboardingLifecycleSendLog,
    User,
)
from accounts.services.onboarding.activation_exporter import (
    ACTIVATION_EXPORT_SCHEMA_VERSION,
)
from accounts.services.onboarding.activation_fact_lifecycle import (
    _receipt_metadata,
    import_activation_fact_lifecycle_evaluations,
    receipt_backed_lifecycle_cohort_report,
)
from accounts.services.onboarding.lifecycle_sender import (
    _receipt_lifecycle_target_url,
    send_limited_onboarding_lifecycle_batch,
)


def _receipt(organization, workspace, user, **overrides):
    now = overrides.pop("evaluated_at", timezone.now())
    fields = {
        "export_log_id": uuid.uuid4(),
        "idempotency_key": f"{workspace.id}:activation:{uuid.uuid4()}",
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
        "journey_config_schema_version": "onboarding-activation-export-config-2026-05-30.v1",
        "primary_cohort_key": "observe_waiting_first_trace",
        "cohort_keys": ["observe_waiting_first_trace"],
        "journey_cohorts": [
            {
                "cohort_key": "observe_waiting_first_trace",
                "target_action_id": "send_first_trace",
                "target_success_event": "trace_received",
                "priority": 95,
            }
        ],
        "payload_hash": "a" * 64,
        "payload": {
            "fact": {
                "activation": {
                    "stage": "waiting_for_first_trace",
                    "primary_path": "observe",
                    "is_activated": False,
                },
                "lifecycle": {
                    "delivery": {
                        "send_enabled": True,
                        "dry_run_only": False,
                        "target_route": "/dashboard/observe/project-1/llm-tracing",
                    }
                },
            }
        },
        "evaluated_at": now,
        "metadata": {
            "source": "activation_fact_receiver",
            "lifecycle_send_enabled": True,
            "lifecycle_dry_run_only": False,
            "lifecycle_target_route": "/dashboard/observe/project-1/llm-tracing",
            "lifecycle_target_action_id": "send_first_trace",
            "lifecycle_target_success_event": "trace_received",
        },
    }
    fields.update(overrides)
    return OnboardingActivationFactReceipt.no_workspace_objects.create(**fields)


def _user(organization, email):
    return User.objects.create_user(
        email=email,
        password="testpassword123",
        name=email.split("@")[0],
        organization=organization,
        organization_role="owner",
    )


def _lifecycle_send_flags(**overrides):
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
        "onboarding_email_eval": True,
        "onboarding_email_voice": True,
        "onboarding_lifecycle_send_enabled": True,
    }
    flags.update(overrides)
    return flags


def _allow_user_for_lifecycle_send(user):
    return OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
        scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
        scope_value=str(user.id),
        environment="local",
        reason="test",
    )


class _ApprovedPreview:
    manifest_sha256 = "a" * 64
    approval_record_sha256 = "b" * 64

    def has_campaign(self, campaign_key):
        return campaign_key == "observe_waiting_for_first_trace"

    def metadata_for_campaign(self, campaign_key):
        return {
            "campaign_key": campaign_key,
            "manifest_sha256": self.manifest_sha256,
            "approval_record_sha256": self.approval_record_sha256,
            "approved_by": "Lifecycle reviewer <reviewer@example.com>",
        }


class _ReviewedDryRun:
    report = SimpleNamespace(sha256="c" * 64)
    review_record_sha256 = "d" * 64

    def has_sendable_candidate(self, evaluation_log_id):
        return True

    def metadata_for_send(self):
        return {
            "path": "/tmp/lifecycle-send-dry-run-report.json",
            "sha256": self.report.sha256,
            "review_record_sha256": self.review_record_sha256,
            "reviewed_by": "Lifecycle reviewer <reviewer@example.com>",
        }


class _ReadyLaunchPacket:
    sha256 = "e" * 64

    def metadata_for_send(self):
        return {
            "path": "/tmp/lifecycle-launch-packet.json",
            "sha256": self.sha256,
            "status": "ready",
            "command": "run_onboarding_lifecycle_send",
        }


def test_receipt_lifecycle_metadata_keeps_receipt_send_disabled():
    receipt = SimpleNamespace(
        id=uuid.uuid4(),
        idempotency_key="receipt-key",
        export_log_id=uuid.uuid4(),
        payload_hash="a" * 64,
        deployment_mode="cloud",
        deployment_region="us",
        plan_tier="payg",
        primary_cohort_key="observe_waiting_first_trace",
        cohort_keys=["observe_waiting_first_trace"],
        journey_config_schema_version="onboarding-activation-export-config-2026-05-30.v1",
        lifecycle_template_key="observe_waiting_for_first_trace_v1",
        metadata={
            "lifecycle_send_enabled": True,
            "lifecycle_dry_run_only": False,
            "lifecycle_target_route": "/dashboard/observe/project-1/llm-tracing",
            "lifecycle_target_action_id": "send_first_trace",
            "lifecycle_target_success_event": "trace_received",
        },
    )

    metadata = _receipt_metadata(receipt)

    assert metadata["send_enabled"] is False
    assert metadata["receipt_lifecycle_send_enabled"] is True
    assert metadata["receipt_lifecycle_dry_run_only"] is False
    assert metadata["receipt_lifecycle_target_route"] == (
        "/dashboard/observe/project-1/llm-tracing"
    )
    assert metadata["receipt_lifecycle_target_action_id"] == "send_first_trace"

    receipt.metadata["lifecycle_send_enabled"] = "false"
    receipt.metadata["lifecycle_dry_run_only"] = "true"
    string_metadata = _receipt_metadata(receipt)

    assert string_metadata["send_enabled"] is False
    assert string_metadata["receipt_lifecycle_send_enabled"] is False
    assert string_metadata["receipt_lifecycle_dry_run_only"] is False


def test_receipt_lifecycle_target_url_keeps_internal_campaign_context():
    evaluation_log = SimpleNamespace(
        metadata={
            "receipt_lifecycle_target_route": "/dashboard/observe/project-1/llm-tracing"
        },
        target_url=None,
        activation_state_snapshot={},
    )

    assert _receipt_lifecycle_target_url(
        evaluation_log,
        {
            "campaign_key": "observe_waiting_for_first_trace",
            "target_success_event": "trace_received",
        },
    ) == (
        "/dashboard/observe/project-1/llm-tracing?"
        "source=onboarding_email&campaign_key=observe_waiting_for_first_trace"
        "&target_event=trace_received"
    )


def test_receipt_lifecycle_target_url_rejects_external_routes():
    evaluation_log = SimpleNamespace(
        metadata={"receipt_lifecycle_target_route": "https://example.com/not-internal"},
        target_url="//example.com/protocol-relative",
        activation_state_snapshot={
            "recommended_action_href": "https://example.com/also-not-internal"
        },
    )

    assert (
        _receipt_lifecycle_target_url(
            evaluation_log,
            {
                "campaign_key": "observe_waiting_for_first_trace",
                "target_success_event": "trace_received",
            },
        )
        is None
    )


@pytest.mark.django_db
def test_activation_fact_lifecycle_import_writes_eligible_log(
    organization,
    workspace,
    user,
):
    receipt = _receipt(organization, workspace, user)

    result = import_activation_fact_lifecycle_evaluations(limit=10)

    assert result.evaluated == 1
    assert result.imported == 1
    assert result.status_counts == {"imported": 1}
    log = OnboardingLifecycleEvaluationLog.no_workspace_objects.get()
    assert log.source_receipt == receipt
    assert log.status == OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE
    assert log.campaign_key == "observe_waiting_for_first_trace"
    assert log.template_key == "observe_waiting_for_first_trace_v1"
    assert log.activation_stage == "waiting_for_first_trace"
    assert log.primary_path == "observe"
    assert log.target_action_id == "send_first_trace"
    assert log.target_success_event == "trace_received"
    assert log.target_url is None
    assert log.eligible_at == receipt.evaluated_at
    assert log.metadata["source"] == "activation_fact_receipt"
    assert log.metadata["send_enabled"] is False
    assert log.metadata["receipt_lifecycle_send_enabled"] is True
    assert log.metadata["receipt_lifecycle_dry_run_only"] is False
    assert log.metadata["receipt_lifecycle_target_route"] == (
        "/dashboard/observe/project-1/llm-tracing"
    )
    assert log.metadata["receipt_lifecycle_target_action_id"] == "send_first_trace"
    assert log.metadata["receipt_lifecycle_target_success_event"] == "trace_received"
    assert log.metadata["receipt_id"] == str(receipt.id)
    assert log.metadata["cohort_keys"] == ["observe_waiting_first_trace"]


@pytest.mark.django_db
def test_activation_fact_lifecycle_import_is_idempotent(
    organization,
    workspace,
    user,
):
    _receipt(organization, workspace, user)

    first = import_activation_fact_lifecycle_evaluations(limit=10)
    second = import_activation_fact_lifecycle_evaluations(limit=10)

    assert first.imported == 1
    assert second.evaluated == 0
    assert second.imported == 0
    assert OnboardingLifecycleEvaluationLog.no_workspace_objects.count() == 1


@pytest.mark.django_db
def test_activation_fact_lifecycle_import_skips_duplicate_campaign_candidate(
    organization,
    workspace,
    user,
):
    _receipt(organization, workspace, user)
    _receipt(organization, workspace, user)

    result = import_activation_fact_lifecycle_evaluations(limit=10)

    assert result.evaluated == 2
    assert result.imported == 1
    assert result.status_counts == {"imported": 1, "skipped": 1}
    assert result.skip_counts == {"existing_lifecycle_candidate": 1}
    assert OnboardingLifecycleEvaluationLog.no_workspace_objects.count() == 1


@pytest.mark.django_db
def test_activation_fact_lifecycle_import_filters_non_paid_cloud_receipts(
    organization,
    workspace,
    user,
):
    _receipt(organization, workspace, user, deployment_mode="self_hosted")
    _receipt(organization, workspace, user, plan_tier="free")
    _receipt(organization, workspace, user, email_suppressed=True)
    _receipt(
        organization,
        workspace,
        user,
        lifecycle_status=OnboardingLifecycleEvaluationLog.STATUS_SUPPRESSED,
    )

    result = import_activation_fact_lifecycle_evaluations(limit=10)

    assert result.evaluated == 0
    assert result.imported == 0
    assert OnboardingLifecycleEvaluationLog.no_workspace_objects.count() == 0


@pytest.mark.django_db
def test_activation_fact_lifecycle_import_skips_unresolved_user(
    organization,
    workspace,
    user,
):
    _receipt(organization, workspace, user, user_id_value=uuid.uuid4())

    result = import_activation_fact_lifecycle_evaluations(limit=10)

    assert result.evaluated == 1
    assert result.imported == 0
    assert result.status_counts == {"skipped": 1}
    assert result.skip_counts == {"missing_user": 1}
    assert OnboardingLifecycleEvaluationLog.no_workspace_objects.count() == 0


@pytest.mark.django_db
def test_activation_fact_lifecycle_import_skips_unknown_campaign(
    organization,
    workspace,
    user,
):
    _receipt(organization, workspace, user, lifecycle_campaign_key="unknown_campaign")

    result = import_activation_fact_lifecycle_evaluations(limit=10)

    assert result.evaluated == 1
    assert result.imported == 0
    assert result.status_counts == {"skipped": 1}
    assert result.skip_counts == {"unknown_campaign": 1}
    assert OnboardingLifecycleEvaluationLog.no_workspace_objects.count() == 0


@pytest.mark.django_db
def test_activation_fact_lifecycle_import_supports_command_no_write(
    organization,
    workspace,
    user,
):
    _receipt(organization, workspace, user)
    stdout = io.StringIO()

    call_command(
        "run_onboarding_activation_fact_lifecycle_import",
        "--limit",
        "10",
        "--no-write",
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert "evaluated=1" in output
    assert "imported=0" in output
    assert "status_counts={'would_import': 1}" in output
    assert OnboardingLifecycleEvaluationLog.no_workspace_objects.count() == 0


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_lifecycle_send_flags())
def test_receipt_sourced_lifecycle_logs_enter_dry_run_send_batch(
    organization,
    workspace,
    user,
):
    _receipt(organization, workspace, user)
    import_activation_fact_lifecycle_evaluations(limit=10)
    _allow_user_for_lifecycle_send(user)

    result = send_limited_onboarding_lifecycle_batch(
        cohort="internal",
        limit=10,
        dry_run=True,
    )

    assert result.evaluated == 1
    assert result.sent == 0
    assert result.status_counts == {"would_send": 1}
    assert result.candidates[0]["campaign_key"] == "observe_waiting_for_first_trace"
    assert result.candidates[0]["status"] == "would_send"
    assert result.candidates[0]["target_route"] == (
        "/dashboard/observe/project-1/llm-tracing?"
        "source=onboarding_email&campaign_key=observe_waiting_for_first_trace"
        "&target_event=trace_received"
    )


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_lifecycle_send_flags())
def test_receipt_sourced_lifecycle_preserves_package_route_context(
    organization,
    workspace,
    user,
):
    _receipt(
        organization,
        workspace,
        user,
        metadata={
            "source": "activation_fact_receiver",
            "lifecycle_send_enabled": True,
            "lifecycle_dry_run_only": False,
            "lifecycle_target_route": (
                "/dashboard/observe/project-1/llm-tracing?"
                "source=onboarding&onboarding=send-first-trace"
                "&provider=anthropic&language=typescript"
            ),
            "lifecycle_target_action_id": "send_first_trace",
            "lifecycle_target_success_event": "trace_received",
        },
    )
    import_activation_fact_lifecycle_evaluations(limit=10)
    _allow_user_for_lifecycle_send(user)

    result = send_limited_onboarding_lifecycle_batch(
        cohort="internal",
        limit=10,
        dry_run=True,
    )

    assert result.evaluated == 1
    assert result.status_counts == {"would_send": 1}
    assert result.candidates[0]["target_route"] == (
        "/dashboard/observe/project-1/llm-tracing?"
        "source=onboarding_email&onboarding=send-first-trace"
        "&provider=anthropic&language=typescript"
        "&campaign_key=observe_waiting_for_first_trace"
        "&target_event=trace_received"
    )


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_lifecycle_send_flags())
def test_receipt_sourced_lifecycle_dry_run_suppresses_unsafe_routes(
    organization,
    workspace,
    user,
):
    _receipt(
        organization,
        workspace,
        user,
        metadata={
            "source": "activation_fact_receiver",
            "lifecycle_send_enabled": True,
            "lifecycle_dry_run_only": False,
            "lifecycle_target_route": "https://example.com/not-internal",
            "lifecycle_target_action_id": "send_first_trace",
            "lifecycle_target_success_event": "trace_received",
        },
    )
    import_activation_fact_lifecycle_evaluations(limit=10)
    _allow_user_for_lifecycle_send(user)

    result = send_limited_onboarding_lifecycle_batch(
        cohort="internal",
        limit=10,
        dry_run=True,
    )

    assert result.evaluated == 1
    assert result.status_counts == {"would_suppress": 1}
    assert result.suppression_counts == {"route_unavailable": 1}
    assert result.candidates[0]["status"] == "would_suppress"
    assert result.candidates[0]["suppression_reason"] == "route_unavailable"
    assert result.candidates[0]["target_route"] == ""


@pytest.mark.django_db
def test_receipt_sourced_lifecycle_logs_remain_excluded_from_real_send_batch(
    organization,
    workspace,
    user,
):
    _receipt(organization, workspace, user)
    import_activation_fact_lifecycle_evaluations(limit=10)

    result = send_limited_onboarding_lifecycle_batch(
        cohort="internal",
        limit=10,
        preview_approval=_ApprovedPreview(),
        dry_run_report_review=_ReviewedDryRun(),
        launch_packet=_ReadyLaunchPacket(),
    )

    assert result.evaluated == 0
    assert result.sent == 0
    assert OnboardingLifecycleSendLog.no_workspace_objects.count() == 0


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_lifecycle_send_flags())
def test_receipt_sourced_lifecycle_logs_can_enter_manual_real_send_batch(
    organization,
    workspace,
    user,
):
    receipt = _receipt(organization, workspace, user)
    import_activation_fact_lifecycle_evaluations(limit=10)
    _allow_user_for_lifecycle_send(user)

    with (
        patch(
            "accounts.services.onboarding.lifecycle_sender._cloud_lifecycle_delivery_enabled",
            return_value=True,
        ),
        patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper,
    ):
        result = send_limited_onboarding_lifecycle_batch(
            cohort="internal",
            limit=10,
            preview_approval=_ApprovedPreview(),
            dry_run_report_review=_ReviewedDryRun(),
            launch_packet=_ReadyLaunchPacket(),
            include_receipt_backed=True,
        )

    assert result.evaluated == 1
    assert result.sent == 1
    assert result.status_counts == {OnboardingLifecycleSendLog.STATUS_SENT: 1}
    helper.assert_called_once()
    send_log = OnboardingLifecycleSendLog.no_workspace_objects.get()
    assert send_log.status == OnboardingLifecycleSendLog.STATUS_SENT
    assert send_log.target_route == (
        "/dashboard/observe/project-1/llm-tracing?"
        "source=onboarding_email&campaign_key=observe_waiting_for_first_trace"
        "&target_event=trace_received"
    )
    assert send_log.metadata["source"] == "activation_fact_receipt"
    assert send_log.metadata["receipt_id"] == str(receipt.id)
    assert send_log.metadata["plan_tier"] == "payg"
    assert send_log.metadata["primary_cohort_key"] == "observe_waiting_first_trace"


@pytest.mark.django_db
@override_settings(ONBOARDING_FEATURE_FLAGS=_lifecycle_send_flags())
def test_receipt_sourced_lifecycle_real_send_respects_dry_run_only_flag(
    organization,
    workspace,
    user,
):
    _receipt(
        organization,
        workspace,
        user,
        metadata={
            "source": "activation_fact_receiver",
            "lifecycle_send_enabled": True,
            "lifecycle_dry_run_only": True,
            "lifecycle_target_route": "/dashboard/observe/project-1/llm-tracing",
            "lifecycle_target_action_id": "send_first_trace",
            "lifecycle_target_success_event": "trace_received",
        },
    )
    import_activation_fact_lifecycle_evaluations(limit=10)
    _allow_user_for_lifecycle_send(user)

    with (
        patch(
            "accounts.services.onboarding.lifecycle_sender._cloud_lifecycle_delivery_enabled",
            return_value=True,
        ),
        patch("accounts.services.onboarding.lifecycle_sender.email_helper") as helper,
    ):
        result = send_limited_onboarding_lifecycle_batch(
            cohort="internal",
            limit=10,
            preview_approval=_ApprovedPreview(),
            dry_run_report_review=_ReviewedDryRun(),
            launch_packet=_ReadyLaunchPacket(),
            include_receipt_backed=True,
        )

    assert result.evaluated == 1
    assert result.sent == 0
    assert result.suppressed == 1
    assert result.suppression_counts == {"receipt_dry_run_only": 1}
    helper.assert_not_called()
    assert OnboardingLifecycleSendLog.no_workspace_objects.filter(
        status=OnboardingLifecycleSendLog.STATUS_SUPPRESSED,
        suppression_reason="receipt_dry_run_only",
    ).exists()


@pytest.mark.django_db
def test_activation_fact_lifecycle_report_groups_receipt_backed_rows_by_cohort(
    organization,
    workspace,
    user,
):
    second_user = _user(organization, "second-report-user@futureagi.com")
    _receipt(organization, workspace, user)
    _receipt(
        organization,
        workspace,
        second_user,
        user_id_value=second_user.id,
        deployment_region="eu",
    )
    import_activation_fact_lifecycle_evaluations(limit=10)

    report = receipt_backed_lifecycle_cohort_report(
        group_by=("campaign_key", "primary_cohort_key", "plan_tier"),
    ).to_payload()

    assert report["group_by"] == ["campaign_key", "primary_cohort_key", "plan_tier"]
    assert report["rows"] == [
        {
            "campaign_key": "observe_waiting_for_first_trace",
            "primary_cohort_key": "observe_waiting_first_trace",
            "plan_tier": "payg",
            "source_receipt_count": 2,
            "eligible_count": 2,
            "workspace_count": 1,
            "user_count": 2,
            "first_evaluated_at": report["rows"][0]["first_evaluated_at"],
            "last_evaluated_at": report["rows"][0]["last_evaluated_at"],
        }
    ]
    assert report["rows"][0]["first_evaluated_at"]
    assert report["rows"][0]["last_evaluated_at"]


@pytest.mark.django_db
def test_activation_fact_lifecycle_report_filters_by_since_until_campaign_workspace(
    organization,
    workspace,
    user,
):
    now = timezone.now()
    old_user = _user(organization, "old-report-user@futureagi.com")
    gateway_user = _user(organization, "gateway-report-user@futureagi.com")
    _receipt(organization, workspace, user, evaluated_at=now)
    _receipt(
        organization,
        workspace,
        old_user,
        user_id_value=old_user.id,
        evaluated_at=now - timedelta(days=3),
    )
    _receipt(
        organization,
        workspace,
        gateway_user,
        user_id_value=gateway_user.id,
        lifecycle_campaign_key="gateway_create_key",
        lifecycle_template_key="gateway_create_key_v1",
        activation_stage="create_gateway_key",
        primary_path="gateway",
        primary_cohort_key="gateway_create_key",
        evaluated_at=now,
    )
    import_activation_fact_lifecycle_evaluations(limit=10)

    report = receipt_backed_lifecycle_cohort_report(
        since=now - timedelta(hours=1),
        until=now + timedelta(hours=1),
        campaign_key="observe_waiting_for_first_trace",
        workspace_id=workspace.id,
        group_by=("campaign_key", "primary_cohort_key"),
    ).to_payload()

    assert len(report["rows"]) == 1
    assert report["rows"][0]["campaign_key"] == "observe_waiting_for_first_trace"
    assert report["rows"][0]["primary_cohort_key"] == "observe_waiting_first_trace"
    assert report["rows"][0]["source_receipt_count"] == 1
    assert report["rows"][0]["workspace_count"] == 1
    assert report["rows"][0]["user_count"] == 1


@pytest.mark.django_db
def test_activation_fact_lifecycle_report_excludes_non_receipt_lifecycle_rows(
    organization,
    workspace,
    user,
):
    _receipt(organization, workspace, user)
    import_activation_fact_lifecycle_evaluations(limit=10)
    OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        run_id=uuid.uuid4(),
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key="observe_waiting_for_first_trace",
        campaign_group="observe",
        template_key="observe_waiting_for_first_trace_v1",
        template_version="v1",
        activation_stage="waiting_for_first_trace",
        primary_path="observe",
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        evaluated_at=timezone.now(),
        activation_state_snapshot={},
        registry_snapshot={},
    )

    report = receipt_backed_lifecycle_cohort_report().to_payload()

    assert len(report["rows"]) == 1
    assert report["rows"][0]["source_receipt_count"] == 1
    assert report["rows"][0]["eligible_count"] == 1


@pytest.mark.django_db
def test_activation_fact_lifecycle_report_command_outputs_json(
    organization,
    workspace,
    user,
):
    _receipt(organization, workspace, user)
    import_activation_fact_lifecycle_evaluations(limit=10)
    stdout = io.StringIO()

    call_command(
        "report_onboarding_activation_fact_lifecycle_cohorts",
        "--group-by",
        "campaign_key,primary_cohort_key,plan_tier",
        "--format",
        "json",
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["group_by"] == [
        "campaign_key",
        "primary_cohort_key",
        "plan_tier",
    ]
    assert payload["rows"][0]["campaign_key"] == "observe_waiting_for_first_trace"
    assert payload["rows"][0]["primary_cohort_key"] == "observe_waiting_first_trace"
    assert payload["rows"][0]["plan_tier"] == "payg"
    assert payload["rows"][0]["source_receipt_count"] == 1
