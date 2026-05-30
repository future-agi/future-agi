from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from accounts.models import (
    NotificationDeliveryLog,
    NotificationPreference,
    OnboardingLifecyclePreference,
    OnboardingLifecycleSendLog,
)
from accounts.services.onboarding.lifecycle_launch_packets import (
    LAUNCH_PACKET_METADATA_KEY,
)
from accounts.services.onboarding.lifecycle_preview_approval import (
    APPROVAL_METADATA_KEY,
)
from accounts.services.onboarding.lifecycle_send_reports import (
    DRY_RUN_REPORT_METADATA_KEY,
)

SEND_EVIDENCE_REPORT_SCHEMA_VERSION = (
    "onboarding-lifecycle-send-evidence-report-2026-05-30.v1"
)
SEND_EVIDENCE_REPORT_SOURCE = "onboarding_lifecycle_send_evidence_report"
SEND_EVIDENCE_REPORT_PASSED_STATUS = "passed"
SEND_EVIDENCE_REPORT_INCOMPLETE_STATUS = "incomplete"

REQUIREMENT_KEYS = (
    "launch_packet",
    "provider_accepted",
    "email_delivery",
    "click",
    "completion",
    "unsubscribe",
    "snooze",
    "frequency_cap",
    "completion_suppression",
)


@dataclass(frozen=True)
class LifecycleSendEvidenceReportResult:
    output_path: str
    report_sha256: str
    status: str
    send_log_count: int
    missing_requirements: tuple[str, ...]

    def to_payload(self):
        return {
            "output_path": self.output_path,
            "report_sha256": self.report_sha256,
            "status": self.status,
            "send_log_count": self.send_log_count,
            "missing_requirements": list(self.missing_requirements),
        }


@dataclass(frozen=True)
class LifecycleSendEvidenceReport:
    path: str
    sha256: str
    generated_at: str
    status: str
    requirements: dict
    missing_requirements: tuple[str, ...]
    aggregate_evidence: dict
    send_log_count: int
    send_logs: tuple[dict, ...]


def _report_error(message):
    return ImproperlyConfigured(
        f"Invalid lifecycle send evidence report inputs: {message}"
    )


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


def _require_text(mapping, key, path):
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _report_error(f"{path}.{key} must be a non-empty string.")
    return value


def _require_nonnegative_int(mapping, key, path):
    value = mapping.get(key)
    if not isinstance(value, int) or value < 0:
        raise _report_error(f"{path}.{key} must be a non-negative integer.")
    return value


def _requirement_flags(**requirements):
    return {key: bool(requirements.get(key, False)) for key in REQUIREMENT_KEYS}


def _safe_metadata_hash(metadata, key):
    value = metadata.get(key)
    if not isinstance(value, dict):
        return None
    sha_value = value.get("sha256")
    if sha_value:
        return sha_value
    if key == APPROVAL_METADATA_KEY:
        return value.get("approval_record_sha256")
    return None


def _delivery_payload(delivery_log):
    return {
        "id": str(delivery_log.id),
        "family": delivery_log.family,
        "channel": delivery_log.channel,
        "status": delivery_log.status,
        "suppressed_reason": delivery_log.suppressed_reason,
        "notification_key": delivery_log.notification_key,
        "stage": delivery_log.stage,
        "route_url": delivery_log.route_url,
        "sent_at": delivery_log.sent_at.isoformat() if delivery_log.sent_at else None,
        "clicked_at": (
            delivery_log.clicked_at.isoformat() if delivery_log.clicked_at else None
        ),
        "completed_at": (
            delivery_log.completed_at.isoformat() if delivery_log.completed_at else None
        ),
    }


def _delivery_logs_for(send_log):
    return tuple(
        NotificationDeliveryLog.no_workspace_objects.filter(
            source_type="onboarding_lifecycle",
            source_id=str(send_log.id),
        ).order_by("channel", "status", "created_at")
    )


def _preference_for(send_log):
    return (
        OnboardingLifecyclePreference.no_workspace_objects.filter(
            user=send_log.user,
            organization=send_log.organization,
            workspace=send_log.workspace,
        ).first()
        or OnboardingLifecyclePreference.no_workspace_objects.filter(
            user=send_log.user,
            organization=send_log.organization,
            workspace__isnull=True,
        ).first()
    )


