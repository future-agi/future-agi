from types import SimpleNamespace
from uuid import UUID

from django.db.models import Q

from accounts.models import (
    NotificationDeliveryLog,
    NotificationPreference,
    OnboardingActivationEvent,
    OnboardingGoal,
    OnboardingLifecycleEvaluationLog,
    OnboardingLifecyclePreference,
    OnboardingLifecycleSendLog,
    OnboardingSampleProject,
    User,
    Workspace,
)
from accounts.models.workspace import WorkspaceMembership
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.context import resolve_onboarding_context
from accounts.services.onboarding.feature_flags import get_onboarding_flags
from accounts.services.onboarding.signal_resolver import (
    OnboardingSignals,
    collect_onboarding_signals,
)

SUPPORT_STATE_SCHEMA_VERSION = "onboarding-support-state-2026-05-31.v1"
SUPPORT_SOURCE = "support_state_inspection"

SUPPORT_NOTIFICATION_FAMILIES = (
    NotificationPreference.FAMILY_PRODUCT_ONBOARDING,
    NotificationPreference.FAMILY_DAILY_QUALITY_DIGEST,
    NotificationPreference.FAMILY_USAGE_BUDGET,
)

IMPORTANT_ONBOARDING_FLAGS = (
    "onboarding_activation_state_api",
    "onboarding_first_run_home",
    "onboarding_goal_picker",
    "onboarding_path_cards",
    "onboarding_sample_project",
    "onboarding_observe_route_modes",
    "onboarding_prompt_path",
    "onboarding_agent_path",
    "onboarding_gateway_path",
    "onboarding_eval_path",
    "onboarding_voice_path",
    "onboarding_lifecycle_send_enabled",
    "onboarding_lifecycle_email_send",
)


def _coerce_uuid(value, label):
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {value}") from exc


def _string(value):
    if value in {None, ""}:
        return None
    return str(value)


def _iso(value):
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _truncate(value, limit=180):
    if value in {None, ""}:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}..."


def mask_email(value):
    if not value or "@" not in str(value):
        return None
    local, domain = str(value).split("@", 1)
    local_start = local[:1] or "*"
    local_end = local[-1:] if len(local) > 2 else ""
    domain_parts = domain.split(".")
    domain_name = domain_parts[0] if domain_parts else ""
    domain_suffix = ".".join(domain_parts[1:])
    masked_domain = f"{domain_name[:1]}***"
    if domain_suffix:
        masked_domain = f"{masked_domain}.{domain_suffix}"
    return f"{local_start}***{local_end}@{masked_domain}"


def mask_text(value):
    if value in {None, ""}:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= 2:
        return "*" * len(text)
    return f"{text[:1]}***{text[-1:]}"


def _metadata_keys(value):
    if not isinstance(value, dict):
        return []
    return sorted(str(key) for key in value.keys())


def _compact_dict(value):
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != "" and item != []
    }


def _serialize_identity(*, user, organization, workspace, user_resolution):
    return {
        "user": {
            "id": _string(getattr(user, "id", None)),
            "email_masked": mask_email(getattr(user, "email", None)),
            "name_masked": mask_text(getattr(user, "name", None)),
            "role": _string(getattr(user, "role", None)),
            "organization_role": _string(getattr(user, "organization_role", None)),
            "resolution": user_resolution,
        },
        "organization": {
            "id": _string(getattr(organization, "id", None)),
            "name_masked": mask_text(getattr(organization, "display_name", None))
            or mask_text(getattr(organization, "name", None)),
            "region": _string(getattr(organization, "region", None)),
        },
        "workspace": {
            "id": _string(getattr(workspace, "id", None)),
            "name_masked": mask_text(getattr(workspace, "display_name", None))
            or mask_text(getattr(workspace, "name", None)),
            "is_default": bool(getattr(workspace, "is_default", False)),
            "is_active": bool(getattr(workspace, "is_active", False)),
        },
    }


def _summarize_flags(flags):
    enabled = sorted(name for name, value in flags.items() if bool(value))
    disabled = sorted(name for name, value in flags.items() if not bool(value))
    important = {
        name: bool(flags.get(name, False)) for name in IMPORTANT_ONBOARDING_FLAGS
    }
    return {
        "important": important,
        "enabled": enabled,
        "disabled": disabled,
    }


