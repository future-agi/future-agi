"""The organization's one shared account, signed in over OAuth (CN24).

A shared server whose credential kind is ``oauth`` (``credential {mode:
shared, kind: oauth}``) is used by every key and person the org's access
rules let call it, with one account an Owner or Admin signs in through
M25x's connect flow (``mode: shared``). Here:

- ``store`` keeps the token pair as the org's vault row for the server
  (scope ``shared``, kind ``oauth``, no user), in the server's one shared
  slot: the active OAuth row is rotated in place, and a stored key there is
  revoked. In the same transaction a new config version names the row
  (``credential_id``, ``state: active``, ``kind`` kept), which the writer
  pushes; the push carries no secret for it (``assemble_for_push`` sends
  static kinds only), and the gateway fetches its token just in time with the
  org's request (``mcp-upstream-token-v1`` SharedRequest).
- ``disconnect`` revokes the org's rows and marks the config's reference
  ``revoked``; the caller revokes the token upstream and drops the gateway's
  copy.
- ``names`` is the rule the token fetch serves a row by: the server is still
  a shared OAuth server, and its config names that row as active.

Only an Owner or Admin starts, completes or ends the org's sign-in
(``is_admin``); a member can read neither its token nor its refresh token,
which no read returns.
"""

import structlog
from django.db import transaction
from django.utils import timezone

from accounts.models import Organization, User
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.services import mcp_credentials as vault
from agentcc.services import mcp_oauth_cancellation
from agentcc.services.credential_manager import encrypt_token
from agentcc.services.org_config_writer import (
    is_gateway_synced,
    lock_org,
    write_config_version_with,
)
from tfc.constants.levels import Level

logger = structlog.get_logger(__name__)

Kind = AgentccMCPCredential.Kind
Scope = AgentccMCPCredential.Scope
Status = AgentccMCPCredential.Status

SHARED = "shared"
USER = "user"
MODES = (USER, SHARED)
KIND = "oauth"


class ServerChanged(Exception):
    """The server stopped being a shared OAuth server at the origin the
    sign-in started for, while it ran."""


class ConnectCancelled(Exception):
    """Disconnect cancelled this callback's generation or removed its state."""


is_shared_oauth = vault.is_shared_oauth


def is_admin(user: User | None, org: Organization) -> bool:
    """An Owner or Admin of ``org``: who signs its shared account in or out."""
    if user is None:
        return False
    level = user.get_membership_level(org)
    return level is not None and level >= Level.ADMIN


def names(entry, row: AgentccMCPCredential) -> bool:
    """Whether the config entry ``entry`` names ``row`` as the server's
    active shared OAuth account."""
    if not is_shared_oauth(entry):
        return False
    credential = entry["credential"]
    return credential.get("credential_id") == str(row.pk) and (
        credential.get("state", Status.ACTIVE) == Status.ACTIVE
    )


def active_rows(org_id, server_id: str):
    """The org's shared OAuth rows of ``server_id`` (any status)."""
    return AgentccMCPCredential.objects.filter(
        organization_id=org_id,
        server_id=server_id,
        scope=Scope.SHARED,
        user__isnull=True,
        account_label="",
        kind=Kind.OAUTH,
    )


