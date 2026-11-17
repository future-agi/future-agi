"""Durable OAuth cleanup, separate from whether a new sign-in is allowed.

Local revocation happens first. Gateway eviction is a hard reconnect barrier.
Vendor outcomes are independent: confirmed, pending (retry Disconnect), or
unconfirmed (terminal refusal/unsupported). A successful replacement sign-in
retries an old pending grant once, then records unconfirmed if it still fails.
Neither terminal outcome retains the old token pair. Receipts contain only
fixed reason codes, never credentials or upstream response text.
"""

from django.db import transaction
from django.db.models import Q

from agentcc.models.mcp_cleanup_receipt import AgentccMCPCleanupReceipt as Receipt
from agentcc.models.mcp_credential import AgentccMCPCredential as Credential
from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
from agentcc.services.credential_manager import encrypt_token
from agentcc.services.mcp_revocation import Revocation
from agentcc.services.mcp_user_credential_lifecycle import lock_identity
from agentcc.services.org_config_writer import lock_org

UNFINISHED = (
    ~Q(status="revoked") | Q(oauth_revoke_pending=True) | Q(gateway_purge_pending=True)
)


def pending(row):
    return row.oauth_revoke_pending or row.gateway_purge_pending


def receipt(row, trigger, step, outcome, reason=""):
    Receipt.objects.create(
        credential=row,
        trigger=trigger,
        step=step,
        outcome=outcome,
        reason=reason,
    )


def mark_revoked(row, *, trigger="disconnect", record=False):
    """Caller holds the identity (personal) or org (shared) and row locks. Persist obligations before any I/O."""
    if row.status != "revoked":
        row.oauth_revoke_pending = True
        row.oauth_revoke_outcome = "pending"
    row.status = "revoked"
    row.gateway_purge_pending = True
    row.save(
        update_fields=[
            "status",
            "oauth_revoke_pending",
            "oauth_revoke_outcome",
            "gateway_purge_pending",
        ]
    )
    if record:
        if row.oauth_revoke_pending:
            receipt(row, trigger, "vendor_revocation", "pending", "recorded")
        receipt(row, trigger, "gateway_purge", "pending", "recorded")


def _vendor_cleanup(row, trigger, *, terminal=False):
    from agentcc.services import mcp_upstream_oauth as connect

    if not row.oauth_revoke_pending:
        return
    result = connect._revoke_upstream(
        row.organization,
        row.server_id,
        row.oauth_issuer,
        row.oauth_client_id,
        row.encrypted_refresh,
        row.encrypted_secret,
        org_only=row.oauth_org_only,
    )
    # Keep the historical fake seam usable without declaring False confirmed.
    if not isinstance(result, Revocation):
        result = Revocation("pending" if result is False else "confirmed")
    outcome = (
        "unconfirmed" if terminal and result.outcome == "pending" else result.outcome
    )
    row.oauth_revoke_outcome = outcome
    row.oauth_revoke_pending = outcome == "pending"
    if not row.oauth_revoke_pending:
        row.encrypted_refresh = None
        # encrypt_token passes empty strings through; an inert tombstone keeps
        # the vault's encrypted-at-rest invariant without retaining the token.
        row.encrypted_secret = encrypt_token("revoked")
    row.save(
        update_fields=[
            "oauth_revoke_outcome",
            "oauth_revoke_pending",
            "encrypted_refresh",
            "encrypted_secret",
        ]
    )
    receipt(row, trigger, "vendor_revocation", outcome, result.reason)


def finish(org, rows, user_id, *, config_synced=True):
    """Retry only outstanding steps, serialised with a replacement sign-in."""
    from agentcc.services import mcp_upstream_oauth as connect

    if not rows:
        return False, True, False
    with transaction.atomic():
        if user_id is None:
            lock_org(org.pk)
        else:
            lock_identity(org.pk, user_id, rows[0].server_id)
        rows = list(
            Credential.objects.select_for_update().filter(
                pk__in=[r.pk for r in rows],
                status="revoked",
            )
        )
        for row in rows:
            _vendor_cleanup(row, "disconnect")
        eviction = [row for row in rows if row.gateway_purge_pending]
        synced = config_synced
        if eviction:
            synced = (
                bool(
                    connect.gateway_credentials.purge(
                        str(org.pk),
                        rows[0].server_id,
                        str(user_id) if user_id else None,
                    )
                )
                and config_synced
            )
            for row in eviction:
                row.gateway_purge_pending = not synced
                row.save(update_fields=["gateway_purge_pending"])
                receipt(
                    row,
                    "disconnect",
                    "gateway_purge",
                    "confirmed" if synced else "pending",
                    "" if synced else "purge_failed",
                )
        return True, synced, any(pending(row) for row in rows)


def disconnect_user(org, user, server_id):
    from agentcc.services import mcp_oauth_cancellation

    with transaction.atomic():
        lock_org(org.pk)
        lock_identity(org.pk, user.pk, server_id)
        mcp_oauth_cancellation.cancel(org.pk, user.pk, server_id)
        rows = list(
            Credential.objects.select_for_update()
            .filter(
                organization=org,
                server_id=server_id,
                scope="user",
                user=user,
                kind="oauth",
            )
            .filter(UNFINISHED)
        )
        AgentccMCPConnectState.objects.filter(
            organization=org,
            user=user,
            server_id=server_id,
            mode="user",
        ).delete()
        for row in rows:
            mark_revoked(row)
    return finish(org, rows, user.pk)


def supersede(org_id, user_id, server_id):
    """Caller holds the identity lock; a new grant never inherits vendor cleanup."""
    from agentcc.services.mcp_upstream_oauth import ConnectRefused

    rows = Credential.objects.select_for_update().filter(
        organization_id=org_id,
        user_id=user_id,
        scope="user",
        server_id=server_id,
        kind="oauth",
    )
    if rows.filter(gateway_purge_pending=True).exists():
        raise ConnectRefused(
            "cleanup_pending", "retry gateway eviction before connecting", 409
        )
    for row in rows.filter(status="revoked", oauth_revoke_pending=True):
        _vendor_cleanup(row, "superseded", terminal=True)


def upstream_outcome(org, user, server_id, mode="user"):
    row = (
        Credential.objects.filter(
            organization=org,
            user=user if mode == "user" else None,
            scope=mode,
            server_id=server_id,
            kind="oauth",
        )
        .order_by("-created_at")
        .first()
    )
    return (row.oauth_revoke_outcome or None) if row else None
