from __future__ import annotations

import json
import uuid
from datetime import UTC, timedelta
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.test import override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.models import (
    NotificationDeliveryLog,
    NotificationPreference,
    OnboardingActivationFactReceipt,
    OnboardingGoal,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecyclePreference,
    OnboardingLifecycleSendAllowlist,
    OnboardingLifecycleSendLog,
    Organization,
    User,
    Workspace,
)
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.services.onboarding.activation_exporter import (
    ACTIVATION_EXPORT_SCHEMA_VERSION,
)
from accounts.services.onboarding.lifecycle_launch_packets import (
    LAUNCH_PACKET_METADATA_KEY,
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
    write_lifecycle_send_dry_run_report_review_record,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

CAMPAIGN_KEY = "welcome_resume_goal"
CAMPAIGN_GROUP = "welcome"
REVIEWER = "Lifecycle reviewer <reviewer@example.com>"


def _aware_datetime(value):
    if value is None:
        return timezone.now()
    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError("--now must be an ISO datetime.")
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone=UTC)
    return parsed


def _feature_flags():
    return {
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


def _create_workspace_subject(now):
    suffix = uuid.uuid4().hex[:12]
    organization = Organization.objects.create(
        name=f"Lifecycle fixture org {suffix}",
        display_name=f"Lifecycle fixture org {suffix}",
    )
    user = User.objects.create_user(
        email=f"lifecycle-fixture+{suffix}@futureagi.com",
        password="testpassword123",
        name="Lifecycle Fixture",
        organization=organization,
        organization_role="Owner",
    )
    workspace = Workspace.no_workspace_objects.create(
        name=f"lifecycle-fixture-{suffix}",
        display_name=f"Lifecycle fixture {suffix}",
        organization=organization,
        created_by=user,
        is_default=True,
    )
    Workspace.no_workspace_objects.filter(id=workspace.id).update(
        created_at=now - timedelta(minutes=30)
    )
    user.config = {
        **(user.config or {}),
        "currentWorkspaceId": str(workspace.id),
        "defaultWorkspaceId": str(workspace.id),
    }
    user.save(update_fields=["config"])
    org_membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization,
        role=OrganizationRoles.OWNER,
        level=Level.OWNER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        organization_membership=org_membership,
        role=OrganizationRoles.WORKSPACE_ADMIN,
        level=Level.WORKSPACE_ADMIN,
        granted_by=user,
        invited_by=user,
        is_active=True,
    )
    OnboardingGoal.no_workspace_objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        goal="monitor_production_ai_app",
        primary_path="observe",
        source="fixture",
        reason="lifecycle_evidence_pack",
        selected_at=now - timedelta(minutes=20),
    )
    return organization, workspace, user


def _eligible_evaluation(user, organization, workspace, now):
    campaign = lifecycle_campaign_by_key(CAMPAIGN_KEY)
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
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
    )


def _receipt(organization, workspace, user, now):
    return OnboardingActivationFactReceipt.no_workspace_objects.create(
        export_log_id=uuid.uuid4(),
        idempotency_key=f"{workspace.id}:fixture-evidence:{uuid.uuid4()}",
        schema_version=ACTIVATION_EXPORT_SCHEMA_VERSION,
        event_cursor=now.isoformat(),
        organization_id_value=organization.id,
        workspace_id_value=workspace.id,
        user_id_value=user.id,
        deployment_mode="cloud",
        deployment_region="us",
        plan_tier="payg",
        activation_stage="choose_goal",
        primary_path="observe",
        is_activated=False,
        lifecycle_campaign_key=CAMPAIGN_KEY,
        lifecycle_template_key="welcome_resume_goal_v1",
        lifecycle_status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        email_next_key="welcome_resume_goal_v1",
        email_eligible=True,
        email_suppressed=False,
        journey_config_schema_version=(
            "onboarding-activation-export-config-2026-05-30.v1"
        ),
        primary_cohort_key="welcome_resume_goal",
        cohort_keys=["welcome_resume_goal"],
        journey_cohorts=[],
        payload_hash="d" * 64,
        payload={},
        evaluated_at=now,
        metadata={"source": "activation_fact_receiver"},
    )


