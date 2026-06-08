from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.dateparse import parse_datetime

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
LAUNCH_PACKET_METADATA_KEY = "launch_packet"
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


@dataclass(frozen=True)
class LifecycleLaunchPacket:
    path: str
    sha256: str
    generated_at: str
    status: str
    command_name: str
    sendable_candidate_count: int

    def metadata_for_send(self):
        return {
            "path": self.path,
            "sha256": self.sha256,
            "generated_at": self.generated_at,
            "status": self.status,
            "command": self.command_name,
            "sendable_candidate_count": self.sendable_candidate_count,
        }


def _packet_error(message):
    return ImproperlyConfigured(f"Invalid lifecycle launch packet inputs: {message}")


def _require_keys(mapping, expected, path):
    if set(mapping) != expected:
        missing = sorted(expected - set(mapping))
        extra = sorted(set(mapping) - expected)
        parts = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra:
            parts.append(f"unexpected {', '.join(extra)}")
        raise _packet_error(f"{path} has invalid fields ({'; '.join(parts)}).")


def _require_text(mapping, key, path, *, allow_none=False):
    value = mapping.get(key)
    if allow_none and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _packet_error(f"{path}.{key} must be a non-empty string.")
    return value


def _require_sha(value, path):
    if not isinstance(value, str) or len(value) != 64:
        raise _packet_error(f"{path} must be a SHA-256 hex digest.")
    try:
        int(value, 16)
    except ValueError as exc:
        raise _packet_error(f"{path} must be a SHA-256 hex digest.") from exc
    return value


def _require_nonnegative_int(mapping, key, path):
    value = mapping.get(key)
    if not isinstance(value, int) or value < 0:
        raise _packet_error(f"{path}.{key} must be a non-negative integer.")
    return value


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


def _expected_parameters(
    *,
    cohort,
    limit,
    campaign_group=None,
    user_id=None,
    workspace_id=None,
    require_campaign_group_allowlist=False,
):
    return {
        "cohort": cohort,
        "limit": limit,
        "campaign_group": campaign_group,
        "user_id": str(user_id) if user_id else None,
        "workspace_id": str(workspace_id) if workspace_id else None,
        "require_campaign_group_allowlist": bool(require_campaign_group_allowlist),
    }


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


def _validate_command(command, *, expected_name):
    if not isinstance(command, dict):
        raise _packet_error("command must be a mapping.")
    _require_keys(command, {"name", "argv"}, "command")
    command_name = _require_text(command, "name", "command")
    if command_name != expected_name:
        raise _packet_error("command.name does not match the send command.")
    argv = command["argv"]
    if not isinstance(argv, list) or not argv:
        raise _packet_error("command.argv must be a non-empty list.")
    if any(not isinstance(item, str) or not item.strip() for item in argv):
        raise _packet_error("command.argv must contain non-empty strings.")
    return command_name


def _validate_send_parameters(parameters, *, expected):
    if not isinstance(parameters, dict):
        raise _packet_error("send_parameters must be a mapping.")
    _require_keys(
        parameters,
        {
            "cohort",
            "limit",
            "campaign_group",
            "user_id",
            "workspace_id",
            "require_campaign_group_allowlist",
        },
        "send_parameters",
    )
    if parameters != expected:
        for key, expected_value in expected.items():
            if parameters[key] != expected_value:
                raise _packet_error(
                    f"send_parameters.{key} does not match the send command."
                )
    return parameters


