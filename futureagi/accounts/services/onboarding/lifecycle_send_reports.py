from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.dateparse import parse_datetime

DRY_RUN_REPORT_SCHEMA_VERSION = "onboarding-lifecycle-send-dry-run-report-2026-05-29.v1"
DRY_RUN_REPORT_SOURCE = "onboarding_lifecycle_send_dry_run_report"
DRY_RUN_REPORT_REVIEW_RECORD_SCHEMA_VERSION = (
    "onboarding-lifecycle-send-dry-run-report-review-record-2026-05-29.v1"
)
DRY_RUN_REPORT_REVIEW_RECORD_SOURCE = "lifecycle_send_dry_run_report_review_record"
DRY_RUN_REPORT_METADATA_KEY = "dry_run_report"
DRY_RUN_REPORT_MISSING_REASON = "dry_run_report_missing"


@dataclass(frozen=True)
class LifecycleSendDryRunReport:
    path: str
    sha256: str
    generated_at: str
    command_name: str
    cohort: str
    limit: int
    campaign_group: str | None
    user_id: str | None
    workspace_id: str | None
    require_campaign_group_allowlist: bool
    approval_manifest_sha256: str | None
    approval_record_sha256: str | None
    evaluated: int
    status_counts: dict
    suppression_counts: dict
    candidate_count: int
    sendable_evaluation_log_ids: tuple[str, ...]

    def has_sendable_candidate(self, evaluation_log_id):
        return str(evaluation_log_id) in self.sendable_evaluation_log_ids

    def metadata_for_send(self):
        return {
            "path": self.path,
            "sha256": self.sha256,
            "generated_at": self.generated_at,
            "command": self.command_name,
            "cohort": self.cohort,
            "limit": self.limit,
            "campaign_group": self.campaign_group,
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "require_campaign_group_allowlist": (self.require_campaign_group_allowlist),
            "approval_manifest_sha256": self.approval_manifest_sha256,
            "approval_record_sha256": self.approval_record_sha256,
            "evaluated": self.evaluated,
            "candidate_count": self.candidate_count,
            "status_counts": self.status_counts,
            "suppression_counts": self.suppression_counts,
        }


@dataclass(frozen=True)
class LifecycleSendDryRunReportReview:
    report: LifecycleSendDryRunReport
    review_record_path: str
    review_record_sha256: str
    reviewed_by: str
    reviewed_at: str

    def has_sendable_candidate(self, evaluation_log_id):
        return self.report.has_sendable_candidate(evaluation_log_id)

    def metadata_for_send(self):
        return {
            **self.report.metadata_for_send(),
            "review_record_path": self.review_record_path,
            "review_record_sha256": self.review_record_sha256,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
        }


@dataclass(frozen=True)
class LifecycleSendDryRunReportReviewRecordResult:
    output_path: str
    report_sha256: str
    review_record_sha256: str
    reviewed_by: str
    reviewed_at: str
    candidate_count: int

    def to_payload(self):
        return {
            "output_path": self.output_path,
            "report_sha256": self.report_sha256,
            "review_record_sha256": self.review_record_sha256,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "candidate_count": self.candidate_count,
        }


def _report_error(message):
    return ImproperlyConfigured(f"Invalid lifecycle send dry-run report: {message}")


def _require_keys(mapping, expected, path):
    if set(mapping) != expected:
        missing = sorted(expected - set(mapping))
        extra = sorted(set(mapping) - expected)
        parts = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra:
            parts.append(f"unexpected {', '.join(extra)}")
        raise _report_error(f"{path} has invalid fields ({'; '.join(parts)}).")


def _require_text(mapping, key, path, *, allow_none=False):
    value = mapping.get(key)
    if allow_none and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _report_error(f"{path}.{key} must be a non-empty string.")
    return value


def _require_sha(value, path, *, allow_none=False):
    if allow_none and value is None:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise _report_error(f"{path} must be a SHA-256 hex digest.")
    try:
        int(value, 16)
    except ValueError as exc:
        raise _report_error(f"{path} must be a SHA-256 hex digest.") from exc
    return value


def _require_nonnegative_int(mapping, key, path):
    value = mapping.get(key)
    if not isinstance(value, int) or value < 0:
        raise _report_error(f"{path}.{key} must be a non-negative integer.")
    return value


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


