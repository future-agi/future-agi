from __future__ import annotations

from django.core import signing
from django.utils import timezone

SALT = "accounts.onboarding.lifecycle"
MAX_TOKEN_AGE_SECONDS = 30 * 24 * 60 * 60


def lifecycle_token_payload(*, send_log, kind, now=None):
    now = now or timezone.now()
    return {
        "kind": kind,
        "send_log_id": str(send_log.id),
        "user_id": str(send_log.user_id),
        "workspace_id": str(send_log.workspace_id) if send_log.workspace_id else None,
        "campaign_key": send_log.campaign_key,
        "target_success_event": send_log.target_success_event,
        "created_at": now.isoformat(),
    }


def sign_lifecycle_token(*, send_log, kind, now=None):
    return signing.dumps(
        lifecycle_token_payload(send_log=send_log, kind=kind, now=now),
        salt=SALT,
        compress=True,
    )


def verify_lifecycle_token(token, *, kind=None, max_age=MAX_TOKEN_AGE_SECONDS):
    try:
        payload = signing.loads(token, salt=SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    if kind and payload.get("kind") != kind:
        return None
    return payload
