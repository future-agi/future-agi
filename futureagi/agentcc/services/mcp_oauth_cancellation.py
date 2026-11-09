"""Personal OAuth cancellation generations and never-published grant cleanup.

Start, Disconnect and the callback's commit-time check hold lock_org first.
Disconnect/store then take the personal identity lock, before credential rows.
The generation survives consumed state deletion, finished cleanup and reconnect.
"""

from django.db import transaction

from agentcc.models.mcp_connect_generation import AgentccMCPConnectGeneration
from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.services.mcp_user_credential_lifecycle import lock_identity


def current(org_id, user_id, server_id):
    """Caller holds the org lock. A never-disconnected identity starts at zero."""
    value = (
        AgentccMCPConnectGeneration.objects.filter(
            organization_id=org_id, user_id=user_id, server_id=server_id
        )
        .values_list("generation", flat=True)
        .first()
    )
    return value if value is not None else 0


def cancel(org_id, user_id, server_id):
    """Caller holds org then identity locks, even when no credential exists."""
    row, _ = AgentccMCPConnectGeneration.objects.get_or_create(
        organization_id=org_id, user_id=user_id, server_id=server_id
    )
    row.generation += 1
    row.save(update_fields=["generation"])


def retain_cancelled(row, fields):
    """Commit a revoked grant's retry obligation before attempting vendor I/O.

    Never published, so this grant must not purge a later successful sign-in.
    Caller holds org and identity locks; do not raise within this transaction.
    """
    from agentcc.services.mcp_connection_cleanup import receipt

    credential = Credential.objects.create(
        organization_id=row.organization_id,
        user_id=row.user_id,
        created_by_id=row.user_id,
        server_id=row.server_id,
        scope="user",
        status="revoked",
        oauth_revoke_pending=True,
        oauth_revoke_outcome="pending",
        **fields,
    )
    receipt(credential, "cancelled", "vendor_revocation", "pending", "recorded")
    return credential


def finish_cancelled(credential):
    """Reuse durable cleanup; transient failure is retried by Disconnect."""
    from agentcc.services.mcp_connection_cleanup import _vendor_cleanup

    with transaction.atomic():
        lock_identity(
            credential.organization_id, credential.user_id, credential.server_id
        )
        credential = Credential.objects.select_for_update().get(pk=credential.pk)
        _vendor_cleanup(credential, "cancelled")