def store(state, tokens) -> tuple[AgentccMCPCredential, bool]:
    """Keep ``tokens`` (``mcp_upstream_oauth._Tokens``) as the org's shared
    account for the connect ``state``'s server, and write the config version
    that names it. Returns the row and whether the gateway took the push.
    Raises ``ServerChanged``, ``OrgConfigRejected`` or ``MCPCredentialError``;
    each rolls back the row with the version. ``ConnectCancelled`` instead
    commits only a revoked grant's cleanup obligation, never a config version.
    Generation comparison and token/config publication hold the same org lock
    for the entire transaction."""
    org = state.organization
    kept = []

    def updater(config):
        servers = (config.mcp or {}).get("servers")
        entry = servers.get(state.server_id) if isinstance(servers, dict) else None
        if not is_shared_oauth(entry) or (
            vault.origin_of(entry.get("url")) != state.origin
        ):
            raise ServerChanged(state.server_id)
        current = (
            AgentccMCPCredential.objects.select_for_update()
            .filter(
                organization=org,
                server_id=state.server_id,
                scope=Scope.SHARED,
                user__isnull=True,
                account_label="",
                status=Status.ACTIVE,
            )
            .first()
        )
        fields = _token_fields(state, tokens)
        if current is not None and current.kind == Kind.OAUTH:
            for name, value in fields.items():
                setattr(current, name, value)
            current.version += 1
            current.rotated_at = timezone.now()
            current.save(update_fields=[*fields, "version", "rotated_at"])
        else:
            if current is not None:
                # A key stored by hand in the server's shared slot: replaced.
                current.status = Status.REVOKED
                current.save(update_fields=["status"])
            current = AgentccMCPCredential.objects.create(
                organization=org,
                server_id=state.server_id,
                scope=Scope.SHARED,
                created_by_id=state.user_id,
                status=Status.ACTIVE,
                **fields,
            )
        vault.set_reference(config, state.server_id, current, Status.ACTIVE)
        kept.append(current)

    from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
    from agentcc.services.mcp_connection_cleanup import finish, receipt

    with transaction.atomic():
        lock_org(org.pk)
        generation = mcp_oauth_cancellation.current_shared(org.pk, state.server_id)
        if state.cancellation_generation == generation and (
            AgentccMCPConnectState.objects.filter(
                pk=state.pk, organization=org, server_id=state.server_id, mode=SHARED
            ).exists()
        ):
            with vault.vault_write():
                _, warnings = write_config_version_with(
                    org,
                    updater,
                    description=f"Sign in the organization's account for {state.server_id}",
                    user=state.user,
                )
            return kept[0], is_gateway_synced(warnings)
        # Disconnect advanced the generation or deleted the retained state.
        # Keep only a vendor-cleanup obligation, never an active slot or config
        # reference. No gateway eviction: it must not purge a later sign-in.
        discarded = AgentccMCPCredential.objects.create(
            organization=org,
            server_id=state.server_id,
            scope=Scope.SHARED,
            created_by_id=state.user_id,
            status=Status.REVOKED,
            oauth_revoke_pending=True,
            oauth_revoke_outcome="pending",
            **_token_fields(state, tokens),
        )
        receipt(discarded, "cancelled", "vendor_revocation", "pending", "recorded")
    # The obligation is durable before attempting the vendor, as for personal
    # cancellation. A subsequent Disconnect retries a transient cleanup failure.
    finish(org, [discarded], None)
    raise ConnectCancelled(state.server_id)


def _token_fields(state, tokens):
    return {
        "kind": Kind.OAUTH,
        "header_name": "",
        "encrypted_secret": encrypt_token(tokens.access.reveal()),
        "encrypted_refresh": encrypt_token(tokens.refresh.reveal())
        if tokens.refresh else None,
        "token_expires_at": tokens.expires_at,
        "oauth_issuer": state.issuer,
        "oauth_client_id": state.client_id,
        "oauth_resource": state.resource,
        "oauth_org_only": state.org_only,
        "scopes": list(tokens.scopes or state.scopes),
        "origin": state.origin,
    }


def disconnect(org: Organization, actor: User, server_id: str):
    """Revoke the org's shared OAuth account on ``server_id`` and mark the
    config's reference ``revoked``. Returns ``(rows, synced)`` including
    previously revoked rows whose durable cleanup has not finished. Every call
    advances the org/server generation, even without a credential or state row."""
    from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
    from agentcc.services.mcp_connection_cleanup import UNFINISHED, mark_revoked

    with transaction.atomic():
        lock_org(org.pk)
        mcp_oauth_cancellation.cancel_shared(org.pk, server_id)
        rows = list(
            active_rows(org.pk, server_id)
            .select_for_update()
            .filter(UNFINISHED)
            .order_by("created_at")
        )
        AgentccMCPConnectState.objects.filter(
            organization=org,
            server_id=server_id,
            mode=SHARED,
        ).delete()
        for row in rows:
            mark_revoked(row)
        synced = True
        if rows:
            synced = vault.write_reference_state(rows[-1], Status.REVOKED, actor)
    if rows:
        logger.info(
            "mcp_shared_oauth_disconnected",
            org_id=str(org.pk),
            server_id=server_id,
            actor_id=str(actor.pk) if actor is not None else None,
            revoked=[str(row.pk) for row in rows],
        )
    return rows, synced is not False
