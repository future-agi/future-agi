"""Sign-in for FutureAGI's own K2 connectors on a hosted connector runtime
(W4b, G14b): Gmail, Google Calendar, Microsoft Outlook and Microsoft Teams.

Off by default. Nothing here applies until the operator sets
``AGENTCC_CONNECTORS_RUNTIME_URL`` (a Django setting, else the environment):
the base URL at which this deployment's gateway reaches its
``agentcc-connectors`` runtime. Unset, every catalogue sign-in stays refused
with ``sign_in_not_served``, exactly as before.

Set, a server is a K2 pilot server only when all of these hold; any other
catalogue server stays held back:

- its ``catalog_ref`` names, in the loaded bundle, FutureAGI's own REST
  provider (``kind: rest``, ``publisher: futureagi``, id ``futureagi-rest``)
  of one of the four pilot apps, in that app's client group (``google`` or
  ``microsoft``), with a declared OAuth 2 spec: PKCE, pre-registered clients
  only, and the per-person account mode;
- its credential mode is ``per_user``;
- its URL is exactly ``<runtime>/<app>/mcp``. A member's Google or Microsoft
  token is bound to, and served for, the operator's own runtime only.

The authorization server is the one the provider declares (``authorizeUrl``,
``tokenUrl``, ``revocation``), with the org's values for its org-scoped URL
variables (Microsoft's ``{tenant}``) filled in. Nothing is discovered, and no
MCP ``resource`` is sent: these are plain OAuth 2 servers.

Registration. The sign-in is always a confidential client: the organization's
own OAuth app registration with Google or Microsoft (CN11, the vault's
``oauth_client`` row for the group or for the server). FutureAGI's platform
client is never a source here, whatever the environment holds, so an org
without a registration of its own is refused
(``connector_oauth_org_client_required``) before anything is stored or
fetched. Registering FutureAGI's own verified apps for hosted use is an owner
action, not this module's.

A served token stays tied to the registration that issued it: once the org
removes or replaces its app, the member's token is no longer served
(``needs_reauth``) until they sign in again through the current app.

Secrets. No function here holds a client secret or a token: the secret is
resolved per request by ``mcp_connect_clients`` and goes only to the declared
token and revocation endpoints.
"""

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import structlog
from django.conf import settings

from agentcc import mcp_features
from agentcc.services import connector_catalog, mcp_safe_fetch
from agentcc.services import connector_oauth_clients as clients
from agentcc.services import mcp_connect_clients as connect_clients

logger = structlog.get_logger(__name__)

RUNTIME_URL_SETTING = "AGENTCC_CONNECTORS_RUNTIME_URL"

# The four pilot apps and the client group each signs in with.
PILOT_GROUPS = {
    "gmail": "google",
    "google-calendar": "google",
    "microsoft-outlook": "microsoft",
    "microsoft-teams": "microsoft",
}
VENDOR_NAMES = {"google": "Google", "microsoft": "Microsoft"}
PROVIDER_ID = "futureagi-rest"
PER_USER = "per_user"

# The org has no registration of its own (CN3's OrgClientRequired code).
ORG_CLIENT_REQUIRED = clients.OrgClientRequired.code

REGISTERED = "registered"
UNREADABLE = "unreadable"
MISSING = "missing"

# Authorize parameters the flow sets itself: a declared extra never replaces one.
_RESERVED_PARAMS = frozenset(
    {
        "response_type",
        "client_id",
        "redirect_uri",
        "state",
        "code_challenge",
        "code_challenge_method",
        "scope",
        "resource",
    }
)
_PARAM_NAME = re.compile(r"^[a-z_]{1,32}$")
_PARAM_VALUE = re.compile(r"^[A-Za-z0-9_.\- ]{1,64}$")


@dataclass(frozen=True)
class Pilot:
    """A K2 pilot server's declared sign-in, its URL variables filled in."""

    app_id: str
    client_group: str
    issuer: str  # the authorize endpoint's origin, recorded on the token row
    authorize_url: str
    token_url: str
    revocation_url: str  # "" where the provider declares none
    scopes: tuple[str, ...]
    authorize_params: tuple[tuple[str, str], ...]

    @property
    def vendor(self) -> str:
        return VENDOR_NAMES[self.client_group]


