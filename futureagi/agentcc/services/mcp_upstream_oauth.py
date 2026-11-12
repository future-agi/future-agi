"""Per-user upstream OAuth for MCP servers, Django half (M25x).

The gateway is an OAuth client of each vendor MCP server's authorization
server. A member connects their own account once; the gateway then fetches
that member's token just in time (``mcp-upstream-token-v1``, the token fetch
below) and sends it only to that server.

Connect (``start`` then ``complete``):

1. The start is an authenticated API call from the console (a FutureAGI
   login). With a ``binding`` (the sealed link a gateway ``auth_required``
   carries), the binding must open and name this org, this server and the
   logged-in user, or nothing starts. The server must be one of the org's
   servers whose credential mode is ``per_user``.
2. Discovery, every fetch through ``mcp_safe_fetch``: the server's
   protected-resource metadata (RFC 9728), then its authorization server's
   metadata (RFC 8414, or OpenID discovery), whose ``issuer`` must be the
   authorization server the resource named. PKCE S256 must be supported.
   A pilot connector (``connector_k2``) and a vendor MCP server that takes only
   the organization's own OAuth app (``connector_org_app_oauth``) discover
   nothing: their authorization server is the one the catalog declares.
3. The client: the org's own OAuth client, else FutureAGI's platform client
   (CN11 and CN3, in ``mcp_connect_clients``' order, both confidential); else
   our client metadata document (CIMD) where the authorization server takes
   one, else a client it registers for us (DCR), else the connect is refused.
   ``choose_client`` is the one place that decides.
4. A connect row holds what the callback needs: ``state`` and the browser
   cookie only as hashes, the PKCE verifier encrypted. The response sets a
   ``__Host-`` cookie (Secure, HttpOnly, SameSite=Lax) on the browser that
   made the authenticated start, and returns the authorize URL (PKCE S256,
   ``state``, RFC 8707 ``resource``).
5. The callback consumes the row on any attempt, so ``state`` works once. It
   completes only in the browser holding the start's cookie, within 10
   minutes, for an issuer that matches the one discovered (``iss``, RFC 9207,
   required where the server advertises it). The code is exchanged with the
   verifier, as the client the start chose (a confidential client's secret
   is resolved again for the exchange, never stored with the connect), and
   the token pair is stored as the member's own vault row (scope ``user``,
   kind ``oauth``), bound to the server's origin.

A link therefore grants nothing to whoever holds it: the start needs the
member's own login, and the callback needs the cookie their browser got.

The organization's one account (``mode: shared``, CN24) runs the same
flow on a shared server whose credential kind is ``oauth``: only an Owner or
Admin starts it, it takes no person's connect link, it completes only while
the one who started is still an Owner or Admin, and the token pair becomes
the organization's row, which the config names (``mcp_shared_oauth``).

Disconnect revokes the token upstream where the server supports it, marks the
row revoked and asks the gateway to drop what it cached.

Secrets. Tokens, the verifier and the client metadata never appear in a log
line, a response other than the token fetch's, or an error. A function here
that holds a token in clear returns a result or a reason and never raises:
the app's error reports carry every frame's local variables (the rule of
``connector_oauth_clients``).
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlencode, urlsplit, urlunsplit

import structlog
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Organization, User
from agentcc import mcp_features
from agentcc.mcp_personal_auth import is_individual_member
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_upstream_oauth import (
    AgentccMCPConnectState,
    AgentccMCPUpstreamClient,
)
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import (
    connector_k2,
    connector_oauth_clients,
    connector_org_app_oauth,
    mcp_oauth_cancellation,
    mcp_safe_fetch,
)
from agentcc.services import mcp_connect_clients as connect_clients
from agentcc.services import mcp_shared_oauth as shared_account
from agentcc.services.credential_manager import decrypt_token, encrypt_token
from agentcc.services.gateway_admin import mcp_credentials as gateway_credentials
from agentcc.services.mcp_credentials import SERVER_ID, origin_of
from agentcc.services.mcp_user_credential_lifecycle import lock_identity
from agentcc.services.org_config_writer import lock_org

logger = structlog.get_logger(__name__)

Kind = AgentccMCPCredential.Kind
Scope = AgentccMCPCredential.Scope
Status = AgentccMCPCredential.Status

CONNECT_TTL = timedelta(minutes=10)
CALLBACK_PATH = "/agentcc/mcp/connect/callback/"
CLIENT_METADATA_PATH = "/agentcc/mcp/connect/client.json"
COOKIE_PREFIX = "__Host-agentcc_mcp_connect_"
CLIENT_NAME = "Future AGI"
PER_USER = "per_user"

# The gateway-to-Django secret (M25's mcp.django.internal_token_secret): it
# authenticates the token fetch and keys the connect binding.
INTERNAL_SECRET_ENV = "AGENTCC_MCP_DJANGO_SECRET"
MIN_INTERNAL_SECRET_CHARS = 32

# The gateway's state token layout (agentcc-gateway/internal/mcp/statecodec.go)
# for the "connect" purpose: version | nonce | AES-256-GCM(expiry || payload).
_STATE_VERSION = 1
_STATE_SALT = b"agentcc-mcp-state"
_STATE_INFO = b"agentcc-mcp-state/v1/"
_BINDING_PURPOSE = "connect"
_NONCE_BYTES = 12
_EXPIRY_BYTES = 8
_MAX_BINDING_CHARS = 4096

METADATA_LIMIT = 256 * 1024


class ConnectRefused(Exception):
    """The connect did not start. ``code`` is for the client; the message
    names what failed, never a value that could be secret."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class Sealed:
    """A token on its way through: never in a repr, a str or a pickle."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("a token is not serialisable")


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    revocation_endpoint: str
    cimd_supported: bool
    iss_required: bool
    resource: str
    scopes: tuple[str, ...]
    # token_endpoint_auth_methods_supported; None where the metadata omits it.
    auth_methods: tuple[str, ...] | None = None
    # A declared spec's extra authorize parameters (connector_k2); never core ones.
    authorize_params: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class StartResult:
    authorize_url: str
    cookie_name: str
    cookie_value: str = field(repr=False)
    max_age: int
    connect_id: str


@dataclass(frozen=True)
class CompleteResult:
    ok: bool
    reason: str
    server_id: str = ""
    cookie_name: str = ""


@dataclass(frozen=True)
class _Tokens:
    access: Sealed
    refresh: Sealed | None
    expires_at: object
    scopes: tuple[str, ...]


# --- Settings ----------------------------------------------------------------


def callback_url() -> str:
    """The one redirect URI every vendor app registers (AM-C19): a configured
    value, never fetched."""
    configured = getattr(settings, "AGENTCC_MCP_CONNECT_CALLBACK_URL", "") or ""
    configured = configured or os.environ.get("AGENTCC_MCP_CONNECT_CALLBACK_URL", "")
    if configured:
        return configured
    return settings.BASE_URL.rstrip("/") + CALLBACK_PATH


def client_metadata_url() -> str:
    """Our CIMD client id: the client metadata document, on the host that
    serves the callback."""
    parts = urlsplit(callback_url())
    return urlunsplit((parts.scheme, parts.netloc, CLIENT_METADATA_PATH, "", ""))


def client_metadata() -> dict:
    """Our client metadata document (CIMD): a public client with one
    redirect URI."""
    return {
        "client_id": client_metadata_url(),
        "client_name": CLIENT_NAME,
        "redirect_uris": [callback_url()],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


def console_connect_url(server_id: str, binding: str) -> str:
    """Where a gateway connect link sends the browser: the console, which
    signs the member in and makes the authenticated start."""
    base = getattr(settings, "AGENTCC_MCP_CONNECT_CONSOLE_URL", "") or (
        settings.FRONTEND_BASE_URL.rstrip("/") + "/dashboard/gateway/integrations"
    )
    query = {"connect": server_id}
    if binding:
        query["binding"] = binding
    return f"{base}?{urlencode(query)}"


def internal_secret() -> str | None:
    """``AGENTCC_MCP_DJANGO_SECRET`` when usable: at least 32 characters and
    not the gateway admin token. None refuses every use."""
    from agentcc.services import gateway_client

    value = os.environ.get(INTERNAL_SECRET_ENV, "")
    if len(value) < MIN_INTERNAL_SECRET_CHARS:
        return None
    admin = gateway_client.AGENTCC_ADMIN_TOKEN or ""
    if admin and hmac.compare_digest(value.encode(), admin.encode()):
        return None
    return value


# --- The connect binding -------------------------------------------------------


def _binding_key(secret: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_STATE_SALT,
        info=_STATE_INFO + _BINDING_PURPOSE.encode(),
    ).derive(secret.encode())


def _aad() -> bytes:
    return bytes([_STATE_VERSION]) + _BINDING_PURPOSE.encode()


def seal_binding(org_id: str, server_id: str, user_id: str, ttl=CONNECT_TTL) -> str:
    """A connect binding as the gateway seals it (M25), for tests and tools."""
    secret = internal_secret()
    if secret is None:
        raise ConnectRefused("binding_unavailable", f"{INTERNAL_SECRET_ENV} is not set")
    expiry = int((timezone.now() + ttl).timestamp() * 1000)
    payload = json.dumps(
        {"org_id": org_id, "server_id": server_id, "user_id": user_id},
        separators=(",", ":"),
    ).encode()
    nonce = secrets.token_bytes(_NONCE_BYTES)
    sealed = AESGCM(_binding_key(secret)).encrypt(
        nonce, expiry.to_bytes(_EXPIRY_BYTES, "big") + payload, _aad()
    )
    raw = bytes([_STATE_VERSION]) + nonce + sealed
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def open_binding(token: str) -> dict | None:
    """The binding's ``{org_id, server_id, user_id}``, or None when it does
    not open, has expired or is malformed. One answer for every failure."""
    secret = internal_secret()
    if secret is None or not isinstance(token, str) or len(token) > _MAX_BINDING_CHARS:
        return None
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        if len(raw) < 1 + _NONCE_BYTES or raw[0] != _STATE_VERSION:
            return None
        nonce, sealed = raw[1 : 1 + _NONCE_BYTES], raw[1 + _NONCE_BYTES :]
        plain = AESGCM(_binding_key(secret)).decrypt(nonce, sealed, _aad())
    except Exception:
        return None
    if len(plain) < _EXPIRY_BYTES:
        return None
    expiry = int.from_bytes(plain[:_EXPIRY_BYTES], "big")
    if timezone.now().timestamp() * 1000 >= expiry:
        return None
    try:
        payload = json.loads(plain[_EXPIRY_BYTES:])
    except ValueError:
        return None
    keys = ("org_id", "server_id", "user_id")
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(k), str) and payload.get(k) for k in keys
    ):
        return None
    return {k: payload[k] for k in keys}


# --- The org's server --------------------------------------------------------


def server_entry(org_id, server_id: str, *, include_hidden=False) -> dict | None:
    """The org's active config entry for ``server_id``, or None."""
    mcp = (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization_id=org_id, is_active=True, deleted=False
        )
        .values_list("mcp", flat=True)
        .first()
    )
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    entry = servers.get(server_id) if isinstance(servers, dict) else None
    return (
        entry
        if isinstance(entry, dict)
        and (include_hidden or mcp_features.server_visible(entry))
        else None
    )


