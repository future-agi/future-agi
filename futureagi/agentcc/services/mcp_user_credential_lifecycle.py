"""Personal vault invalidation through the existing gateway purge seam.

Personal operations take a transaction-scoped identity lock before reading or
writing rows. It also covers a missing row/reconnect, without serializing other
members or servers behind refresh or eviction I/O. Shared config writes retain
their organization lock. All paths acquire the identity lock before row locks.
"""

import hashlib
import json

from django.db import connection

from agentcc.services.gateway_admin import mcp_credentials as gateway


def lock_identity(org_id, user_id, server_id):
    """Serialize one credential identity, including before its first row exists.

    PostgreSQL owns the lock until commit/rollback; processes and workers share
    it. Domain separation avoids other advisory-lock namespaces. A hash collision
    only serializes unrelated work, never permits concurrent identity changes.
    """
    identity = json.dumps(
        ["mcp-personal-credential-v1", str(org_id), str(user_id), server_id],
        separators=(",", ":"),
    )
    number = int.from_bytes(
        hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [number])


def evict(row):
    """Invalidate this person's clients; retain the cleanup obligation on failure."""
    if row.scope != "user":
        return None
    row.gateway_purge_pending = True
    row.save(update_fields=["gateway_purge_pending"])
    synced = gateway.purge(str(row.organization_id), row.server_id, str(row.user_id))
    if synced:
        row.gateway_purge_pending = False
        row.save(update_fields=["gateway_purge_pending"])
    return synced