def _validate_parameters(parameters, *, expected=None):
    if not isinstance(parameters, dict):
        raise _report_error("parameters must be a mapping.")
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
        "parameters",
    )
    report_limit = _require_nonnegative_int(parameters, "limit", "parameters")
    if not isinstance(parameters["cohort"], str) or not parameters["cohort"].strip():
        raise _report_error("parameters.cohort must be a non-empty string.")
    for key in ("campaign_group", "user_id", "workspace_id"):
        value = parameters[key]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise _report_error(f"parameters.{key} must be a non-empty string or null.")
    if not isinstance(parameters["require_campaign_group_allowlist"], bool):
        raise _report_error(
            "parameters.require_campaign_group_allowlist must be a bool."
        )
    if expected:
        if report_limit != expected["limit"]:
            raise _report_error("parameters.limit does not match the send command.")
        for key, expected_value in expected.items():
            if key == "limit":
                continue
            if parameters[key] != expected_value:
                raise _report_error(
                    f"parameters.{key} does not match the send command."
                )
    return parameters


def _validate_approval(
    approval,
    *,
    expected_manifest_sha256=None,
    expected_record_sha256=None,
):
    if not isinstance(approval, dict):
        raise _report_error("approval must be a mapping.")
    _require_keys(approval, {"manifest_sha256", "record_sha256"}, "approval")
    manifest_sha256 = _require_sha(
        approval["manifest_sha256"],
        "approval.manifest_sha256",
        allow_none=True,
    )
    record_sha256 = _require_sha(
        approval["record_sha256"],
        "approval.record_sha256",
        allow_none=True,
    )
    if (
        expected_manifest_sha256 is not None
        and manifest_sha256 != expected_manifest_sha256
    ):
        raise _report_error("approval.manifest_sha256 does not match the send command.")
    if expected_record_sha256 is not None and record_sha256 != expected_record_sha256:
        raise _report_error("approval.record_sha256 does not match the send command.")
    return manifest_sha256, record_sha256


def _validate_summary(summary):
    if not isinstance(summary, dict):
        raise _report_error("summary must be a mapping.")
    _require_keys(
        summary,
        {
            "run_id",
            "evaluated",
            "sent",
            "suppressed",
            "failed",
            "skipped",
            "status_counts",
            "suppression_counts",
        },
        "summary",
    )
    _require_text(summary, "run_id", "summary")
    evaluated = _require_nonnegative_int(summary, "evaluated", "summary")
    for key in ("sent", "suppressed", "failed", "skipped"):
        if _require_nonnegative_int(summary, key, "summary") != 0:
            raise _report_error(f"summary.{key} must be zero for dry-run reports.")
    status_counts = summary["status_counts"]
    suppression_counts = summary["suppression_counts"]
    if not isinstance(status_counts, dict):
        raise _report_error("summary.status_counts must be a mapping.")
    if not isinstance(suppression_counts, dict):
        raise _report_error("summary.suppression_counts must be a mapping.")
    for key, value in {**status_counts, **suppression_counts}.items():
        if not isinstance(key, str) or not isinstance(value, int) or value < 0:
            raise _report_error("summary count mappings must contain integer counts.")
    return evaluated, status_counts, suppression_counts


