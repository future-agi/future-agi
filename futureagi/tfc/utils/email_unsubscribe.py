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
from datetime import timedelta
from typing import NamedTuple
from urllib.parse import quote

import structlog
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction

from tfc.routers import force_primary

ANNOTATION_DIGEST = "annotation_digest"
ANNOTATION_DISCUSSION = "annotation_discussion"
AUTOMATION_RULE_RUNS = "automation_rule_runs"
INTEGRATION_ERRORS = "integration_errors"
MONITOR_ALERTS = "monitor_alerts"

# Category -> the words used on the unsubscribe page ("Stop <label> to ...").
CATEGORY_LABELS = {
    ANNOTATION_DIGEST: "annotation digest emails (daily and realtime)",
    ANNOTATION_DISCUSSION: "annotation comment and review emails",
    AUTOMATION_RULE_RUNS: "automation rule run emails",
    INTEGRATION_ERRORS: "integration error emails",
    MONITOR_ALERTS: "alert emails for this monitor",
}

USER_CONFIG_KEY = "email_optouts"
PAGE_PATH = "/accounts/notifications/email/unsubscribe/"
_SALT = "email-notification-unsubscribe"
logger = structlog.get_logger(__name__)
# How long a link works after the email is sent. US law (CAN-SPAM) requires
# at least 30 days; the older digest links used the same limit.
LINK_MAX_AGE = timedelta(days=30)

# What a preference change did.
CHANGED = "changed"
UNCHANGED = "unchanged"  # already in the requested state
MISSING = "missing"  # the account or monitor no longer exists
REFUSED = "refused"  # a monitor recipient can't add itself back


class Link(NamedTuple):
    """What a signed unsubscribe link refers to."""

    email: str
    category: str
    scope: str | None = None  # monitor id, for monitor alerts
    user_id: str | None = None  # the account the link was sent to


def make_token(
    email: str, category: str, scope: str | None = None, user_id=None
) -> str:
    """Signed token that ``read_token`` accepts for ``LINK_MAX_AGE`` after it
    is made. The signature stops anyone swapping in another address, account
    or category. Account categories carry the account id so the link acts on
    the account it was sent to, whatever other accounts share the address's
    spelling."""
    payload = {"e": email.strip().lower(), "c": category}
    if scope:
        payload["s"] = str(scope)
    if user_id:
        payload["u"] = str(user_id)
    return signing.dumps(payload, salt=_SALT, compress=True)


def read_token(token: str) -> Link | None:
    """The link a valid token refers to, else ``None`` (also once the token
    is older than ``LINK_MAX_AGE``)."""
    try:
        payload = signing.loads(token or "", salt=_SALT, max_age=LINK_MAX_AGE)
    except signing.BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    email, category = payload.get("e"), payload.get("c")
    if not email or category not in CATEGORY_LABELS:
        return None
    if category == MONITOR_ALERTS and not payload.get("s"):
        return None
    return Link(email, category, payload.get("s"), payload.get("u"))


def unsubscribe_url(
    email: str, category: str, scope: str | None = None, user_id=None
) -> str:
    """This install's API host, so a self-hosted link never leaves the install."""
    if user_id is None and category != MONITOR_ALERTS:
        user = _user_for_email(email)
        user_id = user.pk if user else None
    return link_url(Link(email, category, scope, user_id))


def public_api_url() -> str:
    """The API's address as a mail reader's browser reaches it: BACKEND_URL
    when a deployment sets it, else the URL the install publishes for outside
    callers (BASE_URL, else the UI's VITE_HOST_API; see
    ``settings.FI_API_PUBLIC_URL``). The same on the API and on the workers
    that send mail, and on a self-hosted install it never names Future AGI
    Cloud."""
    return (os.environ.get("BACKEND_URL") or settings.FI_API_PUBLIC_URL).rstrip("/")


def page_url(token: str) -> str:
    """The unsubscribe page for ``token``: the emailed link, and the target of
    the page's own buttons, so both reach the API the same way."""
    return f"{public_api_url()}{PAGE_PATH}?token={quote(token, safe=':')}"


def link_url(link: Link) -> str:
    return page_url(make_token(*link))


def recipient_links(recipients, category: str, scope: str | None = None):
    """A ``Link`` for each recipient who still gets ``category``, one per
    address (compared case-insensitively, first spelling kept), in order.

    Runs a fixed number of queries however many recipients there are, all on
    the primary, so an unsubscribe made a moment ago is already honoured.
    """
    addresses = {}
    for recipient in recipients:
        address = (recipient or "").strip()
        if address and address.lower() not in addresses:
            addresses[address.lower()] = address
    if category == MONITOR_ALERTS:
        return [Link(a, category, scope) for a in addresses.values()]

    users = _users_for_emails(list(addresses.values()))
    opted_out = _opted_out_ids(list(users.values()), category)
    links = []
    for address in addresses.values():
        user = users.get(address)
        if user is not None and user.pk in opted_out:
            continue
        links.append(Link(address, category, None, str(user.pk) if user else None))
    return links


def _user_model():
    from django.contrib.auth import get_user_model

    return get_user_model()


def _user_for_email(email: str):
    """The account for an address. An exact match wins over a case variant,
    so two accounts whose addresses differ only in case stay separate."""
    with force_primary():
        users = list(_user_model().objects.filter(email__iexact=email.strip())[:5])
    exact = [u for u in users if u.email == email.strip()]
    return (exact or users or [None])[0]


def _users_for_emails(addresses):
    """Address -> account, in at most two queries: exact spellings first (the
    unique index serves them), then a case-insensitive match for the rest."""
    from django.db.models.functions import Lower

    model = _user_model()
    with force_primary():
        found = {u.email: u for u in model.objects.filter(email__in=addresses)}
        rest = {a.lower(): a for a in addresses if a not in found}
        if rest:
            for user in model.objects.annotate(_email=Lower("email")).filter(
                _email__in=list(rest)
            ):
                found.setdefault(rest[user._email], user)
    return found


