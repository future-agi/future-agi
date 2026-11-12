"""The OAuth client a connect signs in as, and how it proves it (M25x with
AM-C1's client order, CN3's resolver, CN11's organization clients).

A connect, every refresh of the token it got, and the revocation of that
token reach the vendor's authorization server as one client. ``choose``
picks it when the connect starts, in CN3's order:

1. The org's own client (CN11): keyed to the server, always; keyed to the
   server's client group, where the catalog says its authorization server
   takes pre-registered clients.
2. FutureAGI's platform client for that group, on the same condition, and
   never where a URL variable sets the host of an endpoint its secret is sent
   to (``connector_oauth_clients.client_source``), nor for a vendor MCP
   provider that signs in only with the organization's own OAuth app
   (``connector_catalog.oauth2_by_org_app``): it is not even read.
3. Otherwise none here: the connect identifies itself by our client metadata
   document (CIMD), else a client the server registers for us (DCR), both
   public clients (``mcp_upstream_oauth.choose_client``).

A server added from the catalog (``catalog_ref``) takes its client group,
token and revocation URLs, registration methods and client authentication
from its provider's declared spec. A server the org added by URL has no
group: only the org's client keyed to it is a source.

The first two are confidential clients. Their secret goes only in the token,
refresh and revocation requests, by HTTP Basic (RFC 6749 2.3.1, the default
of RFC 8414) or in the form where the server takes only that; a server that
takes neither is refused. The secret is never stored with the connect or the
token: each request resolves the client again (``issuing``), and sends a
secret only while the resolved client is the one that issued the token (the
same client id). A token whose client the org replaced or removed is never
sent with another client's secret, and its member signs in again.

Secrets. As in ``connector_oauth_clients``: a function here that holds a
client secret returns a result and never raises, and the only one that does
is ``post``.
"""

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote_plus

import structlog

from accounts.models import Organization
from agentcc.services import connector_catalog, mcp_safe_fetch
from agentcc.services import microsoft_connect_gate as microsoft
from agentcc.services import connector_oauth_clients as clients

logger = structlog.get_logger(__name__)

ORG = "org"
PLATFORM = "platform"
CONFIDENTIAL = (ORG, PLATFORM)
PUBLIC = "public"
# A token's client while the org's or the platform's client cannot be
# resolved (unreadable, misconfigured): no secret is sent, and the token's
# client may still be that one.
UNRESOLVED = "unresolved"

BASIC = "client_secret_basic"
POST = "client_secret_post"
NONE = "none"
_DECLARED_AUTH = {"basic": BASIC, "body": POST}


@dataclass(frozen=True)
class Client:
    """The client a request authenticates as: ``kind`` ``org`` or
    ``platform`` (confidential, by ``auth``), else a public one (CIMD, DCR,
    ``public`` for a token whose confidential client is gone, or
    ``unresolved`` while the org's or the platform's client cannot be
    resolved)."""

    client_id: str
    kind: str
    auth: str = NONE

    @property
    def confidential(self) -> bool:
        return self.kind in CONFIDENTIAL


@dataclass(frozen=True)
class _Spec:
    """What CN3 needs of a catalog server's provider."""

    app_id: str
    client_group: str | None
    token_url: str
    revocation_url: str | None
    registration: tuple[str, ...] | None  # None: the provider declares none
    client_auth: str | None  # declared; None: the server's metadata decides
    # Signs in only with the organization's own OAuth app
    # (connector_catalog.oauth2_by_org_app): the platform client is never read.
    org_only: bool = False


