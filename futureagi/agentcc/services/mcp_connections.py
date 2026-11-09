"""A member's own accounts on the organization's per-person MCP servers
(M25x), as the console's "Your accounts" panel reads them:
``GET /agentcc/mcp/connect/``.

One row per server where the caller may connect their own account, or still
holds one:

- every server in the organization's active config whose credential mode is
  ``per_user``, except one carrying catalogue provenance, even if its
  provider no longer resolves in the loaded bundle; a K2 pilot server is the
  one catalogue exception, and only while this deployment runs its connector
  runtime (``connector_k2``);
- every server where the caller holds an OAuth connection that is not
  revoked or still has cleanup pending, including a changed or removed server.

A row carries only what is the caller's own: their connection's state and
times. Never another member's connection, the organization's shared account,
a token, a client secret, or the organization's OAuth app's client id. The
read makes no network call and stores nothing.

``start`` says whether the console may start a sign-in now, from stored state
only:

- ``ready``: MCP is on, the server connects each member's own account, this
  deployment can serve members' tokens to the gateway
  (``AGENTCC_MCP_DJANGO_SECRET``), and the organization's own OAuth app for
  the server is stored and readable. The sign-in itself may still be refused
  by the server's metadata, which only the start fetches.
- ``needs_admin`` (``org_oauth_app_required``): a K2 pilot server, which signs
  in only through the organization's own Google or Microsoft app
  registration, and the organization has none (FutureAGI's platform client
  is never used for it).
- ``needs_admin`` (``oauth_client_unreadable``): the organization's OAuth app
  for the server is stored but cannot be read. A sign-in to that server uses
  only that app (``mcp_connect_clients``), so nobody connects until an Owner
  or Admin replaces it.
- ``unknown`` (``client_not_confirmed``): the organization stored no OAuth
  app for the server. A sign-in may still work through our client metadata
  document or a client the server registers for us, but only the server's
  own metadata says whether it takes either, and this read fetches nothing.
  It is never reported as an app the organization must add.
- ``unavailable``: ``mcp_off``; ``per_user_channel_off`` (this deployment
  cannot serve members' tokens); ``server_changed`` (the server no longer
  connects each member's own account, or is gone); ``sign_in_not_served`` (a
  catalogue app), ``server_disabled`` or ``cleanup_pending``.

``status`` is the state of the caller's own stored connection, as the
gateway's token fetch (``mcp_upstream_tokens``) reads it, without a refresh:
``None`` (never connected), ``active``, ``expired``, ``needs_reauth`` (also
an active connection whose server moved to another origin, which the fetch
refuses) or ``revoked``. ``connected`` says whether the gateway uses it for
the caller's calls now: true only for an ``active`` one on a server that
still connects each member's own account, with MCP, server and token channel
on and no pending cleanup. Known expiry without refresh material is projected
as expired without fetching or decrypting a token. Unusable accounts remain
manageable; a refreshable access-token expiry alone is not a disconnected account.
"""

from dataclasses import dataclass

from django.db.models import BooleanField, Case, Value, When
from django.utils import timezone

from accounts.models import Organization, User
from agentcc import mcp_features
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import connector_k2
from agentcc.services import connector_oauth_clients as clients
from agentcc.services import mcp_connect_clients as connect_clients
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services.mcp_connection_cleanup import pending
from agentcc.services.mcp_credentials import SERVER_ID, origin_of

Kind = AgentccMCPCredential.Kind
Scope = AgentccMCPCredential.Scope
Status = AgentccMCPCredential.Status

READY = "ready"
NEEDS_ADMIN = "needs_admin"
UNKNOWN = "unknown"
UNAVAILABLE = "unavailable"

ORG_OAUTH_APP = "org_oauth_app"
OAUTH_CLIENT_UNREADABLE = "oauth_client_unreadable"
CLIENT_NOT_CONFIRMED = "client_not_confirmed"
MCP_OFF = "mcp_off"
PER_USER_CHANNEL_OFF = "per_user_channel_off"
SERVER_CHANGED = "server_changed"
SIGN_IN_NOT_SERVED = "sign_in_not_served"
ORG_OAUTH_APP_REQUIRED = "org_oauth_app_required"