def per_user_server_url(org_id, server_id: str) -> str:
    """The URL of the org's ``per_user`` server ``server_id``; raises
    ``ConnectRefused`` for any other server."""
    if not isinstance(server_id, str) or not SERVER_ID.fullmatch(server_id):
        raise ConnectRefused("server_not_found", "no such MCP server", 404)
    entry = server_entry(org_id, server_id)
    if entry is None:
        raise ConnectRefused("server_not_found", "no such MCP server", 404)
    if connector_k2.held_back(entry):
        raise ConnectRefused(
            "sign_in_not_served", "catalogue sign-in is not served", 409
        )
    credential = entry.get("credential")
    mode = credential.get("mode") if isinstance(credential, dict) else None
    if mode != PER_USER:
        raise ConnectRefused(
            "server_not_per_user",
            "this MCP server does not connect each member's own account",
        )
    url = entry.get("url")
    if not isinstance(url, str) or not origin_of(url):
        raise ConnectRefused("server_not_found", "the MCP server has no URL", 404)
    return url


def shared_oauth_server_url(org_id, server_id: str) -> str:
    """The URL of the org's shared OAuth server ``server_id``; raises
    ``ConnectRefused`` for any other server."""
    if not isinstance(server_id, str) or not SERVER_ID.fullmatch(server_id):
        raise ConnectRefused("server_not_found", "no such MCP server", 404)
    entry = server_entry(org_id, server_id)
    if entry is None:
        raise ConnectRefused("server_not_found", "no such MCP server", 404)
    if not shared_account.is_shared_oauth(entry):
        raise ConnectRefused(
            "server_not_shared_oauth",
            "this MCP server does not sign in one account for the organization "
            "over OAuth",
        )
    url = entry.get("url")
    if not isinstance(url, str) or not origin_of(url):
        raise ConnectRefused("server_not_found", "the MCP server has no URL", 404)
    return url


