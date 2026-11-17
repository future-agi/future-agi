"""Required, value-minimal key events in the key's own database transaction."""

from django.db import router, transaction

from accounts.models.audit_log import AuditLog, AuditScope
from agentcc.models import AgentccAPIKey


class KeyAuditUnavailable(Exception):
    """No key mutation may commit without its required actor audit."""


def record_key_event(api_key, actor, action, *, previous_key=None):
    """Insert directly: the shared best-effort logger may mute or swallow writes.

    A rotation row describes replacement creation, not gateway revocation.
    Require the key's write-selected connection, not a routed audit database.
    Never include a secret, hash, prefix, contact label, or request body here.
    """
    using = router.db_for_write(AgentccAPIKey, instance=api_key)
    if (
        not using
        or api_key._state.db != using
        or not transaction.get_connection(using).in_atomic_block
        or actor is None
        or actor.pk is None
    ):
        raise KeyAuditUnavailable("Required key audit unavailable")
    changes = {}
    metadata = {}
    if previous_key is not None:
        if previous_key._state.db != using:
            raise KeyAuditUnavailable("Required key audit unavailable")
        changes = {
            "old_key_id": str(previous_key.pk),
            "new_key_id": str(api_key.pk),
        }
        metadata = {"phase": "replacement_created"}
    try:
        entry = AuditLog.no_workspace_objects.using(using).create(
            organization_id=api_key.organization_id,
            actor_id=actor.pk,
            action=action,
            scope=AuditScope.ORGANIZATION,
            target_id=api_key.pk,
            changes=changes,
            metadata=metadata,
        )
        if entry is None or entry._state.adding or entry._state.db != using:
            raise KeyAuditUnavailable("Required key audit unavailable")
    except Exception:
        # Propagate a value-free failure; the enclosing key transaction rolls back.
        raise KeyAuditUnavailable("Required key audit unavailable") from None
    return entry
