from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from accounts.services.onboarding.lifecycle_preview_approval import (
    load_lifecycle_preview_approval,
)
from accounts.services.onboarding.lifecycle_send_reports import (
    load_lifecycle_send_dry_run_report_review,
)

LAUNCH_PACKET_SCHEMA_VERSION = "onboarding-lifecycle-launch-packet-2026-05-30.v1"
LAUNCH_PACKET_SOURCE = "onboarding_lifecycle_launch_packet"
LAUNCH_PACKET_READY_STATUS = "ready_for_send"
LAUNCH_PACKET_NO_SENDABLE_STATUS = "ready_no_sendable_candidates"
LIFECYCLE_SEND_COMMAND = "run_onboarding_lifecycle_send"
WELCOME_BETA_SEND_COMMAND = "run_onboarding_welcome_email_beta"
WELCOME_CAMPAIGN_GROUP = "welcome"


@dataclass(frozen=True)
class LifecycleLaunchPacketResult:
    output_path: str
    packet_sha256: str
    status: str
    command_name: str
    evaluated: int
    sendable_candidate_count: int

    def to_payload(self):
        return {
            "output_path": self.output_path,
            "packet_sha256": self.packet_sha256,
            "status": self.status,
            "command_name": self.command_name,
            "evaluated": self.evaluated,
            "sendable_candidate_count": self.sendable_candidate_count,
        }


def _packet_error(message):
    return ImproperlyConfigured(f"Invalid lifecycle launch packet inputs: {message}")


def _command_args_for_send(
    *,
    command_name,
    report,
    approval_manifest_path,
    approval_record_path,
    dry_run_report_path,
    dry_run_report_review_record_path,
):
    args = [command_name]
    if command_name == WELCOME_BETA_SEND_COMMAND:
        args.append("--send")
    args.extend(["--cohort", report.cohort])
    args.extend(["--limit", str(report.limit)])
    if command_name == LIFECYCLE_SEND_COMMAND:
        if report.campaign_group:
            args.extend(["--campaign-family", report.campaign_group])
    elif command_name != WELCOME_BETA_SEND_COMMAND:
        raise _packet_error(f"{command_name} is not supported.")
    if report.user_id:
        args.extend(["--user-id", report.user_id])
    if report.workspace_id:
        args.extend(["--workspace-id", report.workspace_id])
    args.extend(["--approval-manifest", str(approval_manifest_path)])
    args.extend(["--approval-record", str(approval_record_path)])
    args.extend(["--dry-run-report", str(dry_run_report_path)])
    args.extend(
        [
            "--dry-run-report-review-record",
            str(dry_run_report_review_record_path),
        ]
    )
    return args


def _validate_report_command_scope(report):
    if report.command_name == WELCOME_BETA_SEND_COMMAND:
        if report.campaign_group != WELCOME_CAMPAIGN_GROUP:
            raise _packet_error(
                "welcome beta reports must use the welcome campaign group."
            )
        if report.require_campaign_group_allowlist is not True:
            raise _packet_error("welcome beta reports must require group allowlisting.")
        return
    if report.command_name == LIFECYCLE_SEND_COMMAND:
        if report.require_campaign_group_allowlist:
            raise _packet_error(
                "lifecycle send reports cannot require group allowlisting."
            )
        return
    raise _packet_error(f"{report.command_name} is not supported.")


def _preview_payload(approval):
    approved_campaign_keys = list(approval.campaign_keys)
    return {
        "manifest_path": approval.path,
        "manifest_sha256": approval.manifest_sha256,
        "manifest_generated_at": approval.generated_at,
        "manifest_campaign_count": len(approval.campaign_entries),
        "manifest_campaign_keys": list(approval.campaign_entries),
        "approval_record_path": approval.approval_record_path,
        "approval_record_sha256": approval.approval_record_sha256,
        "approved_by": approval.approved_by,
        "approved_at": approval.approved_at,
        "approved_campaign_count": len(approved_campaign_keys),
        "approved_campaign_keys": approved_campaign_keys,
    }


