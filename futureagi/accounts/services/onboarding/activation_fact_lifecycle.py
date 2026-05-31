import uuid
from collections import Counter
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count, Max, Min, Q

from accounts.models import (
    OnboardingActivationFactReceipt,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendLog,
    Organization,
    User,
    Workspace,
)
from accounts.services.onboarding.lifecycle_eligibility import (
    activation_state_snapshot,
)
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key

RECEIPT_IMPORT_SOURCE = "activation_fact_receipt"
UNPAID_PLAN_TIERS = frozenset({"", "free", "oss", "open_source", "community"})
ACTIVE_SEND_STATUSES = frozenset(
    {
        OnboardingLifecycleSendLog.STATUS_QUEUED,
        OnboardingLifecycleSendLog.STATUS_SENT,
        OnboardingLifecycleSendLog.STATUS_CLICKED,
        OnboardingLifecycleSendLog.STATUS_COMPLETED,
    }
)
LIFECYCLE_COHORT_REPORT_GROUPS = {
    "activation_stage": "activation_stage",
    "campaign_group": "campaign_group",
    "campaign_key": "campaign_key",
    "deployment_region": "source_receipt__deployment_region",
    "plan_tier": "source_receipt__plan_tier",
    "primary_cohort_key": "source_receipt__primary_cohort_key",
    "primary_path": "primary_path",
    "status": "status",
    "target_success_event": "target_success_event",
}


def _isoformat(value):
    return value.isoformat() if value else None


