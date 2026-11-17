"""Atomic local credential erasure with best-effort, post-commit revocation.

Only a frozen in-memory revocation closure survives the write transaction. No
callback has a model or a database identifier, so it cannot restore credentials.
"""

import logging

from django.db import router, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def _after_commit(provider, revoke):
    try:
        status = revoke()
    except Exception:
        # Provider/transport exceptions can contain tokens. Never stringify them
        # or attach traceback/response/request objects to the outcome record.
        status = "REVOCATION_FAILED"
    try:
        logger.info(
            "credential_revocation",
            extra={"provider": provider, "status": status},
        )
    except Exception:
        # A broken log handler must not turn a committed delete into an error.
        pass


def erase_and_delete(instance, *, clear, capture):
    """Reload and lock on the WRITE router; ignore replica object contents.

    Like BaseModel.delete this is a soft delete and returns None. A repeated
    delete is inert. Explicit `.using` and instance provenance cannot redirect
    the lock, update, transaction, or on_commit hook onto a read replica.
    """
    model = type(instance)
    alias = router.db_for_write(model)
    with transaction.atomic(using=alias):
        current = model.all_objects.using(alias).select_for_update().get(pk=instance.pk)
        if current.deleted and all(
            getattr(current, field) == value for field, value in clear.items()
        ):
            for field in (*clear, "deleted", "deleted_at", "updated_at"):
                setattr(instance, field, getattr(current, field))
            return
        provider, revoke = capture(current)
        changes = {
            **clear,
            "deleted": True,
            "deleted_at": current.deleted_at or timezone.now(),
            "updated_at": timezone.now(),
        }
        model.all_objects.using(alias).filter(pk=current.pk).update(**changes)
        for field, value in changes.items():
            setattr(instance, field, value)
        transaction.on_commit(lambda: _after_commit(provider, revoke), using=alias)


def _unsupported():
    return "REVOCATION_UNSUPPORTED"


def _failed():
    return "REVOCATION_FAILED"


def _no_token():
    return "REVOCATION_NO_TOKEN"


def capture_integration_revocation(connection):
    from integrations.models import IntegrationPlatform
    from integrations.services.credentials import CredentialManager
    from integrations.services.slack_service import slack_api

    platform = connection.platform
    provider = platform if platform in IntegrationPlatform.values else "unknown"
    # All other currently supported integrations use API keys, routing keys,
    # service accounts or cloud access keys, not an OAuth grant we can revoke.
    if platform != IntegrationPlatform.SLACK:
        return provider, _unsupported
    try:
        blob = connection.encrypted_credentials
        credentials = CredentialManager.decrypt(bytes(blob)) if blob else {}
        token = credentials.get("bot_token", "")
        if not isinstance(token, str):
            return provider, _failed
    except Exception:
        # A missing key or corrupt ciphertext cannot prevent local erasure.
        return provider, _failed
    if not token:
        return provider, _no_token

    def revoke():
        # Slack's vendor equivalent of RFC 7009; existing HTTP helper, timeout
        # and operator endpoint policy are preserved. Never read the row again.
        body = slack_api("auth.revoke", token, payload={})
        return "REVOCATION_SUCCEEDED" if body.get("revoked") else "REVOCATION_FAILED"

    return provider, revoke


def disconnect_integration(connection):
    erase_and_delete(
        connection,
        clear={"encrypted_credentials": b"", "status_message": None},
        capture=capture_integration_revocation,
    )
