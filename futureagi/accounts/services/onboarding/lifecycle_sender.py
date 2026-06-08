from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import (
    NotificationDeliveryLog,
    NotificationPreference,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecycleSendAllowlist,
    OnboardingLifecycleSendLog,
)
from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.cloud_runtime import (
    lifecycle_delivery_cloud_enabled,
)
from accounts.services.onboarding.context import resolve_onboarding_context
from accounts.services.onboarding.feature_flags import get_onboarding_flags
from accounts.services.onboarding.lifecycle_completion import (
    lifecycle_completion_is_sample,
    lifecycle_target_completed,
)
from accounts.services.onboarding.lifecycle_eligibility import (
    LifecycleDecision,
    evaluate_lifecycle_decision,
    lifecycle_campaign_send_enabled,
)
from accounts.services.onboarding.lifecycle_launch_packets import (
    LAUNCH_PACKET_METADATA_KEY,
)
from accounts.services.onboarding.lifecycle_preferences import lifecycle_preference_for
from accounts.services.onboarding.lifecycle_preview_approval import (
    APPROVAL_METADATA_KEY,
    PREVIEW_APPROVAL_MISSING_REASON,
)
from accounts.services.onboarding.lifecycle_send_policy import (
    lifecycle_preference_group_field,
    send_frequency_caps,
    send_non_cloud_suppression_reason,
)
from accounts.services.onboarding.lifecycle_send_reports import (
    DRY_RUN_REPORT_METADATA_KEY,
    DRY_RUN_REPORT_MISSING_REASON,
)
from accounts.services.onboarding.lifecycle_template_context import (
    build_lifecycle_template_context,
    template_path,
)
from accounts.services.onboarding.notification_delivery import (
    deliver_onboarding_lifecycle_external_channels,
)
from accounts.services.onboarding.notification_preferences import (
    notification_preference_decision,
    record_notification_delivery,
)
from accounts.services.onboarding.notification_registry import family_for_campaign_group
from accounts.services.onboarding.signal_resolver import collect_onboarding_signals
from analytics.posthog_util import posthog_tracker
from tfc.utils.email import email_helper

SUCCESS_SEND_STATUSES = {
    OnboardingLifecycleSendLog.STATUS_SENT,
    OnboardingLifecycleSendLog.STATUS_CLICKED,
    OnboardingLifecycleSendLog.STATUS_COMPLETED,
}
COMPLETABLE_SEND_STATUSES = {
    OnboardingLifecycleSendLog.STATUS_SENT,
    OnboardingLifecycleSendLog.STATUS_CLICKED,
}
LIFECYCLE_SEND_CONTEXT_METADATA_KEYS = (
    "observe_credentials_ready",
    "observe_credentials_ready_at",
    "observe_credential_step",
    "observe_setup_language",
    "observe_setup_language_label",
    "observe_setup_provider",
    "observe_setup_provider_label",
)
RECEIPT_BACKED_SEND_METADATA_KEYS = (
    "source",
    "receipt_id",
    "idempotency_key",
    "export_log_id",
    "payload_hash",
    "deployment_mode",
    "deployment_region",
    "plan_tier",
    "primary_cohort_key",
    "cohort_keys",
    "journey_config_schema_version",
    "receipt_template_key",
)
UNPAID_RECEIPT_PLAN_TIERS = frozenset({"", "free", "oss", "open_source", "community"})


@dataclass(frozen=True)
class LifecycleSendBatchResult:
    run_id: uuid.UUID
    generated_at: str
    dry_run: bool
    evaluated: int
    sent: int
    suppressed: int
    failed: int
    skipped: int
    status_counts: dict
    suppression_counts: dict
    candidates: tuple[dict, ...] = ()
    approval_manifest_sha256: str | None = None
    approval_record_sha256: str | None = None
    dry_run_report_sha256: str | None = None
    dry_run_report_review_record_sha256: str | None = None
    launch_packet_sha256: str | None = None

    def to_payload(self):
        return {
            "run_id": str(self.run_id),
            "generated_at": self.generated_at,
            "dry_run": self.dry_run,
            "evaluated": self.evaluated,
            "sent": self.sent,
            "suppressed": self.suppressed,
            "failed": self.failed,
            "skipped": self.skipped,
            "status_counts": self.status_counts,
            "suppression_counts": self.suppression_counts,
            "candidates": list(self.candidates),
            "approval_manifest_sha256": self.approval_manifest_sha256,
            "approval_record_sha256": self.approval_record_sha256,
            "dry_run_report_sha256": self.dry_run_report_sha256,
            "dry_run_report_review_record_sha256": (
                self.dry_run_report_review_record_sha256
            ),
            "launch_packet_sha256": self.launch_packet_sha256,
        }


@dataclass(frozen=True)
class _LifecycleSendRequest:
    user: object
    organization: object
    workspace: object
    source: str

    @property
    def query_params(self):
        return {"source": self.source}


def send_environment():
    return getattr(settings, "ONBOARDING_LIFECYCLE_SEND_ENVIRONMENT", "local")


def _cloud_lifecycle_delivery_enabled():
    return lifecycle_delivery_cloud_enabled()