def _summarize_action(action):
    if not action:
        return None
    return _compact_dict(
        {
            "id": action.get("id"),
            "label": action.get("label"),
            "href": action.get("href"),
            "fallback_href": action.get("fallback_href"),
            "blocked": bool(action.get("blocked", False)),
            "blocked_reason": action.get("blocked_reason") or action.get("reason"),
            "requires_permission": action.get("requires_permission"),
            "event_name": action.get("event_name"),
        }
    )


def _summarize_routes(routes):
    if not isinstance(routes, dict):
        return {"total": 0, "available": [], "unavailable": []}
    available = []
    unavailable = []
    for key, route in sorted(routes.items()):
        if not isinstance(route, dict):
            continue
        target = {
            "key": key,
            "href": route.get("href"),
            "reason": route.get("reason") or route.get("blocked_reason"),
        }
        if route.get("is_available"):
            available.append(_compact_dict(target))
        else:
            unavailable.append(_compact_dict(target))
    return {
        "total": len(available) + len(unavailable),
        "available": available[:20],
        "unavailable": unavailable[:20],
    }


def _summarize_sample_state(sample):
    if not isinstance(sample, dict):
        return None
    return _compact_dict(
        {
            "available": bool(sample.get("available", False)),
            "created": bool(sample.get("created", False)),
            "status": sample.get("status"),
            "href": sample.get("href"),
            "is_hidden": bool(sample.get("is_hidden", False)),
            "hidden_reason": sample.get("hidden_reason"),
            "blocked_reason": sample.get("blocked_reason"),
            "manifest_id": sample.get("manifest_id"),
            "manifest_version": sample.get("manifest_version"),
            "missing_artifacts": sample.get("missing_artifacts") or [],
            "is_repairable": bool(sample.get("is_repairable", False)),
            "last_opened_at": _iso(sample.get("last_opened_at")),
            "artifact_ref_keys": _metadata_keys(sample.get("artifact_refs")),
            "health_keys": _metadata_keys(sample.get("health")),
            "real_setup_href": sample.get("real_setup_href"),
        }
    )


def _resolved_recommended_route(activation_summary):
    action = activation_summary.get("recommended_action") or {}
    fallback = activation_summary.get("fallback_action") or {}
    return (
        action.get("href")
        or action.get("fallback_href")
        or fallback.get("href")
        or fallback.get("fallback_href")
    )


def summarize_activation_state(activation_state, *, include_raw=False):
    if not activation_state:
        return {
            "status": "unavailable",
            "error": None,
        }
    if activation_state.get("error"):
        return {
            "status": "error",
            "error": activation_state["error"],
        }

    recommended_action = _summarize_action(activation_state.get("recommended_action"))
    fallback_action = _summarize_action(activation_state.get("fallback_action"))
    email_eligibility = activation_state.get("email_eligibility") or {}
    lifecycle = activation_state.get("lifecycle") or {}
    summary = {
        "status": "resolved",
        "schema_version": activation_state.get("schema_version"),
        "request_id": activation_state.get("request_id"),
        "server_time": _iso(activation_state.get("server_time")),
        "organization_id": activation_state.get("organization_id"),
        "workspace_id": activation_state.get("workspace_id"),
        "user_id": activation_state.get("user_id"),
        "goal": activation_state.get("goal"),
        "persona": activation_state.get("persona"),
        "primary_path": activation_state.get("primary_path"),
        "stage": activation_state.get("stage"),
        "home_mode": activation_state.get("home_mode"),
        "is_activated": bool(activation_state.get("is_activated", False)),
        "activated_at": _iso(activation_state.get("activated_at")),
        "recommended_action": recommended_action,
        "fallback_action": fallback_action,
        "current_resolved_recommended_route": None,
        "sample_project": _summarize_sample_state(
            activation_state.get("sample_project")
        ),
        "permissions": activation_state.get("permissions") or {},
        "email_eligibility": _compact_dict(
            {
                "eligible": email_eligibility.get("eligible"),
                "suppressed": email_eligibility.get("suppressed"),
                "suppression_reason": email_eligibility.get("suppression_reason"),
                "next_email_key": email_eligibility.get("next_email_key"),
                "dry_run_only": email_eligibility.get("dry_run_only"),
            }
        ),
        "lifecycle": _compact_dict(
            {
                "status": lifecycle.get("status"),
                "campaign_key": lifecycle.get("campaign_key"),
                "template_key": lifecycle.get("template_key"),
                "suppression_reason": lifecycle.get("suppression_reason"),
                "target_success_event": lifecycle.get("target_success_event"),
            }
        ),
        "route_availability": _summarize_routes(
            activation_state.get("route_availability")
        ),
        "warnings": list(activation_state.get("warnings") or []),
    }
    summary["current_resolved_recommended_route"] = _resolved_recommended_route(summary)
    if include_raw:
        summary["raw_activation_state"] = activation_state
    return summary