def _evidence_flags(send_log, delivery_logs, preference):
    metadata = send_log.metadata if isinstance(send_log.metadata, dict) else {}
    email_delivery = any(
        delivery.channel == NotificationPreference.CHANNEL_EMAIL
        for delivery in delivery_logs
        if delivery.status == NotificationDeliveryLog.STATUS_SENT
    )
    frequency_cap = (
        send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
        and send_log.suppression_reason == "frequency_capped"
    ) or any(
        delivery.status == NotificationDeliveryLog.STATUS_SUPPRESSED
        and delivery.suppressed_reason == "frequency_capped"
        for delivery in delivery_logs
    )
    completion_suppression = (
        send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED
        and send_log.suppression_reason == "target_success_event_completed"
    ) or any(
        delivery.status == NotificationDeliveryLog.STATUS_SUPPRESSED
        and delivery.suppressed_reason == "target_success_event_completed"
        for delivery in delivery_logs
    )
    return {
        "launch_packet": isinstance(metadata.get(LAUNCH_PACKET_METADATA_KEY), dict),
        "preview_approval": isinstance(metadata.get(APPROVAL_METADATA_KEY), dict),
        "dry_run_report": isinstance(metadata.get(DRY_RUN_REPORT_METADATA_KEY), dict),
        "provider_accepted": (
            send_log.status
            in {
                OnboardingLifecycleSendLog.STATUS_SENT,
                OnboardingLifecycleSendLog.STATUS_CLICKED,
                OnboardingLifecycleSendLog.STATUS_COMPLETED,
            }
            and send_log.provider_status == "accepted"
            and send_log.sent_at is not None
        ),
        "email_delivery": email_delivery,
        "click": send_log.clicked_at is not None
        and send_log.status
        in {
            OnboardingLifecycleSendLog.STATUS_CLICKED,
            OnboardingLifecycleSendLog.STATUS_COMPLETED,
        },
        "completion": send_log.completed_at is not None
        and send_log.status == OnboardingLifecycleSendLog.STATUS_COMPLETED,
        "unsubscribe": bool(
            send_log.unsubscribed_at
            or (preference and preference.unsubscribed_at is not None)
        ),
        "snooze": bool(preference and preference.snoozed_until is not None),
        "frequency_cap": frequency_cap,
        "completion_suppression": completion_suppression,
    }


def _send_log_payload(send_log):
    delivery_logs = _delivery_logs_for(send_log)
    preference = _preference_for(send_log)
    metadata = send_log.metadata if isinstance(send_log.metadata, dict) else {}
    flags = _evidence_flags(send_log, delivery_logs, preference)
    delivery_counts = Counter(
        f"{delivery.channel}:{delivery.status}" for delivery in delivery_logs
    )
    return {
        "send_log_id": str(send_log.id),
        "evaluation_log_id": str(send_log.evaluation_log_id),
        "organization_id": str(send_log.organization_id),
        "workspace_id": str(send_log.workspace_id) if send_log.workspace_id else None,
        "user_id": str(send_log.user_id),
        "campaign_key": send_log.campaign_key,
        "campaign_group": send_log.campaign_group,
        "template_key": send_log.template_key,
        "template_version": send_log.template_version,
        "primary_path": send_log.primary_path,
        "activation_stage": send_log.activation_stage,
        "recommended_action_id": send_log.recommended_action_id,
        "target_success_event": send_log.target_success_event,
        "target_route": send_log.target_route,
        "status": send_log.status,
        "suppression_reason": send_log.suppression_reason,
        "provider_status": send_log.provider_status,
        "queued_at": send_log.queued_at.isoformat() if send_log.queued_at else None,
        "sent_at": send_log.sent_at.isoformat() if send_log.sent_at else None,
        "clicked_at": send_log.clicked_at.isoformat() if send_log.clicked_at else None,
        "completed_at": (
            send_log.completed_at.isoformat() if send_log.completed_at else None
        ),
        "unsubscribed_at": (
            send_log.unsubscribed_at.isoformat() if send_log.unsubscribed_at else None
        ),
        "artifact_hashes": {
            "preview_approval": _safe_metadata_hash(metadata, APPROVAL_METADATA_KEY),
            "dry_run_report": _safe_metadata_hash(
                metadata,
                DRY_RUN_REPORT_METADATA_KEY,
            ),
            "launch_packet": _safe_metadata_hash(metadata, LAUNCH_PACKET_METADATA_KEY),
        },
        "evidence": flags,
        "delivery_counts": dict(delivery_counts),
        "delivery_logs": [_delivery_payload(log) for log in delivery_logs],
        "preference": {
            "exists": preference is not None,
            "onboarding_enabled": (
                preference.onboarding_enabled if preference else None
            ),
            "unsubscribed_at": (
                preference.unsubscribed_at.isoformat()
                if preference and preference.unsubscribed_at
                else None
            ),
            "snoozed_until": (
                preference.snoozed_until.isoformat()
                if preference and preference.snoozed_until
                else None
            ),
        },
    }


def _load_send_logs(send_log_ids):
    if not send_log_ids:
        raise _report_error("at least one --send-log-id is required.")
    send_logs = []
    seen = set()
    for raw_id in send_log_ids:
        raw_id = str(raw_id)
        if raw_id in seen:
            raise _report_error(f"send log {raw_id} is duplicated.")
        seen.add(raw_id)
        send_log = (
            OnboardingLifecycleSendLog.no_workspace_objects.select_related(
                "evaluation_log",
                "user",
                "organization",
                "workspace",
            )
            .filter(id=raw_id)
            .first()
        )
        if not send_log:
            raise _report_error(f"send log {raw_id} was not found.")
        send_logs.append(send_log)
    return tuple(send_logs)