def _internal_route(route):
    if not isinstance(route, str) or not route:
        return False
    parts = urlsplit(route)
    return not parts.scheme and not parts.netloc and route.startswith("/")


def _with_lifecycle_campaign_params(route, campaign):
    if not _internal_route(route):
        return None
    parts = urlsplit(route)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(
        {
            "source": "onboarding_email",
            "campaign_key": (campaign or {}).get("campaign_key"),
            "target_event": (campaign or {}).get("target_success_event"),
        }
    )
    safe_query = urlencode({key: value for key, value in query.items() if value})
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            safe_query,
            parts.fragment,
        )
    )


def _receipt_lifecycle_target_url(evaluation_log, campaign):
    metadata = (
        evaluation_log.metadata if isinstance(evaluation_log.metadata, dict) else {}
    )
    snapshot = (
        evaluation_log.activation_state_snapshot
        if isinstance(evaluation_log.activation_state_snapshot, dict)
        else {}
    )
    candidate_routes = (
        metadata.get("receipt_lifecycle_target_route"),
        evaluation_log.target_url,
        snapshot.get("recommended_action_href"),
    )
    for route in candidate_routes:
        target_url = _with_lifecycle_campaign_params(route, campaign)
        if target_url:
            return target_url
    return None


def _target_success_event_completed(send_log):
    campaign = send_log.evaluation_log.registry_snapshot or {
        "campaign_key": send_log.campaign_key,
        "target_success_event": send_log.target_success_event,
        "sample_policy": "real_only",
    }
    return lifecycle_target_completed(
        organization=send_log.organization,
        workspace=send_log.workspace,
        campaign=campaign,
        target_success_event=send_log.target_success_event,
    )


def _campaign_for_completion(send_log):
    return send_log.evaluation_log.registry_snapshot or {
        "campaign_key": send_log.campaign_key,
        "target_success_event": send_log.target_success_event,
        "sample_policy": "real_only",
    }


def _event_metadata_value(event, key):
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    value = metadata.get(key)
    if value in {None, ""}:
        return None
    return str(value)


def _event_send_log_id(event):
    raw_id = _event_metadata_value(event, "send_log_id")
    if not raw_id:
        return None, False
    try:
        return uuid.UUID(raw_id), True
    except (TypeError, ValueError):
        return None, True


def _send_log_matches_event_context(send_log, event):
    if send_log.target_success_event != event.event_name:
        return False

    campaign_key = _event_metadata_value(event, "campaign_key")
    if campaign_key and send_log.campaign_key != campaign_key:
        return False

    email_key = _event_metadata_value(event, "email_key")
    if email_key and send_log.template_key != email_key:
        return False

    target_stage = _event_metadata_value(event, "target_stage")
    if target_stage and send_log.activation_stage != target_stage:
        return False

    target_event = _event_metadata_value(event, "target_event")
    if target_event and target_event != event.event_name:
        return False

    return lifecycle_completion_is_sample(_campaign_for_completion(send_log)) == bool(
        event.is_sample
    )


def _completion_send_queryset(event):
    return (
        OnboardingLifecycleSendLog.no_workspace_objects.select_related(
            "evaluation_log",
        )
        .filter(
            user=event.user,
            organization=event.organization,
            workspace=event.workspace,
            target_success_event=event.event_name,
            status__in=COMPLETABLE_SEND_STATUSES,
            sent_at__lte=event.occurred_at,
        )
        .order_by("-clicked_at", "-sent_at")
    )


def _exact_completion_send_log(event, send_log_id):
    send_log = _completion_send_queryset(event).filter(id=send_log_id).first()
    if not send_log or not _send_log_matches_event_context(send_log, event):
        return None
    return send_log


def _fallback_completion_send_log(event):
    for send_log in _completion_send_queryset(event):
        if _send_log_matches_event_context(send_log, event):
            return send_log
    return None


def _mark_completed(send_log, event, completion_source):
    send_log.status = OnboardingLifecycleSendLog.STATUS_COMPLETED
    send_log.completed_at = event.occurred_at
    send_log.metadata = {
        **(send_log.metadata or {}),
        "completed_event_id": str(event.id),
        "completed_event_source": event.source,
        "completion_source": completion_source,
    }
    send_log.save(update_fields=["status", "completed_at", "metadata", "updated_at"])
    _track(
        "lifecycle_email_completed",
        send_log,
        extra={
            "activation_event_id": str(event.id),
            "activation_event_source": event.source,
            "completion_source": completion_source,
        },
    )
    return send_log


def _groups(send_log):
    groups = {"organization": str(send_log.organization_id)}
    if send_log.workspace_id:
        groups["workspace"] = str(send_log.workspace_id)
    return groups


def _track(event_name, send_log, extra=None):
    properties = {
        "workspace_id": str(send_log.workspace_id) if send_log.workspace_id else None,
        "organization_id": str(send_log.organization_id),
        "user_id": str(send_log.user_id),
        "campaign_key": send_log.campaign_key,
        "campaign_family": send_log.campaign_group,
        "template_key": send_log.template_key,
        "template_version": send_log.template_version,
        "primary_path": send_log.primary_path,
        "activation_stage": send_log.activation_stage,
        "recommended_action_id": send_log.recommended_action_id,
        "target_success_event": send_log.target_success_event,
        "send_log_id": str(send_log.id),
        "evaluation_log_id": str(send_log.evaluation_log_id),
        "status": send_log.status,
        "suppression_reason": send_log.suppression_reason,
        "route": send_log.target_route,
        "is_sample": False,
        "cohort": send_log.metadata.get("cohort"),
    }
    if extra:
        properties.update(extra)
    posthog_tracker.capture(
        send_log.user_id,
        event_name,
        properties=properties,
        groups=_groups(send_log),
    )


