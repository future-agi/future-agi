"""Sign-in for vendor MCP servers whose OAuth takes only a pre-registered
client, with the organization's own OAuth app
(``connector_catalog.oauth2_by_org_app``: Slack, HubSpot, Asana, Zoom,
Docusign and Salesforce, among others).

The authorization server is the one the provider record declares: its
``authorizeUrl``, ``tokenUrl``, ``revocation``, ``scopes`` and
``extraAuthorizeParams``, with the org's values for the record's org-scoped
URL variables filled in. Nothing is discovered and no MCP ``resource`` is
sent: these vendors run plain OAuth 2 servers beside their MCP servers. PKCE
S256 is always sent.

The client is the organization's own registration with the vendor, stored by
Configure OAuth (a manual client) or the organization's OAuth clients (the
vault's ``oauth_client`` row for the server, or for the record's client group,
else its app): a confidential client that proves its secret by the declared
``clientAuth``, resolved again for every token, refresh and revocation request
(``mcp_connect_clients``). FutureAGI's platform client is never a source, so
an organization without its own app is refused before anything is stored or
fetched. A member's token stays tied to the app that issued it: once the org
removes or replaces that app, the token is no longer served (``needs_reauth``,
``connector_k2.issued_by_registration``) until the member signs in again.

The gateway needs nothing new: it fetches the member's token for a per-person
server just in time (``mcp_upstream_tokens``), as for any other.

Secrets. No function here holds a client secret or a token.
"""

from collections.abc import Mapping
from urllib.parse import urlsplit

import structlog

from agentcc.services import connector_catalog, connector_k2
from agentcc.services import connector_oauth_clients as clients
from agentcc.services import mcp_connect_clients as connect_clients

logger = structlog.get_logger(__name__)

PER_USER = "per_user"
ORG_CLIENT_REQUIRED = clients.OrgClientRequired.code


def is_org_app_server(entry) -> bool:
    """Whether a server was added from a provider that signs in only with
    the organization's own OAuth app, for each person's own account."""
    if not isinstance(entry, Mapping):
        return False
    credential = entry.get("credential")
    if not isinstance(credential, Mapping) or credential.get("mode") != PER_USER:
        return False
    return connector_catalog.oauth2_by_org_app(connector_catalog.provider_record(entry))


def declared(entry) -> "connector_k2.Pilot | None":
    """The declared sign-in of such a server, its URL variables filled in, in
    the shape of a pilot connector's; None for any other server, or for a
    record or value this module cannot vouch for (fail closed)."""
    try:
        return _declared(entry)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "connector_org_app_record_unreadable", error_type=type(exc).__name__
        )
        return None


def _declared(entry):
    if not is_org_app_server(entry):
        return None
    record = connector_catalog.provider_record(entry)
    oauth2 = record["auth"]["oauth2"]
    values = connector_k2._variables(entry, oauth2.get("urlVariables") or {})
    if values is None:
        return None
    authorize = connector_k2._render(oauth2.get("authorizeUrl"), values)
    token = connector_k2._render(oauth2.get("tokenUrl"), values)
    revocation = oauth2.get("revocation")
    revoke = ""
    if isinstance(revocation, Mapping):
        revoke = connector_k2._render(revocation.get("url"), values)
        if revoke is None:
            return None
    reachable = connector_k2._reachable
    if not (reachable(authorize) and reachable(token)) or (
        revoke and not reachable(revoke)
    ):
        return None
    params = connector_k2._authorize_params(oauth2.get("extraAuthorizeParams") or {})
    scopes = oauth2.get("scopes") or []
    if params is None or not all(isinstance(s, str) and s for s in scopes):
        return None
    parts = urlsplit(authorize)
    return connector_k2.Pilot(
        app_id=record["app"],
        client_group=oauth2.get("clientGroup") or record["app"],
        issuer=f"{parts.scheme}://{parts.netloc}",
        authorize_url=authorize,
        token_url=token,
        revocation_url=revoke,
        scopes=tuple(scopes),
        authorize_params=params,
    )


def required_message(app_id: str) -> str:
    try:
        app = connector_catalog.load_bundle().apps.get(app_id)
    except connector_catalog.ConnectorCatalogError:
        app = None
    name = app.card["name"] if app is not None else app_id
    return (
        f"{name} signs in only through your organization's own {name} OAuth "
        "app; an Owner or Admin registers it with Configure OAuth under "
        "Integrations first"
    )


def sign_in(org, server_id: str, entry):
    """``(Discovery, Client)`` for such a server's connect start, or None for
    any other server. Raises ``ConnectRefused`` when the org has no readable
    app of its own for it, before anything is stored or fetched."""
    from agentcc.services import mcp_upstream_oauth as connect

    spec = declared(entry)
    if spec is None:
        if is_org_app_server(entry):
            # Never fall back to discovery with the organization's secret.
            raise connect.ConnectRefused(
                "sign_in_not_served", "catalogue sign-in is not served", 409
            )
        return None
    refused = None
    chosen = None
    try:
        chosen = connect_clients.choose(
            org, server_id, entry, cimd=False, dcr=False, auth_methods=None
        )
    except clients.ConnectorOAuthClientError as error:
        refused = error.code
    if refused == clients.OrgClientUnreadable.code:
        raise connect.ConnectRefused(
            refused,
            "the organization's OAuth app for this server cannot be read; an "
            "Owner or Admin replaces it",
            409,
        )
    if chosen is None or chosen.kind != connect_clients.ORG:
        raise connect.ConnectRefused(
            ORG_CLIENT_REQUIRED, required_message(spec.app_id), 409
        )
    found = connect.Discovery(
        issuer=spec.issuer,
        authorization_endpoint=spec.authorize_url,
        token_endpoint=spec.token_url,
        registration_endpoint="",
        revocation_endpoint=spec.revocation_url,
        cimd_supported=False,
        iss_required=False,
        resource="",
        scopes=spec.scopes,
        authorize_params=spec.authorize_params,
    )
    return found, chosen