def _spec(entry: Mapping) -> _Spec | None:
    """The declared spec of the provider a server was added from; None for a
    server added by URL (or one whose provider the catalog no longer has)."""
    record = connector_catalog.provider_record(entry)
    if record is None:
        return None
    app_id = record.get("app")
    auth = record.get("auth") if isinstance(record.get("auth"), Mapping) else {}
    transport = record.get("transport")
    url = transport.get("url") if isinstance(transport, Mapping) else None
    if auth.get("kind") == "mcp_oauth" or connector_catalog.oauth2_by_discovery(
        record
    ):
        # Discovered endpoints: CN3 is given the URL discovery starts from. An
        # oauth2 record with dcr or cimd signs in as mcp_oauth does: its
        # declared group and endpoints are not read, so only the org's own
        # client for this server is confidential, else CIMD or DCR.
        if not isinstance(url, str):
            return None
        return _Spec(app_id, None, url, None, None, None)
    if auth.get("kind") == "oauth2" and isinstance(auth.get("oauth2"), Mapping):
        oauth2 = auth["oauth2"]
        revocation = oauth2.get("revocation")
        registration = oauth2.get("registration")
        return _Spec(
            app_id=app_id,
            client_group=oauth2.get("clientGroup"),
            token_url=oauth2.get("tokenUrl"),
            revocation_url=revocation.get("url")
            if isinstance(revocation, Mapping)
            else None,
            registration=tuple(registration)
            if isinstance(registration, list)
            else None,
            client_auth=_DECLARED_AUTH.get(oauth2.get("clientAuth")),
            org_only=connector_catalog.oauth2_by_org_app(record),
        )
    return None


def resolve(
    org: Organization, server_id: str, entry: Mapping, *, platform: bool = True,
    issuing_client_id: str | None = None,
) -> clients.ClientRef | None:
    """The org's or the platform's client for the server (CN3), or None;
    with ``platform=False`` the org's own only, the platform's never read.
    Raises ``connector_oauth_clients.ConnectorOAuthClientError``."""
    microsoft.require_microsoft_connect_enabled(entry, server_id=server_id)
    platform = platform and microsoft.platform_allowed(entry, server_id=server_id)
    spec = _spec(entry)
    if spec is None:
        return clients.server_client(org, server_id, issuing_client_id=issuing_client_id)
    return clients.resolve(
        org,
        spec.app_id,
        spec.client_group,
        token_url=spec.token_url,
        revocation_url=spec.revocation_url,
        server_id=server_id,
        platform=platform and not spec.org_only,
        issuing_client_id=issuing_client_id,
    )


def preflight_identity(org, server_id, entry):
    """Check a winning org registration's ID before remote discovery.

    This does not resolve/decrypt a client or replace any start authority.
    Labels select a registration, but never exempt its identity from exclusion.
    """
    from agentcc.models.mcp_credential import AgentccMCPCredential
    from agentcc.services.microsoft_client_identity import deny_if_excluded

    spec = _spec(entry or {})
    keys = [clients.org_client_key(server_id=server_id)]
    if spec is not None:
        keys.append(clients.org_client_key(client_group=spec.client_group or spec.app_id))
    rows = dict(AgentccMCPCredential.objects.filter(
        organization=org, server_id__in=keys, kind="oauth_client", scope="shared",
        user__isnull=True, account_label="", status="active",
    ).values_list("server_id", "oauth_client_id"))
    for key in keys:
        if key in rows:
            deny_if_excluded(rows[key])
            return


def choose(
    org: Organization,
    server_id: str,
    entry: Mapping,
    *,
    cimd: bool,
    dcr: bool,
    auth_methods,
) -> Client | None:
    """The confidential client a connect to the server signs in as, or None
    for a public one. ``cimd``, ``dcr`` and ``auth_methods`` are what the
    server's authorization server advertised. Raises
    ``connector_oauth_clients.ConnectorOAuthClientError``: an org client that
    cannot be read, a misconfigured platform client, no client the server
    accepts, or a server that takes no client secret for the org's client."""
    ref = resolve(org, server_id, entry)
    if not microsoft.platform_allowed(entry, server_id=server_id) and (
        ref is None or ref.source != ORG
    ):
        raise clients.OrgClientRequired(microsoft.ORG_CLIENT_MESSAGE)
    spec = _spec(entry)
    if spec is None:
        if ref is None:
            return None
        kind = ORG  # keyed to the server: always the org's choice
    else:
        # A provider that declares its registration methods is held to them,
        # with or without a client of ours: one that takes only
        # pre-registered clients is refused rather than sent to CIMD or DCR.
        registration = spec.registration
        if registration is None:
            registration = tuple(m for m, ok in (("cimd", cimd), ("dcr", dcr)) if ok)
        kind = clients.client_source(
            ref,
            registration=registration,
            client_group=spec.client_group or spec.app_id,
            token_url=spec.token_url,
            revocation_url=spec.revocation_url,
        )
        if kind not in CONFIDENTIAL:
            return None
    auth = auth_method(spec.client_auth if spec else None, auth_methods)
    if auth is None:
        raise clients.OAuthClientUnavailable(
            "the server's authorization server takes no client secret, so the "
            "organization's own OAuth app cannot sign in to it"
        )
    return Client(ref.client_id, kind, auth)