def _record_lifecycle_event(event_name, send_log, now, metadata=None):
    if not send_log.workspace_id:
        return None
    return record_event(
        user=send_log.user,
        organization=send_log.organization,
        workspace=send_log.workspace,
        event_name=event_name,
        source="onboarding_lifecycle_email",
        product_path=send_log.primary_path,
        activation_stage=send_log.activation_stage,
        is_sample=False,
        occurred_at=now,
        metadata={
            "send_log_id": str(send_log.id),
            "evaluation_log_id": str(send_log.evaluation_log_id),
            "campaign_key": send_log.campaign_key,
            "campaign_family": send_log.campaign_group,
            "template_key": send_log.template_key,
            "target_success_event": send_log.target_success_event,
            **(metadata or {}),
        },
        idempotency_key=f"{event_name}:{send_log.id}",
    )


def _notification_family_for_campaign(campaign_group):
    return family_for_campaign_group(campaign_group)


def _record_delivery(send_log, *, status, now, reason=None, error=None):
    family = _notification_family_for_campaign(send_log.campaign_group)
    return record_notification_delivery(
        organization=send_log.organization,
        workspace=send_log.workspace,
        user=send_log.user,
        family=family,
        source_type="onboarding_lifecycle",
        source_id=str(send_log.id),
        channel=NotificationPreference.CHANNEL_EMAIL,
        status=status,
        recipient_type="user",
        recipient_identifier=getattr(send_log.user, "email", ""),
        notification_key=send_log.campaign_key,
        idempotency_key=f"onboarding_lifecycle:{send_log.id}:email:{status}",
        stage=send_log.activation_stage,
        severity="info",
        suppressed_reason=reason,
        route_url=send_log.target_route,
        error=error,
        metadata={
            "campaign_group": send_log.campaign_group,
            "template_key": send_log.template_key,
            "target_success_event": send_log.target_success_event,
        },
        now=now,
    )


def _fresh_activation_state(evaluation_log, now):
    request = _LifecycleSendRequest(
        user=evaluation_log.user,
        organization=evaluation_log.organization,
        workspace=evaluation_log.workspace,
        source="lifecycle_send",
    )
    context = resolve_onboarding_context(request)
    flags = get_onboarding_flags(
        user=context.user,
        organization=context.organization,
        workspace=context.workspace,
    )
    signals = collect_onboarding_signals(
        user=context.user,
        organization=context.organization,
        workspace=context.workspace,
    )
    activation_state = resolve_activation_state(
        context=context,
        flags=flags,
        signals=signals,
    )
    return context, flags, activation_state


def _fresh_decision(evaluation_log, now):
    context, flags, activation_state = _fresh_activation_state(evaluation_log, now)
    decision = evaluate_lifecycle_decision(
        user=context.user,
        organization=context.organization,
        workspace=context.workspace,
        activation_state=activation_state,
        flags=flags,
        now=now,
        run_id=uuid.uuid4(),
        source="lifecycle_send",
        campaign_key=evaluation_log.campaign_key,
        skip_frequency=True,
    )
    return context, flags, decision


def _receipt_backed_decision(evaluation_log, now, *, source):
    flags = get_onboarding_flags(
        user=evaluation_log.user,
        organization=evaluation_log.organization,
        workspace=evaluation_log.workspace,
    )
    campaign = evaluation_log.registry_snapshot or {}
    target_url = _receipt_lifecycle_target_url(evaluation_log, campaign)
    activation_state = {
        "stage": evaluation_log.activation_stage,
        "primary_path": evaluation_log.primary_path,
        "is_activated": (evaluation_log.activation_state_snapshot or {}).get(
            "is_activated"
        ),
        "recommended_action": {
            "id": evaluation_log.target_action_id,
            "href": target_url,
        },
        "fallback_action": {},
        "sample_project": {},
    }
    metadata = (
        evaluation_log.metadata if isinstance(evaluation_log.metadata, dict) else {}
    )
    decision = LifecycleDecision(
        run_id=uuid.uuid4(),
        user=evaluation_log.user,
        organization=evaluation_log.organization,
        workspace=evaluation_log.workspace,
        status=evaluation_log.status,
        campaign=campaign,
        activation_state=activation_state,
        target_url=target_url,
        eligible_at=evaluation_log.eligible_at,
        suppression_reason=evaluation_log.suppression_reason,
        suppression_details=evaluation_log.suppression_details or {},
        evaluated_at=now,
        metadata={
            "source": source,
            "receipt_id": metadata.get("receipt_id"),
            "receipt_lifecycle_send_enabled": metadata.get(
                "receipt_lifecycle_send_enabled"
            ),
            "receipt_lifecycle_dry_run_only": metadata.get(
                "receipt_lifecycle_dry_run_only"
            ),
        },
    )
    return flags, decision