def _validate_preview(
    preview,
    *,
    approval_manifest_path,
    approval_record_path,
    approval_manifest_sha256,
    approval_record_sha256,
):
    if not isinstance(preview, dict):
        raise _packet_error("preview must be a mapping.")
    _require_keys(
        preview,
        {
            "manifest_path",
            "manifest_sha256",
            "manifest_generated_at",
            "manifest_campaign_count",
            "manifest_campaign_keys",
            "approval_record_path",
            "approval_record_sha256",
            "approved_by",
            "approved_at",
            "approved_campaign_count",
            "approved_campaign_keys",
        },
        "preview",
    )
    if preview["manifest_path"] != str(approval_manifest_path):
        raise _packet_error("preview.manifest_path does not match the send command.")
    if preview["approval_record_path"] != str(approval_record_path):
        raise _packet_error(
            "preview.approval_record_path does not match the send command."
        )
    if preview["manifest_sha256"] != approval_manifest_sha256:
        raise _packet_error("preview.manifest_sha256 does not match the send command.")
    if preview["approval_record_sha256"] != approval_record_sha256:
        raise _packet_error(
            "preview.approval_record_sha256 does not match the send command."
        )
    _require_sha(preview["manifest_sha256"], "preview.manifest_sha256")
    _require_sha(preview["approval_record_sha256"], "preview.approval_record_sha256")
    _require_text(preview, "manifest_generated_at", "preview")
    _require_text(preview, "approved_by", "preview")
    _require_text(preview, "approved_at", "preview")
    for key in (
        "manifest_campaign_count",
        "approved_campaign_count",
    ):
        _require_nonnegative_int(preview, key, "preview")
    for key in ("manifest_campaign_keys", "approved_campaign_keys"):
        value = preview[key]
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            raise _packet_error(f"preview.{key} must contain non-empty strings.")


def _validate_dry_run(
    dry_run,
    *,
    dry_run_report_path,
    dry_run_report_review_record_path,
    dry_run_report_sha256,
    dry_run_report_review_record_sha256,
    approval_manifest_sha256,
    approval_record_sha256,
):
    if not isinstance(dry_run, dict):
        raise _packet_error("dry_run must be a mapping.")
    _require_keys(
        dry_run,
        {
            "report_path",
            "report_sha256",
            "report_generated_at",
            "approval_manifest_sha256",
            "approval_record_sha256",
            "review_record_path",
            "review_record_sha256",
            "reviewed_by",
            "reviewed_at",
            "evaluated",
            "candidate_count",
            "sendable_candidate_count",
            "status_counts",
            "suppression_counts",
        },
        "dry_run",
    )
    expected_values = {
        "report_path": str(dry_run_report_path),
        "review_record_path": str(dry_run_report_review_record_path),
        "report_sha256": dry_run_report_sha256,
        "review_record_sha256": dry_run_report_review_record_sha256,
        "approval_manifest_sha256": approval_manifest_sha256,
        "approval_record_sha256": approval_record_sha256,
    }
    for key, expected_value in expected_values.items():
        if dry_run[key] != expected_value:
            raise _packet_error(f"dry_run.{key} does not match the send command.")
    for key in (
        "report_sha256",
        "review_record_sha256",
        "approval_manifest_sha256",
        "approval_record_sha256",
    ):
        _require_sha(dry_run[key], f"dry_run.{key}")
    _require_text(dry_run, "report_generated_at", "dry_run")
    _require_text(dry_run, "reviewed_by", "dry_run")
    _require_text(dry_run, "reviewed_at", "dry_run")
    for key in ("evaluated", "candidate_count", "sendable_candidate_count"):
        _require_nonnegative_int(dry_run, key, "dry_run")
    for key in ("status_counts", "suppression_counts"):
        value = dry_run[key]
        if not isinstance(value, dict):
            raise _packet_error(f"dry_run.{key} must be a mapping.")
    return dry_run["sendable_candidate_count"]


