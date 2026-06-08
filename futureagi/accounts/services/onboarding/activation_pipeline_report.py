from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count

from accounts.models import (
    NotificationDeliveryLog,
    OnboardingActivationFactReceipt,
    OnboardingActivationFactReceiptRejection,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendLog,
    OnboardingPaidCloudActivationExportLog,
)
from accounts.services.onboarding.activation_fact_lifecycle import UNPAID_PLAN_TIERS

ACTIVATION_PIPELINE_REPORT_SCHEMA_VERSION = (
    "onboarding-activation-pipeline-report-2026-05-31.v1"
)


@dataclass(frozen=True)
class ActivationPipelineReportResult:
    since: object | None
    until: object | None
    lifecycle_evaluations: dict
    activation_exports: dict
    activation_receipts: dict
    lifecycle_sends: dict
    notification_deliveries: dict
    risks: dict

    def to_payload(self):
        return {
            "schema_version": ACTIVATION_PIPELINE_REPORT_SCHEMA_VERSION,
            "source": "onboarding_activation_pipeline_report",
            "since": _isoformat(self.since),
            "until": _isoformat(self.until),
            "status": self.risks["status"],
            "lifecycle_evaluations": self.lifecycle_evaluations,
            "activation_exports": self.activation_exports,
            "activation_receipts": self.activation_receipts,
            "lifecycle_sends": self.lifecycle_sends,
            "notification_deliveries": self.notification_deliveries,
            "risks": self.risks,
        }


def _isoformat(value):
    return value.isoformat() if value else None


def _window(queryset, *, field, since=None, until=None):
    if since:
        queryset = queryset.filter(**{f"{field}__gte": since})
    if until:
        queryset = queryset.filter(**{f"{field}__lt": until})
    return queryset


def _count_by(queryset, field):
    counts = queryset.values(field).annotate(count=Count("id")).order_by(field)
    return {
        str(row[field] if row[field] not in (None, "") else "none"): row["count"]
        for row in counts
    }


def _receipt_import_backlog_query():
    return (
        OnboardingActivationFactReceipt.no_workspace_objects.filter(
            deployment_mode="cloud",
            email_eligible=True,
            email_suppressed=False,
            lifecycle_status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
            lifecycle_evaluation__isnull=True,
            user_id_value__isnull=False,
        )
        .exclude(lifecycle_campaign_key="")
        .exclude(plan_tier__in=UNPAID_PLAN_TIERS)
    )


def _lifecycle_evaluation_section(*, since=None, until=None):
    queryset = _window(
        OnboardingLifecycleEvaluationLog.no_workspace_objects.all(),
        field="evaluated_at",
        since=since,
        until=until,
    )
    return {
        "evaluated_count": queryset.count(),
        "status_counts": _count_by(queryset, "status"),
        "campaign_group_counts": _count_by(queryset, "campaign_group"),
        "suppression_counts": _count_by(queryset, "suppression_reason"),
        "receipt_backed_count": queryset.filter(source_receipt__isnull=False).count(),
        "error_count": queryset.filter(
            status=OnboardingLifecycleEvaluationLog.STATUS_ERROR,
        ).count(),
    }


def _activation_export_section(*, since=None, until=None):
    queryset = _window(
        OnboardingPaidCloudActivationExportLog.no_workspace_objects.all(),
        field="evaluated_at",
        since=since,
        until=until,
    )
    all_exports = OnboardingPaidCloudActivationExportLog.no_workspace_objects.all()
    return {
        "evaluated_count": queryset.count(),
        "status_counts": _count_by(queryset, "status"),
        "suppression_counts": _count_by(queryset, "suppression_reason"),
        "ready_count": queryset.filter(
            status=OnboardingPaidCloudActivationExportLog.STATUS_READY,
        ).count(),
        "exported_count": queryset.filter(
            status=OnboardingPaidCloudActivationExportLog.STATUS_EXPORTED,
        ).count(),
        "failed_count": queryset.filter(
            status=OnboardingPaidCloudActivationExportLog.STATUS_FAILED,
        ).count(),
        "current_ready_backlog_count": all_exports.filter(
            status=OnboardingPaidCloudActivationExportLog.STATUS_READY,
        ).count(),
        "current_failed_backlog_count": all_exports.filter(
            status=OnboardingPaidCloudActivationExportLog.STATUS_FAILED,
        ).count(),
    }