def _receipt_backed_dry_run_decision(evaluation_log, now):
    return _receipt_backed_decision(
        evaluation_log,
        now,
        source="activation_fact_receipt_dry_run",
    )


def _receipt_backed_send_decision(evaluation_log, now):
    return _receipt_backed_decision(
        evaluation_log,
        now,
        source="activation_fact_receipt_send",
    )


def _denylisted(evaluation_log):
    entries = getattr(settings, "ONBOARDING_LIFECYCLE_SEND_DENYLIST", []) or []
    email = (getattr(evaluation_log.user, "email", "") or "").lower()
    domain = email.split("@", 1)[1] if "@" in email else ""
    values = {
        "user": str(evaluation_log.user_id),
        "workspace": str(evaluation_log.workspace_id),
        "organization": str(evaluation_log.organization_id),
        "domain": domain,
    }
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        scope_type = entry.get("scope_type")
        if values.get(scope_type) == str(entry.get("scope_value", "")).lower():
            return True
    return False


def _allowlisted(evaluation_log, campaign, *, require_campaign_group=False):
    email = (getattr(evaluation_log.user, "email", "") or "").lower()
    domain = email.split("@", 1)[1] if "@" in email else ""
    values = {
        OnboardingLifecycleSendAllowlist.SCOPE_USER: str(evaluation_log.user_id),
        OnboardingLifecycleSendAllowlist.SCOPE_WORKSPACE: str(
            evaluation_log.workspace_id
        ),
        OnboardingLifecycleSendAllowlist.SCOPE_ORGANIZATION: str(
            evaluation_log.organization_id
        ),
        OnboardingLifecycleSendAllowlist.SCOPE_DOMAIN: domain,
    }
    campaign_group = campaign.get("campaign_group") if campaign else None
    if require_campaign_group and not campaign_group:
        return False
    queryset = OnboardingLifecycleSendAllowlist.no_workspace_objects.filter(
        enabled=True,
        environment=send_environment(),
    )
    if require_campaign_group:
        queryset = queryset.filter(campaign_group=campaign_group)
    else:
        queryset = queryset.filter(models_q_campaign_group(campaign_group))
    for allowlist in queryset:
        if values.get(allowlist.scope_type) == allowlist.scope_value.lower():
            return True
    return False


def models_q_campaign_group(campaign_group):
    from django.db.models import Q

    return Q(campaign_group__isnull=True) | Q(campaign_group=campaign_group)


def _preference_suppression(evaluation_log, now, campaign):
    preference = lifecycle_preference_for(
        user=evaluation_log.user,
        organization=evaluation_log.organization,
        workspace=evaluation_log.workspace,
    )
    if preference:
        if not preference.onboarding_enabled or preference.unsubscribed_at:
            return "unsubscribed"
        if preference.snoozed_until and preference.snoozed_until > now:
            return "snoozed"
        group_field = lifecycle_preference_group_field(
            campaign.get("campaign_group") if campaign else None
        )
        if group_field and not getattr(preference, group_field):
            return "unsubscribed"
    family = _notification_family_for_campaign(
        campaign.get("campaign_group") if campaign else None
    )
    decision = notification_preference_decision(
        organization=evaluation_log.organization,
        workspace=evaluation_log.workspace,
        user=evaluation_log.user,
        family=family,
        channel=NotificationPreference.CHANNEL_EMAIL,
        now=now,
    )
    if not decision.allowed:
        return decision.reason or "user_disabled_family"
    return None


def _send_log_frequency_suppression(evaluation_log, now, campaign):
    if not campaign:
        return "dry_run_not_eligible"
    successful = OnboardingLifecycleSendLog.no_workspace_objects.filter(
        user=evaluation_log.user,
        status__in=SUCCESS_SEND_STATUSES,
    )
    for cap in send_frequency_caps():
        campaign_keys = cap.get("campaign_keys") or ()
        if campaign_keys and campaign.get("campaign_key") not in campaign_keys:
            continue
        campaign_groups = cap.get("campaign_groups") or ()
        if campaign_groups and campaign.get("campaign_group") not in campaign_groups:
            continue
        frequency_cap_keys = cap.get("frequency_cap_keys") or ()
        if (
            frequency_cap_keys
            and campaign.get("frequency_cap_key") not in frequency_cap_keys
        ):
            continue

        capped_logs = successful
        if cap["scope"] == "campaign_group":
            capped_logs = capped_logs.filter(campaign_group=campaign["campaign_group"])
        elif cap["scope"] == "campaign_key":
            capped_logs = capped_logs.filter(campaign_key=campaign["campaign_key"])
        elif cap["scope"] != "user":
            continue
        if cap.get("window_hours") is not None:
            capped_logs = capped_logs.filter(
                created_at__gte=now - timedelta(hours=cap["window_hours"])
            )
        if capped_logs.count() >= cap["limit"]:
            return cap["reason"]
    return None


def _digest_preview_for(decision, evaluation_log):
    return (decision.metadata or {}).get("digest_preview") or (
        evaluation_log.metadata or {}
    ).get("digest_preview")


def _missing_required_digest_preview(evaluation_log, campaign, decision):
    if not campaign or not campaign.get("requires_digest_preview"):
        return False
    return not bool(_digest_preview_for(decision, evaluation_log))