def _opted_out_ids(users, category: str) -> set:
    """Primary keys of the accounts that turned ``category`` off."""
    if category == ANNOTATION_DIGEST:
        from model_hub.models.annotation_queues import AnnotationNotificationState

        if not users:
            return set()
        with force_primary():
            return set(
                AnnotationNotificationState.objects.filter(
                    user__in=users, digest_enabled=False
                ).values_list("user_id", flat=True)
            )
    return {
        u.pk for u in users if category in ((u.config or {}).get(USER_CONFIG_KEY) or [])
    }


def _user_for(link: Link):
    if link.user_id:
        try:
            with force_primary():
                return _user_model().objects.filter(pk=link.user_id).first()
        except (ValueError, ValidationError):  # malformed id
            return None
    return _user_for_email(link.email)


def _opted_out(user, category: str) -> bool:
    return user.pk in _opted_out_ids([user], category)


def is_opted_out(email: str, category: str) -> bool:
    """Whether ``email`` has turned ``category`` off.

    Monitor alerts always answer False: the monitor's recipient list is the
    opt-out record, so an unsubscribed address is simply not a recipient.
    """
    if category == MONITOR_ALERTS:
        return False
    user = _user_for_email(email)
    return user is not None and _opted_out(user, category)


def describe(link: Link) -> str:
    """What the link turns off, for the page: the category, and for a
    monitor alert the monitor's name."""
    if link.category == MONITOR_ALERTS:
        monitor = _monitor(link.scope)
        if monitor is not None and monitor.name:
            return f"alert emails for the monitor \u201c{monitor.name}\u201d"
    return CATEGORY_LABELS[link.category]


def is_subscribed(link: Link) -> bool:
    """Current state, for the unsubscribe page."""
    if link.category == MONITOR_ALERTS:
        monitor = _monitor(link.scope)
        return monitor is not None and any(
            e.lower() == link.email for e in (monitor.notification_emails or [])
        )
    user = _user_for(link)
    return user is not None and not _opted_out(user, link.category)


def destination_exists(link: Link) -> bool:
    """Whether the account or monitor the link was for still exists."""
    if link.category == MONITOR_ALERTS:
        return _monitor(link.scope) is not None
    return _user_for(link) is not None


def _monitor(scope, lock: bool = False):
    from tracer.models.monitor import UserAlertMonitor

    queryset = UserAlertMonitor.no_workspace_objects.filter(id=scope, deleted=False)
    if lock:
        queryset = queryset.select_for_update()
    try:
        with force_primary():
            return queryset.first()
    except (ValueError, ValidationError):  # malformed monitor id
        return None


def set_subscribed(link: Link, subscribed: bool) -> str:
    """Turn the link's category on or off; returns what happened."""
    if link.category == MONITOR_ALERTS:
        return _remove_monitor_recipient(link) if not subscribed else REFUSED

    user = _user_for(link)
    if user is None:
        return MISSING

    if link.category == ANNOTATION_DIGEST:
        from model_hub.models.annotation_queues import AnnotationNotificationState

        state, _ = AnnotationNotificationState.objects.get_or_create(user=user)
        if state.digest_enabled == subscribed:
            return UNCHANGED
        state.digest_enabled = subscribed
        state.save(update_fields=["digest_enabled", "updated_at"])
        return CHANGED

    with transaction.atomic():
        user = type(user).objects.select_for_update().get(pk=user.pk)
        config = dict(user.config or {})
        stored = config.get(USER_CONFIG_KEY) or []
        optouts = [c for c in stored if c != link.category]
        if (len(optouts) == len(stored)) == subscribed:
            return UNCHANGED
        if not subscribed:
            optouts.append(link.category)
        config[USER_CONFIG_KEY] = sorted(optouts)
        user.config = config
        user.save(update_fields=["config"])
        transaction.on_commit(lambda: _forget_cached_sessions(user.pk))
    return CHANGED


def _forget_cached_sessions(user_id) -> None:
    """Drop the user objects the auth cache holds for this account.

    Every authenticated request reuses a user object cached with its access
    token, and an org or workspace switch saves that object's whole
    ``config``. A copy cached before this opt-out change would write the old
    opt-outs back; with the entries gone the next request loads the account
    fresh. The tokens stay active: the person stays signed in."""
    from django.core.cache import cache

    from accounts.models.auth_token import AuthToken, AuthTokenType

    try:
        token_ids = AuthToken.objects.filter(
            user_id=user_id, is_active=True, auth_type=AuthTokenType.ACCESS.value
        ).values_list("id", flat=True)
        cache.delete_many([f"access_token_{token_id}" for token_id in token_ids])
    except Exception:
        # Best effort: the opt-out is already saved, and a cache outage must
        # not turn a successful unsubscribe into an error page.
        logger.exception(
            "email_optout_session_cache_clear_failed", user_id=str(user_id)
        )


def _remove_monitor_recipient(link: Link) -> str:
    """Drop the address from one monitor's recipient list, under its row lock.
    Only someone who can edit the monitor adds an address back, so a link
    never does: the owner may have removed it on purpose since."""
    with transaction.atomic():
        monitor = _monitor(link.scope, lock=True)
        if monitor is None:
            return MISSING
        recipients = list(monitor.notification_emails or [])
        kept = [e for e in recipients if e.lower() != link.email]
        if len(kept) == len(recipients):
            return UNCHANGED
        monitor.notification_emails = kept
        monitor.save(update_fields=["notification_emails", "updated_at"])
    return CHANGED