def _activation_receipt_section(*, since=None, until=None):
    receipts = _window(
        OnboardingActivationFactReceipt.no_workspace_objects.all(),
        field="received_at",
        since=since,
        until=until,
    )
    rejections = _window(
        OnboardingActivationFactReceiptRejection.no_workspace_objects.all(),
        field="received_at",
        since=since,
        until=until,
    )
    return {
        "accepted_count": receipts.count(),
        "rejected_count": rejections.count(),
        "rejection_reason_counts": _count_by(rejections, "reason"),
        "deployment_mode_counts": _count_by(receipts, "deployment_mode"),
        "region_counts": _count_by(receipts, "deployment_region"),
        "plan_tier_counts": _count_by(receipts, "plan_tier"),
        "email_eligible_count": receipts.filter(email_eligible=True).count(),
        "imported_count": receipts.filter(lifecycle_evaluation__isnull=False).count(),
        "current_import_backlog_count": _receipt_import_backlog_query().count(),
    }


def _lifecycle_send_section(*, since=None, until=None):
    queryset = _window(
        OnboardingLifecycleSendLog.no_workspace_objects.all(),
        field="created_at",
        since=since,
        until=until,
    )
    return {
        "send_log_count": queryset.count(),
        "status_counts": _count_by(queryset, "status"),
        "campaign_group_counts": _count_by(queryset, "campaign_group"),
        "sent_count": queryset.filter(
            status=OnboardingLifecycleSendLog.STATUS_SENT,
        ).count(),
        "failed_count": queryset.filter(
            status=OnboardingLifecycleSendLog.STATUS_FAILED,
        ).count(),
        "suppressed_count": queryset.filter(
            status=OnboardingLifecycleSendLog.STATUS_SUPPRESSED,
        ).count(),
    }


def _notification_delivery_section(*, since=None, until=None):
    queryset = _window(
        NotificationDeliveryLog.no_workspace_objects.all(),
        field="created_at",
        since=since,
        until=until,
    )
    return {
        "delivery_log_count": queryset.count(),
        "status_counts": _count_by(queryset, "status"),
        "family_counts": _count_by(queryset, "family"),
        "channel_counts": _count_by(queryset, "channel"),
        "failed_count": queryset.filter(
            status=NotificationDeliveryLog.STATUS_FAILED,
        ).count(),
        "suppressed_count": queryset.filter(
            status=NotificationDeliveryLog.STATUS_SUPPRESSED,
        ).count(),
    }


def _risk_section(
    *,
    lifecycle_evaluations,
    activation_exports,
    activation_receipts,
    lifecycle_sends,
    notification_deliveries,
):
    risk_counts = {
        "export_ready_backlog_count": activation_exports["current_ready_backlog_count"],
        "export_failed_backlog_count": activation_exports[
            "current_failed_backlog_count"
        ],
        "receipt_import_backlog_count": activation_receipts[
            "current_import_backlog_count"
        ],
        "receipt_rejection_count": activation_receipts["rejected_count"],
        "lifecycle_error_count": lifecycle_evaluations["error_count"],
        "send_failed_count": lifecycle_sends["failed_count"],
        "notification_failed_count": notification_deliveries["failed_count"],
    }
    status = (
        "attention_required"
        if any(count > 0 for count in risk_counts.values())
        else "healthy"
    )
    return {
        "status": status,
        **risk_counts,
    }


def activation_pipeline_report(*, since=None, until=None):
    lifecycle_evaluations = _lifecycle_evaluation_section(
        since=since,
        until=until,
    )
    activation_exports = _activation_export_section(since=since, until=until)
    activation_receipts = _activation_receipt_section(since=since, until=until)
    lifecycle_sends = _lifecycle_send_section(since=since, until=until)
    notification_deliveries = _notification_delivery_section(
        since=since,
        until=until,
    )
    risks = _risk_section(
        lifecycle_evaluations=lifecycle_evaluations,
        activation_exports=activation_exports,
        activation_receipts=activation_receipts,
        lifecycle_sends=lifecycle_sends,
        notification_deliveries=notification_deliveries,
    )
    return ActivationPipelineReportResult(
        since=since,
        until=until,
        lifecycle_evaluations=lifecycle_evaluations,
        activation_exports=activation_exports,
        activation_receipts=activation_receipts,
        lifecycle_sends=lifecycle_sends,
        notification_deliveries=notification_deliveries,
        risks=risks,
    )