def _receipt_backed_suppression_reason(evaluation_log):
    if not evaluation_log.source_receipt_id:
        return None
    metadata = (
        evaluation_log.metadata if isinstance(evaluation_log.metadata, dict) else {}
    )
    receipt = getattr(evaluation_log, "source_receipt", None)
    if receipt:
        if receipt.deployment_mode != "cloud":
            return "receipt_not_cloud"
        if receipt.plan_tier in UNPAID_RECEIPT_PLAN_TIERS:
            return "receipt_unpaid_plan"
        if receipt.email_suppressed:
            return "receipt_email_suppressed"
    if not metadata.get("receipt_lifecycle_send_enabled"):
        return "receipt_send_disabled"
    if metadata.get("receipt_lifecycle_dry_run_only"):
        return "receipt_dry_run_only"
    return None


def _suppression_reason(
    evaluation_log,
    flags,
    decision,
    now,
    *,
    require_campaign_group_allowlist=False,
    preview_approval=None,
    dry_run_report_review=None,
):
    campaign = decision.campaign
    if not flags.get("onboarding_lifecycle_email_dry_run"):
        return "dry_run_not_eligible"
    if not campaign:
        return "dry_run_not_eligible"
    if not lifecycle_campaign_send_enabled(campaign, flags=flags):
        return "send_flag_disabled"
    if not flags.get(campaign["dry_run_flag"]):
        return "campaign_flag_disabled"
    if evaluation_log.status != OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE:
        return "dry_run_not_eligible"
    receipt_reason = _receipt_backed_suppression_reason(evaluation_log)
    if receipt_reason:
        return receipt_reason
    if decision.status != OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE:
        if decision.suppression_reason == "target_event_complete":
            return "target_success_event_completed"
        if decision.suppression_reason == "route_unavailable":
            return "route_unavailable"
        if decision.suppression_reason == "permission_limited":
            return "permission_limited"
        if decision.suppression_reason == "user_unsubscribed":
            return "unsubscribed"
        if decision.suppression_reason == "user_snoozed":
            return "snoozed"
        return "activation_state_changed"
    if _missing_required_digest_preview(evaluation_log, campaign, decision):
        return "missing_digest_preview"
    if preview_approval and not preview_approval.has_campaign(campaign["campaign_key"]):
        return PREVIEW_APPROVAL_MISSING_REASON
    if _denylisted(evaluation_log):
        return "denylisted"
    if not _allowlisted(
        evaluation_log,
        campaign,
        require_campaign_group=require_campaign_group_allowlist,
    ):
        return "not_in_send_cohort"
    preference_reason = _preference_suppression(evaluation_log, now, campaign)
    if preference_reason:
        return preference_reason
    frequency_reason = _send_log_frequency_suppression(evaluation_log, now, campaign)
    if frequency_reason:
        return frequency_reason
    if not _internal_route(decision.target_url):
        return "route_unavailable"
    if dry_run_report_review and not dry_run_report_review.has_sendable_candidate(
        evaluation_log.id
    ):
        return DRY_RUN_REPORT_MISSING_REASON
    return None


def _send_log_defaults(
    evaluation_log,
    campaign,
    decision,
    now,
    cohort,
    preview_approval=None,
    dry_run_report_review=None,
    launch_packet=None,
):
    campaign = campaign or {}
    snapshot = decision.activation_state or {}
    recommended_action = snapshot.get("recommended_action") or {}
    metadata = {
        "cohort": cohort,
        "source_run_id": str(evaluation_log.run_id),
    }
    digest_preview = (decision.metadata or {}).get("digest_preview") or (
        evaluation_log.metadata or {}
    ).get("digest_preview")
    if digest_preview:
        metadata["digest_preview"] = digest_preview
    if evaluation_log.source_receipt_id:
        evaluation_metadata = (
            evaluation_log.metadata if isinstance(evaluation_log.metadata, dict) else {}
        )
        for key in RECEIPT_BACKED_SEND_METADATA_KEYS:
            value = evaluation_metadata.get(key)
            if value is not None and value != "":
                metadata[key] = value
    for key in LIFECYCLE_SEND_CONTEXT_METADATA_KEYS:
        value = (decision.metadata or {}).get(key)
        if value in {None, ""}:
            value = (evaluation_log.metadata or {}).get(key)
        if value not in {None, ""}:
            metadata[key] = value
    campaign_key = campaign.get("campaign_key") or evaluation_log.campaign_key
    if preview_approval and preview_approval.has_campaign(campaign_key):
        metadata[APPROVAL_METADATA_KEY] = preview_approval.metadata_for_campaign(
            campaign_key
        )
    if dry_run_report_review and dry_run_report_review.has_sendable_candidate(
        evaluation_log.id
    ):
        metadata[DRY_RUN_REPORT_METADATA_KEY] = (
            dry_run_report_review.metadata_for_send()
        )
    if launch_packet:
        metadata[LAUNCH_PACKET_METADATA_KEY] = launch_packet.metadata_for_send()
    return {
        "user": evaluation_log.user,
        "organization": evaluation_log.organization,
        "workspace": evaluation_log.workspace,
        "campaign_key": campaign_key,
        "campaign_group": campaign.get("campaign_group")
        or evaluation_log.campaign_group,
        "template_key": campaign.get("template_key") or evaluation_log.template_key,
        "template_version": campaign.get("template_version")
        or evaluation_log.template_version,
        "primary_path": snapshot.get("primary_path") or evaluation_log.primary_path,
        "activation_stage": snapshot.get("stage") or evaluation_log.activation_stage,
        "recommended_action_id": campaign.get("target_action_id")
        or recommended_action.get("id")
        or evaluation_log.target_action_id
        or evaluation_log.recommendation_id,
        "target_success_event": campaign.get("target_success_event")
        or evaluation_log.target_success_event,
        "target_route": decision.target_url or evaluation_log.target_url or "",
        "queued_at": now,
        "metadata": metadata,
    }