def _validate_candidate(candidate, index, *, approval_record_sha256=None):
    if not isinstance(candidate, dict):
        raise _report_error(f"candidates.{index} must be a mapping.")
    _require_keys(
        candidate,
        {
            "evaluation_log_id",
            "user_id",
            "organization_id",
            "workspace_id",
            "campaign_key",
            "campaign_group",
            "template_key",
            "template_version",
            "primary_path",
            "activation_stage",
            "recommended_action_id",
            "target_action_id",
            "target_success_event",
            "target_route",
            "status",
            "suppression_reason",
            "approval_status",
            "eligible_at",
            "evaluated_at",
        },
        f"candidates.{index}",
    )
    for key in (
        "evaluation_log_id",
        "user_id",
        "organization_id",
        "workspace_id",
        "campaign_key",
        "campaign_group",
        "template_key",
        "template_version",
        "primary_path",
        "activation_stage",
        "target_action_id",
        "target_success_event",
        "target_route",
    ):
        _require_text(candidate, key, f"candidates.{index}")
    if candidate["recommended_action_id"] is not None:
        _require_text(candidate, "recommended_action_id", f"candidates.{index}")
    status = candidate["status"]
    if status not in {"would_send", "would_suppress"}:
        raise _report_error(f"candidates.{index}.status is not supported.")
    suppression_reason = candidate["suppression_reason"]
    if status == "would_send" and suppression_reason is not None:
        raise _report_error(
            f"candidates.{index}.suppression_reason must be null for would_send."
        )
    if status == "would_suppress" and (
        not isinstance(suppression_reason, str) or not suppression_reason.strip()
    ):
        raise _report_error(
            f"candidates.{index}.suppression_reason must explain suppression."
        )
    approval_status = candidate["approval_status"]
    if approval_status not in {"not_supplied", "approved", "missing"}:
        raise _report_error(f"candidates.{index}.approval_status is not supported.")
    if (
        approval_record_sha256
        and status == "would_send"
        and approval_status != "approved"
    ):
        raise _report_error(
            f"candidates.{index}.approval_status must be approved for would_send."
        )
    for key in ("eligible_at", "evaluated_at"):
        value = candidate[key]
        if value is not None and (
            not isinstance(value, str) or parse_datetime(value) is None
        ):
            raise _report_error(f"candidates.{index}.{key} must be an ISO datetime.")
    return candidate["evaluation_log_id"], status


def load_lifecycle_send_dry_run_report(
    path,
    *,
    command_name=None,
    cohort=None,
    limit=None,
    campaign_group=None,
    user_id=None,
    workspace_id=None,
    require_campaign_group_allowlist=False,
    approval_manifest_sha256=None,
    approval_record_sha256=None,
):
    report_path = Path(path)
    try:
        raw = report_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _report_error(f"{report_path} could not be read.") from exc
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _report_error(f"{report_path} is not valid JSON.") from exc
    if not isinstance(report, dict):
        raise _report_error("report root must be a mapping.")
    _require_keys(
        report,
        {
            "schema_version",
            "source",
            "generated_at",
            "command",
            "parameters",
            "approval",
            "summary",
            "candidates",
        },
        "report",
    )
    if report["schema_version"] != DRY_RUN_REPORT_SCHEMA_VERSION:
        raise _report_error("schema_version is not supported.")
    if report["source"] != DRY_RUN_REPORT_SOURCE:
        raise _report_error("source is not lifecycle send dry-run report.")
    generated_at = _require_text(report, "generated_at", "report")
    if parse_datetime(generated_at) is None:
        raise _report_error("report.generated_at must be an ISO datetime.")
    if command_name is not None and report["command"] != command_name:
        raise _report_error("command does not match the send command.")
    expected_parameters = None
    if command_name is not None:
        if cohort is None or limit is None:
            raise _report_error("send command validation requires cohort and limit.")
        expected_parameters = _expected_parameters(
            cohort=cohort,
            limit=limit,
            campaign_group=campaign_group,
            user_id=user_id,
            workspace_id=workspace_id,
            require_campaign_group_allowlist=require_campaign_group_allowlist,
        )
    parameters = _validate_parameters(
        report["parameters"],
        expected=expected_parameters,
    )
    manifest_sha256, record_sha256 = _validate_approval(
        report["approval"],
        expected_manifest_sha256=approval_manifest_sha256,
        expected_record_sha256=approval_record_sha256,
    )
    evaluated, status_counts, suppression_counts = _validate_summary(report["summary"])
    candidates = report["candidates"]
    if not isinstance(candidates, list):
        raise _report_error("candidates must be a list.")
    if len(candidates) != evaluated:
        raise _report_error("candidate count does not match summary.evaluated.")
    sendable_evaluation_log_ids = []
    for index, candidate in enumerate(candidates):
        evaluation_log_id, status = _validate_candidate(
            candidate,
            index,
            approval_record_sha256=record_sha256,
        )
        if status == "would_send":
            sendable_evaluation_log_ids.append(evaluation_log_id)
    return LifecycleSendDryRunReport(
        path=str(report_path),
        sha256=sha256(raw.encode("utf-8")).hexdigest(),
        generated_at=generated_at,
        command_name=report["command"],
        cohort=parameters["cohort"],
        limit=parameters["limit"],
        campaign_group=parameters["campaign_group"],
        user_id=parameters["user_id"],
        workspace_id=parameters["workspace_id"],
        require_campaign_group_allowlist=parameters["require_campaign_group_allowlist"],
        approval_manifest_sha256=manifest_sha256,
        approval_record_sha256=record_sha256,
        evaluated=evaluated,
        status_counts=status_counts,
        suppression_counts=suppression_counts,
        candidate_count=len(candidates),
        sendable_evaluation_log_ids=tuple(sendable_evaluation_log_ids),
    )


