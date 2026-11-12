"""Who is asking: client resolution and redirect URI rules.

A ``client_id`` resolves, in order, to the first-party client (Future AGI
Agents, configured by AGENTCC_MCP_OAUTH_EE_*), a stored pre-registered client,
then each self-registration resolver in ``RESOLVERS``. An id nothing resolves
is refused before anything else happens.

Redirect URIs are matched exactly, with one standard allowance: a loopback
``http`` redirect may use any port (RFC 8252 section 7.3), because native
clients such as VS Code and Claude Code listen on a port picked at sign-in.
"""

import hashlib
import hmac
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from agentcc.mcp_oauth import config
from agentcc.models.mcp_oauth import AgentccMCPOAuthClient

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
# Schemes a private-use redirect may never use.
_FORBIDDEN_SCHEMES = {
    "javascript",
    "data",
    "file",
    "vbscript",
    "about",
    "blob",
    "ws",
    "wss",
    "ftp",
}


@dataclass
class Client:
    row: AgentccMCPOAuthClient
    name: str
    redirect_uris: list[str]
    auth_method: str = "none"
    first_party: bool = False
    verified: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def client_id(self) -> str:
        return self.row.client_id

    @property
    def kind(self) -> str:
        return self.row.kind


def from_row(row: AgentccMCPOAuthClient, **overrides) -> Client:
    metadata = row.metadata if isinstance(row.metadata, dict) else {}
    values = {
        "row": row,
        "name": str(metadata.get("client_name") or row.client_id)[:200],
        "redirect_uris": [u for u in row.redirect_uris or [] if isinstance(u, str)],
        "auth_method": str(metadata.get("token_endpoint_auth_method") or "none"),
        "verified": row.kind == AgentccMCPOAuthClient.Kind.PREREGISTERED,
    }
    values.update(overrides)
    return Client(**values)


def _first_party(client_id: str) -> Client | None:
    spec = config.first_party_client()
    if spec is None or client_id != spec["client_id"]:
        return None
    metadata = {"client_name": spec["client_name"], "first_party": True}
    row, created = AgentccMCPOAuthClient.objects.get_or_create(
        client_id=client_id,
        defaults={
            "kind": AgentccMCPOAuthClient.Kind.PREREGISTERED,
            "metadata": metadata,
            "redirect_uris": spec["redirect_uris"],
        },
    )
    if not created and (
        row.kind != AgentccMCPOAuthClient.Kind.PREREGISTERED
        or row.metadata != metadata
        or row.redirect_uris != spec["redirect_uris"]
    ):
        row.kind = AgentccMCPOAuthClient.Kind.PREREGISTERED
        row.metadata = metadata
        row.redirect_uris = spec["redirect_uris"]
        row.save(update_fields=["kind", "metadata", "redirect_uris"])
    return from_row(row, first_party=True, verified=True)


def _stored(client_id: str) -> Client | None:
    first_party = config.first_party_client()
    if first_party is not None and client_id == first_party["client_id"]:
        return None
    row = AgentccMCPOAuthClient.objects.filter(
        client_id=client_id, kind__in=STORED_KINDS
    ).first()
    return from_row(row) if row is not None else None


STORED_KINDS = [
    AgentccMCPOAuthClient.Kind.PREREGISTERED,
    AgentccMCPOAuthClient.Kind.DCR,
]


def _metadata_document(client_id: str) -> Client | None:
    from agentcc.mcp_oauth import cimd

    return cimd.resolve(client_id)


# Self-registration resolvers, tried after the stored clients (M24x).
RESOLVERS: list = [_metadata_document]


def resolve(client_id) -> Client | None:
    if not isinstance(client_id, str) or not client_id or len(client_id) > 2048:
        return None
    for resolver in (_first_party, _stored, *RESOLVERS):
        client = resolver(client_id)
        if client is not None:
            return client
    return None


# --- Redirect URIs -----------------------------------------------------------


def _loopback(parts) -> bool:
    return parts.scheme == "http" and (parts.hostname or "") in LOOPBACK_HOSTS


def redirect_uri_kind(uri) -> str | None:
    """``https``, ``loopback`` or ``private`` for an acceptable redirect URI,
    None for one never accepted (a fragment, credentials, plain http to a
    remote host, or a dangerous scheme)."""
    if not isinstance(uri, str) or not uri or len(uri) > 2048:
        return None
    try:
        parts = urlsplit(uri)
        parts.port  # noqa: B018 -- raises on a bad port
    except ValueError:
        return None
    if parts.fragment or "#" in uri or parts.username or parts.password:
        return None
    scheme = parts.scheme.lower()
    if scheme == "https":
        return "https" if parts.hostname else None
    if scheme == "http":
        return "loopback" if _loopback(parts) else None
    if not scheme or scheme in _FORBIDDEN_SCHEMES or not scheme[0].isalpha():
        return None
    return "private"


def redirect_uri_allowed(client: Client, uri) -> bool:
    if redirect_uri_kind(uri) is None:
        return False
    if uri in client.redirect_uris:
        return True
    asked = urlsplit(uri)
    if not _loopback(asked):
        return False
    for registered in client.redirect_uris:
        reg = urlsplit(registered)
        if (
            _loopback(reg)
            and reg.hostname == asked.hostname
            and reg.path == asked.path
            and reg.query == asked.query
        ):
            return True
    return False


def pre_approved(client: Client, redirect_uri: str) -> bool:
    """The first-party client skips the consent screen only on one of its
    registered web callbacks, never on a loopback or app redirect."""
    return (
        client.first_party
        and redirect_uri in client.redirect_uris
        and redirect_uri_kind(redirect_uri) == "https"
    )


# --- Client authentication at the token and revocation endpoints -------------


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def authenticate(client: Client, presented_secret) -> bool:
    """A public client (``none``) presents no secret; a confidential one
    presents the secret it was issued, compared in constant time."""
    if client.auth_method == "none":
        return not presented_secret
    stored = (client.row.metadata or {}).get("client_secret_hash")
    if not stored or not isinstance(presented_secret, str) or not presented_secret:
        return False
    return hmac.compare_digest(stored, secret_hash(presented_secret))