def _approval_status_for(preview_approval, campaign_key):
    if not preview_approval:
        return "not_supplied"
    if preview_approval.has_campaign(campaign_key):
        return "approved"
    return "missing"


def _dry_run_candidate_payload(
    *,
    evaluation_log,
    decision,
    status,
    suppression_reason,
    preview_approval=None,
):
    campaign = decision.campaign or evaluation_log.registry_snapshot or {}
    snapshot = decision.activation_state or {}
    recommended_action = snapshot.get("recommended_action") or {}
    campaign_key = campaign.get("campaign_key") or evaluation_log.campaign_key
    return {
        "evaluation_log_id": str(evaluation_log.id),
        "user_id": str(evaluation_log.user_id),
        "organization_id": str(evaluation_log.organization_id),
        "workspace_id": str(evaluation_log.workspace_id),
        "campaign_key": campaign_key,
        "campaign_group": campaign.get("campaign_group")
        or evaluation_log.campaign_group,
        "template_key": campaign.get("template_key") or evaluation_log.template_key,
        "template_version": campaign.get("template_version")
        or evaluation_log.template_version,
        "primary_path": snapshot.get("primary_path") or evaluation_log.primary_path,
        "activation_stage": snapshot.get("stage") or evaluation_log.activation_stage,
        "recommended_action_id": campaign.get("target_action_id")
        or recommended_action.get("id")
        or evaluation_log.target_action_id
        or evaluation_log.recommendation_id,
        "target_action_id": campaign.get("target_action_id")
        or evaluation_log.target_action_id,
        "target_success_event": campaign.get("target_success_event")
        or evaluation_log.target_success_event,
        "target_route": decision.target_url or evaluation_log.target_url or "",
        "status": status,
        "suppression_reason": suppression_reason,
        "approval_status": _approval_status_for(preview_approval, campaign_key),
        "eligible_at": (
            evaluation_log.eligible_at.isoformat()
            if evaluation_log.eligible_at
            else None
        ),
        "evaluated_at": (
            evaluation_log.evaluated_at.isoformat()
            if evaluation_log.evaluated_at
            else None
        ),
    }


def _get_or_create_send_log(
    evaluation_log,
    campaign,
    decision,
    now,
    cohort,
    preview_approval=None,
    dry_run_report_review=None,
    launch_packet=None,
):
    defaults = _send_log_defaults(
        evaluation_log,
        campaign,
        decision,
        now,
        cohort,
        preview_approval=preview_approval,
        dry_run_report_review=dry_run_report_review,
        launch_packet=launch_packet,
    )
    try:
        with transaction.atomic():
            send_log, created = (
                OnboardingLifecycleSendLog.no_workspace_objects.get_or_create(
                    evaluation_log=evaluation_log,
                    campaign_key=defaults["campaign_key"],
                    user=evaluation_log.user,
                    workspace=evaluation_log.workspace,
                    defaults=defaults,
                )
            )
            if not created and send_log.status not in SUCCESS_SEND_STATUSES:
                for key, value in defaults.items():
                    setattr(send_log, key, value)
                send_log.save()
            return send_log
    except IntegrityError:
        return OnboardingLifecycleSendLog.no_workspace_objects.get(
            evaluation_log=evaluation_log,
            campaign_key=defaults["campaign_key"],
            user=evaluation_log.user,
            workspace=evaluation_log.workspace,
        )


def _mark_suppressed(send_log, reason, now):
    send_log.status = OnboardingLifecycleSendLog.STATUS_SUPPRESSED
    send_log.suppression_reason = reason
    send_log.queued_at = send_log.queued_at or now
    send_log.save(
        update_fields=[
            "status",
            "suppression_reason",
            "queued_at",
            "updated_at",
        ]
    )
    _track("lifecycle_email_send_suppressed", send_log)
    _record_lifecycle_event("lifecycle_email_send_suppressed", send_log, now)
    _record_delivery(
        send_log,
        status=NotificationDeliveryLog.STATUS_SUPPRESSED,
        now=now,
        reason=reason,
    )
    return send_log