def is_member(user, org) -> bool:
    return is_individual_member(user, org)


# --- Discovery ---------------------------------------------------------------


def discover(server_url: str) -> Discovery:
    """The server's resource and authorization server metadata, checked.
    Raises ``ConnectRefused`` (``discovery_failed``, 502)."""
    prm, from_root = _protected_resource(server_url)
    resource = prm.get("resource")
    if not isinstance(resource, str) or not _resource_covers(
        resource, server_url, from_root
    ):
        raise _discovery("the server's resource metadata names another resource")
    servers = prm.get("authorization_servers")
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], str):
        raise _discovery("the server's resource metadata names no authorization server")
    issuer = servers[0]
    meta = _authorization_server(issuer)
    if (meta.get("issuer") or "").rstrip("/") != issuer.rstrip("/"):
        raise _discovery("the authorization server's metadata names another issuer")
    methods = meta.get("code_challenge_methods_supported")
    if not isinstance(methods, list) or "S256" not in methods:
        raise _discovery("the authorization server does not support PKCE S256")
    authorize = endpoint_of(meta, "authorization_endpoint", issuer, required=True)
    token = endpoint_of(meta, "token_endpoint", issuer, required=True)
    scopes = prm.get("scopes_supported")
    auth_methods = meta.get("token_endpoint_auth_methods_supported")
    return Discovery(
        issuer=meta["issuer"],
        authorization_endpoint=authorize,
        token_endpoint=token,
        registration_endpoint=endpoint_of(meta, "registration_endpoint", issuer),
        revocation_endpoint=endpoint_of(meta, "revocation_endpoint", issuer),
        cimd_supported=meta.get("client_id_metadata_document_supported") is True,
        iss_required=meta.get("authorization_response_iss_parameter_supported") is True,
        resource=resource,
        scopes=tuple(s for s in scopes if isinstance(s, str))
        if isinstance(scopes, list)
        else (),
        auth_methods=tuple(m for m in auth_methods if isinstance(m, str))
        if isinstance(auth_methods, list)
        else None,
    )