def lifecycle_send_evidence_report_payload(
    *,
    send_log_ids,
    generated_at=None,
    **requirements,
):
    generated_at = generated_at or timezone.now()
    requirement_flags = _requirement_flags(**requirements)
    send_log_payloads = [
        _send_log_payload(send_log) for send_log in _load_send_logs(send_log_ids)
    ]
    aggregate_evidence = {
        key: any(payload["evidence"].get(key) for payload in send_log_payloads)
        for key in REQUIREMENT_KEYS
    }
    missing_requirements = [
        key
        for key, required in requirement_flags.items()
        if required and not aggregate_evidence.get(key)
    ]
    return {
        "schema_version": SEND_EVIDENCE_REPORT_SCHEMA_VERSION,
        "source": SEND_EVIDENCE_REPORT_SOURCE,
        "generated_at": generated_at.isoformat(),
        "status": (
            SEND_EVIDENCE_REPORT_PASSED_STATUS
            if not missing_requirements
            else SEND_EVIDENCE_REPORT_INCOMPLETE_STATUS
        ),
        "requirements": requirement_flags,
        "missing_requirements": missing_requirements,
        "aggregate_evidence": aggregate_evidence,
        "send_log_count": len(send_log_payloads),
        "send_logs": send_log_payloads,
    }


def write_lifecycle_send_evidence_report(
    *,
    output_path,
    force=False,
    **payload_kwargs,
):
    path = Path(output_path)
    if path.exists() and not force:
        raise _report_error(f"{path} already exists. Use --force to overwrite.")
    report = lifecycle_send_evidence_report_payload(**payload_kwargs)
    raw = json.dumps(report, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return LifecycleSendEvidenceReportResult(
        output_path=str(path),
        report_sha256=sha256(raw.encode("utf-8")).hexdigest(),
        status=report["status"],
        send_log_count=report["send_log_count"],
        missing_requirements=tuple(report["missing_requirements"]),
    )


def load_lifecycle_send_evidence_report(path, *, require_passed=False):
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
            "status",
            "requirements",
            "missing_requirements",
            "aggregate_evidence",
            "send_log_count",
            "send_logs",
        },
        "report",
    )
    if report["schema_version"] != SEND_EVIDENCE_REPORT_SCHEMA_VERSION:
        raise _report_error("schema_version is not supported.")
    if report["source"] != SEND_EVIDENCE_REPORT_SOURCE:
        raise _report_error("source is not lifecycle send evidence report.")
    generated_at = _require_text(report, "generated_at", "report")
    status = _require_text(report, "status", "report")
    if status not in {
        SEND_EVIDENCE_REPORT_PASSED_STATUS,
        SEND_EVIDENCE_REPORT_INCOMPLETE_STATUS,
    }:
        raise _report_error("report.status is not supported.")
    if require_passed and status != SEND_EVIDENCE_REPORT_PASSED_STATUS:
        raise _report_error("report.status must be passed.")
    requirements = report["requirements"]
    aggregate_evidence = report["aggregate_evidence"]
    missing_requirements = report["missing_requirements"]
    send_logs = report["send_logs"]
    if not isinstance(requirements, dict):
        raise _report_error("requirements must be a mapping.")
    if not isinstance(aggregate_evidence, dict):
        raise _report_error("aggregate_evidence must be a mapping.")
    for key in REQUIREMENT_KEYS:
        if key not in requirements:
            raise _report_error(f"requirements.{key} is required.")
        if key not in aggregate_evidence:
            raise _report_error(f"aggregate_evidence.{key} is required.")
        if not isinstance(requirements[key], bool):
            raise _report_error(f"requirements.{key} must be a bool.")
        if not isinstance(aggregate_evidence[key], bool):
            raise _report_error(f"aggregate_evidence.{key} must be a bool.")
    if not isinstance(missing_requirements, list) or not all(
        isinstance(item, str) and item in REQUIREMENT_KEYS
        for item in missing_requirements
    ):
        raise _report_error("missing_requirements must contain supported keys.")
    send_log_count = _require_nonnegative_int(report, "send_log_count", "report")
    if not isinstance(send_logs, list):
        raise _report_error("send_logs must be a list.")
    if len(send_logs) != send_log_count:
        raise _report_error("send_log_count does not match send_logs.")
    for index, send_log in enumerate(send_logs):
        if not isinstance(send_log, dict):
            raise _report_error(f"send_logs.{index} must be a mapping.")
        for key in ("send_log_id", "campaign_key", "status", "artifact_hashes"):
            if key not in send_log:
                raise _report_error(f"send_logs.{index}.{key} is required.")
        if not isinstance(send_log["artifact_hashes"], dict):
            raise _report_error(f"send_logs.{index}.artifact_hashes must be a mapping.")
    return LifecycleSendEvidenceReport(
        path=str(report_path),
        sha256=sha256(raw.encode("utf-8")).hexdigest(),
        generated_at=generated_at,
        status=status,
        requirements=requirements,
        missing_requirements=tuple(missing_requirements),
        aggregate_evidence=aggregate_evidence,
        send_log_count=send_log_count,
        send_logs=tuple(send_logs),
    )