def _latest_goal(*, organization, workspace):
    return (
        OnboardingGoal.no_workspace_objects.filter(
            organization=organization,
            workspace=workspace,
        )
        .order_by("-is_active", "-selected_at", "-created_at")
        .first()
    )


def _summarize_goal(goal):
    if not goal:
        return None
    return {
        "id": str(goal.id),
        "goal": goal.goal,
        "primary_path": goal.primary_path,
        "source": goal.source or None,
        "reason": goal.reason or None,
        "is_active": bool(goal.is_active),
        "selected_at": _iso(goal.selected_at),
        "user_id": _string(goal.user_id),
        "metadata_keys": _metadata_keys(goal.metadata),
    }


def _latest_sample_record(*, organization, workspace):
    return (
        OnboardingSampleProject.no_workspace_objects.filter(
            organization=organization,
            workspace=workspace,
        )
        .order_by("-last_opened_at", "-updated_at", "-created_at")
        .first()
    )


def _summarize_sample_record(sample):
    if not sample:
        return None
    return {
        "id": str(sample.id),
        "manifest_id": sample.manifest_id,
        "manifest_version": sample.manifest_version,
        "status": sample.status,
        "is_hidden": bool(sample.hidden_at),
        "hidden_at": _iso(sample.hidden_at),
        "repair_attempts": sample.repair_attempts,
        "last_repair_attempt_at": _iso(sample.last_repair_attempt_at),
        "last_opened_at": _iso(sample.last_opened_at),
        "missing_artifacts": list(sample.missing_artifacts or []),
        "artifact_ref_keys": _metadata_keys(sample.artifact_refs),
        "health_keys": _metadata_keys(sample.health),
        "metadata_keys": _metadata_keys(sample.metadata),
    }


def _activation_events(*, organization, workspace, user, limit):
    queryset = OnboardingActivationEvent.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
    )
    if user:
        queryset = queryset.filter(user=user)
    return list(queryset.order_by("-occurred_at", "-created_at")[:limit])


def _summarize_activation_event(event):
    return {
        "id": str(event.id),
        "event_name": event.event_name,
        "product_path": event.product_path or None,
        "activation_stage": event.activation_stage or None,
        "source": event.source or None,
        "is_sample": bool(event.is_sample),
        "occurred_at": _iso(event.occurred_at),
        "user_id": _string(event.user_id),
        "metadata_keys": _metadata_keys(event.metadata),
    }


def _latest_lifecycle_evaluation(*, organization, workspace, user):
    queryset = OnboardingLifecycleEvaluationLog.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
    )
    if user:
        queryset = queryset.filter(user=user)
    return queryset.order_by("-evaluated_at", "-created_at").first()


def _summarize_lifecycle_evaluation(log):
    if not log:
        return None
    snapshot = log.activation_state_snapshot or {}
    return {
        "id": str(log.id),
        "run_id": str(log.run_id),
        "campaign_key": log.campaign_key,
        "campaign_group": log.campaign_group,
        "template_key": log.template_key,
        "template_version": log.template_version,
        "activation_stage": log.activation_stage,
        "primary_path": log.primary_path,
        "recommendation_id": log.recommendation_id,
        "target_action_id": log.target_action_id,
        "target_success_event": log.target_success_event,
        "target_url": log.target_url,
        "status": log.status,
        "suppression_reason": log.suppression_reason,
        "eligible_at": _iso(log.eligible_at),
        "evaluated_at": _iso(log.evaluated_at),
        "source_receipt_id": _string(log.source_receipt_id),
        "snapshot_stage": snapshot.get("stage"),
        "snapshot_primary_path": snapshot.get("primary_path"),
        "snapshot_recommended_action_id": (
            (snapshot.get("recommended_action") or {}).get("id")
            if isinstance(snapshot.get("recommended_action"), dict)
            else None
        ),
        "suppression_detail_keys": _metadata_keys(log.suppression_details),
        "metadata_keys": _metadata_keys(log.metadata),
    }