def authorization_server_metadata(issuer: str) -> dict:
    """An issuer's metadata, checked to name that issuer (for refresh and
    revocation, which start from the issuer the vault row recorded)."""
    meta = _authorization_server(issuer)
    if (meta.get("issuer") or "").rstrip("/") != issuer.rstrip("/"):
        raise _discovery("the authorization server's metadata names another issuer")
    return meta


def _discovery(message: str) -> ConnectRefused:
    return ConnectRefused("discovery_failed", message, 502)


def _protected_resource(server_url: str) -> tuple[dict, bool]:
    parts = urlsplit(server_url)
    root = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    path = parts.path.rstrip("/")
    candidates = []
    if path:
        candidates.append(
            (root + "/.well-known/oauth-protected-resource" + path, False)
        )
    candidates.append((root + "/.well-known/oauth-protected-resource", True))
    for url, from_root in candidates:
        body = _metadata(url)
        if body is not None:
            return body, from_root
    raise _discovery("the server publishes no protected-resource metadata")


def _authorization_server(issuer: str) -> dict:
    parts = urlsplit(issuer)
    if parts.scheme not in ("https", "http") or not parts.netloc or parts.query:
        raise _discovery("the authorization server's issuer is not a URL")
    root = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    path = parts.path.rstrip("/")
    for url in (
        root + "/.well-known/oauth-authorization-server" + path,
        root + "/.well-known/openid-configuration" + path,
        root + path + "/.well-known/openid-configuration",
    ):
        body = _metadata(url)
        if body is not None:
            return body
    raise _discovery("the authorization server publishes no metadata")


def _metadata(url: str) -> dict | None:
    try:
        response = mcp_safe_fetch.fetch(url, max_bytes=METADATA_LIMIT)
    except mcp_safe_fetch.EgressRefused as refused:
        if refused.reason in ("forbidden", "not_allowed", "https_required"):
            raise ConnectRefused("discovery_refused", str(refused), 502) from None
        return None
    if response.status != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _resource_covers(resource: str, server_url: str, from_root: bool) -> bool:
    """Whether the resource the metadata names is the server, or (for
    metadata found at the root) the server's origin or a parent path of it."""
    if origin_of(resource) != origin_of(server_url):
        return False
    resource_path = urlsplit(resource).path.rstrip("/")
    server_path = urlsplit(server_url).path.rstrip("/")
    if resource_path == server_path:
        return True
    return from_root and (
        not resource_path or server_path.startswith(resource_path + "/")
    )


def endpoint_of(meta: dict, name: str, issuer: str, *, required=False) -> str:
    value = meta.get(name)
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise _discovery(f"the authorization server's {name} is missing")
    scheme = urlsplit(value).scheme
    # Plain http only for an issuer the operator's list opened to plain http.
    if scheme != "https" and not (scheme == "http" and issuer.startswith("http://")):
        raise _discovery(f"the authorization server's {name} is not https")
    return value


# --- The client ---------------------------------------------------------------


def choose_client(
    org: Organization, server_id: str, found: Discovery
) -> connect_clients.Client:
    """The client the connect identifies itself with: the org's own or the
    platform's (``mcp_connect_clients.choose``), else our client metadata
    document where the authorization server takes one, else a client it
    registers for us, else ``ConnectRefused``."""
    refused = None
    try:
        chosen = connect_clients.choose(
            org,
            server_id,
            server_entry(org.pk, server_id) or {},
            cimd=found.cimd_supported,
            dcr=bool(found.registration_endpoint),
            auth_methods=found.auth_methods,
        )
    except connector_oauth_clients.ConnectorOAuthClientError as error:
        refused, chosen = (error.code, str(error)), None
    if refused is not None:
        # Raised outside the handler: nothing is chained to the refusal.
        raise ConnectRefused(refused[0], refused[1], 409)
    if chosen is not None:
        return chosen
    if found.cimd_supported:
        return connect_clients.Client(
            client_metadata_url(), AgentccMCPConnectState.ClientKind.CIMD
        )
    if found.registration_endpoint:
        return connect_clients.Client(
            _registered_client(found), AgentccMCPConnectState.ClientKind.DCR
        )
    raise ConnectRefused(
        "oauth_client_unavailable",
        "the server's authorization server accepts neither a client metadata "
        "document nor client registration",
        409,
    )


def _registered_client(found: Discovery) -> str:
    redirect = callback_url()
    known = AgentccMCPUpstreamClient.objects.filter(
        issuer=found.issuer, redirect_uri=redirect
    ).first()
    if known is not None:
        return known.client_id
    metadata = {k: v for k, v in client_metadata().items() if k != "client_id"}
    try:
        response = mcp_safe_fetch.fetch(
            found.registration_endpoint,
            method="POST",
            json_body=metadata,
            max_bytes=METADATA_LIMIT,
        )
        body = response.json() if response.status in (200, 201) else None
    except (mcp_safe_fetch.EgressRefused, ValueError):
        body = None
    client_id = body.get("client_id") if isinstance(body, dict) else None
    method = body.get("token_endpoint_auth_method", "none") if body else None
    if not isinstance(client_id, str) or not client_id or method != "none":
        raise ConnectRefused(
            "oauth_client_unavailable",
            "the authorization server did not register a public client",
            502,
        )
    try:
        with transaction.atomic():
            AgentccMCPUpstreamClient.objects.create(
                issuer=found.issuer, redirect_uri=redirect, client_id=client_id
            )
    except IntegrityError:
        # A concurrent connect registered first: use the one stored.
        return (
            AgentccMCPUpstreamClient.objects.filter(
                issuer=found.issuer, redirect_uri=redirect
            )
            .values_list("client_id", flat=True)
            .first()
            or client_id
        )
    return client_id