START_PATH = "/agentcc/mcp/connect/{}/start/"
DISCONNECT_PATH = "/agentcc/mcp/connect/{}/"


@dataclass(frozen=True)
class _Server:
    """What the panel needs of one config entry."""

    entry: dict | None
    per_user: bool
    url: str
    from_catalogue: bool
    # A K2 pilot server on this deployment's runtime: the one catalogue server
    # whose sign-in is served (connector_k2).
    k2: bool = False


def own_connections(
    org: Organization, user: User, server_id: str | None = None
) -> list[dict]:
    """The caller's rows, by server id. Raises ``ConnectRefused``
    (``not_member``, 403) for a caller who is not a member of ``org``."""
    if not connect.is_member(user, org):
        raise connect.ConnectRefused(
            "not_member", "not a member of this organization", 403
        )
    mcp = _active_mcp(org)
    servers = mcp.get("servers") if isinstance(mcp.get("servers"), dict) else {}
    rows = _own_rows(org, user, server_id)
    ids = {
        sid
        for sid, entry in servers.items()
        if _listed(sid, entry) and (server_id is None or sid == server_id)
    }
    ids.update(
        row.server_id for row in rows if row.status != Status.REVOKED or pending(row)
    )
    channel_on = connect.internal_secret() is not None
    mcp_on = mcp.get("enabled") is True
    result = []
    for sid in sorted(ids):
        if not mcp_features.server_visible(servers.get(sid)):
            continue
        server = _server(servers.get(sid))
        mine = [row for row in rows if row.server_id == sid]
        result.append(
            _row(org, sid, server, mine, mcp_on=mcp_on, channel_on=channel_on)
        )
    return result


def _active_mcp(org: Organization) -> dict:
    mcp = (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=org, is_active=True, deleted=False
        )
        .values_list("mcp", flat=True)
        .first()
    )
    return mcp if isinstance(mcp, dict) else {}


def _own_rows(org, user, server_id) -> list[AgentccMCPCredential]:
    """The caller's own OAuth connections, newest first; the encrypted fields
    are never loaded."""
    rows = (
        AgentccMCPCredential.objects.filter(
            organization=org, scope=Scope.USER, user=user, kind=Kind.OAUTH
        )
        .defer("encrypted_secret", "encrypted_refresh")
        .annotate(
            has_refresh=Case(
                When(encrypted_refresh__isnull=True, then=Value(False)),
                When(encrypted_refresh="", then=Value(False)),
                default=Value(True),
                output_field=BooleanField(),
            )
        )
        .order_by("-created_at")
    )
    if server_id is not None:
        rows = rows.filter(server_id=server_id)
    return list(rows)


def _listed(server_id, entry) -> bool:
    """A configured server the panel offers to everyone in the org: a
    per-person server added by URL."""
    if not isinstance(server_id, str) or not SERVER_ID.fullmatch(server_id):
        return False
    server = _server(entry)
    return server.per_user and not connector_k2.held_back(entry)


def _server(entry) -> _Server:
    if not isinstance(entry, dict):
        return _Server(None, False, "", False)
    credential = entry.get("credential")
    mode = credential.get("mode") if isinstance(credential, dict) else None
    url = entry.get("url") if isinstance(entry.get("url"), str) else ""
    return _Server(
        entry,
        mode == connect.PER_USER and bool(origin_of(url)),
        url,
        # Provenance survives retirement, an unavailable bundle, malformed
        # references and incompatible major versions. Only URL-added entries
        # (no catalogue reference at all) enter URL readiness.
        "catalog_ref" in entry,
        "catalog_ref" in entry and connector_k2.pilot(entry) is not None,
    )


