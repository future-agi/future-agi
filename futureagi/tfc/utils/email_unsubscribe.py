"""Per-category unsubscribe for notification emails.

``email_helper(..., notification_category=...)`` uses this module to drop
opted-out recipients, sign a per-recipient unsubscribe link, and add the
RFC 8058 ``List-Unsubscribe`` / ``List-Unsubscribe-Post`` headers that make
Gmail and Yahoo show their native Unsubscribe button. Transactional mail
(invites, password reset, credentials) passes no category and is untouched.

Opt-outs live in storage that already exists, so no schema change:

- ``annotation_digest``: ``AnnotationNotificationState.digest_enabled``.
- ``monitor_alerts``: the monitor's own ``notification_emails`` list. Alert
  recipients can be any address, not just account holders, so unsubscribing
  removes the address from that one monitor; the token carries the monitor id.
- every other category: ``User.config["email_optouts"]`` on the account
  that owns the address.
"""

import os

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction

ANNOTATION_DIGEST = "annotation_digest"
ANNOTATION_DISCUSSION = "annotation_discussion"
AUTOMATION_RULE_RUNS = "automation_rule_runs"
INTEGRATION_ERRORS = "integration_errors"
MONITOR_ALERTS = "monitor_alerts"

# Category -> the words used on the unsubscribe page ("Stop <label> to ...").
CATEGORY_LABELS = {
    ANNOTATION_DIGEST: "annotation digest emails",
    ANNOTATION_DISCUSSION: "annotation comment and review emails",
    AUTOMATION_RULE_RUNS: "automation rule run emails",
    INTEGRATION_ERRORS: "integration error emails",
    MONITOR_ALERTS: "alert emails for this monitor",
}

USER_CONFIG_KEY = "email_optouts"
_SALT = "email-notification-unsubscribe"
_UNDO_SALT = "email-notification-unsubscribe-undo"
# A monitor's recipient list belongs to its owner, so an address may only put
# itself back right after it unsubscribed (the undo button), not with a
# never-expiring link after the owner removed it.
MONITOR_UNDO_MAX_AGE = 60 * 60
# Mirrors the cap the monitor serializer enforces on notification_emails.
MONITOR_MAX_RECIPIENTS = 5


def make_token(email: str, category: str, scope: str | None = None) -> str:
    """Signed, non-expiring token. Mail-client unsubscribe buttons get pressed
    months after delivery, so the token must outlive any time limit; the
    signature stops anyone swapping in another address or category."""
    payload = {"e": email.strip().lower(), "c": category}
    if scope:
        payload["s"] = str(scope)
    return signing.dumps(payload, salt=_SALT, compress=True)


def read_token(token: str) -> tuple[str, str, str | None] | None:
    """``(email, category, scope)`` for a valid token, else ``None``."""
    try:
        payload = signing.loads(token or "", salt=_SALT)
    except signing.BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    email, category = payload.get("e"), payload.get("c")
    if not email or category not in CATEGORY_LABELS:
        return None
    if category == MONITOR_ALERTS and not payload.get("s"):
        return None
    return email, category, payload.get("s")


def make_undo_token(email: str, scope: str | None) -> str:
    """Short-lived token for a monitor's Resubscribe (undo) button."""
    return signing.dumps({"e": email, "s": str(scope)}, salt=_UNDO_SALT)


def undo_token_matches(token: str, email: str, scope: str | None) -> bool:
    try:
        payload = signing.loads(
            token or "", salt=_UNDO_SALT, max_age=MONITOR_UNDO_MAX_AGE
        )
    except signing.BadSignature:  # includes SignatureExpired
        return False
    return bool(payload == {"e": email, "s": str(scope)})


def monitor_has_room(scope: str | None) -> bool:
    monitor = _monitor(scope)
    return monitor is not None and (
        len(monitor.notification_emails or []) < MONITOR_MAX_RECIPIENTS
    )


def unsubscribe_url(email: str, category: str, scope: str | None = None) -> str:
    """This install's API host, so a self-hosted link never leaves the install."""
    backend = (os.environ.get("BACKEND_URL") or settings.BASE_URL).rstrip("/")
    token = make_token(email, category, scope)
    return f"{backend}/accounts/notifications/email/unsubscribe/?token={token}"


def _user_for(email: str):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(email__iexact=email).first()


def is_opted_out(email: str, category: str) -> bool:
    """Whether ``email`` has turned ``category`` off.

    Monitor alerts always answer False: the monitor's recipient list is the
    opt-out record, so an unsubscribed address is simply not a recipient.
    """
    if category == MONITOR_ALERTS:
        return False
    user = _user_for(email)
    if user is None:
        return False
    if category == ANNOTATION_DIGEST:
        from model_hub.models.annotation_queues import AnnotationNotificationState

        return AnnotationNotificationState.objects.filter(
            user=user, digest_enabled=False
        ).exists()
    return category in ((user.config or {}).get(USER_CONFIG_KEY) or [])


def is_subscribed(email: str, category: str, scope: str | None = None) -> bool:
    """Current state, for the unsubscribe page."""
    if category == MONITOR_ALERTS:
        monitor = _monitor(scope)
        return monitor is not None and any(
            e.lower() == email for e in (monitor.notification_emails or [])
        )
    return not is_opted_out(email, category)


def _monitor(scope):
    from tracer.models.monitor import UserAlertMonitor

    try:
        return UserAlertMonitor.no_workspace_objects.filter(
            id=scope, deleted=False
        ).first()
    except (ValueError, ValidationError):  # malformed monitor id
        return None


def set_subscribed(
    email: str, category: str, scope: str | None, subscribed: bool
) -> bool:
    """Turn ``category`` on or off for ``email``. False when there is nothing
    to change it on (no such account or monitor)."""
    if category == MONITOR_ALERTS:
        return _set_monitor_recipient(email, scope, subscribed)

    user = _user_for(email)
    if user is None:
        return False

    if category == ANNOTATION_DIGEST:
        from model_hub.models.annotation_queues import AnnotationNotificationState

        state, _ = AnnotationNotificationState.objects.get_or_create(user=user)
        state.digest_enabled = subscribed
        state.save(update_fields=["digest_enabled", "updated_at"])
        return True

    with transaction.atomic():
        user = type(user).objects.select_for_update().get(pk=user.pk)
        config = dict(user.config or {})
        optouts = [c for c in (config.get(USER_CONFIG_KEY) or []) if c != category]
        if not subscribed:
            optouts.append(category)
        config[USER_CONFIG_KEY] = sorted(optouts)
        user.config = config
        user.save(update_fields=["config"])
    return True


def _set_monitor_recipient(email: str, scope: str | None, subscribed: bool) -> bool:
    from tracer.models.monitor import UserAlertMonitor

    found = _monitor(scope)
    if found is None:
        return False
    with transaction.atomic():
        monitor = UserAlertMonitor.no_workspace_objects.select_for_update().get(
            pk=found.pk
        )
        recipients = [
            e for e in (monitor.notification_emails or []) if e.lower() != email
        ]
        if subscribed:
            recipients.append(email)
        monitor.notification_emails = recipients
        monitor.save(update_fields=["notification_emails", "updated_at"])
    return True