@dataclass(frozen=True)
class ActivationFactLifecycleImportResult:
    run_id: uuid.UUID
    evaluated: int
    imported: int
    status_counts: dict
    campaign_counts: dict
    skip_counts: dict
    errors: list[dict]

    def to_payload(self):
        return {
            "run_id": str(self.run_id),
            "evaluated": self.evaluated,
            "imported": self.imported,
            "status_counts": self.status_counts,
            "campaign_counts": self.campaign_counts,
            "skip_counts": self.skip_counts,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class ActivationFactLifecycleCohortReportResult:
    since: object | None
    until: object | None
    group_by: tuple[str, ...]
    rows: tuple[dict, ...]

    def to_payload(self):
        return {
            "since": _isoformat(self.since),
            "until": _isoformat(self.until),
            "group_by": list(self.group_by),
            "rows": list(self.rows),
        }


def _candidate_receipts(*, limit, campaign_key=None, user_id=None, workspace_id=None):
    queryset = (
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
        .order_by("evaluated_at", "created_at")
    )
    if campaign_key:
        queryset = queryset.filter(lifecycle_campaign_key=campaign_key)
    if user_id:
        queryset = queryset.filter(user_id_value=user_id)
    if workspace_id:
        queryset = queryset.filter(workspace_id_value=workspace_id)
    return queryset[:limit]


def _resolve_entities(receipt):
    organization = Organization.objects.filter(id=receipt.organization_id_value).first()
    if not organization:
        return None, None, None, "missing_organization"

    workspace = (
        Workspace.no_workspace_objects.select_related("organization")
        .filter(id=receipt.workspace_id_value)
        .first()
    )
    if not workspace:
        return organization, None, None, "missing_workspace"
    if workspace.organization_id != organization.id:
        return organization, workspace, None, "workspace_organization_mismatch"

    user = User.objects.filter(id=receipt.user_id_value).first()
    if not user:
        return organization, workspace, None, "missing_user"

    return organization, workspace, user, None


def _receipt_activation_state(receipt, campaign):
    return {
        "stage": receipt.activation_stage,
        "primary_path": receipt.primary_path,
        "is_activated": receipt.is_activated,
        "recommended_action": {"id": campaign.get("target_action_id")},
        "fallback_action": {},
        "sample_project": {},
    }


def _metadata_bool(value):
    return value if isinstance(value, bool) else False


def _receipt_metadata(receipt):
    source_metadata = receipt.metadata if isinstance(receipt.metadata, dict) else {}
    return {
        "source": RECEIPT_IMPORT_SOURCE,
        "send_enabled": False,
        "receipt_lifecycle_send_enabled": _metadata_bool(
            source_metadata.get("lifecycle_send_enabled")
        ),
        "receipt_lifecycle_dry_run_only": _metadata_bool(
            source_metadata.get("lifecycle_dry_run_only")
        ),
        "receipt_lifecycle_target_route": source_metadata.get("lifecycle_target_route"),
        "receipt_lifecycle_target_action_id": source_metadata.get(
            "lifecycle_target_action_id"
        ),
        "receipt_lifecycle_target_success_event": source_metadata.get(
            "lifecycle_target_success_event"
        ),
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


def _write_receipt_lifecycle_log(
    *, receipt, organization, workspace, user, campaign, run_id
):
    activation_state = _receipt_activation_state(receipt, campaign)
    return OnboardingLifecycleEvaluationLog.no_workspace_objects.create(
        source_receipt=receipt,
        run_id=run_id,
        user=user,
        organization=organization,
        workspace=workspace,
        campaign_key=campaign["campaign_key"],
        campaign_group=campaign.get("campaign_group"),
        template_key=campaign.get("template_key"),
        template_version=campaign.get("template_version"),
        activation_stage=receipt.activation_stage,
        primary_path=receipt.primary_path or None,
        recommendation_id=receipt.primary_cohort_key or None,
        target_action_id=campaign.get("target_action_id"),
        target_success_event=campaign.get("target_success_event"),
        target_url=None,
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        eligible_at=receipt.evaluated_at,
        evaluated_at=receipt.evaluated_at,
        activation_state_snapshot=activation_state_snapshot(activation_state),
        registry_snapshot=campaign,
        metadata=_receipt_metadata(receipt),
    )


def _existing_candidate_reason(*, receipt, workspace, user):
    existing_evaluation = (
        OnboardingLifecycleEvaluationLog.no_workspace_objects.filter(
            user=user,
            workspace=workspace,
            campaign_key=receipt.lifecycle_campaign_key,
            status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        )
        .exclude(source_receipt=receipt)
        .exists()
    )
    if existing_evaluation:
        return "existing_lifecycle_candidate"

    existing_send = OnboardingLifecycleSendLog.no_workspace_objects.filter(
        user=user,
        workspace=workspace,
        campaign_key=receipt.lifecycle_campaign_key,
        status__in=ACTIVE_SEND_STATUSES,
    ).exists()
    if existing_send:
        return "existing_lifecycle_send"

    return None


def import_activation_fact_lifecycle_evaluations(
    *,
    limit=100,
    campaign_key=None,
    user_id=None,
    workspace_id=None,
    write=True,
    run_id=None,
):
    run_id = run_id or uuid.uuid4()
    status_counts = Counter()
    campaign_counts = Counter()
    skip_counts = Counter()
    errors = []
    imported = 0
    evaluated = 0

    for receipt in _candidate_receipts(
        limit=limit,
        campaign_key=campaign_key,
        user_id=user_id,
        workspace_id=workspace_id,
    ):
        evaluated += 1
        campaign = lifecycle_campaign_by_key(receipt.lifecycle_campaign_key)
        if not campaign:
            status_counts["skipped"] += 1
            skip_counts["unknown_campaign"] += 1
            continue

        organization, workspace, user, skip_reason = _resolve_entities(receipt)
        if skip_reason:
            status_counts["skipped"] += 1
            skip_counts[skip_reason] += 1
            continue

        existing_reason = _existing_candidate_reason(
            receipt=receipt,
            workspace=workspace,
            user=user,
        )
        if existing_reason:
            status_counts["skipped"] += 1
            skip_counts[existing_reason] += 1
            continue

        campaign_counts[campaign["campaign_key"]] += 1
        if not write:
            status_counts["would_import"] += 1
            continue

        try:
            with transaction.atomic():
                locked_receipt = OnboardingActivationFactReceipt.no_workspace_objects.select_for_update().get(
                    id=receipt.id
                )
                if hasattr(locked_receipt, "lifecycle_evaluation"):
                    status_counts["skipped"] += 1
                    skip_counts["already_imported"] += 1
                    continue
                locked_existing_reason = _existing_candidate_reason(
                    receipt=locked_receipt,
                    workspace=workspace,
                    user=user,
                )
                if locked_existing_reason:
                    status_counts["skipped"] += 1
                    skip_counts[locked_existing_reason] += 1
                    continue
                _write_receipt_lifecycle_log(
                    receipt=locked_receipt,
                    organization=organization,
                    workspace=workspace,
                    user=user,
                    campaign=campaign,
                    run_id=run_id,
                )
            status_counts["imported"] += 1
            imported += 1
        except Exception as exc:
            status_counts["error"] += 1
            errors.append(
                {
                    "receipt_id": str(receipt.id),
                    "idempotency_key": receipt.idempotency_key,
                    "error": str(exc)[:500],
                }
            )

    return ActivationFactLifecycleImportResult(
        run_id=run_id,
        evaluated=evaluated,
        imported=imported,
        status_counts=dict(status_counts),
        campaign_counts=dict(campaign_counts),
        skip_counts=dict(skip_counts),
        errors=errors,
    )


def receipt_backed_lifecycle_cohort_report(
    *,
    since=None,
    until=None,
    campaign_key=None,
    workspace_id=None,
    organization_id=None,
    group_by=("campaign_key", "primary_cohort_key"),
):
    normalized_group_by = tuple(group_by or ("campaign_key", "primary_cohort_key"))
    unknown_groups = sorted(
        key for key in normalized_group_by if key not in LIFECYCLE_COHORT_REPORT_GROUPS
    )
    if unknown_groups:
        supported = ", ".join(sorted(LIFECYCLE_COHORT_REPORT_GROUPS))
        unknown = ", ".join(unknown_groups)
        raise ValueError(
            f"Unsupported group_by value(s): {unknown}. Supported: {supported}."
        )

    queryset = OnboardingLifecycleEvaluationLog.no_workspace_objects.filter(
        source_receipt__isnull=False,
    ).select_related("source_receipt")
    if since:
        queryset = queryset.filter(evaluated_at__gte=since)
    if until:
        queryset = queryset.filter(evaluated_at__lte=until)
    if campaign_key:
        queryset = queryset.filter(campaign_key=campaign_key)
    if workspace_id:
        queryset = queryset.filter(workspace_id=workspace_id)
    if organization_id:
        queryset = queryset.filter(organization_id=organization_id)

    value_fields = [LIFECYCLE_COHORT_REPORT_GROUPS[key] for key in normalized_group_by]
    grouped = queryset.values(*value_fields)
    grouped = grouped.annotate(
        eligible_count=Count(
            "id",
            filter=Q(status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE),
        ),
        first_evaluated_at=Min("evaluated_at"),
        last_evaluated_at=Max("evaluated_at"),
        source_receipt_count=Count("source_receipt_id", distinct=True),
        user_count=Count("user_id", distinct=True),
        workspace_count=Count("workspace_id", distinct=True),
    ).order_by(*value_fields)

    rows = []
    for row in grouped:
        rows.append(
            {
                **{
                    key: row.get(LIFECYCLE_COHORT_REPORT_GROUPS[key])
                    for key in normalized_group_by
                },
                "source_receipt_count": row["source_receipt_count"],
                "eligible_count": row["eligible_count"],
                "workspace_count": row["workspace_count"],
                "user_count": row["user_count"],
                "first_evaluated_at": _isoformat(row["first_evaluated_at"]),
                "last_evaluated_at": _isoformat(row["last_evaluated_at"]),
            }
        )

    return ActivationFactLifecycleCohortReportResult(
        since=since,
        until=until,
        group_by=normalized_group_by,
        rows=tuple(rows),
    )
