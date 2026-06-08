from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.authentication import decrypt_message, generate_encrypted_message
from accounts.models import (
    NotificationChannel,
    NotificationDeliveryLog,
    NotificationPreference,
)
from accounts.services.onboarding.notification_registry import (
    NOTIFICATION_CHANNELS,
    NOTIFICATION_FAMILIES,
    family_payloads,
    validate_family,
)

EMAIL_SETTING_KEYS = {"emails", "recipients"}
SECRET_SETTING_KEYS = {"url", "webhook_url", "secret", "token"}


@dataclass(frozen=True)
class NotificationPreferenceDecision:
    allowed: bool
    family: str
    channel: str
    reason: str | None
    source: str
    preference_id: str | None = None

    def to_payload(self):
        return {
            "allowed": self.allowed,
            "family": self.family,
            "channel": self.channel,
            "reason": self.reason,
            "source": self.source,
            "preference_id": self.preference_id,
        }


def _preference_scope(preference):
    if preference.user_id and preference.workspace_id:
        return "user_workspace"
    if preference.user_id:
        return "user"
    if preference.workspace_id:
        return "workspace"
    return "organization"


def _scope_filter(*, organization, workspace, user, scope):
    filters = {"organization": organization}
    if scope == "user":
        filters.update({"workspace": None, "user": user})
    elif scope == "workspace":
        filters.update({"workspace": workspace, "user": None})
    elif scope == "user_workspace":
        filters.update({"workspace": workspace, "user": user})
    elif scope == "organization":
        filters.update({"workspace": None, "user": None})
    else:
        raise ValidationError({"scope": "Invalid notification preference scope."})
    return filters


def _candidate_preferences(*, organization, workspace, user, family, channel):
    queryset = NotificationPreference.no_workspace_objects.filter(
        organization=organization,
        family=family,
        channel=channel,
    ).order_by("-updated_at")
    user_id = getattr(user, "id", None)
    workspace_id = getattr(workspace, "id", None)
    scoped_queries = []
    if user_id and workspace_id:
        scoped_queries.append(
            queryset.filter(user_id=user_id, workspace_id=workspace_id)
        )
    if user_id:
        scoped_queries.append(queryset.filter(user_id=user_id, workspace__isnull=True))
    if workspace_id:
        scoped_queries.append(
            queryset.filter(user__isnull=True, workspace_id=workspace_id)
        )
    scoped_queries.append(queryset.filter(user__isnull=True, workspace__isnull=True))
    ordered = []
    for scoped_query in scoped_queries:
        ordered.extend(scoped_query)
    return ordered


def _frequency_cap_suppression(
    *,
    organization,
    workspace,
    user,
    family,
    channel,
    preference,
    now,
):
    if not preference.frequency_cap_minutes:
        return None
    window_start = now - timedelta(minutes=preference.frequency_cap_minutes)
    sent_logs = NotificationDeliveryLog.no_workspace_objects.filter(
        organization=organization,
        family=family,
        channel=channel,
        status=NotificationDeliveryLog.STATUS_SENT,
        sent_at__gte=window_start,
    )
    if preference.workspace_id:
        sent_logs = sent_logs.filter(workspace=workspace)
    if preference.user_id:
        sent_logs = sent_logs.filter(user=user)
    if sent_logs.exists():
        return "frequency_capped"
    return None


def _active_channel_exists(*, organization, workspace, channel):
    if channel not in {
        NotificationPreference.CHANNEL_SLACK,
        NotificationPreference.CHANNEL_WEBHOOK,
    }:
        return True
    channel_type = (
        NotificationChannel.TYPE_SLACK_WEBHOOK
        if channel == NotificationPreference.CHANNEL_SLACK
        else NotificationChannel.TYPE_WEBHOOK
    )
    queryset = NotificationChannel.no_workspace_objects.filter(
        organization=organization,
        type=channel_type,
        is_active=True,
    )
    if workspace:
        queryset = queryset.filter(workspace__in=[workspace, None])
    else:
        queryset = queryset.filter(workspace__isnull=True)
    return queryset.exists()


def notification_channels_for_delivery(*, organization, workspace, channel):
    if channel not in {
        NotificationPreference.CHANNEL_SLACK,
        NotificationPreference.CHANNEL_WEBHOOK,
    }:
        return []
    channel_type = (
        NotificationChannel.TYPE_SLACK_WEBHOOK
        if channel == NotificationPreference.CHANNEL_SLACK
        else NotificationChannel.TYPE_WEBHOOK
    )
    queryset = NotificationChannel.no_workspace_objects.filter(
        organization=organization,
        type=channel_type,
        is_active=True,
    )
    if not workspace:
        return list(queryset.filter(workspace__isnull=True)[:20])
    workspace_channels = list(queryset.filter(workspace=workspace)[:20])
    if workspace_channels:
        return workspace_channels
    return list(queryset.filter(workspace__isnull=True)[:20])