def _validate_checks(checks):
    if not isinstance(checks, dict):
        raise _packet_error("checks must be a mapping.")
    _require_keys(
        checks,
        {
            "preview_approval_record_present",
            "dry_run_review_record_present",
            "dry_run_approval_matches_preview",
            "sendable_candidate_required",
        },
        "checks",
    )
    for key in (
        "preview_approval_record_present",
        "dry_run_review_record_present",
        "dry_run_approval_matches_preview",
        "sendable_candidate_required",
    ):
        if not isinstance(checks[key], bool):
            raise _packet_error(f"checks.{key} must be a bool.")
    for key in (
        "preview_approval_record_present",
        "dry_run_review_record_present",
        "dry_run_approval_matches_preview",
    ):
        if checks[key] is not True:
            raise _packet_error(f"checks.{key} must be true.")


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


def load_lifecycle_launch_packet(
    path,
    *,
    command_name,
    cohort,
    limit,
    campaign_group=None,
    user_id=None,
    workspace_id=None,
    require_campaign_group_allowlist=False,
    approval_manifest_path,
    approval_record_path,
    dry_run_report_path,
    dry_run_report_review_record_path,
    approval_manifest_sha256,
    approval_record_sha256,
    dry_run_report_sha256,
    dry_run_report_review_record_sha256,
    require_ready=False,
):
    packet_path = Path(path)
    try:
        raw = packet_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _packet_error(f"{packet_path} could not be read.") from exc
    try:
        packet = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _packet_error(f"{packet_path} is not valid JSON.") from exc
    if not isinstance(packet, dict):
        raise _packet_error("packet root must be a mapping.")
    _require_keys(
        packet,
        {
            "schema_version",
            "source",
            "generated_at",
            "status",
            "command",
            "send_parameters",
            "preview",
            "dry_run",
            "checks",
        },
        "packet",
    )
    if packet["schema_version"] != LAUNCH_PACKET_SCHEMA_VERSION:
        raise _packet_error("schema_version is not supported.")
    if packet["source"] != LAUNCH_PACKET_SOURCE:
        raise _packet_error("source is not lifecycle launch packet.")
    generated_at = _require_text(packet, "generated_at", "packet")
    if parse_datetime(generated_at) is None:
        raise _packet_error("packet.generated_at must be an ISO datetime.")
    status = _require_text(packet, "status", "packet")
    if status not in {LAUNCH_PACKET_READY_STATUS, LAUNCH_PACKET_NO_SENDABLE_STATUS}:
        raise _packet_error("packet.status is not supported.")
    if require_ready and status != LAUNCH_PACKET_READY_STATUS:
        raise _packet_error("packet.status must be ready_for_send.")
    validated_command = _validate_command(
        packet["command"],
        expected_name=command_name,
    )
    expected_parameters = _expected_parameters(
        cohort=cohort,
        limit=limit,
        campaign_group=campaign_group,
        user_id=user_id,
        workspace_id=workspace_id,
        require_campaign_group_allowlist=require_campaign_group_allowlist,
    )
    _validate_send_parameters(
        packet["send_parameters"],
        expected=expected_parameters,
    )
    _validate_preview(
        packet["preview"],
        approval_manifest_path=approval_manifest_path,
        approval_record_path=approval_record_path,
        approval_manifest_sha256=approval_manifest_sha256,
        approval_record_sha256=approval_record_sha256,
    )
    sendable_candidate_count = _validate_dry_run(
        packet["dry_run"],
        dry_run_report_path=dry_run_report_path,
        dry_run_report_review_record_path=dry_run_report_review_record_path,
        dry_run_report_sha256=dry_run_report_sha256,
        dry_run_report_review_record_sha256=dry_run_report_review_record_sha256,
        approval_manifest_sha256=approval_manifest_sha256,
        approval_record_sha256=approval_record_sha256,
    )
    if require_ready and sendable_candidate_count < 1:
        raise _packet_error("dry_run.sendable_candidate_count must be positive.")
    _validate_checks(packet["checks"])
    return LifecycleLaunchPacket(
        path=str(packet_path),
        sha256=sha256(raw.encode("utf-8")).hexdigest(),
        generated_at=generated_at,
        status=status,
        command_name=validated_command,
        sendable_candidate_count=sendable_candidate_count,
    )


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