# --- Start ---------------------------------------------------------------------


def cookie_name(state: str) -> str:
    return COOKIE_PREFIX + _hash(state)[:16]


def start(
    org: Organization,
    user: User,
    server_id: str,
    binding: str | None = None,
    mode: str = shared_account.USER,
    *,
    individual_authenticated: bool = False,
) -> StartResult:
    """Begin a connect for ``user``'s own account on ``server_id``, or, with
    ``mode`` ``shared``, for the organization's one account (an Owner or Admin
    only). Raises ``ConnectRefused`` before anything is stored or fetched when
    the binding, the membership or role, or the server is wrong."""
    if not individual_authenticated or not is_member(user, org):
        raise ConnectRefused(
            "individual_required", "an authenticated individual is required", 403
        )
    if mcp_features.hidden_server(org.pk, server_id):
        raise ConnectRefused("not_found", "Not found.", 404)
    if mode not in shared_account.MODES:
        raise ConnectRefused("invalid_mode", "mode is user or shared")
    from agentcc.services import microsoft_connect_gate as microsoft

    entry = server_entry(org.pk, server_id, include_hidden=True)
    microsoft.require_microsoft_connect_enabled(entry, server_id=server_id)
    if not microsoft.platform_allowed(entry, server_id=server_id):
        # No discovery, state or platform credential read before the org-only
        # requirement, including shared, non-pilot and WorkIQ providers.
        if mode == shared_account.SHARED and not shared_account.is_admin(user, org):
            _shared_start(org, user, server_id, binding)  # retain its authority check
        try:
            connect_clients.choose(
                org, server_id, entry or {}, cimd=False, dcr=False, auth_methods=None
            )
        except connector_oauth_clients.ConnectorOAuthClientError as refused:
            raise ConnectRefused(refused.code, str(refused), 409) from None
    shared_generation = None
    if mode == shared_account.SHARED:
        server_url = _shared_start(org, user, server_id, binding)
        # Linearize the shared start before discovery/client I/O. A Disconnect
        # during that work must not let this older start join the new generation.
        with transaction.atomic():
            lock_org(org.pk)
            shared_generation = mcp_oauth_cancellation.current_shared(org.pk, server_id)
    else:
        server_url = _user_start(org, user, server_id, binding)
    try:
        connect_clients.preflight_identity(org, server_id, entry)
    except connector_oauth_clients.ConnectorOAuthClientError as refused:
        raise ConnectRefused(refused.code, str(refused), 409) from None
    declared = None
    if mode != shared_account.SHARED:
        current = server_entry(org.pk, server_id)
        declared = connector_k2.sign_in(org, server_id, current)
        if declared is None:
            declared = connector_org_app_oauth.sign_in(org, server_id, current)
    if declared is not None:
        found, client = declared
    else:
        found = discover(server_url)
        # Held as the grant it would store is: before any client or state.
        microsoft.require_microsoft_connect_enabled(
            entry, server_id=server_id, issuer=found.issuer
        )
        client = choose_client(org, server_id, found)
    # A pilot connector's sign-in, or one with the organization's own OAuth app,
    # is the org registration's only, and its grant records that.
    org_only = declared is not None
    return _begin(
        org, user, server_id, server_url, found, client, mode,
        org_only=org_only, shared_generation=shared_generation,
    )


def _shared_start(org, user, server_id: str, binding) -> str:
    if binding is not None:
        raise ConnectRefused(
            "binding_not_shared",
            "a connect link names a person; the organization's account is "
            "signed in without one",
        )
    if not shared_account.is_admin(user, org):
        raise ConnectRefused(
            "not_admin",
            "only an Owner or Admin signs in the organization's account",
            403,
        )
    return shared_oauth_server_url(org.pk, server_id)


def _user_start(org, user, server_id: str, binding) -> str:
    if binding is not None:
        bound = open_binding(binding)
        if bound is None:
            raise ConnectRefused(
                "binding_invalid", "the connect link is not valid or has expired", 403
            )
        if (bound["org_id"], bound["server_id"], bound["user_id"]) != (
            str(org.pk),
            server_id,
            str(user.pk),
        ):
            raise ConnectRefused(
                "binding_mismatch", "the connect link was made for someone else", 403
            )
    if not is_member(user, org):
        raise ConnectRefused("not_member", "not a member of this organization", 403)
    if (
        AgentccMCPCredential.objects.filter(
            organization=org,
            user=user,
            server_id=server_id,
            scope=Scope.USER,
            kind=Kind.OAUTH,
        )
        .filter(gateway_purge_pending=True)
        .exists()
    ):
        raise ConnectRefused(
            "cleanup_pending", "retry disconnect cleanup before connecting again", 409
        )
    return per_user_server_url(org.pk, server_id)