def _send_metadata(*, receipt, approval_sha, dry_run_sha, launch_packet_sha):
    return {
        APPROVAL_METADATA_KEY: {
            "approval_record_sha256": approval_sha,
        },
        DRY_RUN_REPORT_METADATA_KEY: {
            "sha256": dry_run_sha,
        },
        LAUNCH_PACKET_METADATA_KEY: {
            "sha256": launch_packet_sha,
            "status": "ready_for_send",
            "command": "run_onboarding_welcome_email_beta",
        },
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
        "journey_config_schema_version": receipt.journey_config_schema_version,
        "receipt_template_key": receipt.lifecycle_template_key,
    }


def _send_log(
    user,
    organization,
    workspace,
    *,
    now,
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
    campaign = lifecycle_campaign_by_key(CAMPAIGN_KEY)
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


def _delivery_log(send_log, *, status, suppressed_reason=None, now):
    return NotificationDeliveryLog.no_workspace_objects.create(
        organization=send_log.organization,
        workspace=send_log.workspace,
        user=send_log.user,
        family=NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
        source_type="onboarding_lifecycle",
        source_id=str(send_log.id),
        channel=NotificationPreference.CHANNEL_EMAIL,
        recipient_type="user",
        recipient_identifier_masked="li***@futureagi.com",
        notification_key=send_log.campaign_key,
        stage=send_log.activation_stage,
        status=status,
        suppressed_reason=suppressed_reason,
        route_url=send_log.target_route,
        sent_at=now if status == NotificationDeliveryLog.STATUS_SENT else None,
    )


class Command(BaseCommand):
    help = (
        "Generate a local fixture proof pack for receipt-backed onboarding "
        "lifecycle send evidence without sending email."
    )

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", required=True)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--now")
        parser.add_argument(
            "--allow-non-test-database",
            action="store_true",
            help="Permit writing synthetic proof rows outside Django test settings.",
        )

    def handle(self, *args, **options):
        if (
            not getattr(settings, "TESTING", False)
            and not options["allow_non_test_database"]
        ):
            raise CommandError(
                "This fixture pack writes synthetic rows. Run with test settings "
                "or pass --allow-non-test-database explicitly."
            )

        now = _aware_datetime(options.get("now"))
        force = options["force"]
        output_dir = Path(options["output_dir"])
        if output_dir.exists() and any(output_dir.iterdir()) and not force:
            raise CommandError(f"{output_dir} is not empty. Use --force to overwrite.")
        output_dir.mkdir(parents=True, exist_ok=True)

        with override_settings(ONBOARDING_FEATURE_FLAGS=_feature_flags()):
            organization, workspace, user = _create_workspace_subject(now)
            _eligible_evaluation(user, organization, workspace, now)
            OnboardingLifecycleSendAllowlist.no_workspace_objects.create(
                scope_type=OnboardingLifecycleSendAllowlist.SCOPE_USER,
                scope_value=str(user.id),
                environment="local",
                campaign_group=CAMPAIGN_GROUP,
                reason="fixture proof pack",
            )

            preview_dir = output_dir / "previews"
            write_lifecycle_preview_snapshots(
                output_dir=preview_dir,
                campaign_key=CAMPAIGN_KEY,
                now=now,
                force=force,
            )
            approval_manifest = preview_dir / "manifest.json"
            approval_record = preview_dir / "approval-record.json"
            approval_result = write_lifecycle_preview_approval_record(
                manifest_path=approval_manifest,
                output_path=approval_record,
                approved_by=REVIEWER,
                approved_at=now + timedelta(minutes=1),
                campaign_keys=[CAMPAIGN_KEY],
                force=force,
            )

            dry_run_report = output_dir / "welcome-dry-run-report.json"
            dry_run_args = [
                "run_onboarding_welcome_email_beta",
                "--limit",
                "1",
                "--user-id",
                str(user.id),
                "--workspace-id",
                str(workspace.id),
                "--approval-manifest",
                str(approval_manifest),
                "--approval-record",
                str(approval_record),
                "--report-output",
                str(dry_run_report),
                "--now",
                now.isoformat(),
            ]
            if force:
                dry_run_args.append("--report-force")
            call_command(
                *dry_run_args,
                stdout=StringIO(),
            )
            dry_run_review = output_dir / "welcome-dry-run-report-review.json"
            dry_run_review_result = write_lifecycle_send_dry_run_report_review_record(
                report_path=dry_run_report,
                output_path=dry_run_review,
                reviewed_by=REVIEWER,
                reviewed_at=now + timedelta(minutes=2),
                force=force,
            )
            launch_packet = output_dir / "launch-packet.json"
            launch_packet_result = write_lifecycle_launch_packet(
                output_path=launch_packet,
                approval_manifest_path=approval_manifest,
                approval_record_path=approval_record,
                dry_run_report_path=dry_run_report,
                dry_run_report_review_record_path=dry_run_review,
                require_sendable_candidate=True,
                generated_at=now + timedelta(minutes=3),
                force=force,
            )

            receipt = _receipt(organization, workspace, user, now)
            sent_log = _send_log(
                user,
                organization,
                workspace,
                now=now,
                status=OnboardingLifecycleSendLog.STATUS_COMPLETED,
                provider_status="accepted",
                sent_at=now + timedelta(minutes=3),
                clicked_at=now + timedelta(minutes=4),
                completed_at=now + timedelta(minutes=5),
                unsubscribed_at=now + timedelta(minutes=6),
                metadata=_send_metadata(
                    receipt=receipt,
                    approval_sha=approval_result.approval_record_sha256,
                    dry_run_sha=dry_run_review_result.report_sha256,
                    launch_packet_sha=launch_packet_result.packet_sha256,
                ),
                source_receipt=receipt,
            )
            _delivery_log(
                sent_log,
                status=NotificationDeliveryLog.STATUS_SENT,
                now=now + timedelta(minutes=3),
            )
            OnboardingLifecyclePreference.no_workspace_objects.create(
                user=user,
                organization=organization,
                workspace=workspace,
                onboarding_enabled=False,
                unsubscribed_at=now + timedelta(minutes=6),
                snoozed_until=now + timedelta(days=7),
            )
            frequency_capped = _send_log(
                user,
                organization,
                workspace,
                now=now,
                status=OnboardingLifecycleSendLog.STATUS_SUPPRESSED,
                suppression_reason="frequency_capped",
            )
            _delivery_log(
                frequency_capped,
                status=NotificationDeliveryLog.STATUS_SUPPRESSED,
                suppressed_reason="frequency_capped",
                now=now + timedelta(minutes=7),
            )
            completion_suppressed = _send_log(
                user,
                organization,
                workspace,
                now=now,
                status=OnboardingLifecycleSendLog.STATUS_SUPPRESSED,
                suppression_reason="target_success_event_completed",
            )

            send_evidence = output_dir / "send-evidence-report.json"
            send_evidence_args = [
                "generate_onboarding_lifecycle_send_evidence_report",
                "--send-log-id",
                str(sent_log.id),
                "--send-log-id",
                str(frequency_capped.id),
                "--send-log-id",
                str(completion_suppressed.id),
                "--output",
                str(send_evidence),
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
                (now + timedelta(minutes=8)).isoformat(),
            ]
            if force:
                send_evidence_args.append("--force")
            call_command(
                *send_evidence_args,
                stdout=StringIO(),
            )
            launch_review = output_dir / "launch-review.json"
            launch_review_args = [
                "generate_onboarding_lifecycle_launch_review",
                "--approval-manifest",
                str(approval_manifest),
                "--approval-record",
                str(approval_record),
                "--dry-run-report",
                str(dry_run_report),
                "--dry-run-report-review-record",
                str(dry_run_review),
                "--launch-packet",
                str(launch_packet),
                "--send-evidence-report",
                str(send_evidence),
                "--output",
                str(launch_review),
                "--now",
                (now + timedelta(minutes=9)).isoformat(),
            ]
            if force:
                launch_review_args.append("--force")
            call_command(
                *launch_review_args,
                stdout=StringIO(),
            )

        summary = {
            "status": "passed",
            "source": "onboarding_lifecycle_evidence_fixture_pack",
            "generated_at": (now + timedelta(minutes=9)).isoformat(),
            "organization_id": str(organization.id),
            "workspace_id": str(workspace.id),
            "user_id": str(user.id),
            "artifacts": {
                "approval_manifest": str(approval_manifest),
                "approval_record": str(approval_record),
                "dry_run_report": str(dry_run_report),
                "dry_run_report_review": str(dry_run_review),
                "launch_packet": str(launch_packet),
                "send_evidence_report": str(send_evidence),
                "launch_review": str(launch_review),
            },
            "send_log_ids": [
                str(sent_log.id),
                str(frequency_capped.id),
                str(completion_suppressed.id),
            ],
        }
        summary_path = output_dir / "fixture-summary.json"
        summary_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        self.stdout.write(f"output_dir={output_dir}")
        self.stdout.write(f"summary={summary_path}")
        self.stdout.write(f"send_evidence_report={send_evidence}")
        self.stdout.write(f"launch_review={launch_review}")
        self.stdout.write("status=passed")