def notification_preference_decision(
    *,
    organization,
    workspace=None,
    user=None,
    family,
    channel,
    now=None,
):
    now = now or timezone.now()
    family = validate_family(family)
    if channel not in NOTIFICATION_CHANNELS:
        raise ValueError(f"Unknown notification channel: {channel}")
    family_config = NOTIFICATION_FAMILIES[family]

    if not _active_channel_exists(
        organization=organization,
        workspace=workspace,
        channel=channel,
    ):
        return NotificationPreferenceDecision(
            allowed=False,
            family=family,
            channel=channel,
            reason="channel_not_configured",
            source="channel",
        )

    for preference in _candidate_preferences(
        organization=organization,
        workspace=workspace,
        user=user,
        family=family,
        channel=channel,
    ):
        source = _preference_scope(preference)
        if preference.mute_until and preference.mute_until > now:
            return NotificationPreferenceDecision(
                allowed=False,
                family=family,
                channel=channel,
                reason="muted",
                source=source,
                preference_id=str(preference.id),
            )
        frequency_reason = _frequency_cap_suppression(
            organization=organization,
            workspace=workspace,
            user=user,
            family=family,
            channel=channel,
            preference=preference,
            now=now,
        )
        if frequency_reason:
            return NotificationPreferenceDecision(
                allowed=False,
                family=family,
                channel=channel,
                reason=frequency_reason,
                source=source,
                preference_id=str(preference.id),
            )
        if not preference.enabled:
            if not family_config.non_critical and preference.user_id:
                return NotificationPreferenceDecision(
                    allowed=True,
                    family=family,
                    channel=channel,
                    reason="critical_family_owner_visible",
                    source=source,
                    preference_id=str(preference.id),
                )
            return NotificationPreferenceDecision(
                allowed=False,
                family=family,
                channel=channel,
                reason=(
                    "user_disabled_family"
                    if preference.user_id
                    else "workspace_disabled_family"
                ),
                source=source,
                preference_id=str(preference.id),
            )
        return NotificationPreferenceDecision(
            allowed=True,
            family=family,
            channel=channel,
            reason=None,
            source=source,
            preference_id=str(preference.id),
        )

    if channel not in family_config.default_channels:
        return NotificationPreferenceDecision(
            allowed=False,
            family=family,
            channel=channel,
            reason="channel_not_enabled",
            source="default",
        )

    return NotificationPreferenceDecision(
        allowed=True,
        family=family,
        channel=channel,
        reason=None,
        source="default",
    )


def _normalize_settings(settings):
    if settings in (None, ""):
        return {}
    if not isinstance(settings, dict):
        raise ValidationError({"settings": "Settings must be an object."})
    return settings


def upsert_notification_preference(
    *,
    organization,
    workspace,
    user,
    actor,
    scope,
    family,
    channel,
    enabled=True,
    mute_until=None,
    frequency_cap_minutes=None,
    settings=None,
):
    family = validate_family(family)
    if channel not in NOTIFICATION_CHANNELS:
        raise ValidationError({"channel": "Invalid notification channel."})
    filters = _scope_filter(
        organization=organization,
        workspace=workspace,
        user=user,
        scope=scope,
    )
    settings = _normalize_settings(settings)
    defaults = {
        "enabled": bool(enabled),
        "mute_until": mute_until,
        "frequency_cap_minutes": frequency_cap_minutes,
        "settings": settings,
        "updated_by": actor,
    }
    try:
        with transaction.atomic():
            preference, _created = (
                NotificationPreference.no_workspace_objects.update_or_create(
                    family=family,
                    channel=channel,
                    defaults={
                        **defaults,
                        "created_by": actor,
                    },
                    **filters,
                )
            )
    except IntegrityError:
        preference = NotificationPreference.no_workspace_objects.get(
            family=family,
            channel=channel,
            **filters,
        )
        for key, value in defaults.items():
            setattr(preference, key, value)
        preference.save()
    return preference


def _mask_email(email):
    if not email or "@" not in email:
        return ""
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = f"{local[:1]}*"
    else:
        masked_local = f"{local[:2]}***"
    return f"{masked_local}@{domain}"


def mask_identifier(value):
    if not value:
        return ""
    value = str(value)
    if "@" in value and not value.startswith("http"):
        return ", ".join(_mask_email(item.strip()) for item in value.split(","))
    if len(value) <= 12:
        return "****"
    return f"{value[:8]}...{value[-4:]}"