@transaction.atomic
def _begin(
    org, user, server_id, server_url, found, client, mode, *, org_only=False,
    shared_generation=None,
) -> StartResult:
    # State creation, Disconnect and publication use the same org authority.
    lock_org(org.pk)
    if mode == shared_account.USER:
        generation = mcp_oauth_cancellation.current(org.pk, user.pk, server_id)
    else:
        generation = mcp_oauth_cancellation.current_shared(org.pk, server_id)
        if shared_generation != generation:
            raise ConnectRefused(
                "connect_cancelled", "Disconnect cancelled this sign-in", 409
            )
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    cookie = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    now = timezone.now()
    AgentccMCPConnectState.objects.filter(
        organization=org, user=user, expires_at__lt=now
    ).delete()
    row = AgentccMCPConnectState.objects.create(
        organization=org,
        user=user,
        server_id=server_id,
        state_hash=_hash(state),
        cookie_hash=_hash(cookie),
        individual_authenticated=True,
        cancellation_generation=generation,
        encrypted_verifier=encrypt_token(verifier),
        issuer=found.issuer,
        token_endpoint=found.token_endpoint,
        client_id=client.client_id,
        client_kind=client.kind,
        client_auth=client.auth,
        org_only=org_only,
        resource=found.resource,
        redirect_uri=callback_url(),
        origin=origin_of(server_url),
        scopes=list(found.scopes),
        iss_required=found.iss_required,
        mode=mode,
        expires_at=now + CONNECT_TTL,
    )
    query = {
        "response_type": "code",
        "client_id": client.client_id,
        "redirect_uri": row.redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": found.resource,
    }
    if not found.resource:
        del query["resource"]  # a declared OAuth 2 server (K2) names no resource
    if found.scopes:
        query["scope"] = " ".join(found.scopes)
    query.update(found.authorize_params)
    logger.info(
        "mcp_connect_started",
        org_id=str(org.pk),
        user_id=str(user.pk),
        server_id=server_id,
        connect_id=str(row.pk),
        client_kind=client.kind,
        mode=mode,
    )
    return StartResult(
        authorize_url=_with_query(found.authorization_endpoint, query),
        cookie_name=cookie_name(state),
        cookie_value=cookie,
        max_age=int(CONNECT_TTL.total_seconds()),
        connect_id=str(row.pk),
    )


# --- Callback ------------------------------------------------------------------


def callback_hidden(state) -> bool:
    """An in-flight callback cannot complete a sub-feature switched OFF."""
    if mcp_features.enabled(mcp_features.K2_APPS):
        return False
    if not isinstance(state, str) or not state or len(state) > 4096:
        return False
    row = (
        AgentccMCPConnectState.objects.filter(state_hash=_hash(state))
        .only("organization_id", "server_id")
        .first()
    )
    return row is not None and mcp_features.hidden_server(
        row.organization_id, row.server_id
    )


def complete(params, cookies) -> CompleteResult:
    """Finish a connect from the query and browser cookies, single-use.

    Shared callbacks retain an unreachable state row through exchange as an
    additional fence (including for older workers). Publication also checks the
    durable shared generation; neither fence holds the org lock over exchange.
    """
    state = params.get("state")
    if not isinstance(state, str) or not state:
        return CompleteResult(False, "invalid_state")
    name = cookie_name(state)
    with transaction.atomic():
        row = (
            AgentccMCPConnectState.objects.select_for_update()
            .filter(state_hash=_hash(state))
            .first()
        )
        if row is not None:
            from agentcc.services import microsoft_connect_gate as microsoft

            microsoft.require_microsoft_connect_enabled(
                server_entry(row.organization_id, row.server_id, include_hidden=True),
                server_id=row.server_id,
                issuer=row.issuer,
            )
            if row.mode == shared_account.SHARED:
                # Consume the public lookup immediately, but retain the row as
                # a cancellation fence while provider I/O runs. Disconnect
                # deletes it, including callbacks already exchanging a code.
                row.state_hash = _hash(secrets.token_urlsafe(32))
                row.save(update_fields=["state_hash"])
            else:
                row.delete()
    if row is None:
        return CompleteResult(False, "invalid_state", cookie_name=name)
    try:
        return _complete_consumed(row, params, cookies, name)
    finally:
        if row.mode == shared_account.SHARED:
            AgentccMCPConnectState.objects.filter(pk=row.pk).delete()