def queue_onboarding_lifecycle_email(
    evaluation_log,
    *,
    now=None,
    cohort="internal",
    require_campaign_group_allowlist=False,
    preview_approval=None,
    dry_run_report_review=None,
    launch_packet=None,
    receipt_backed=False,
):
    now = now or timezone.now()
    if receipt_backed:
        flags, decision = _receipt_backed_send_decision(evaluation_log, now)
    else:
        _context, flags, decision = _fresh_decision(evaluation_log, now)
    campaign = decision.campaign or evaluation_log.registry_snapshot or {}
    send_log = _get_or_create_send_log(
        evaluation_log,
        campaign,
        decision,
        now,
        cohort,
        preview_approval=preview_approval,
        dry_run_report_review=dry_run_report_review,
        launch_packet=launch_packet,
    )
    if send_log.status in SUCCESS_SEND_STATUSES:
        return send_log
    reason = _suppression_reason(
        evaluation_log,
        flags,
        decision,
        now,
        require_campaign_group_allowlist=require_campaign_group_allowlist,
        preview_approval=preview_approval,
        dry_run_report_review=dry_run_report_review,
    )
    if reason:
        return _mark_suppressed(send_log, reason, now)
    send_log.status = OnboardingLifecycleSendLog.STATUS_QUEUED
    send_log.suppression_reason = None
    send_log.queued_at = now
    send_log.target_route = decision.target_url
    send_log.save(
        update_fields=[
            "status",
            "suppression_reason",
            "queued_at",
            "target_route",
            "updated_at",
        ]
    )
    _track("lifecycle_email_send_queued", send_log)
    _record_lifecycle_event("lifecycle_email_send_queued", send_log, now)
    return send_log


def _has_preview_approval_metadata(send_log):
    metadata = (send_log.metadata or {}).get(APPROVAL_METADATA_KEY)
    return isinstance(metadata, dict) and bool(metadata.get("approval_record_sha256"))


def _has_dry_run_report_metadata(send_log):
    metadata = (send_log.metadata or {}).get(DRY_RUN_REPORT_METADATA_KEY)
    return isinstance(metadata, dict) and bool(metadata.get("review_record_sha256"))


def send_onboarding_lifecycle_email(send_log, *, now=None):
    now = now or timezone.now()
    if send_log.status in SUCCESS_SEND_STATUSES:
        return send_log
    if send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED:
        return send_log
    if _target_success_event_completed(send_log):
        return _mark_suppressed(send_log, "target_success_event_completed", now)
    if not _has_preview_approval_metadata(send_log):
        return _mark_suppressed(send_log, PREVIEW_APPROVAL_MISSING_REASON, now)
    if not _has_dry_run_report_metadata(send_log):
        return _mark_suppressed(send_log, DRY_RUN_REPORT_MISSING_REASON, now)
    campaign = send_log.evaluation_log.registry_snapshot or {}
    if not campaign:
        send_log.status = OnboardingLifecycleSendLog.STATUS_FAILED
        send_log.failure_reason = "missing_template"
        send_log.provider_status = "failed"
        send_log.save(
            update_fields=["status", "failure_reason", "provider_status", "updated_at"]
        )
        _track("lifecycle_email_send_failed", send_log)
        _record_delivery(
            send_log,
            status=NotificationDeliveryLog.STATUS_FAILED,
            now=now,
            error="missing_template",
        )
        return send_log
    context = build_lifecycle_template_context(
        send_log=send_log,
        campaign=campaign,
        target_route=send_log.target_route,
        now=now,
    )
    send_log.click_url = context["primary_action_url"]
    send_log.save(update_fields=["click_url", "updated_at"])
    if not _cloud_lifecycle_delivery_enabled():
        return _mark_suppressed(send_log, send_non_cloud_suppression_reason(), now)
    try:
        email_helper(
            context["email_subject"],
            template_path(send_log.template_key),
            context,
            [send_log.user.email],
        )
    except Exception as exc:
        send_log.status = OnboardingLifecycleSendLog.STATUS_FAILED
        send_log.provider_status = "failed"
        send_log.failure_reason = str(exc)[:1000]
        send_log.save(
            update_fields=[
                "status",
                "provider_status",
                "failure_reason",
                "updated_at",
            ]
        )
        _track("lifecycle_email_send_failed", send_log)
        _record_lifecycle_event("lifecycle_email_send_failed", send_log, now)
        _record_delivery(
            send_log,
            status=NotificationDeliveryLog.STATUS_FAILED,
            now=now,
            error=exc,
        )
        return send_log

    send_log.status = OnboardingLifecycleSendLog.STATUS_SENT
    send_log.sent_at = now
    send_log.provider_status = "accepted"
    send_log.failure_reason = None
    send_log.save(
        update_fields=[
            "status",
            "sent_at",
            "provider_status",
            "failure_reason",
            "updated_at",
        ]
    )
    _track("lifecycle_email_sent", send_log)
    _record_lifecycle_event("lifecycle_email_sent", send_log, now)
    _record_delivery(
        send_log,
        status=NotificationDeliveryLog.STATUS_SENT,
        now=now,
    )
    deliver_onboarding_lifecycle_external_channels(send_log, now=now)
    return send_log


