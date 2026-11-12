"""Verified email for likes, reports and new publishers (SPEC §6.1, decision 13; HUB-25).

`send_link` mails a signed link to the account's current address; `confirm` checks the signature,
the account and the address, and records an `EmailVerification`. Nothing else writes one, so an
account counts as verified only after a person opened a link sent to its address, while signed in
as that account. A changed email address needs a new confirmation.
"""

from __future__ import annotations

from django.core import signing
from django.db import transaction
from django.utils import timezone

from omega_hub import conf
from omega_hub.errors import HubError
from omega_hub.models import EmailVerification
from omega_hub.services import audit

SALT = "omega_hub.email-verification"
MAX_AGE_SECONDS = 24 * 3600
UNVERIFIED = "OMEGA_HUB_EMAIL_UNVERIFIED"


def _normal(email) -> str:
    return (email or "").strip().lower()


def current_email(user) -> str:
    """The account's address as stored now. A request's user can come from the platform's auth cache,
    which may still hold the address from before a change."""
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=user.pk).values_list("email", flat=True).first() or ""


def is_verified(user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    row = EmailVerification.objects.filter(user_id=user.pk).values_list("email", "user__email").first()
    return row is not None and bool(row[1]) and _normal(row[0]) == _normal(row[1])


def require_verified(user) -> None:
    if not is_verified(user):
        raise HubError(UNVERIFIED, 403)


def _token(user, email: str) -> str:
    return signing.dumps({"u": str(user.pk), "e": _normal(email)}, salt=SALT, compress=True)


def link_for(user, email: str) -> str:
    return f"{conf.app_url()}/verify-email?token={_token(user, email)}"


def send_link(user) -> dict:
    """Mail the link (after commit); returns what the page shows. Already verified: nothing is sent."""
    from omega_hub.services import notify

    if is_verified(user):
        return {"verified": True, "sent": False}
    email = current_email(user)
    if not email:
        raise HubError("OMEGA_HUB_INVALID", 400, "This account has no email address.")
    notify._send("verify_email", [email], {"name": user.name or email, "email": email, "link": link_for(user, email)})
    return {"verified": False, "sent": True, "sentTo": email}


@transaction.atomic
def confirm(user, token) -> dict:
    if not isinstance(token, str) or not token:
        raise HubError("OMEGA_HUB_EMAIL_LINK_INVALID", 400)
    try:
        claim = signing.loads(token, salt=SALT, max_age=MAX_AGE_SECONDS)
    except signing.SignatureExpired as exc:
        raise HubError("OMEGA_HUB_EMAIL_LINK_EXPIRED", 400) from exc
    except signing.BadSignature as exc:
        raise HubError("OMEGA_HUB_EMAIL_LINK_INVALID", 400) from exc
    if not isinstance(claim, dict) or claim.get("u") != str(user.pk):
        raise HubError("OMEGA_HUB_EMAIL_LINK_INVALID", 400, "This link was sent to another account. Sign in as that account and open it again.")
    email = current_email(user)
    if not email or claim.get("e") != _normal(email):
        raise HubError("OMEGA_HUB_EMAIL_LINK_INVALID", 400, "Your email address changed after this link was sent. Send a new link.")
    if is_verified(user):
        return {"verified": True}
    EmailVerification.objects.update_or_create(
        user_id=user.pk, defaults={"email": email, "method": EmailVerification.Method.LINK, "verified_at": timezone.now()}
    )
    audit.record("email.verified", target_type="user", target_id=user.pk, actor=user)
    return {"verified": True}