def _review_record_error(message):
    return ImproperlyConfigured(
        f"Invalid lifecycle send dry-run report review record: {message}"
    )


def _review_record_payload(
    report,
    *,
    reviewed_by,
    reviewed_at,
    note="",
):
    if not isinstance(reviewed_by, str) or not reviewed_by.strip():
        raise _review_record_error("reviewed_by must be a non-empty string.")
    if not isinstance(note, str):
        raise _review_record_error("note must be a string.")
    return {
        "schema_version": DRY_RUN_REPORT_REVIEW_RECORD_SCHEMA_VERSION,
        "source": DRY_RUN_REPORT_REVIEW_RECORD_SOURCE,
        "decision": "approved",
        "reviewed_by": reviewed_by.strip(),
        "reviewed_at": reviewed_at.isoformat(),
        "report_sha256": report.sha256,
        "report_generated_at": report.generated_at,
        "command": report.command_name,
        "parameters": _expected_parameters(
            cohort=report.cohort,
            limit=report.limit,
            campaign_group=report.campaign_group,
            user_id=report.user_id,
            workspace_id=report.workspace_id,
            require_campaign_group_allowlist=(report.require_campaign_group_allowlist),
        ),
        "approval": {
            "manifest_sha256": report.approval_manifest_sha256,
            "record_sha256": report.approval_record_sha256,
        },
        "evaluated": report.evaluated,
        "candidate_count": report.candidate_count,
        "sendable_candidate_count": len(report.sendable_evaluation_log_ids),
        "note": note,
    }


def write_lifecycle_send_dry_run_report_review_record(
    *,
    report_path,
    output_path,
    reviewed_by,
    reviewed_at=None,
    note="",
    force=False,
):
    report = load_lifecycle_send_dry_run_report(report_path)
    output = Path(output_path)
    if output.exists() and not force:
        raise _review_record_error(
            f"{output} already exists. Use --force to overwrite."
        )
    reviewed_at = reviewed_at or timezone.now()
    record = _review_record_payload(
        report,
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        note=note,
    )
    raw = json.dumps(record, indent=2, sort_keys=True) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(raw, encoding="utf-8")
    return LifecycleSendDryRunReportReviewRecordResult(
        output_path=str(output),
        report_sha256=report.sha256,
        review_record_sha256=sha256(raw.encode("utf-8")).hexdigest(),
        reviewed_by=record["reviewed_by"],
        reviewed_at=record["reviewed_at"],
        candidate_count=report.candidate_count,
    )


