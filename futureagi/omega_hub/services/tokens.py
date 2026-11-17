"""Hub tokens: created in the app, shown once, revocable (SPEC §8, §9.3)."""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from omega_hub.auth import hash_token, new_token
from omega_hub.errors import HubError
from omega_hub.models import HubToken, Publisher
from omega_hub.permissions import can_act_for_publisher
from omega_hub.services import audit

DEFAULT_DAYS = 90
MAX_DAYS = 365


@transaction.atomic
def create_token(user, publisher: Publisher, name: str, days: int | None = None) -> tuple[HubToken, str]:
    if not can_act_for_publisher(user, publisher):
        raise HubError("OMEGA_HUB_NOT_PUBLISHER_OWNER", 403)
    if publisher.suspended_at is not None:
        raise HubError("OMEGA_HUB_PUBLISHER_SUSPENDED", 403)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
        raise HubError("OMEGA_HUB_INVALID", 400, "name is 1-60 characters")
    days = DEFAULT_DAYS if days in (None, "") else days
    if not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise HubError("OMEGA_HUB_INVALID", 400, f"days is 1-{MAX_DAYS}")
    plaintext = new_token()
    token = HubToken.objects.create(
        user=user, publisher=publisher, name=name.strip(), prefix=plaintext[:8],
        token_hash=hash_token(plaintext), scopes=["publish"], expires_at=timezone.now() + timedelta(days=days),
    )
    audit.record("token.created", target_type="token", target_id=token.pk, actor=user, publisher=publisher,
                 data={"prefix": token.prefix, "expiresAt": token.expires_at.isoformat()})
    return token, plaintext


@transaction.atomic
def revoke_token(user, token_id) -> HubToken:
    token = HubToken.objects.select_related("publisher").filter(pk=token_id, user=user).first()
    if token is None:
        raise HubError("OMEGA_HUB_NOT_FOUND", 404)
    if token.revoked_at is None:
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at", "updated_at"])
        audit.record("token.revoked", target_type="token", target_id=token.pk, actor=user,
                     publisher=token.publisher, data={"prefix": token.prefix})
    return token


def revoke_all_for_publisher(publisher: Publisher, actor) -> int:
    now = timezone.now()
    count = 0
    for token in HubToken.objects.filter(publisher=publisher, revoked_at__isnull=True):
        token.revoked_at = now
        token.save(update_fields=["revoked_at", "updated_at"])
        audit.record("token.revoked", target_type="token", target_id=token.pk, actor=actor,
                     publisher=publisher, data={"prefix": token.prefix, "via": "suspension"})
        count += 1
    return count
