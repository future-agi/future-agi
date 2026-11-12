"""Per-invitation capabilities, independent of the shared password credential.

Email delivery proves recipient control for initial activation. OSS share links
allow credential setup only for an inactive identity originating in the same
organization. Active recipients must sign in; accept-only links can never become
activation capabilities. Pending status and offer generation enforce one-time use.
"""
import hashlib
import json

from django.conf import settings
from django.core import signing
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from tfc.constants.levels import INVITE_VALIDITY_DAYS

SALT = "accounts.organization-invitation.v1"


def generation(invite):
    content = {
        "created_at": invite.created_at.isoformat(),
        "level": invite.level,
        "workspace_access": invite.workspace_access,
        "invited_by": str(invite.invited_by_id),
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def pending_invitation(user, organization):
    if organization is None:
        raise ValueError("An invitation organization is required.")
    invite = OrganizationInvite.objects.filter(
        organization=organization, target_email__iexact=user.email,
        status=InviteStatus.PENDING,
    ).first()
    if invite is None or invite.is_expired:
        raise ValueError("This invitation has been cancelled or expired.")
    return invite


def make_invitation_token(user, invite, *, delivery="email"):
    if (
        invite.status != InviteStatus.PENDING or invite.is_expired
        or invite.target_email.casefold() != user.email.casefold()
        or delivery not in {"email", "share"}
    ):
        raise ValueError("Invalid invitation.")
    if delivery == "share" and (
        not user.is_active and user.organization_id != invite.organization_id
    ):
        raise ValueError("Recipient email verification is required.")
    return signing.dumps({
        "invite": str(invite.id), "organization": str(invite.organization_id),
        "user": str(user.pk), "email": user.email.casefold(),
        "generation": generation(invite), "delivery": delivery,
        "activation": not user.is_active,
    }, salt=SALT, compress=True)


def read_invitation_token(token, user_id):
    try:
        payload = signing.loads(token, salt=SALT, max_age=INVITE_VALIDITY_DAYS * 86400)
    except signing.BadSignature as exc:
        raise ValueError("Invalid invitation.") from exc
    if not isinstance(payload, dict) or payload.get("user") != str(user_id):
        raise ValueError("Invalid invitation.")
    if payload.get("delivery") not in {"email", "share"}:
        raise ValueError("Invalid invitation.")
    return payload


def invitation_matches(payload, invite, user):
    return (
        invite is not None
        and invite.status == InviteStatus.PENDING and not invite.is_expired
        and payload.get("invite") == str(invite.pk)
        and payload.get("organization") == str(invite.organization_id)
        and payload.get("user") == str(user.pk)
        and payload.get("email") == invite.target_email.casefold() == user.email.casefold()
        and payload.get("generation") == generation(invite)
        and (user.is_active or payload.get("activation") is True)
        and (payload["delivery"] != "share" or user.is_active or user.organization_id == invite.organization_id)
    )


def invitation_link(user, invite, *, delivery="email"):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = make_invitation_token(user, invite, delivery=delivery)
    return f"{settings.APP_BASE_URL}/auth/jwt/invitation/accept/{uid}/{token}"