def _encrypt_channel_config(config):
    config = _normalize_settings(config)
    if not config:
        return None
    return generate_encrypted_message(config)


def _decrypt_channel_config(channel):
    if not channel.encrypted_config:
        return {}
    try:
        return decrypt_message(channel.encrypted_config)
    except Exception:
        return {}


def notification_channel_delivery_config(channel):
    return _decrypt_channel_config(channel)


def _masked_config(channel):
    config = _decrypt_channel_config(channel)
    masked = {}
    for key, value in config.items():
        if key in EMAIL_SETTING_KEYS and isinstance(value, list):
            masked[key] = [_mask_email(item) for item in value]
        elif key in SECRET_SETTING_KEYS:
            masked[key] = mask_identifier(value)
        else:
            masked[key] = value
    return masked


def _target_identifier(channel_type, config, display_name):
    if channel_type == NotificationChannel.TYPE_EMAIL_LIST:
        emails = config.get("emails") or config.get("recipients") or []
        if isinstance(emails, str):
            emails = [emails]
        return ", ".join(_mask_email(email) for email in emails)
    if channel_type == NotificationChannel.TYPE_SLACK_WEBHOOK:
        return "Slack webhook configured" if config.get("webhook_url") else ""
    if channel_type == NotificationChannel.TYPE_WEBHOOK:
        return mask_identifier(config.get("url") or display_name)
    return ""


def upsert_notification_channel(
    *,
    organization,
    workspace,
    actor,
    channel_id=None,
    type,
    display_name,
    config=None,
    is_active=True,
    metadata=None,
):
    if type not in dict(NotificationChannel.TYPE_CHOICES):
        raise ValidationError({"type": "Invalid notification channel type."})
    channel = None
    if channel_id:
        channel = NotificationChannel.no_workspace_objects.get(
            id=channel_id,
            organization=organization,
        )
        if channel.workspace_id != getattr(workspace, "id", None):
            raise ValidationError({"channel_id": "Notification channel not found."})

    config_was_provided = config is not None
    config = _normalize_settings(config) if config_was_provided else {}
    effective_config = config if config_was_provided else {}
    if channel and not config_was_provided:
        effective_config = _decrypt_channel_config(channel)

    if type == NotificationChannel.TYPE_SLACK_WEBHOOK and not effective_config.get(
        "webhook_url"
    ):
        raise ValidationError({"config": "Slack webhook URL is required."})
    if type == NotificationChannel.TYPE_WEBHOOK and not effective_config.get("url"):
        raise ValidationError({"config": "Webhook URL is required."})
    if type == NotificationChannel.TYPE_EMAIL_LIST and not (
        effective_config.get("emails") or effective_config.get("recipients")
    ):
        raise ValidationError({"config": "At least one email is required."})

    fields = {
        "organization": organization,
        "workspace": workspace,
        "type": type,
        "display_name": display_name.strip(),
        "target_identifier": _target_identifier(type, effective_config, display_name),
        "is_active": bool(is_active),
    }
    if metadata is not None or not channel:
        fields["metadata"] = metadata or {}
    encrypted = _encrypt_channel_config(config) if config_was_provided else None
    if config_was_provided:
        fields["encrypted_config"] = encrypted

    if channel:
        for key, value in fields.items():
            setattr(channel, key, value)
        channel.save()
        return channel

    return NotificationChannel.no_workspace_objects.create(
        **fields,
        created_by=actor,
    )


def serialize_preference(preference):
    return {
        "id": str(preference.id),
        "scope": _preference_scope(preference),
        "family": preference.family,
        "channel": preference.channel,
        "enabled": preference.enabled,
        "mute_until": preference.mute_until,
        "frequency_cap_minutes": preference.frequency_cap_minutes,
        "settings": preference.settings or {},
        "source": "stored",
    }


def serialize_channel(channel):
    return {
        "id": str(channel.id),
        "scope": "workspace" if channel.workspace_id else "organization",
        "type": channel.type,
        "display_name": channel.display_name,
        "target_identifier": channel.target_identifier,
        "config": _masked_config(channel),
        "is_active": channel.is_active,
        "last_tested_at": channel.last_tested_at,
        "last_test_status": channel.last_test_status,
        "metadata": channel.metadata or {},
    }


def serialize_delivery_log(log):
    return {
        "id": str(log.id),
        "family": log.family,
        "source_type": log.source_type,
        "source_id": log.source_id,
        "channel": log.channel,
        "recipient_type": log.recipient_type,
        "recipient_identifier_masked": log.recipient_identifier_masked,
        "notification_key": log.notification_key,
        "stage": log.stage,
        "severity": log.severity,
        "status": log.status,
        "suppressed_reason": log.suppressed_reason,
        "route_url": log.route_url,
        "sent_at": log.sent_at,
        "created_at": log.created_at,
        "metadata": log.metadata or {},
    }