def _dry_run_payload(review):
    report = review.report
    return {
        "report_path": report.path,
        "report_sha256": report.sha256,
        "report_generated_at": report.generated_at,
        "approval_manifest_sha256": report.approval_manifest_sha256,
        "approval_record_sha256": report.approval_record_sha256,
        "review_record_path": review.review_record_path,
        "review_record_sha256": review.review_record_sha256,
        "reviewed_by": review.reviewed_by,
        "reviewed_at": review.reviewed_at,
        "evaluated": report.evaluated,
        "candidate_count": report.candidate_count,
        "sendable_candidate_count": len(report.sendable_evaluation_log_ids),
        "status_counts": report.status_counts,
        "suppression_counts": report.suppression_counts,
    }


def lifecycle_launch_packet_payload(
    *,
    approval_manifest_path,
    approval_record_path,
    dry_run_report_path,
    dry_run_report_review_record_path,
    generated_at=None,
    require_sendable_candidate=False,
):
    generated_at = generated_at or timezone.now()
    approval = load_lifecycle_preview_approval(
        approval_manifest_path,
        approval_record_path=approval_record_path,
    )
    if not approval.approval_record_sha256:
        raise _packet_error("preview approval record is required.")

    dry_run_review = load_lifecycle_send_dry_run_report_review(
        report_path=dry_run_report_path,
        review_record_path=dry_run_report_review_record_path,
    )
    report = dry_run_review.report
    _validate_report_command_scope(report)
    if report.approval_manifest_sha256 != approval.manifest_sha256:
        raise _packet_error(
            "dry-run report approval manifest does not match the preview approval."
        )
    if report.approval_record_sha256 != approval.approval_record_sha256:
        raise _packet_error(
            "dry-run report approval record does not match the preview approval."
        )

    sendable_count = len(report.sendable_evaluation_log_ids)
    if require_sendable_candidate and sendable_count < 1:
        raise _packet_error("dry-run report has no sendable candidates.")

    command_args = _command_args_for_send(
        command_name=report.command_name,
        report=report,
        approval_manifest_path=approval_manifest_path,
        approval_record_path=approval_record_path,
        dry_run_report_path=dry_run_report_path,
        dry_run_report_review_record_path=dry_run_report_review_record_path,
    )
    status = (
        LAUNCH_PACKET_READY_STATUS
        if sendable_count
        else LAUNCH_PACKET_NO_SENDABLE_STATUS
    )
    return {
        "schema_version": LAUNCH_PACKET_SCHEMA_VERSION,
        "source": LAUNCH_PACKET_SOURCE,
        "generated_at": generated_at.isoformat(),
        "status": status,
        "command": {
            "name": report.command_name,
            "argv": command_args,
        },
        "send_parameters": {
            "cohort": report.cohort,
            "limit": report.limit,
            "campaign_group": report.campaign_group,
            "user_id": report.user_id,
            "workspace_id": report.workspace_id,
            "require_campaign_group_allowlist": (
                report.require_campaign_group_allowlist
            ),
        },
        "preview": _preview_payload(approval),
        "dry_run": _dry_run_payload(dry_run_review),
        "checks": {
            "preview_approval_record_present": True,
            "dry_run_review_record_present": True,
            "dry_run_approval_matches_preview": True,
            "sendable_candidate_required": bool(require_sendable_candidate),
        },
    }


def write_lifecycle_launch_packet(
    *,
    output_path,
    force=False,
    **payload_kwargs,
):
    path = Path(output_path)
    if path.exists() and not force:
        raise _packet_error(f"{path} already exists. Use --force to overwrite.")
    packet = lifecycle_launch_packet_payload(**payload_kwargs)
    raw = json.dumps(packet, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return LifecycleLaunchPacketResult(
        output_path=str(path),
        packet_sha256=sha256(raw.encode("utf-8")).hexdigest(),
        status=packet["status"],
        command_name=packet["command"]["name"],
        evaluated=packet["dry_run"]["evaluated"],
        sendable_candidate_count=packet["dry_run"]["sendable_candidate_count"],
    )