def _latest_lifecycle_send(*, organization, workspace, user):
    queryset = OnboardingLifecycleSendLog.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
    )
    if user:
        queryset = queryset.filter(user=user)
    return queryset.order_by("-created_at").first()


def _summarize_lifecycle_send(log):
    if not log:
        return None
    return {
        "id": str(log.id),
        "evaluation_log_id": _string(log.evaluation_log_id),
        "campaign_key": log.campaign_key,
        "campaign_group": log.campaign_group,
        "template_key": log.template_key,
        "template_version": log.template_version,
        "primary_path": log.primary_path,
        "activation_stage": log.activation_stage,
        "recommended_action_id": log.recommended_action_id,
        "target_success_event": log.target_success_event,
        "target_route": log.target_route,
        "status": log.status,
        "suppression_reason": log.suppression_reason,
        "provider_status": log.provider_status,
        "failure_reason": _truncate(log.failure_reason),
        "queued_at": _iso(log.queued_at),
        "sent_at": _iso(log.sent_at),
        "clicked_at": _iso(log.clicked_at),
        "completed_at": _iso(log.completed_at),
        "unsubscribed_at": _iso(log.unsubscribed_at),
        "metadata_keys": _metadata_keys(log.metadata),
    }


def _latest_notification_delivery(*, organization, workspace, user):
    queryset = NotificationDeliveryLog.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        family__in=SUPPORT_NOTIFICATION_FAMILIES,
    )
    if user:
        queryset = queryset.filter(user=user)
    return queryset.order_by("-created_at").first()


def _summarize_notification_delivery(log):
    if not log:
        return None
    return {
        "id": str(log.id),
        "family": log.family,
        "source_type": log.source_type,
        "source_id": log.source_id,
        "channel": log.channel,
        "recipient_type": log.recipient_type or None,
        "recipient_identifier_masked": log.recipient_identifier_masked or None,
        "notification_key": log.notification_key or None,
        "stage": log.stage or None,
        "severity": log.severity or None,
        "status": log.status,
        "suppressed_reason": log.suppressed_reason,
        "route_url": log.route_url,
        "sent_at": _iso(log.sent_at),
        "clicked_at": _iso(log.clicked_at),
        "completed_at": _iso(log.completed_at),
        "error": _truncate(log.error),
        "metadata_keys": _metadata_keys(log.metadata),
    }


def _notification_preferences(*, organization, workspace, user):
    queryset = NotificationPreference.no_workspace_objects.filter(
        organization=organization,
        family__in=SUPPORT_NOTIFICATION_FAMILIES,
    ).filter(Q(workspace=workspace) | Q(workspace__isnull=True))
    if user:
        queryset = queryset.filter(Q(user=user) | Q(user__isnull=True))
    else:
        queryset = queryset.filter(user__isnull=True)
    return queryset.order_by("family", "channel", "-updated_at")


def _summarize_notification_preference(preference):
    if not preference:
        return None
    if preference.user_id:
        scope = "user_workspace" if preference.workspace_id else "user"
    else:
        scope = "workspace" if preference.workspace_id else "organization"
    return {
        "id": str(preference.id),
        "scope": scope,
        "family": preference.family,
        "channel": preference.channel,
        "enabled": bool(preference.enabled),
        "mute_until": _iso(preference.mute_until),
        "frequency_cap_minutes": preference.frequency_cap_minutes,
        "settings_keys": _metadata_keys(preference.settings),
    }


def _latest_lifecycle_preference(*, organization, workspace, user):
    if not user:
        return None
    queryset = OnboardingLifecyclePreference.no_workspace_objects.filter(
        user=user,
        organization=organization,
    )
    workspace_preference = (
        queryset.filter(workspace=workspace)
        .order_by("-updated_at", "-created_at")
        .first()
    )
    if workspace_preference:
        return workspace_preference
    return (
        queryset.filter(workspace__isnull=True)
        .order_by("-updated_at", "-created_at")
        .first()
    )