def runtime_url() -> str | None:
    """The operator's runtime base URL, or None (off). A value that is not an
    absolute http(s) URL with a host and nothing after its path is off too."""
    if not mcp_features.enabled(mcp_features.K2_APPS):
        return None
    raw = getattr(settings, RUNTIME_URL_SETTING, "") or os.environ.get(
        RUNTIME_URL_SETTING, ""
    )
    if not isinstance(raw, str) or not raw:
        return None
    parts = urlsplit(raw)
    if (
        parts.scheme not in ("https", "http")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        logger.warning("connector_k2_runtime_url_invalid", setting=RUNTIME_URL_SETTING)
        return None
    return raw.rstrip("/")


def pilot(entry, *, provider=None) -> Pilot | None:
    """The declared sign-in of a K2 pilot server entry, or None: off, not a
    pilot, or a record this module cannot vouch for (fail closed)."""
    try:
        return _pilot(entry, provider)
    except (AttributeError, KeyError, TypeError, ValueError, re.error) as exc:
        logger.warning("connector_k2_record_unreadable", error_type=type(exc).__name__)
        return None


def _pilot(entry, record=None) -> Pilot | None:
    base = runtime_url()
    if base is None or not isinstance(entry, Mapping):
        return None
    credential = entry.get("credential")
    if not isinstance(credential, Mapping) or credential.get("mode") != PER_USER:
        return None
    # During bundle planning the record is already in hand; do not load recursively.
    if record is None:
        record = connector_catalog.provider_record(entry)
    if not isinstance(record, Mapping):
        return None
    app_id = record.get("app")
    group = PILOT_GROUPS.get(app_id)
    auth = record.get("auth")
    oauth2 = auth.get("oauth2") if isinstance(auth, Mapping) else None
    if (
        group is None
        or record.get("id") != PROVIDER_ID
        or record.get("kind") != "rest"
        or record.get("publisher") != "futureagi"
        or "user" not in (record.get("accountModes") or [])
        or auth.get("kind") != "oauth2"
        or not isinstance(oauth2, Mapping)
        or oauth2.get("clientGroup") != group
        or oauth2.get("pkce") is not True
        or list(oauth2.get("registration") or []) != ["preregistered"]
        or entry.get("url") != f"{base}/{app_id}/mcp"
    ):
        return None
    values = _variables(entry, oauth2.get("urlVariables") or {})
    if values is None:
        return None
    authorize = _render(oauth2.get("authorizeUrl"), values)
    token = _render(oauth2.get("tokenUrl"), values)
    revocation = oauth2.get("revocation")
    revoke = ""
    if isinstance(revocation, Mapping):
        revoke = _render(revocation.get("url"), values)
        if revoke is None or revocation.get("tokenField", "token") != "token":
            return None
    if not (_reachable(authorize) and _reachable(token)) or (
        revoke and not _reachable(revoke)
    ):
        return None
    params = _authorize_params(oauth2.get("extraAuthorizeParams") or {})
    scopes = oauth2.get("scopes") or []
    if params is None or not all(isinstance(s, str) and s for s in scopes):
        return None
    parts = urlsplit(authorize)
    return Pilot(
        app_id=app_id,
        client_group=group,
        issuer=f"{parts.scheme}://{parts.netloc}",
        authorize_url=authorize,
        token_url=token,
        revocation_url=revoke or "",
        scopes=tuple(scopes),
        authorize_params=params,
    )


def _variables(entry: Mapping, declared: Mapping) -> dict[str, str] | None:
    """The org's value, else the default, for each org-scoped variable of the
    declared spec, each meeting its pattern; None when one cannot be filled."""
    supplied = entry.get("url_variables")
    supplied = supplied if isinstance(supplied, Mapping) else {}
    values = {}
    for name, spec in declared.items():
        if not isinstance(spec, Mapping) or spec.get("scope") != "org":
            return None
        value = supplied.get(name, spec.get("default"))
        pattern = spec.get("pattern")
        if not isinstance(value, str) or not isinstance(pattern, str):
            return None
        if not re.fullmatch(pattern, value):
            return None
        values[name] = value
    return values


def _render(template, values: Mapping[str, str]) -> str | None:
    if not isinstance(template, str) or not template:
        return None
    missing = []

    def fill(match):
        name = match.group(0)[1:-1]
        if name not in values:
            missing.append(name)
            return ""
        return quote(values[name], safe="")

    url = clients.TEMPLATE_VARIABLE.sub(fill, template)
    return None if missing or "{" in url or "}" in url else url


def _reachable(url) -> bool:
    """https, or plain http to an address the operator's egress list opens
    (``mcp_safe_fetch``: the local mocks); never userinfo, query or fragment."""
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    if (
        not parts.hostname
        or parts.username is not None
        or parts.fragment
        or parts.query
    ):
        return False
    if parts.scheme == "https":
        return True
    if parts.scheme != "http":
        return False
    host, port = parts.hostname, parts.port or 80
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    for allowed in mcp_safe_fetch.private_allow():
        if allowed.port is not None and allowed.port != port:
            continue
        if allowed.host and allowed.host == host:
            return True
        if ip is not None and (allowed.network is not None or allowed.ip is not None):
            if allowed.matches(host, ip, port):
                return True
    return False


def _authorize_params(declared: Mapping) -> tuple[tuple[str, str], ...] | None:
    params = []
    for name, value in declared.items():
        if (
            not isinstance(name, str)
            or not _PARAM_NAME.fullmatch(name)
            or name in _RESERVED_PARAMS
            or not isinstance(value, str)
            or not _PARAM_VALUE.fullmatch(value)
        ):
            return None
        params.append((name, value))
    return tuple(params)


def pilot_provider(entry) -> bool:
    """Whether a server was added from FutureAGI's own provider of a pilot app,
    whatever the runtime's state: its grants are the org registration's only."""
    if not isinstance(entry, Mapping):
        return False
    record = connector_catalog.provider_record(entry)
    return (
        isinstance(record, Mapping)
        and record.get("id") == PROVIDER_ID
        and record.get("app") in PILOT_GROUPS
    )


def held_back(entry) -> bool:
    """Only a served K2 pilot or discovered vendor MCP OAuth may sign in."""
    from agentcc.services.connector_mcp_oauth_app import is_catalog_oauth

    return (
        isinstance(entry, Mapping)
        and "catalog_ref" in entry
        and pilot(entry) is None
        and not is_catalog_oauth(entry)
    )


# --- Registration ----------------------------------------------------------------


def registration(org, server_id: str, entry) -> tuple[str, str | None]:
    """The org's own OAuth app for a pilot server, from stored state only:
    ``(registered, client_id)``, ``(unreadable, None)`` or ``(missing,
    None)``. A platform client is never read. Reads no secret into a result."""
    try:
        ref = connect_clients.resolve(org, server_id, entry, platform=False)
    except clients.OrgClientUnreadable:
        return UNREADABLE, None
    except clients.ConnectorOAuthClientError as refused:
        logger.warning("connector_k2_client_unresolved", code=refused.code)
        return MISSING, None
    if ref is None or ref.source != connect_clients.ORG:
        return MISSING, None
    return REGISTERED, ref.client_id


def issued_by_registration(org, server_id: str, entry, client_id: str) -> bool:
    """For a pilot server, whether the org's current registration is the client
    that issued a token with ``client_id``; True for any other server."""
    if pilot(entry) is None:
        return True
    state, current = registration(org, server_id, entry)
    return state == REGISTERED and bool(client_id) and current == client_id


def required_message(app_id: str, group: str) -> str:
    try:
        app = connector_catalog.load_bundle().apps.get(app_id)
    except connector_catalog.ConnectorCatalogError:
        app = None
    name = app.card["name"] if app is not None else app_id
    return (
        f"{name} signs in only through your organization's own "
        f"{VENDOR_NAMES.get(group, group)} OAuth app registration; an Owner or "
        "Admin registers it under Integrations first"
    )


# --- The sign-in -------------------------------------------------------------------


def sign_in(org, server_id: str, entry):
    """``(Discovery, Client)`` for a pilot server's connect start, or None for
    any other server. Raises ``ConnectRefused`` when the org has no readable
    registration of its own for the app's group, before anything is stored."""
    from agentcc.services import mcp_upstream_oauth as connect

    declared = pilot(entry)
    if declared is None:
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
            ORG_CLIENT_REQUIRED,
            required_message(declared.app_id, declared.client_group),
            409,
        )
    found = connect.Discovery(
        issuer=declared.issuer,
        authorization_endpoint=declared.authorize_url,
        token_endpoint=declared.token_url,
        registration_endpoint="",
        revocation_endpoint=declared.revocation_url,
        cimd_supported=False,
        iss_required=False,
        resource="",
        scopes=declared.scopes,
        authorize_params=declared.authorize_params,
    )
    return found, chosen