def _complete_consumed(row, params, cookies, name):
    done = _result_for(row.server_id, name)
    if timezone.now() >= row.expires_at:
        return done(False, "expired")
    cookie = cookies.get(name)
    if not isinstance(cookie, str) or not hmac.compare_digest(
        _hash(cookie), row.cookie_hash
    ):
        return done(False, "browser_mismatch")
    if params.get("error"):
        return done(False, "denied")
    iss = params.get("iss")
    if (row.iss_required and not iss) or (
        iss and iss.rstrip("/") != row.issuer.rstrip("/")
    ):
        return done(False, "issuer_mismatch")
    code = params.get("code")
    if not isinstance(code, str) or not code:
        return done(False, "invalid_response")
    org, user = row.organization, row.user
    if not row.individual_authenticated:
        return done(False, "individual_required")
    is_shared = row.mode == shared_account.SHARED
    if not is_member(user, org):
        return done(False, "not_member")
    if is_shared and not shared_account.is_admin(user, org):
        return done(False, "not_admin")
    try:
        if is_shared:
            url = shared_oauth_server_url(org.pk, row.server_id)
        else:
            url = per_user_server_url(org.pk, row.server_id)
    except ConnectRefused:
        return done(False, "server_changed")
    if origin_of(url) != row.origin:
        return done(False, "server_changed")
    tokens, reason = _exchange_code(row, code, org)
    if tokens is None:
        return done(False, reason)
    if is_shared:
        credential, reason = _store_shared(row, tokens)
        if credential is None:
            return done(False, reason)
        gateway_credentials.purge(str(org.pk), row.server_id, None)
    else:
        try:
            credential = _store_tokens(row, tokens)
        except ConnectRefused as refused:
            return done(False, refused.code)
        if credential.gateway_purge_pending:
            return done(False, "cleanup_pending")
    logger.info(
        "mcp_connect_completed",
        org_id=str(org.pk),
        user_id=str(user.pk),
        server_id=row.server_id,
        credential_id=str(credential.pk),
        version=credential.version,
        mode=row.mode,
    )
    return done(True, "connected")


def _store_shared(row: AgentccMCPConnectState, tokens: _Tokens):
    """``(credential, "")``, or ``(None, reason)`` when the server changed or
    the config write was refused (the row is rolled back with it). Never
    raises."""
    from agentcc.services.config_push import OrgConfigRejected
    from agentcc.services.mcp_credentials import MCPCredentialError

    failed = None
    try:
        credential, _ = shared_account.store(row, tokens)
    except shared_account.ConnectCancelled:
        return None, "connect_cancelled"
    except (shared_account.ServerChanged, OrgConfigRejected, MCPCredentialError) as e:
        failed = type(e).__name__
    if failed is not None:
        logger.warning(
            "mcp_connect_shared_not_stored", server_id=row.server_id, error_type=failed
        )
        return None, "server_changed"
    return credential, ""


def _result_for(server_id: str, name: str):
    def done(ok: bool, reason: str) -> CompleteResult:
        if not ok:
            logger.warning("mcp_connect_refused", server_id=server_id, reason=reason)
        return CompleteResult(ok, reason, server_id=server_id, cookie_name=name)

    return done


def _exchange_code(row: AgentccMCPConnectState, code: str, org: Organization):
    """``(_Tokens, "")`` or ``(None, reason)``; never raises. A confidential
    client is resolved again here, and the code is exchanged only while it is
    the client the start chose (``client_changed`` otherwise)."""
    from agentcc.services.microsoft_client_identity import deny_if_excluded

    try:
        deny_if_excluded(row.client_id, origins=(row.token_endpoint, row.resource))
    except connector_oauth_clients.ConnectorOAuthClientError:
        return None, "client_changed"
    client = connect_clients.Client(row.client_id, row.client_kind, row.client_auth)
    ref = None
    own_token_type = False
    if client.confidential:
        entry = server_entry(org.pk, row.server_id) or {}
        try:
            issued, ref = connect_clients.issuing(org, row.server_id, entry, row.client_id)
        except connector_oauth_clients.ConnectorOAuthClientError:
            # A removed/replaced org registration or excluded legacy platform
            # state is a value-free callback refusal, before verifier custody.
            return None, "client_changed"
        if issued.kind != client.kind:
            return None, "client_changed"
        # Only a server that signs in with the organization's own OAuth app
        # may name its own token type (``parse_token_response``).
        org_app_server = connector_org_app_oauth.is_org_app_server(entry)
        own_token_type = bool(row.org_only) and org_app_server
    try:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": row.redirect_uri,
            "code_verifier": decrypt_token(row.encrypted_verifier),
            "resource": row.resource,
        }
        if not row.resource:
            del form["resource"]
    except Exception as error:
        logger.warning("mcp_connect_exchange_failed", error_type=type(error).__name__)
        return None, "exchange_failed"
    response = connect_clients.post(
        row.token_endpoint, form, client, ref, max_bytes=METADATA_LIMIT
    )
    if response is None:
        return None, "exchange_failed"
    tokens = parse_token_response(response, any_token_type=own_token_type)
    if tokens is None:
        return None, "exchange_failed"
    return tokens, ""