def _load_review_record(path, report):
    record_path = Path(path)
    try:
        raw = record_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _review_record_error(f"{record_path} could not be read.") from exc
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _review_record_error(f"{record_path} is not valid JSON.") from exc
    if not isinstance(record, dict):
        raise _review_record_error("record root must be a mapping.")
    _require_keys(
        record,
        {
            "schema_version",
            "source",
            "decision",
            "reviewed_by",
            "reviewed_at",
            "report_sha256",
            "report_generated_at",
            "command",
            "parameters",
            "approval",
            "evaluated",
            "candidate_count",
            "sendable_candidate_count",
            "note",
        },
        "record",
    )
    if record["schema_version"] != DRY_RUN_REPORT_REVIEW_RECORD_SCHEMA_VERSION:
        raise _review_record_error("schema_version is not supported.")
    if record["source"] != DRY_RUN_REPORT_REVIEW_RECORD_SOURCE:
        raise _review_record_error("source is not lifecycle dry-run report review.")
    if record["decision"] != "approved":
        raise _review_record_error("decision must be approved.")
    reviewed_by = _require_text(record, "reviewed_by", "record")
    reviewed_at = _require_text(record, "reviewed_at", "record")
    if parse_datetime(reviewed_at) is None:
        raise _review_record_error("record.reviewed_at must be an ISO datetime.")
    if record["report_sha256"] != report.sha256:
        raise _review_record_error("report_sha256 does not match the report.")
    _require_sha(record["report_sha256"], "record.report_sha256")
    if record["report_generated_at"] != report.generated_at:
        raise _review_record_error("report_generated_at does not match the report.")
    if record["command"] != report.command_name:
        raise _review_record_error("command does not match the report.")
    expected_parameters = _expected_parameters(
        cohort=report.cohort,
        limit=report.limit,
        campaign_group=report.campaign_group,
        user_id=report.user_id,
        workspace_id=report.workspace_id,
        require_campaign_group_allowlist=report.require_campaign_group_allowlist,
    )
    if record["parameters"] != expected_parameters:
        raise _review_record_error("parameters do not match the report.")
    expected_approval = {
        "manifest_sha256": report.approval_manifest_sha256,
        "record_sha256": report.approval_record_sha256,
    }
    if record["approval"] != expected_approval:
        raise _review_record_error("approval metadata does not match the report.")
    if record["evaluated"] != report.evaluated:
        raise _review_record_error("evaluated does not match the report.")
    if record["candidate_count"] != report.candidate_count:
        raise _review_record_error("candidate_count does not match the report.")
    if record["sendable_candidate_count"] != len(report.sendable_evaluation_log_ids):
        raise _review_record_error(
            "sendable_candidate_count does not match the report."
        )
    if not isinstance(record["note"], str):
        raise _review_record_error("record.note must be a string.")
    return {
        "path": str(record_path),
        "sha256": sha256(raw.encode("utf-8")).hexdigest(),
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
    }


def load_lifecycle_send_dry_run_report_review(
    *,
    report_path,
    review_record_path,
    **report_kwargs,
):
    report = load_lifecycle_send_dry_run_report(report_path, **report_kwargs)
    record = _load_review_record(review_record_path, report)
    return LifecycleSendDryRunReportReview(
        report=report,
        review_record_path=record["path"],
        review_record_sha256=record["sha256"],
        reviewed_by=record["reviewed_by"],
        reviewed_at=record["reviewed_at"],
    )


def lifecycle_send_dry_run_report_payload(
    *,
    command_name,
    result,
    cohort,
    limit,
    campaign_group=None,
    user_id=None,
    workspace_id=None,
    require_campaign_group_allowlist=False,
):
    payload = result.to_payload()
    if not payload["dry_run"]:
        raise ImproperlyConfigured("Lifecycle send reports require dry-run mode.")
    return {
        "schema_version": DRY_RUN_REPORT_SCHEMA_VERSION,
        "source": DRY_RUN_REPORT_SOURCE,
        "generated_at": payload["generated_at"],
        "command": command_name,
        "parameters": {
            "cohort": cohort,
            "limit": limit,
            "campaign_group": campaign_group,
            "user_id": str(user_id) if user_id else None,
            "workspace_id": str(workspace_id) if workspace_id else None,
            "require_campaign_group_allowlist": bool(require_campaign_group_allowlist),
        },
        "approval": {
            "manifest_sha256": payload["approval_manifest_sha256"],
            "record_sha256": payload["approval_record_sha256"],
        },
        "summary": {
            "run_id": payload["run_id"],
            "evaluated": payload["evaluated"],
            "sent": payload["sent"],
            "suppressed": payload["suppressed"],
            "failed": payload["failed"],
            "skipped": payload["skipped"],
            "status_counts": payload["status_counts"],
            "suppression_counts": payload["suppression_counts"],
        },
        "candidates": payload["candidates"],
    }


def write_lifecycle_send_dry_run_report(
    *,
    output_path,
    force=False,
    **payload_kwargs,
):
    path = Path(output_path)
    if path.exists() and not force:
        raise ImproperlyConfigured(
            f"{path} already exists. Use --report-force to overwrite."
        )
    report = lifecycle_send_dry_run_report_payload(**payload_kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(path)
