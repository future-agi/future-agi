"""Own key commits; remote effects never precede the durable local pair."""

from contextlib import contextmanager

import structlog
from django.db import DatabaseError, router, transaction

from accounts.models.audit_log import AuditAction
from agentcc.models import AgentccAPIKey
from agentcc.services import auth_bridge
from agentcc.services.key_audit import record_key_event
from agentcc.services.key_issuance import Issuance, KeyTransactionUnavailable

logger = structlog.get_logger(__name__)


def require_key_transaction_boundary(using=None):
    """Refuse ambient/manual transactions before lookup, mutation or callbacks."""
    if using is None:
        using = AgentccAPIKey.no_workspace_objects.select_for_update().db
    connection = transaction.get_connection(using)
    if connection.in_atomic_block or not connection.get_autocommit():
        raise KeyTransactionUnavailable("Key transaction unavailable")
    return using


@contextmanager
def key_creation_transaction():
    """Refuse caller transactions, including manual transactions and TestCase.

    durable=True alone does not reject manual autocommit-off transactions or
    Django TestCase's special atomic blocks. Check both public connection flags
    before any membership lookup, key generation, local mutation or remote call.
    The API callback itself opts out of ATOMIC_REQUESTS, not just its method.
    """
    using = require_key_transaction_boundary()
    issuance = Issuance(using)
    try:
        with transaction.atomic(using=using, durable=True):
            # Registered first, but runs only after the actual commit and after
            # Django restores autocommit. No connection hooks or rollback state.
            transaction.on_commit(issuance.after_commit, using=using, robust=True)
            yield issuance
    except DatabaseError:
        raise KeyTransactionUnavailable("Key transaction unavailable") from None
    if not issuance.committed:
        # Silent set_rollback(True) discards on_commit too.
        raise KeyTransactionUnavailable("Key transaction unavailable")


def revoke_key_with_audit(api_key, actor, *, outcome=None, locked_source=False):
    """Audit then push: standalone owns a durable commit; rotation owns its lock.

    Rotation passes the exact actor-owned row it locked for issuance; it does
    not substitute a weaker admin-authorized lookup. Standalone revoke retains
    its separately authorized own-or-admin object access and locked reread.
    """
    using = router.db_for_write(AgentccAPIKey, instance=api_key)
    if locked_source:
        if (
            api_key._state.db != using
            or api_key.user_id != actor.pk
            or not transaction.get_connection(using).in_atomic_block
        ):
            raise KeyTransactionUnavailable("Key transaction unavailable")
    else:
        using = require_key_transaction_boundary(using)
    if outcome is None:
        outcome = {}
    outcome["gateway_failed"] = True
    committed = False

    def push_revocation():
        nonlocal committed
        committed = True
        try:
            confirmed = auth_bridge.get_gateway_client().revoke_key_confirmed(
                gateway_key_id
            )
            outcome["gateway_failed"] = confirmed is not True
        except Exception:
            logger.warning("gateway_key_revocation_push_pending")

    try:
        with transaction.atomic(using=using, durable=not locked_source):
            if not locked_source:
                api_key = (
                    AgentccAPIKey.unscoped_objects.using(using)
                    .select_for_update()
                    .get(pk=api_key.pk, organization_id=api_key.organization_id)
                )
                # The first revoker owns its audit and after-commit push. A
                # repeated command is not a retry; explicit sync repairs drift.
                if api_key.deleted or api_key.status == AgentccAPIKey.REVOKED:
                    return api_key, outcome["gateway_failed"]
            gateway_key_id = api_key.gateway_key_id
            api_key.status = AgentccAPIKey.REVOKED
            api_key.save(using=using, update_fields=["status", "updated_at"])
            record_key_event(api_key, actor, AuditAction.GATEWAY_KEY_REVOKED)
            transaction.on_commit(push_revocation, using=using, robust=True)
    except DatabaseError:
        raise KeyTransactionUnavailable("Key transaction unavailable") from None
    if not locked_source and not committed:
        raise KeyTransactionUnavailable("Key transaction unavailable")
    return api_key, outcome["gateway_failed"]