def _summarize_lifecycle_preference(preference):
    if not preference:
        return None
    return {
        "id": str(preference.id),
        "scope": "workspace" if preference.workspace_id else "user",
        "onboarding_enabled": bool(preference.onboarding_enabled),
        "first_action_recovery_enabled": bool(preference.first_action_recovery_enabled),
        "sample_bridge_enabled": bool(preference.sample_bridge_enabled),
        "next_loop_enabled": bool(preference.next_loop_enabled),
        "daily_digest_enabled": bool(preference.daily_digest_enabled),
        "reactivation_enabled": bool(preference.reactivation_enabled),
        "snoozed_until": _iso(preference.snoozed_until),
        "unsubscribed_at": _iso(preference.unsubscribed_at),
        "metadata_keys": _metadata_keys(preference.metadata),
    }


def support_readiness_checks(
    *,
    flags,
    activation_summary,
    latest_sample,
    latest_lifecycle_evaluation,
    latest_lifecycle_send,
    latest_notification_delivery,
):
    has_flag_state = bool(flags)
    has_activation_stage = activation_summary.get("status") == "resolved" and bool(
        activation_summary.get("stage")
    )
    recommended_action = activation_summary.get("recommended_action") or {}
    fallback_action = activation_summary.get("fallback_action") or {}
    has_recommendation = bool(recommended_action.get("id")) and bool(
        activation_summary.get("current_resolved_recommended_route")
        or fallback_action.get("href")
    )
    activation_sample = activation_summary.get("sample_project") or {}
    has_sample_state = bool(activation_sample.get("status")) or bool(latest_sample)
    lifecycle_preview = activation_summary.get("lifecycle") or {}
    has_delivery_context = bool(
        latest_lifecycle_evaluation
        or latest_lifecycle_send
        or latest_notification_delivery
        or lifecycle_preview.get("status")
    )

    checks = {
        "flag_state": has_flag_state,
        "activation_stage": has_activation_stage,
        "recommendation": has_recommendation,
        "sample_state": has_sample_state,
        "delivery_log_context": has_delivery_context,
    }
    missing = sorted(key for key, value in checks.items() if not value)
    return {
        "ready": not missing,
        "checks": checks,
        "missing": missing,
    }


def _resolve_workspace(workspace_id):
    workspace_uuid = _coerce_uuid(workspace_id, "workspace id")
    workspace = (
        Workspace.no_workspace_objects.select_related("organization", "created_by")
        .filter(id=workspace_uuid)
        .first()
    )
    if not workspace:
        raise ValueError(f"Workspace not found: {workspace_id}")
    return workspace


def _resolve_user(*, workspace, user_id=None, user_email=None):
    explicit_user = None
    user_resolution = "workspace_created_by"
    if user_id:
        user_uuid = _coerce_uuid(user_id, "user id")
        explicit_user = User.objects.filter(id=user_uuid).first()
        user_resolution = "user_id"
        if not explicit_user:
            raise ValueError(f"User not found: {user_id}")
    if user_email:
        email_user = User.objects.filter(email__iexact=str(user_email).strip()).first()
        if not email_user:
            raise ValueError(f"User not found for email: {mask_email(user_email)}")
        if explicit_user and explicit_user.id != email_user.id:
            raise ValueError("Provided user id and email resolve to different users.")
        explicit_user = email_user
        user_resolution = "user_email"

    if explicit_user:
        return explicit_user, user_resolution

    membership = (
        WorkspaceMembership.no_workspace_objects.select_related("user")
        .filter(workspace=workspace, is_active=True)
        .order_by("-level", "-granted_at", "-created_at")
        .first()
    )
    if membership:
        return membership.user, "workspace_membership"
    return workspace.created_by, user_resolution


def _support_request(*, user, organization, workspace):
    return SimpleNamespace(
        user=user,
        organization=organization,
        workspace=workspace,
        query_params={
            "organization_id": str(organization.id),
            "workspace_id": str(workspace.id),
            "source": SUPPORT_SOURCE,
        },
    )