def _row(org, server_id, server, mine, *, mcp_on, channel_on) -> dict:
    active = next((row for row in mine if row.status == Status.ACTIVE), None)
    current = active or (mine[0] if mine else None)
    cleanup_pending = any(pending(row) for row in mine)
    held = cleanup_pending or any(row.status != Status.REVOKED for row in mine)
    status = current.status if current is not None else None
    if (
        active is not None
        and active.token_expires_at is not None
        and (active.token_expires_at <= timezone.now() and not active.has_refresh)
    ):
        status = Status.EXPIRED.value
    moved = active is not None and active.origin != origin_of(server.url)
    if server.per_user and moved:
        # The token fetch refuses a token for a host it was not issued for.
        status = Status.NEEDS_REAUTH.value
    state, reason, registered = _start(org, server_id, server, mcp_on, channel_on)
    # Only gateway eviction blocks reconnect. Vendor failure remains visible
    # and retryable, but can be superseded by completing a fresh sign-in.
    if any(row.gateway_purge_pending for row in mine):
        state, reason = UNAVAILABLE, "cleanup_pending"
    return {
        "server_id": server_id,
        "connected": bool(
            status == Status.ACTIVE
            and server.per_user
            and mcp_on
            and channel_on
            and not connector_k2.held_back(server.entry)
            and server.entry.get("enabled", True) is True
            and not cleanup_pending
            # A K2 token is served only for the registration that issued it.
            and (not server.k2 or registered == active.oauth_client_id)
        ),
        "cleanup_pending": cleanup_pending,
        "upstream_revocation": (current.oauth_revoke_outcome or None)
        if current
        else None,
        "status": status,
        "connected_at": current.created_at if current is not None else None,
        "rotated_at": current.rotated_at if current is not None else None,
        "start": {"state": state, "reason": reason},
        "start_url": START_PATH.format(server_id) if state == READY else None,
        "disconnect_url": DISCONNECT_PATH.format(server_id) if held else None,
    }


def _start(org, server_id, server, mcp_on, channel_on) -> tuple[str, str, str | None]:
    """``(state, reason, registered_client_id)``; the client id only for a K2
    pilot server whose organization registered its own app."""
    state, reason = _readiness(org, server_id, server, mcp_on, channel_on)
    if not server.k2 or state == UNAVAILABLE:
        return state, reason, None
    found, client_id = connector_k2.registration(org, server_id, server.entry)
    if found == connector_k2.UNREADABLE:
        return NEEDS_ADMIN, OAUTH_CLIENT_UNREADABLE, None
    if found != connector_k2.REGISTERED:
        return NEEDS_ADMIN, ORG_OAUTH_APP_REQUIRED, None
    return READY, ORG_OAUTH_APP, client_id


def _readiness(org, server_id, server, mcp_on, channel_on) -> tuple[str, str]:
    if connector_k2.held_back(server.entry):
        return UNAVAILABLE, SIGN_IN_NOT_SERVED
    if not server.per_user:
        return UNAVAILABLE, SERVER_CHANGED
    if not mcp_on:
        return UNAVAILABLE, MCP_OFF
    if server.entry.get("enabled", True) is not True:
        return UNAVAILABLE, "server_disabled"
    if not channel_on:
        return UNAVAILABLE, PER_USER_CHANNEL_OFF
    if server.k2:
        return READY, ORG_OAUTH_APP  # refined by _start from the registration
    # A server added by URL (catalogue servers returned above): only the org's
    # own app keyed to it is a source, and the read never sends its secret.
    try:
        found = connect_clients.resolve(org, server_id, server.entry) is not None
    except clients.OrgClientUnreadable:
        return NEEDS_ADMIN, OAUTH_CLIENT_UNREADABLE
    if not found:
        # A configured native MCP OAuth app can try DCR/CIMD. Start performs
        # discovery again; readiness promises no token or successful sign-in.
        if server.from_catalogue:
            return READY, CLIENT_NOT_CONFIRMED
        return UNKNOWN, CLIENT_NOT_CONFIRMED
    return READY, ORG_OAUTH_APP
