from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
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
from accounts.services.onboarding.context import resolve_onboarding_context
from accounts.services.onboarding.feature_flags import get_onboarding_flags
from accounts.services.onboarding.lifecycle_completion import lifecycle_target_completed
from accounts.services.onboarding.lifecycle_eligibility import (
    evaluate_lifecycle_decision,
)
from accounts.services.onboarding.lifecycle_preferences import lifecycle_preference_for
from accounts.services.onboarding.lifecycle_template_context import (
    build_lifecycle_template_context,
    subject_for_campaign,
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

_NON_CLOUD_SUPPRESSION_REASON = "cloud_deployment_required"


@dataclass(frozen=True)
class LifecycleSendBatchResult:
    run_id: uuid.UUID
    evaluated: int
    sent: int
    suppressed: int
    failed: int
    skipped: int
    status_counts: dict
    suppression_counts: dict

    def to_payload(self):
        return {
            "run_id": str(self.run_id),
            "evaluated": self.evaluated,
            "sent": self.sent,
            "suppressed": self.suppressed,
            "failed": self.failed,
            "skipped": self.skipped,
            "status_counts": self.status_counts,
            "suppression_counts": self.suppression_counts,
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
    try:
        from ee.usage.deployment import DeploymentMode
    except ImportError:
        return False
    return bool(DeploymentMode.is_cloud())


def _internal_route(route):
    if not isinstance(route, str) or not route:
        return False
    parts = urlsplit(route)
    return not parts.scheme and not parts.netloc and route.startswith("/")


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
        group_field = {
            "welcome": "first_action_recovery_enabled",
            "recovery": "first_action_recovery_enabled",
            "sample": "sample_bridge_enabled",
            "first_signal": "first_action_recovery_enabled",
            "prompt": "first_action_recovery_enabled",
            "agent": "first_action_recovery_enabled",
            "gateway": "first_action_recovery_enabled",
            "next_loop": "next_loop_enabled",
            "activation_success": "daily_digest_enabled",
        }.get(campaign.get("campaign_group") if campaign else None)
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
    if successful.filter(created_at__gte=now - timedelta(hours=24)).exists():
        return "frequency_capped"
    if successful.filter(
        campaign_group=campaign["campaign_group"],
        created_at__gte=now - timedelta(hours=72),
    ).exists():
        return "frequency_capped"
    if (
        campaign.get("frequency_cap_key") == "daily_digest"
        and successful.filter(
            campaign_group=campaign["campaign_group"],
            created_at__gte=now - timedelta(hours=24),
        ).exists()
    ):
        return "frequency_capped"
    if campaign["campaign_group"] in {"recovery", "first_signal", "next_loop"}:
        if successful.filter(campaign_group=campaign["campaign_group"]).count() >= 2:
            return "frequency_capped"
    return None


def _digest_preview_for(decision, evaluation_log):
    return (decision.metadata or {}).get("digest_preview") or (
        evaluation_log.metadata or {}
    ).get("digest_preview")


def _missing_required_digest_preview(evaluation_log, campaign, decision):
    if not campaign or not campaign.get("requires_digest_preview"):
        return False
    return not bool(_digest_preview_for(decision, evaluation_log))


def _suppression_reason(
    evaluation_log,
    flags,
    decision,
    now,
    *,
    require_campaign_group_allowlist=False,
):
    campaign = decision.campaign
    if not flags.get("onboarding_lifecycle_email_dry_run"):
        return "dry_run_not_eligible"
    if not flags.get("onboarding_lifecycle_send_enabled"):
        return "send_flag_disabled"
    if not campaign:
        return "dry_run_not_eligible"
    if not flags.get(campaign["dry_run_flag"]):
        return "campaign_flag_disabled"
    if evaluation_log.status != OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE:
        return "dry_run_not_eligible"
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
    return None


def _send_log_defaults(evaluation_log, campaign, decision, now, cohort):
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
    return {
        "user": evaluation_log.user,
        "organization": evaluation_log.organization,
        "workspace": evaluation_log.workspace,
        "campaign_key": campaign.get("campaign_key") or evaluation_log.campaign_key,
        "campaign_group": campaign.get("campaign_group")
        or evaluation_log.campaign_group,
        "template_key": campaign.get("template_key") or evaluation_log.template_key,
        "template_version": campaign.get("template_version")
        or evaluation_log.template_version,
        "primary_path": snapshot.get("primary_path") or evaluation_log.primary_path,
        "activation_stage": snapshot.get("stage") or evaluation_log.activation_stage,
        "recommended_action_id": recommended_action.get("id")
        or evaluation_log.recommendation_id,
        "target_success_event": campaign.get("target_success_event")
        or evaluation_log.target_success_event,
        "target_route": decision.target_url or evaluation_log.target_url or "",
        "queued_at": now,
        "metadata": metadata,
    }


def _get_or_create_send_log(evaluation_log, campaign, decision, now, cohort):
    defaults = _send_log_defaults(evaluation_log, campaign, decision, now, cohort)
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
            if (
                not created
                and send_log.status == OnboardingLifecycleSendLog.STATUS_QUEUED
            ):
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
):
    now = now or timezone.now()
    _context, flags, decision = _fresh_decision(evaluation_log, now)
    campaign = decision.campaign or evaluation_log.registry_snapshot or {}
    send_log = _get_or_create_send_log(evaluation_log, campaign, decision, now, cohort)
    if send_log.status in SUCCESS_SEND_STATUSES:
        return send_log
    reason = _suppression_reason(
        evaluation_log,
        flags,
        decision,
        now,
        require_campaign_group_allowlist=require_campaign_group_allowlist,
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


def send_onboarding_lifecycle_email(send_log, *, now=None):
    now = now or timezone.now()
    if send_log.status in SUCCESS_SEND_STATUSES:
        return send_log
    if send_log.status == OnboardingLifecycleSendLog.STATUS_SUPPRESSED:
        return send_log
    if _target_success_event_completed(send_log):
        return _mark_suppressed(send_log, "target_success_event_completed", now)
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
        return _mark_suppressed(send_log, _NON_CLOUD_SUPPRESSION_REASON, now)
    try:
        email_helper(
            subject_for_campaign(campaign),
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
):
    now = now or timezone.now()
    run_id = uuid.uuid4()
    queryset = OnboardingLifecycleEvaluationLog.no_workspace_objects.filter(
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
    ).order_by("-evaluated_at")
    if campaign_group:
        queryset = queryset.filter(campaign_group=campaign_group)
    if user_id:
        queryset = queryset.filter(user_id=user_id)
    if workspace_id:
        queryset = queryset.filter(workspace_id=workspace_id)

    status_counts = Counter()
    suppression_counts = Counter()
    evaluated = sent = suppressed = failed = skipped = 0

    for evaluation_log in queryset[:limit]:
        evaluated += 1
        if dry_run:
            _context, flags, decision = _fresh_decision(evaluation_log, now)
            reason = _suppression_reason(
                evaluation_log,
                flags,
                decision,
                now,
                require_campaign_group_allowlist=require_campaign_group_allowlist,
            )
            status = "would_suppress" if reason else "would_send"
            status_counts[status] += 1
            if reason:
                suppression_counts[reason] += 1
            continue
        send_log = queue_onboarding_lifecycle_email(
            evaluation_log,
            now=now,
            cohort=cohort,
            require_campaign_group_allowlist=require_campaign_group_allowlist,
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
        evaluated=evaluated,
        sent=sent,
        suppressed=suppressed,
        failed=failed,
        skipped=skipped,
        status_counts=dict(status_counts),
        suppression_counts=dict(suppression_counts),
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
    if not event.user_id or event.is_sample:
        return None
    send_log = (
        OnboardingLifecycleSendLog.no_workspace_objects.filter(
            user=event.user,
            organization=event.organization,
            workspace=event.workspace,
            target_success_event=event.event_name,
            status__in=[
                OnboardingLifecycleSendLog.STATUS_SENT,
                OnboardingLifecycleSendLog.STATUS_CLICKED,
            ],
            sent_at__lte=event.occurred_at,
        )
        .order_by("-clicked_at", "-sent_at")
        .first()
    )
    if not send_log:
        return None
    send_log.status = OnboardingLifecycleSendLog.STATUS_COMPLETED
    send_log.completed_at = event.occurred_at
    send_log.save(update_fields=["status", "completed_at", "updated_at"])
    _track("lifecycle_email_completed", send_log)
    return send_log
