from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from accounts.services.onboarding.lifecycle_launch_packets import (
    load_lifecycle_launch_packet,
)
from accounts.services.onboarding.lifecycle_preview_approval import (
    load_lifecycle_preview_approval,
)
from accounts.services.onboarding.lifecycle_send_reports import (
    load_lifecycle_send_dry_run_report_review,
    write_lifecycle_send_dry_run_report,
)
from accounts.services.onboarding.lifecycle_sender import (
    send_limited_onboarding_lifecycle_batch,
)


class Command(BaseCommand):
    help = "Send tightly gated onboarding lifecycle emails to an allowlisted cohort."

    def add_arguments(self, parser):
        parser.add_argument("--cohort", choices=["internal", "beta"], required=True)
        parser.add_argument("--limit", type=int, required=True)
        parser.add_argument("--campaign-family")
        parser.add_argument("--workspace-id")
        parser.add_argument("--user-id")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--approval-manifest")
        parser.add_argument("--approval-record")
        parser.add_argument("--report-output")
        parser.add_argument("--report-force", action="store_true")
        parser.add_argument("--dry-run-report")
        parser.add_argument("--dry-run-report-review-record")
        parser.add_argument("--launch-packet")
        parser.add_argument("--include-receipt-backed", action="store_true")
        parser.add_argument("--now")

    def handle(self, *args, **options):
        if options["limit"] < 1:
            raise CommandError("--limit must be greater than zero.")
        if options.get("report_output") and not options["dry_run"]:
            raise CommandError("--report-output requires --dry-run.")
        if options["dry_run"] and (
            options.get("dry_run_report") or options.get("dry_run_report_review_record")
        ):
            raise CommandError("--dry-run-report is only supported for sends.")
        if options["dry_run"] and options.get("launch_packet"):
            raise CommandError("--launch-packet is only supported for sends.")
        if options["dry_run"] and options["include_receipt_backed"]:
            raise CommandError("--include-receipt-backed is only supported for sends.")
        now = None
        if options.get("now"):
            now = parse_datetime(options["now"])
            if now is None:
                raise CommandError("--now must be an ISO datetime.")
        preview_approval = None
        if options.get("approval_manifest"):
            try:
                preview_approval = load_lifecycle_preview_approval(
                    options["approval_manifest"],
                    approval_record_path=options.get("approval_record"),
                )
            except ImproperlyConfigured as exc:
                raise CommandError(str(exc)) from exc
        elif options.get("approval_record"):
            raise CommandError(
                "--approval-manifest is required with --approval-record."
            )
        elif not options["dry_run"]:
            raise CommandError("--approval-manifest is required for sends.")
        if not options["dry_run"] and not options.get("approval_record"):
            raise CommandError("--approval-record is required for sends.")
        dry_run_report_review = None
        if not options["dry_run"]:
            if not options.get("dry_run_report"):
                raise CommandError("--dry-run-report is required for sends.")
            if not options.get("dry_run_report_review_record"):
                raise CommandError(
                    "--dry-run-report-review-record is required for sends."
                )
            if not options.get("launch_packet"):
                raise CommandError("--launch-packet is required for sends.")
            try:
                dry_run_report_review = load_lifecycle_send_dry_run_report_review(
                    report_path=options["dry_run_report"],
                    review_record_path=options["dry_run_report_review_record"],
                    command_name="run_onboarding_lifecycle_send",
                    cohort=options["cohort"],
                    limit=options["limit"],
                    campaign_group=options.get("campaign_family"),
                    user_id=options.get("user_id"),
                    workspace_id=options.get("workspace_id"),
                    approval_manifest_sha256=preview_approval.manifest_sha256,
                    approval_record_sha256=preview_approval.approval_record_sha256,
                )
            except ImproperlyConfigured as exc:
                raise CommandError(str(exc)) from exc
        launch_packet = None
        if not options["dry_run"]:
            try:
                launch_packet = load_lifecycle_launch_packet(
                    options["launch_packet"],
                    command_name="run_onboarding_lifecycle_send",
                    cohort=options["cohort"],
                    limit=options["limit"],
                    campaign_group=options.get("campaign_family"),
                    user_id=options.get("user_id"),
                    workspace_id=options.get("workspace_id"),
                    require_campaign_group_allowlist=False,
                    approval_manifest_path=options["approval_manifest"],
                    approval_record_path=options["approval_record"],
                    dry_run_report_path=options["dry_run_report"],
                    dry_run_report_review_record_path=(
                        options["dry_run_report_review_record"]
                    ),
                    approval_manifest_sha256=preview_approval.manifest_sha256,
                    approval_record_sha256=preview_approval.approval_record_sha256,
                    dry_run_report_sha256=dry_run_report_review.report.sha256,
                    dry_run_report_review_record_sha256=(
                        dry_run_report_review.review_record_sha256
                    ),
                    require_ready=True,
                )
            except ImproperlyConfigured as exc:
                raise CommandError(str(exc)) from exc

        result = send_limited_onboarding_lifecycle_batch(
            cohort=options["cohort"],
            limit=options["limit"],
            campaign_group=options.get("campaign_family"),
            workspace_id=options.get("workspace_id"),
            user_id=options.get("user_id"),
            dry_run=options["dry_run"],
            now=now,
            preview_approval=preview_approval,
            dry_run_report_review=dry_run_report_review,
            launch_packet=launch_packet,
            include_receipt_backed=options["include_receipt_backed"],
        )
        payload = result.to_payload()
        if options.get("report_output"):
            try:
                report_output = write_lifecycle_send_dry_run_report(
                    output_path=options["report_output"],
                    force=options["report_force"],
                    command_name="run_onboarding_lifecycle_send",
                    result=result,
                    cohort=options["cohort"],
                    limit=options["limit"],
                    campaign_group=options.get("campaign_family"),
                    user_id=options.get("user_id"),
                    workspace_id=options.get("workspace_id"),
                )
            except ImproperlyConfigured as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(f"report_output={report_output}")
        if payload["approval_manifest_sha256"]:
            self.stdout.write(
                f"approval_manifest_sha256={payload['approval_manifest_sha256']}"
            )
        if payload["approval_record_sha256"]:
            self.stdout.write(
                f"approval_record_sha256={payload['approval_record_sha256']}"
            )
        if payload["dry_run_report_sha256"]:
            self.stdout.write(
                f"dry_run_report_sha256={payload['dry_run_report_sha256']}"
            )
        if payload["dry_run_report_review_record_sha256"]:
            self.stdout.write(
                "dry_run_report_review_record_sha256="
                f"{payload['dry_run_report_review_record_sha256']}"
            )
        if payload["launch_packet_sha256"]:
            self.stdout.write(f"launch_packet_sha256={payload['launch_packet_sha256']}")
        self.stdout.write(f"run_id={payload['run_id']}")
        self.stdout.write(f"evaluated={payload['evaluated']}")
        self.stdout.write(f"sent={payload['sent']}")
        self.stdout.write(f"suppressed={payload['suppressed']}")
        self.stdout.write(f"failed={payload['failed']}")
        self.stdout.write(f"skipped={payload['skipped']}")
        self.stdout.write(f"status_counts={payload['status_counts']}")
        self.stdout.write(f"suppression_counts={payload['suppression_counts']}")