def notification_settings_payload(*, user, organization, workspace, can_manage):
    preferences = NotificationPreference.no_workspace_objects.filter(
        organization=organization,
    ).filter(
        models_scope_query(user=user, workspace=workspace),
    )
    channels = NotificationChannel.no_workspace_objects.filter(
        organization=organization,
    ).filter(
        models_channel_scope_query(workspace=workspace),
    )
    logs = NotificationDeliveryLog.no_workspace_objects.filter(
        organization=organization,
    )
    if workspace:
        logs = logs.filter(workspace__in=[workspace, None])
    return {
        "families": family_payloads(),
        "channels": [serialize_channel(channel) for channel in channels[:50]],
        "preferences": [
            serialize_preference(preference) for preference in preferences[:200]
        ],
        "decisions": [
            notification_preference_decision(
                user=user,
                organization=organization,
                workspace=workspace,
                family=family_id,
                channel=channel,
            ).to_payload()
            for family_id in NOTIFICATION_FAMILIES
            for channel in NOTIFICATION_CHANNELS
        ],
        "delivery_logs": [serialize_delivery_log(log) for log in logs[:25]],
        "can_manage_workspace": bool(can_manage),
    }


def models_scope_query(*, user, workspace):
    from django.db.models import Q

    query = Q(workspace__isnull=True, user__isnull=True)
    if workspace:
        query |= Q(workspace=workspace, user__isnull=True)
    if user:
        query |= Q(workspace__isnull=True, user=user)
    if user and workspace:
        query |= Q(workspace=workspace, user=user)
    return query


def models_channel_scope_query(*, workspace):
    from django.db.models import Q

    query = Q(workspace__isnull=True)
    if workspace:
        query |= Q(workspace=workspace)
    return query


def record_notification_delivery(
    *,
    organization,
    workspace=None,
    user=None,
    family,
    source_type,
    channel,
    status,
    source_id=None,
    recipient_type="",
    recipient_identifier="",
    notification_key="",
    idempotency_key=None,
    stage="",
    severity="",
    suppressed_reason=None,
    route_url="",
    error=None,
    metadata=None,
    now=None,
):
    now = now or timezone.now()
    defaults = {
        "workspace": workspace,
        "user": user,
        "family": family,
        "source_type": source_type,
        "source_id": str(source_id) if source_id else None,
        "channel": channel,
        "recipient_type": recipient_type,
        "recipient_identifier_masked": mask_identifier(recipient_identifier),
        "notification_key": notification_key,
        "stage": stage,
        "severity": severity,
        "status": status,
        "suppressed_reason": suppressed_reason,
        "route_url": route_url,
        "sent_at": now if status == NotificationDeliveryLog.STATUS_SENT else None,
        "error": (str(error)[:1000] if error else None),
        "metadata": metadata or {},
    }
    if idempotency_key:
        log, created = NotificationDeliveryLog.no_workspace_objects.get_or_create(
            organization=organization,
            idempotency_key=idempotency_key,
            defaults=defaults,
        )
        if not created:
            for key, value in defaults.items():
                setattr(log, key, value)
            log.save()
        return log
    return NotificationDeliveryLog.no_workspace_objects.create(
        organization=organization,
        **defaults,
    )


def test_notification_channel(*, channel, actor, now=None):
    now = now or timezone.now()
    channel.last_tested_at = now
    channel.last_test_status = NotificationChannel.STATUS_READY
    channel.save(update_fields=["last_tested_at", "last_test_status", "updated_at"])
    delivery_channel = (
        NotificationPreference.CHANNEL_SLACK
        if channel.type == NotificationChannel.TYPE_SLACK_WEBHOOK
        else NotificationPreference.CHANNEL_WEBHOOK
    )
    if channel.type == NotificationChannel.TYPE_EMAIL_LIST:
        delivery_channel = NotificationPreference.CHANNEL_EMAIL
    record_notification_delivery(
        organization=channel.organization,
        workspace=channel.workspace,
        user=actor,
        family=NotificationPreference.FAMILY_WORKSPACE_ADMIN,
        source_type="notification_channel",
        source_id=str(channel.id),
        channel=delivery_channel,
        status=NotificationDeliveryLog.STATUS_ELIGIBLE,
        recipient_type=channel.type,
        recipient_identifier=channel.target_identifier,
        notification_key="notification_channel_test",
        idempotency_key=f"notification_channel_test:{channel.id}:{now.isoformat()}",
        stage="test",
        severity="info",
        metadata={"dry_run": True},
        now=now,
    )
    return channel