def _resolve_activation_packet(
    *, request, requested_flags, include_raw_activation_state
):
    try:
        context = resolve_onboarding_context(request)
        flags = get_onboarding_flags(
            user=context.user,
            organization=context.organization,
            workspace=context.workspace,
        )
        if flags.get("onboarding_activation_state_api") and (
            context.organization and context.workspace
        ):
            signals = collect_onboarding_signals(
                user=context.user,
                organization=context.organization,
                workspace=context.workspace,
            )
        else:
            signals = OnboardingSignals(first_checks={})
        activation_state = resolve_activation_state(
            context=context,
            flags=flags,
            signals=signals,
        )
        return {
            "resolved_context": {
                "organization_id": _string(getattr(context.organization, "id", None)),
                "workspace_id": _string(getattr(context.workspace, "id", None)),
                "organization_role": context.organization_role,
                "workspace_role": context.workspace_role,
                "selected_goal": context.selected_goal,
                "primary_path": context.primary_path,
                "permissions": context.permissions,
                "warnings": context.warnings,
            },
            "feature_flags": _summarize_flags(flags),
            "activation_state": summarize_activation_state(
                activation_state,
                include_raw=include_raw_activation_state,
            ),
        }
    except Exception as exc:  # pragma: no cover - defensive incident output
        return {
            "resolved_context": None,
            "feature_flags": _summarize_flags(requested_flags),
            "activation_state": summarize_activation_state(
                {
                    "error": {
                        "type": exc.__class__.__name__,
                        "message": _truncate(str(exc), limit=300),
                    }
                }
            ),
        }


def build_onboarding_support_state(
    *,
    workspace_id,
    user_id=None,
    user_email=None,
    include_raw_activation_state=False,
    event_limit=5,
):
    workspace = _resolve_workspace(workspace_id)
    organization = workspace.organization
    user, user_resolution = _resolve_user(
        workspace=workspace,
        user_id=user_id,
        user_email=user_email,
    )
    requested_flags = get_onboarding_flags(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    request = _support_request(
        user=user, organization=organization, workspace=workspace
    )
    activation_packet = _resolve_activation_packet(
        request=request,
        requested_flags=requested_flags,
        include_raw_activation_state=include_raw_activation_state,
    )

    latest_goal = _latest_goal(organization=organization, workspace=workspace)
    latest_sample = _latest_sample_record(
        organization=organization, workspace=workspace
    )
    activation_events = _activation_events(
        organization=organization,
        workspace=workspace,
        user=user,
        limit=event_limit,
    )
    latest_lifecycle_evaluation = _latest_lifecycle_evaluation(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    latest_lifecycle_send = _latest_lifecycle_send(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    latest_notification_delivery = _latest_notification_delivery(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    notification_preferences = list(
        _notification_preferences(
            organization=organization,
            workspace=workspace,
            user=user,
        )
    )
    latest_lifecycle_preference = _latest_lifecycle_preference(
        organization=organization,
        workspace=workspace,
        user=user,
    )

    activation_summary = activation_packet["activation_state"]
    logs = {
        "latest_goal": _summarize_goal(latest_goal),
        "latest_sample_project": _summarize_sample_record(latest_sample),
        "latest_activation_events": [
            _summarize_activation_event(event) for event in activation_events
        ],
        "latest_lifecycle_evaluation": _summarize_lifecycle_evaluation(
            latest_lifecycle_evaluation
        ),
        "latest_lifecycle_send": _summarize_lifecycle_send(latest_lifecycle_send),
        "latest_notification_delivery": _summarize_notification_delivery(
            latest_notification_delivery
        ),
        "notification_preferences": [
            _summarize_notification_preference(preference)
            for preference in notification_preferences
        ],
        "lifecycle_preference": _summarize_lifecycle_preference(
            latest_lifecycle_preference
        ),
    }

    return {
        "schema_version": SUPPORT_STATE_SCHEMA_VERSION,
        "source": SUPPORT_SOURCE,
        "identity": _serialize_identity(
            user=user,
            organization=organization,
            workspace=workspace,
            user_resolution=user_resolution,
        ),
        "feature_flags": {
            "requested_workspace": _summarize_flags(requested_flags),
            "resolved_context": activation_packet["feature_flags"],
        },
        "resolved_context": activation_packet["resolved_context"],
        "activation_state": activation_summary,
        "logs": logs,
        "support_readiness": support_readiness_checks(
            flags=requested_flags,
            activation_summary=activation_summary,
            latest_sample=logs["latest_sample_project"],
            latest_lifecycle_evaluation=logs["latest_lifecycle_evaluation"],
            latest_lifecycle_send=logs["latest_lifecycle_send"],
            latest_notification_delivery=logs["latest_notification_delivery"],
        ),
    }
