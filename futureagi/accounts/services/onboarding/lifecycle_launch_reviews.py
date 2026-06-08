from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from accounts.services.onboarding.lifecycle_launch_packets import (
    LAUNCH_PACKET_READY_STATUS,
    load_lifecycle_launch_packet,
)
from accounts.services.onboarding.lifecycle_preview_approval import (
    load_lifecycle_preview_approval,
)
from accounts.services.onboarding.lifecycle_send_evidence import (
    CORE_REQUIREMENT_KEYS,
    REQUIREMENT_KEYS,
    load_lifecycle_send_evidence_report,
)
from accounts.services.onboarding.lifecycle_send_reports import (
    load_lifecycle_send_dry_run_report_review,
)

LAUNCH_REVIEW_SCHEMA_VERSION = "onboarding-lifecycle-launch-review-2026-05-30.v1"
LAUNCH_REVIEW_SOURCE = "onboarding_lifecycle_launch_review"
LAUNCH_REVIEW_PASSED_STATUS = "passed"
LAUNCH_REVIEW_INCOMPLETE_STATUS = "incomplete"


@dataclass(frozen=True)
class LifecycleLaunchReviewResult:
    output_path: str
    review_sha256: str
    status: str
    missing_checks: tuple[str, ...]

    def to_payload(self):
        return {
            "output_path": self.output_path,
            "review_sha256": self.review_sha256,
            "status": self.status,
            "missing_checks": list(self.missing_checks),
        }


def _review_error(message):
    return ImproperlyConfigured(f"Invalid lifecycle launch review inputs: {message}")


def _artifact(path, sha_value, *, status=None):
    payload = {
        "path": str(path),
        "sha256": sha_value,
    }
    if status:
        payload["status"] = status
    return payload


def _launch_packet_params(report):
    return {
        "command_name": report.command_name,
        "cohort": report.cohort,
        "limit": report.limit,
        "campaign_group": report.campaign_group,
        "user_id": report.user_id,
        "workspace_id": report.workspace_id,
        "require_campaign_group_allowlist": report.require_campaign_group_allowlist,
    }


def _evidence_references_launch_packet(evidence_report, launch_packet):
    return any(
        isinstance(send_log.get("artifact_hashes"), dict)
        and send_log["artifact_hashes"].get("launch_packet") == launch_packet.sha256
        for send_log in evidence_report.send_logs
    )


def _required_evidence_missing(evidence_report):
    missing = []
    for key in CORE_REQUIREMENT_KEYS:
        if evidence_report.requirements.get(key) is not True:
            missing.append(f"evidence_requirement_{key}")
        elif evidence_report.aggregate_evidence.get(key) is not True:
            missing.append(f"evidence_{key}")
    for key in set(REQUIREMENT_KEYS) - set(CORE_REQUIREMENT_KEYS):
        if (
            evidence_report.requirements.get(key) is True
            and evidence_report.aggregate_evidence.get(key) is not True
        ):
            missing.append(f"evidence_{key}")
    return missing


def lifecycle_launch_review_payload(
    *,
    approval_manifest_path,
    approval_record_path,
    dry_run_report_path,
    dry_run_report_review_record_path,
    launch_packet_path,
    send_evidence_report_path,
    generated_at=None,
):
    generated_at = generated_at or timezone.now()
    approval = load_lifecycle_preview_approval(
        approval_manifest_path,
        approval_record_path=approval_record_path,
    )
    dry_run_review = load_lifecycle_send_dry_run_report_review(
        report_path=dry_run_report_path,
        review_record_path=dry_run_report_review_record_path,
    )
    dry_run_report = dry_run_review.report
    launch_packet = load_lifecycle_launch_packet(
        launch_packet_path,
        **_launch_packet_params(dry_run_report),
        approval_manifest_path=approval_manifest_path,
        approval_record_path=approval_record_path,
        dry_run_report_path=dry_run_report_path,
        dry_run_report_review_record_path=dry_run_report_review_record_path,
        approval_manifest_sha256=approval.manifest_sha256,
        approval_record_sha256=approval.approval_record_sha256,
        dry_run_report_sha256=dry_run_report.sha256,
        dry_run_report_review_record_sha256=dry_run_review.review_record_sha256,
        require_ready=True,
    )
    evidence_report = load_lifecycle_send_evidence_report(send_evidence_report_path)

    checks = {
        "preview_approval_valid": bool(approval.approval_record_sha256),
        "dry_run_report_review_valid": bool(dry_run_review.review_record_sha256),
        "launch_packet_ready": launch_packet.status == LAUNCH_PACKET_READY_STATUS,
        "launch_packet_references_dry_run": True,
        "send_evidence_report_passed": not evidence_report.missing_requirements,
        "send_evidence_references_launch_packet": _evidence_references_launch_packet(
            evidence_report,
            launch_packet,
        ),
    }
    missing_checks = [key for key, value in checks.items() if not value]
    missing_checks.extend(_required_evidence_missing(evidence_report))
    status = (
        LAUNCH_REVIEW_PASSED_STATUS
        if not missing_checks
        else LAUNCH_REVIEW_INCOMPLETE_STATUS
    )
    return {
        "schema_version": LAUNCH_REVIEW_SCHEMA_VERSION,
        "source": LAUNCH_REVIEW_SOURCE,
        "generated_at": generated_at.isoformat(),
        "status": status,
        "missing_checks": missing_checks,
        "artifacts": {
            "preview_manifest": _artifact(
                approval.path,
                approval.manifest_sha256,
            ),
            "preview_approval_record": _artifact(
                approval.approval_record_path,
                approval.approval_record_sha256,
            ),
            "dry_run_report": _artifact(
                dry_run_report.path,
                dry_run_report.sha256,
            ),
            "dry_run_report_review_record": _artifact(
                dry_run_review.review_record_path,
                dry_run_review.review_record_sha256,
            ),
            "launch_packet": _artifact(
                launch_packet.path,
                launch_packet.sha256,
                status=launch_packet.status,
            ),
            "send_evidence_report": _artifact(
                evidence_report.path,
                evidence_report.sha256,
                status=evidence_report.status,
            ),
        },
        "command": {
            "name": dry_run_report.command_name,
            "cohort": dry_run_report.cohort,
            "limit": dry_run_report.limit,
            "campaign_group": dry_run_report.campaign_group,
            "user_id": dry_run_report.user_id,
            "workspace_id": dry_run_report.workspace_id,
            "require_campaign_group_allowlist": (
                dry_run_report.require_campaign_group_allowlist
            ),
        },
        "evidence": {
            "requirements": evidence_report.requirements,
            "aggregate_evidence": evidence_report.aggregate_evidence,
            "send_log_count": evidence_report.send_log_count,
        },
        "checks": checks,
    }


def write_lifecycle_launch_review(
    *,
    output_path,
    force=False,
    **payload_kwargs,
):
    path = Path(output_path)
    if path.exists() and not force:
        raise _review_error(f"{path} already exists. Use --force to overwrite.")
    review = lifecycle_launch_review_payload(**payload_kwargs)
    raw = json.dumps(review, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return LifecycleLaunchReviewResult(
        output_path=str(path),
        review_sha256=sha256(raw.encode("utf-8")).hexdigest(),
        status=review["status"],
        missing_checks=tuple(review["missing_checks"]),
    )