def parse_token_response(response, *, any_token_type=False) -> "_Tokens | None":
    """The tokens of a 200 token response (RFC 6749 5.1), sealed; None for
    anything else. Never raises. The token type is Bearer, or with
    ``any_token_type`` any non-empty one: a server that signs in only with the
    organization's own OAuth app (``connector_org_app_oauth``) may name its
    own (Slack's user token method answers ``user``), and the gateway presents
    the token as a bearer token either way."""
    try:
        if response.status != 200:
            return None
        body = response.json()
        access = body.get("access_token")
        if not isinstance(access, str) or not access:
            return None
        token_type = body.get("token_type")
        if not isinstance(token_type, str) or not (
            token_type.lower() == "bearer" or (any_token_type and token_type.strip())
        ):
            return None
        refresh = body.get("refresh_token")
        expires_in = body.get("expires_in")
        expires_at = None
        if isinstance(expires_in, int | float) and not isinstance(expires_in, bool):
            expires_at = timezone.now() + timedelta(seconds=max(0, int(expires_in)))
        scope = body.get("scope")
        return _Tokens(
            access=Sealed(access),
            refresh=Sealed(refresh) if isinstance(refresh, str) and refresh else None,
            expires_at=expires_at,
            scopes=tuple(scope.split()) if isinstance(scope, str) else (),
        )
    except Exception:
        return None


def _store_tokens(row: AgentccMCPConnectState, tokens: _Tokens) -> AgentccMCPCredential:
    """Publish only if no Disconnect cancelled this start, even after its
    one-use state has been consumed. The check and write share the org lock."""
    fields = {
        "kind": Kind.OAUTH,
        "header_name": "",
        "encrypted_secret": encrypt_token(tokens.access.reveal()),
        "encrypted_refresh": encrypt_token(tokens.refresh.reveal())
        if tokens.refresh
        else None,
        "token_expires_at": tokens.expires_at,
        "oauth_issuer": row.issuer,
        "oauth_client_id": row.client_id,
        "oauth_resource": row.resource,
        "oauth_org_only": row.org_only,
        "scopes": list(tokens.scopes or row.scopes),
        "origin": row.origin,
    }
    with transaction.atomic():
        lock_org(row.organization_id)
        lock_identity(row.organization_id, row.user_id, row.server_id)
        generation = mcp_oauth_cancellation.current(
            row.organization_id, row.user_id, row.server_id
        )
        if row.cancellation_generation == generation:
            return _store_current_tokens(row, fields)
        discarded = mcp_oauth_cancellation.retain_cancelled(row, fields)
    # Persist the retry obligation before vendor I/O or raising the refusal.
    # A discarded grant must never evict or overwrite a later valid sign-in.
    mcp_oauth_cancellation.finish_cancelled(discarded)
    raise ConnectRefused("connect_cancelled", "Disconnect cancelled this sign-in", 409)


def _store_current_tokens(row, fields):
    """Caller holds org and identity locks and validated cancellation generation."""
    from agentcc.services.mcp_connection_cleanup import supersede
    from agentcc.services.mcp_user_credential_lifecycle import evict

    supersede(row.organization_id, row.user_id, row.server_id)
    current = (
        AgentccMCPCredential.objects.select_for_update()
        .filter(
            organization_id=row.organization_id,
            server_id=row.server_id,
            scope=Scope.USER,
            user_id=row.user_id,
            account_label="",
            status=Status.ACTIVE,
        )
        .first()
    )
    if current is not None and current.kind == Kind.OAUTH:
        for name, value in fields.items():
            setattr(current, name, value)
        current.version += 1
        current.rotated_at = timezone.now()
        current.save(update_fields=[*fields, "version", "rotated_at"])
        evict(current)
        return current
    if current is not None:
        # A static secret this member entered by hand: replaced.
        current.status = Status.REVOKED
        current.save(update_fields=["status"])
    current = AgentccMCPCredential.objects.create(
        organization_id=row.organization_id,
        server_id=row.server_id,
        scope=Scope.USER,
        user_id=row.user_id,
        created_by_id=row.user_id,
        status=Status.ACTIVE,
        **fields,
    )
    evict(current)
    return current


# --- Disconnect ----------------------------------------------------------------


def disconnect(
    org: Organization, user: User, server_id: str, mode: str = shared_account.USER
) -> tuple[bool, bool, bool]:
    """Return (found, gateway_synced, cleanup_pending). Local revocation is
    not confirmation of gateway eviction or upstream invalidation."""
    if mode == shared_account.SHARED:
        return _disconnect_shared(org, user, server_id)
    from agentcc.services.mcp_connection_cleanup import disconnect_user

    return disconnect_user(org, user, server_id)


def _disconnect_shared(org, user, server_id: str) -> tuple[bool, bool, bool]:
    from agentcc.services.mcp_connection_cleanup import finish

    rows, config_synced = shared_account.disconnect(org, user, server_id)
    return finish(org, rows, None, config_synced=config_synced)


def _revoke_upstream(
    org,
    server_id,
    issuer,
    client_id,
    encrypted_refresh,
    encrypted_access,
    *,
    org_only=False,
):
    from agentcc.services.mcp_revocation import revoke

    return revoke(
        org,
        server_id,
        issuer,
        client_id,
        encrypted_refresh,
        encrypted_access,
        org_only=org_only,
    )


# --- Helpers -------------------------------------------------------------------


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _with_query(url: str, query: dict) -> str:
    parts = urlsplit(url)
    joined = f"{parts.query}&{urlencode(query)}" if parts.query else urlencode(query)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, joined, ""))