def auth_method(declared: str | None, advertised) -> str | None:
    """How a confidential client proves its secret: as the catalog declares,
    else HTTP Basic where the server's metadata names no methods or names it,
    else the form where it names that; None where it takes neither."""
    if declared:
        return declared
    if advertised is None:
        return BASIC
    methods = advertised if isinstance(advertised, list | tuple) else ()
    for method in (BASIC, POST):
        if method in methods:
            return method
    return None


def issuing(
    org: Organization,
    server_id: str,
    entry: Mapping | None,
    client_id: str,
    *,
    auth_methods=None,
    org_only: bool = False,
) -> tuple[Client, clients.ClientRef | None]:
    """The client that issued a token with ``client_id``, for its refresh or
    revocation: the org's or the platform's while it still resolves to that
    client id, with its secret's holder; else the public client of that id
    (a token from CIMD or DCR, or one whose confidential client is gone: no
    secret is sent, and the server decides). Where resolving raised (an org
    client that cannot be read, a misconfigured platform client), the kind
    is ``unresolved``: no secret is sent either, but the token's client may
    still be that one. With ``org_only`` the platform client is never
    resolved, so the answer is the org's client or a public one; a caller
    that needs the org's (a pilot connector's grant) refuses the public one."""
    microsoft.require_microsoft_connect_enabled(entry, server_id=server_id)
    restricted = not microsoft.platform_allowed(entry, server_id=server_id)
    microsoft.require_org_client_identity(client_id, entry, server_id=server_id)
    ref = None
    unresolved = False
    if entry is not None:
        try:
            ref = resolve(
                org, server_id, entry, platform=not org_only,
                issuing_client_id=client_id if restricted else None,
            )
        except clients.ConnectorOAuthClientError as refused:
            unresolved = True
            logger.warning(
                "mcp_connect_client_unresolved", server_id=server_id, code=refused.code
            )
    if restricted and (ref is None or ref.source != ORG or ref.client_id != client_id):
        # Never degrade an excluded confidential client to public refresh/revoke.
        raise clients.OrgClientRequired(microsoft.ORG_CLIENT_MESSAGE)
    if ref is not None and ref.client_id == client_id:
        spec = _spec(entry)
        auth = auth_method(spec.client_auth if spec else None, auth_methods) or BASIC
        return Client(client_id, ref.source, auth), ref
    return Client(client_id, UNRESOLVED if unresolved else PUBLIC), None


def post(url: str, form: dict, client: Client, ref, *, max_bytes: int):
    """POST ``form`` to the token or revocation endpoint ``url`` as
    ``client``, with its secret when it is confidential and ``ref`` is still
    that client. Returns the response, or None when the request did not go
    out (logged by type only). Never raises: its frame holds the secret."""
    try:
        data = dict(form)
        headers = None
        data["client_id"] = client.client_id
        if client.confidential:
            if ref is None or ref.client_id != client.client_id:
                return None
            if client.auth == POST:
                data["client_secret"] = ref.token_exchange_secret()
            else:
                del data["client_id"]
                pair = (
                    quote_plus(client.client_id)
                    + ":"
                    + quote_plus(ref.token_exchange_secret())
                )
                headers = {
                    "Authorization": "Basic "
                    + base64.b64encode(pair.encode()).decode("ascii")
                }
        return mcp_safe_fetch.fetch(
            url, method="POST", data=data, headers=headers, max_bytes=max_bytes
        )
    except Exception as error:
        logger.warning(
            "mcp_connect_token_request_failed", error_type=type(error).__name__
        )
        return None