def send_limited_onboarding_lifecycle_batch(
    *,
    cohort,
    limit=100,
    campaign_group=None,
    user_id=None,
    workspace_id=None,
    dry_run=False,
    now=None,
    require_campaign_group_allowlist=False,
    preview_approval=None,
    dry_run_report_review=None,
    launch_packet=None,
    include_receipt_backed=False,
):
    now = now or timezone.now()
    if not dry_run and (
        not preview_approval or not preview_approval.approval_record_sha256
    ):
        raise ImproperlyConfigured(
            "Lifecycle preview approval record is required for sends."
        )
    if not dry_run and not dry_run_report_review:
        raise ImproperlyConfigured(
            "Lifecycle send dry-run report review is required for sends."
        )
    if not dry_run and not launch_packet:
        raise ImproperlyConfigured("Lifecycle launch packet is required for sends.")
    run_id = uuid.uuid4()
    queryset = OnboardingLifecycleEvaluationLog.no_workspace_objects.filter(
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
    ).order_by("-evaluated_at")
    if not dry_run and not include_receipt_backed:
        queryset = queryset.filter(source_receipt__isnull=True)
    if not dry_run and include_receipt_backed:
        queryset = queryset.select_related("source_receipt")
    if campaign_group:
        queryset = queryset.filter(campaign_group=campaign_group)
    if user_id:
        queryset = queryset.filter(user_id=user_id)
    if workspace_id:
        queryset = queryset.filter(workspace_id=workspace_id)

    status_counts = Counter()
    suppression_counts = Counter()
    candidates = []
    evaluated = sent = suppressed = failed = skipped = 0

    for evaluation_log in queryset[:limit]:
        evaluated += 1
        if dry_run:
            if evaluation_log.source_receipt_id:
                flags, decision = _receipt_backed_dry_run_decision(
                    evaluation_log,
                    now,
                )
            else:
                _context, flags, decision = _fresh_decision(evaluation_log, now)
            reason = _suppression_reason(
                evaluation_log,
                flags,
                decision,
                now,
                require_campaign_group_allowlist=require_campaign_group_allowlist,
                preview_approval=preview_approval,
                dry_run_report_review=dry_run_report_review,
            )
            status = "would_suppress" if reason else "would_send"
            status_counts[status] += 1
            if reason:
                suppression_counts[reason] += 1
            candidates.append(
                _dry_run_candidate_payload(
                    evaluation_log=evaluation_log,
                    decision=decision,
                    status=status,
                    suppression_reason=reason,
                    preview_approval=preview_approval,
                )
            )
            continue
        send_log = queue_onboarding_lifecycle_email(
            evaluation_log,
            now=now,
            cohort=cohort,
            require_campaign_group_allowlist=require_campaign_group_allowlist,
            preview_approval=preview_approval,
            dry_run_report_review=dry_run_report_review,
            launch_packet=launch_packet,
            receipt_backed=bool(evaluation_log.source_receipt_id),
        )
        if send_log.status == OnboardingLifecycleSendLog.STATUS_QUEUED:
            send_log = send_onboarding_lifecycle_email(send_log, now=now)
        status_counts[send_log.status] += 1
        if send_log.suppression_reason:
            suppression_counts[send_log.suppression_reason] += 1
        if send_log.status == OnboardingLifecycleSendLog.STATUS_SENT:
            sent += 1
        elif send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED:
            suppressed += 1
        elif send_log.status == OnboardingLifecycleSendLog.STATUS_FAILED:
            failed += 1
        else:
            skipped += 1

    return LifecycleSendBatchResult(
        run_id=run_id,
        generated_at=now.isoformat(),
        dry_run=dry_run,
        evaluated=evaluated,
        sent=sent,
        suppressed=suppressed,
        failed=failed,
        skipped=skipped,
        status_counts=dict(status_counts),
        suppression_counts=dict(suppression_counts),
        candidates=tuple(candidates),
        approval_manifest_sha256=(
            preview_approval.manifest_sha256 if preview_approval else None
        ),
        approval_record_sha256=(
            preview_approval.approval_record_sha256 if preview_approval else None
        ),
        dry_run_report_sha256=(
            dry_run_report_review.report.sha256 if dry_run_report_review else None
        ),
        dry_run_report_review_record_sha256=(
            dry_run_report_review.review_record_sha256
            if dry_run_report_review
            else None
        ),
        launch_packet_sha256=launch_packet.sha256 if launch_packet else None,
    )


def mark_lifecycle_send_clicked(send_log, *, now=None, metadata=None):
    now = now or timezone.now()
    if not send_log.clicked_at:
        send_log.clicked_at = now
    if send_log.status == OnboardingLifecycleSendLog.STATUS_SENT:
        send_log.status = OnboardingLifecycleSendLog.STATUS_CLICKED
    if metadata:
        send_log.metadata = {**(send_log.metadata or {}), **metadata}
    send_log.save(update_fields=["clicked_at", "status", "metadata", "updated_at"])
    _track("lifecycle_email_clicked", send_log)
    _record_lifecycle_event("lifecycle_email_clicked", send_log, now)
    return send_log


def mark_lifecycle_send_completed_for_event(event):
    if not event.user_id:
        return None
    send_log_id, send_log_id_was_present = _event_send_log_id(event)
    if send_log_id_was_present:
        if not send_log_id:
            return None
        send_log = _exact_completion_send_log(event, send_log_id)
        if not send_log:
            return None
        return _mark_completed(send_log, event, "exact_send_log_id")

    send_log = _fallback_completion_send_log(event)
    if not send_log:
        return None
    return _mark_completed(send_log, event, "latest_matching_send")
