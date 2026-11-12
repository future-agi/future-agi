"""The connector catalog for an org (CN9): the bundle Django reads, what an
org can add from it today, and "add from catalog".

The bundle is CN6's Django copy of ``dist/catalog.json``
(connector-catalog-bundle/v1) at ``BUNDLE_PATH``. It is parsed once per file
change, and every field this module uses is read and checked then, so a
request never meets an unchecked record. A missing or malformed bundle makes
the catalog unavailable (``CatalogUnavailable``, 503), never silently empty;
a provider record that cannot be read is refused alone. The bundle's
``version``, ``sha256`` and ``sourceCommit`` are passed on as CN6 wrote them.

An org sees, and may add, only a provider the gateway and the credential vault
run end to end today (``plan_for``). Every other provider is refused with its
reason, and an app with none is not listed unless the org already has it or
it is a held Microsoft app shown as coming soon, with no addable providers.
On this build an addable provider is:
- a vendor's remote MCP server (kind ``vendor_mcp``, over http or sse, in an
  era the gateway serves);
- in agent mode, one shared account for the org (the gateway's per-user
  credential hook, M25, is a no-op);
- with no credential, or one secret part the vault stores in a header:
  bearer for ``Authorization: Bearer``, or api_key for another header with
  no prefix; or an api_key in a query parameter of the server's URL;
- with org-scoped URL variables only.

The key goes in a header or a query parameter with no constant part because
the add lists the provider's tools with it through the probe, which places
one key, alone, in either. A key in a URL variable, and a constant part, are
planned and checked but not addable yet (``LISTING_NOT_APPLIED``). Each
secret is checked at plan time and again at add by the rule the push applies
(``mcp_credential_placement.check_catalog_secret`` with the provider's
declared parts, AM-C23 (e)), so nothing is added that the push or the gateway
would refuse. OAuth connect (M25x, CN24), a vault that stores several secret
parts (CN11 R5), partners (V10) and the REST runtime (CN10, CN23) each widen
``plan_for`` with their own tests when they land.

"Add" (CN-ADD, MCP-APPS-PLAN section 2.3) takes the org's credential and lists
the provider's live tools through the gateway's probe with it
(``connector_tool_listing``). Each listed tool gets its posture from its
annotations (``listed_tool_policy``): a read-only tool is allowed, and a write
or destructive one runs after an approval. The catalog only
tightens that. That first listing is the connector's baseline: the virtual
server composes the tools that run then, approved or not, and no other, so a
tool the provider adds later is not exposed. Its ``tools_hash`` is returned
and recorded in the config version's description; pinning a changed
definition is done by the tool revisions check.

An add with no tool that runs, approved or not, is refused, and writes
nothing. Otherwise one config version, written through the org config
writer, holds:
- the server ``mcp.servers.<app>`` with its ``catalog_ref``;
- the credential, stored in the vault and referenced from the server;
- the connector's own virtual server ``mcp.virtual_servers.<app>`` (M10),
  which carries the posture: ``tools.include`` lists the tools that run, its
  ``approval`` policy lists those among them whose every call waits for an
  Owner or Admin to approve it, and an access rule for every principal
  denies each listed tool the posture does not allow.
"""

import copy
import dataclasses
import ipaddress
import json
import re
import threading
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import structlog
from django.db.models import Q

from accounts.models import Organization, User
from agentcc import mcp_features
from agentcc.contracts.gateway_admin import MCPOrgConfig, MCPServerOrgConfig
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import (
    connector_catalog_parts,
    connector_tool_listing,
    mcp_credential_placement,
    mcp_credentials,
    mcp_revisions,
)
from agentcc.services.config_push import field_input_names
from agentcc.services.org_config_writer import (
    GATEWAY_SYNC_FAILED,
    is_gateway_synced,
    write_config_version_with,
)

logger = structlog.get_logger(__name__)

BUNDLE_PATH = Path(__file__).resolve().parents[1] / "connector_catalog" / "catalog.json"
BUNDLE_SCHEMA = "connector-catalog-bundle/v1"

PROVIDER_KINDS = ("vendor_mcp", "rest", "partner", "vendor_local")
TIERS = ("listed", "verified", "optimized")
ACCOUNT_MODES = ("agent", "user")
AGENT = "agent"
ALLOW, DENY = "allow", "deny"
SSE_ERA = "2024-11-05"

_APP_ID = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
_PROVIDER_ID = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)$")
_CATALOG_REF = re.compile(r"^([a-z][a-z0-9-]{1,39})/([a-z][a-z0-9-]{1,23})@(\S+)$")
_URL_VARIABLE = re.compile(r"[{]([A-Za-z_][A-Za-z0-9_]{0,31})[}]")
# AM-C9's rule for a url_variables value: 1-253 printable ASCII, no space.
_URL_VARIABLE_VALUE = re.compile(r"^[!-~]{1,253}$")
_HOST_VALUE = re.compile(r"^[A-Za-z0-9.-]+$")
_HOSTNAME = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?([.][a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)
# The value the vault's own header check runs with; never stored or sent.
_HEADER_CHECK_VALUE = "connector-catalog-header-check"
_HINTS = (
    ("read_only", "readOnlyHint"),
    ("destructive", "destructiveHint"),
    ("idempotent", "idempotentHint"),
    ("open_world", "openWorldHint"),
)

# Refusal codes. 503:
CATALOG_UNAVAILABLE = "connector_catalog_unavailable"
# 404:
APP_NOT_FOUND = "connector_app_not_found"
PROVIDER_NOT_FOUND = "connector_provider_not_found"
NOT_ADDED = "connector_not_added"
# 400:
MODE_NOT_DECLARED = "connector_account_mode_not_declared"
URL_VARIABLE_MISSING = "url_variable_missing"
URL_VARIABLE_UNKNOWN = "url_variable_unknown"
URL_VARIABLE_INVALID = "url_variable_invalid"
CREDENTIAL_REQUIRED = "connector_credential_required"
CREDENTIAL_NOT_TAKEN = "connector_credential_not_taken"
CREDENTIAL_INVALID = "connector_credential_invalid"
CREDENTIAL_REJECTED = connector_tool_listing.CREDENTIAL_REJECTED
# 502, no listing could be taken at add (connector_tool_listing):
LISTING_FAILED = connector_tool_listing.LISTING_FAILED
LISTING_NOT_SUPPORTED = connector_tool_listing.LISTING_NOT_SUPPORTED
GATEWAY_UNREACHABLE = connector_tool_listing.GATEWAY_UNREACHABLE
# 409, the org's config:
ALREADY_ADDED = "connector_already_added"
SERVER_ID_TAKEN = "connector_server_id_taken"
ACCESS_DEFAULT_ALLOW = "connector_mcp_access_default_allow"
MCP_DISABLED = "connector_mcp_disabled"
MCP_CONFIG_SPELLING = "connector_mcp_config_spelling"
# 409, what the provider needs that is not built yet (plan_for):
SUNSET = "connector_sunset"
KIND_NOT_SERVED = "connector_kind_not_served"
ERA_NOT_SERVED = "connector_era_not_served"
PER_USER_NOT_BUILT = "connector_per_user_not_built"
OAUTH_NOT_BUILT = "connector_oauth_not_built"
AUTH_NOT_SUPPORTED = "connector_auth_not_supported"
PARTS_NOT_APPLIED = "connector_credential_parts_not_applied"
PLACEMENT_NOT_APPLIED = "connector_credential_placement_not_applied"
PREFIX_NOT_APPLIED = "connector_credential_prefix_not_applied"
USER_URL_VARIABLE_NOT_BUILT = "connector_user_url_variable_not_built"
LISTING_NOT_APPLIED = "connector_tool_listing_not_applied"
# At plan time when the catalog denies every tool it does not declare and
# none it declares runs, approved or not; at add when no listed tool runs.
NO_TOOL_ALLOWED = "connector_no_tool_allowed"
DATA_INVALID = "connector_catalog_data_invalid"
# An explicitly disabled Microsoft declaration (plan_for).
OAUTH_NOT_ENABLED = "connector_oauth_not_enabled"
# What a gateway has not built yet: an app whose every provider is refused
# for one of these (or hidden by a sign-in flag) is a Coming-soon card.
# SUNSET, DATA_INVALID and the Arcade catalogue refusal never are.
_NOT_BUILT = frozenset(
    {
        OAUTH_NOT_BUILT,
        KIND_NOT_SERVED,
        LISTING_NOT_APPLIED,
        PARTS_NOT_APPLIED,
        PLACEMENT_NOT_APPLIED,
        PREFIX_NOT_APPLIED,
        PER_USER_NOT_BUILT,
        USER_URL_VARIABLE_NOT_BUILT,
        AUTH_NOT_SUPPORTED,
        ERA_NOT_SERVED,
        OAUTH_NOT_ENABLED,
    }
)

# Why the gateway denies a declared tool (tool_policy) or a listed one
# (listed_tool_policy).
POSTURE_DENY = "posture_deny"
GENERIC_TOOL = "generic_tool"
INVALID_TOOL_NAME = "invalid_tool_name"
# The reason of an allowed tool that runs only after an approval: a
# write or destructive tool, or one the catalog asks about.
APPROVAL_REQUIRED = "approval_required"

# User-facing reasons name no lane; the lane that lifts each is in a comment.
_KIND_REASONS = {
    # CN10 and CN23.
    "rest": "is served by FutureAGI's connector runtime, which this gateway "
    "does not run yet",
    # V10.
    "partner": "needs the partner platform's per-person identity, which the "
    "gateway does not support yet",
    "vendor_local": "is a stdio package, which runs only on the customer's own "
    "machine or from the operator's config",
}
# M25 and M25x.
_PER_USER_MESSAGE = (
    "each person's own account needs per-user credentials, which the gateway "
    "does not resolve yet; only agent mode (one shared account) works"
)
_PREFIX_MESSAGE = (
    "the gateway sends a stored key as Authorization: Bearer <key>, or in "
    "another header with no prefix; this provider needs another form"
)
# The probe, which lists the tools at add, places one key, alone, in a header
# or in a query parameter of the URL (mcp/probe.go), as the pool places it.
_LISTING_MESSAGE = (
    "the gateway lists a provider's tools with the organization's key when "
    "it is added, and it can send that key only in a header or a query "
    "parameter, alone; this provider {needs}"
)
_LISTING_NEEDS = {
    mcp_credentials.URL_VARIABLE: "takes its key in the URL",
}
_LISTED_PLACEMENTS = (mcp_credentials.HEADER, mcp_credentials.QUERY)


def _literal_values(annotation) -> frozenset[str]:
    values: set[str] = set()
    for arg in typing.get_args(annotation):
        if typing.get_origin(arg) is typing.Literal:
            values.update(typing.get_args(arg))
    return frozenset(values)


# The eras an org server may name: the contract's, which the gateway checks
# against the revisions it serves.
SERVED_ERAS = _literal_values(MCPServerOrgConfig.model_fields["era"].annotation)


class ConnectorCatalogError(Exception):
    """A refusal with a stable ``code``. The message names fields and
    reasons, never a value a caller sent."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class CatalogUnavailable(ConnectorCatalogError):
    pass


class ConnectorNotFound(ConnectorCatalogError):
    pass


class ConnectorInvalid(ConnectorCatalogError):
    pass


class ConnectorConflict(ConnectorCatalogError):
    pass


class ConnectorListingFailed(ConnectorCatalogError):
    """No live listing could be taken at add (502)."""


# --- What an org can add ------------------------------------------------------


@dataclass(frozen=True)
class Unavailable:
    code: str
    message: str


@dataclass(frozen=True)
class ToolPolicy:
    """A tool's posture at the gateway. An allowed tool whose ``reason`` is
    APPROVAL_REQUIRED runs only after an Owner or Admin approves the call
    and the API shows it so, allowed and not denied."""

    name: str
    gateway_posture: str  # ALLOW or DENY
    reason: str | None

    @property
    def needs_approval(self) -> bool:
        return self.gateway_posture == ALLOW and self.reason == APPROVAL_REQUIRED


@dataclass(frozen=True)
class CredentialPlan:
    """The server's ``credential.mode``, and what the admin stores in the
    vault for it: ``kind``, ``header_name``, ``in`` and ``name`` as the vault
    takes them (no kind when the provider needs no credential). ``placement``
    is ``in``: header, query or url_variable (empty with no credential);
    ``name`` is the header (as the vault's describe() names it, Authorization
    for a bearer), the query parameter or the URL variable. ``constants`` are
    the catalog's fixed parts, ``(in, name, prefix + value)``, which the
    gateway adds and CN9 never stores."""

    mode: str
    kind: str | None = None
    header_name: str = ""
    placement: str = ""
    name: str = ""
    constants: tuple[tuple[str, str, str], ...] = ()
    label: str = ""
    help: str = ""
    help_url: str = ""

    def card(self) -> dict:
        return {
            "mode": self.mode,
            "kind": self.kind,
            "header_name": self.header_name,
            "in": self.placement,
            "name": self.name,
            "label": self.label,
            "help": self.help,
            "help_url": self.help_url,
        }

    @property
    def url_variable(self) -> str | None:
        """The secret URL variable the gateway fills, which the URL keeps as
        ``{name}``; None when the key goes elsewhere."""
        return self.name if self.placement == mcp_credentials.URL_VARIABLE else None

    def secret(self, value: str) -> mcp_credentials.Secret:
        """The vault secret this credential stores, holding ``value``.
        Raises ``MCPCredentialError`` for a value the vault refuses."""
        if self.placement == mcp_credentials.HEADER:
            return mcp_credentials.new_secret(
                self.kind, value, self.header_name or None
            )
        return mcp_credentials.new_secret(
            self.kind, value, placement=self.placement, name=self.name
        )

    def placement_problem(
        self, url: str, catalog_ref: str, parts
    ) -> tuple[str, str] | None:
        """``(field, reason)`` when the push would refuse this credential's
        secret for the server ``url`` added as ``catalog_ref``, by the rule it
        applies (``check_catalog_secret`` with the provider's declared parts
        ``parts``, AM-C23 (e)): the gateway adds the constant parts from the
        catalog and places them with the key. None when it can, or when
        there is no credential. The value checked is a placeholder, never a
        key."""
        if self.mode != "shared" or self.kind == "oauth":
            return None
        secret = self.secret(_HEADER_CHECK_VALUE).push_form()
        return mcp_credential_placement.check_catalog_secret(
            url, secret, "", catalog_ref, parts
        )

    def listing_problem(self) -> str | None:
        """Why the probe cannot list the provider's tools with this
        credential, or None: it places one key, alone, in a header or a
        query parameter, so a key in a URL variable, or a constant part,
        cannot ride."""
        if self.mode != "shared" or self.kind == "oauth":
            return None
        if self.placement not in _LISTED_PLACEMENTS:
            return _LISTING_MESSAGE.format(needs=_LISTING_NEEDS[self.placement])
        if self.constants:
            where, name, _ = self.constants[0]
            return _LISTING_MESSAGE.format(
                needs=f"also needs its fixed {name} {where} part"
            )
        return None


@dataclass(frozen=True)
class URLVariable:
    name: str
    description: str
    pattern: str
    default: str | None
    regex: re.Pattern


@dataclass(frozen=True)
class Plan:
    """What adding one provider writes, and the provider as the API shows
    it (``card``)."""

    catalog_ref: str
    kind: str
    tier: str
    declared_modes: tuple[str, ...]
    modes: tuple[str, ...]
    transport: str
    era: str
    url_template: str
    url_variables: dict[str, URLVariable]
    credential: CredentialPlan
    tools: tuple[ToolPolicy, ...]  # the catalog's declared tools
    default_posture: str  # the catalog's posture for a tool it does not declare
    timeout_ms: int | None
    forward_headers: tuple[str, ...]
    # The provider's declared credential parts, as the push applies them.
    parts: connector_catalog_parts.CatalogParts
    card: dict


def _microsoft_catalog_provider(app_id: str, provider: Mapping) -> bool:
    """The owner-held browse group, including aliases with Microsoft OAuth."""
    auth = provider.get("auth")
    oauth = auth.get("oauth2") if isinstance(auth, Mapping) else None
    return app_id.startswith("microsoft-") or (
        isinstance(oauth, Mapping) and oauth.get("clientGroup") == "microsoft"
    )


def oauth2_by_discovery(record: Mapping) -> bool:
    """Whether a provider record that declares ``oauth2`` signs in as
    discovered vendor MCP OAuth, the same as ``mcp_oauth``: a vendor MCP server
    whose authorization server takes dynamic client registration or a client
    metadata document (``registration`` names ``dcr`` or ``cimd``), so it needs
    no app of ours. The K2 pilots and groups and the Microsoft group keep their
    declared sign-in; a record whose server takes only pre-registered clients
    signs in with the organization's own OAuth app (``oauth2_by_org_app``),
    and any other ``oauth2`` record stays OAUTH_NOT_BUILT.
    Never raises: callers read records outside ``plan_for``."""
    if not isinstance(record, Mapping) or record.get("kind") != "vendor_mcp":
        return False
    auth = record.get("auth")
    if not isinstance(auth, Mapping) or auth.get("kind") != "oauth2":
        return False
    spec = auth.get("oauth2")
    app_id = record.get("app")
    if not isinstance(spec, Mapping) or not isinstance(app_id, str):
        return False
    registration = spec.get("registration")
    group = spec.get("clientGroup")
    if not isinstance(registration, list) or not isinstance(group, str | None):
        return False
    methods = {method for method in registration if isinstance(method, str)}
    return (
        bool(methods & {"dcr", "cimd"})
        and app_id not in mcp_features.K2_IDS
        and group not in mcp_features.K2_GROUPS
        and not _microsoft_catalog_provider(app_id, record)
    )


def oauth2_by_org_app(record: Mapping) -> bool:
    """Whether a provider record that declares ``oauth2`` signs each person in
    with the organization's own OAuth app (``connector_org_app_oauth``): a
    vendor MCP server over http whose authorization server takes only
    pre-registered clients (``registration`` names ``preregistered`` and
    neither ``dcr`` nor ``cimd``), with the per-person account mode, a form
    token request by HTTP Basic or in the form, and, where it declares one, a
    form POST revocation of ``token`` with the same client authentication.
    The pilot connectors and groups and the Microsoft group keep their declared
    sign-in. Never raises: callers read records outside ``plan_for``."""
    if not isinstance(record, Mapping) or record.get("kind") != "vendor_mcp":
        return False
    transport, auth = record.get("transport"), record.get("auth")
    if not isinstance(transport, Mapping) or transport.get("kind") != "http":
        return False
    if not isinstance(auth, Mapping) or auth.get("kind") != "oauth2":
        return False
    spec, app_id = auth.get("oauth2"), record.get("app")
    modes = record.get("accountModes")
    if not isinstance(spec, Mapping) or not isinstance(app_id, str):
        return False
    if not isinstance(modes, list):
        return False
    registration, group = spec.get("registration"), spec.get("clientGroup")
    if not isinstance(registration, list) or not isinstance(group, str | None):
        return False
    methods = {method for method in registration if isinstance(method, str)}
    revocation = spec.get("revocation")
    revokes = revocation is None or (
        isinstance(revocation, Mapping)
        and revocation.get("method") == "POST"
        and revocation.get("tokenBody") == "form"
        and revocation.get("tokenField", "token") == "token"
        and revocation.get("clientAuth") == spec.get("clientAuth")
    )
    return (
        "preregistered" in methods
        and not methods & {"dcr", "cimd"}
        and "user" in modes
        and isinstance(spec.get("authorizeUrl"), str)
        and isinstance(spec.get("tokenUrl"), str)
        and spec.get("tokenBody") == "form"
        and spec.get("clientAuth") in ("basic", "body")
        and revokes
        and app_id not in mcp_features.K2_IDS
        and group not in mcp_features.K2_GROUPS
        and not _microsoft_catalog_provider(app_id, record)
    )


def plan_for(app: Mapping, provider: Mapping) -> Plan | Unavailable:
    """The plan to add ``provider`` of ``app``, or why it cannot be added
    now. A record this module cannot read is refused for that provider alone
    (fail closed), never added in part."""
    try:
        # Explicitly disabled Microsoft declarations must not fall through to
        # a servable MCP/OAuth plan. Other groups keep their existing planning.
        if _microsoft_catalog_provider(app["id"], provider):
            enablement = provider.get("x-gateway", {}).get("connectionEnablement")
            if isinstance(enablement, Mapping) and enablement.get("enabled") is False:
                return Unavailable(OAUTH_NOT_ENABLED, "Coming soon")
        from agentcc.services import connector_catalog_oauth

        oauth_plan = connector_catalog_oauth.plan_for(app, provider)
        return oauth_plan if oauth_plan is not None else _plan(app, provider)
    except (AttributeError, KeyError, TypeError, ValueError, re.error) as exc:
        logger.warning(
            "connector_catalog_record_unreadable",
            app=app.get("id"),
            provider=provider.get("id"),
            error_type=type(exc).__name__,
        )
        return Unavailable(DATA_INVALID, "the catalog record cannot be read")


def _plan(app: Mapping, provider: Mapping) -> Plan | Unavailable:
    if "sunset" in (app["lifecycle"]["status"], provider["lifecycle"]["status"]):
        return Unavailable(SUNSET, "the vendor has retired this connector")
    kind = provider["kind"]
    if kind != "vendor_mcp":
        reason = _KIND_REASONS.get(kind, "is not a kind the gateway serves")
        return Unavailable(KIND_NOT_SERVED, f"a {kind} provider {reason}")
    transport = provider["transport"]
    if transport["kind"] != "http":
        return Unavailable(DATA_INVALID, "a vendor MCP server is reached over http")
    era = transport.get("era", "auto")
    if era not in SERVED_ERAS:
        return Unavailable(ERA_NOT_SERVED, f"the gateway does not serve era {era}")
    # The gateway speaks HTTP+SSE only as the 2024-11-05 era, and that era
    # only over HTTP+SSE (mcp.ClientConfig: "sse" is always 2024-11-05), so
    # the era written is the one that runs.
    variant = transport.get("variant", "streamable")
    if variant == "sse" and era in ("auto", SSE_ERA):
        transport_kind, era = "sse", SSE_ERA
    elif variant == "streamable" and era != SSE_ERA:
        transport_kind = "http"
    else:
        return Unavailable(
            DATA_INVALID, f"a {variant} server cannot speak the {era} era"
        )
    org_app = oauth2_by_org_app(provider)
    credential = _credential_plan(
        provider["auth"], discovered=oauth2_by_discovery(provider) or org_app
    )
    if isinstance(credential, Unavailable):
        return credential
    declared_modes = tuple(_text(mode) for mode in provider["accountModes"])
    if AGENT not in declared_modes and credential.kind != "oauth":
        return Unavailable(PER_USER_NOT_BUILT, _PER_USER_MESSAGE)
    url_template = _text(transport["url"])
    variables = _url_variables(
        url_template, transport.get("urlVariables", {}), credential.url_variable
    )
    if isinstance(variables, Unavailable):
        return variables
    version = _text(provider["version"])
    catalog_ref = f"{app['id']}/{provider['id']}@{version}"
    # The provider's own declared parts: the records the push places its
    # key by (the bundle refuses a repeated provider before this).
    parts = connector_catalog_parts.from_providers([provider])
    # The org's own variables filled with a placeholder, the secret one kept:
    # what the push's rule sees once the org's values are in.
    problem = credential.placement_problem(
        _URL_VARIABLE.sub(
            lambda m: m.group(0) if m.group(1) == credential.url_variable else "a",
            url_template,
        ),
        catalog_ref,
        parts,
    )
    if problem is not None:
        return _placement_unavailable(problem)
    listing = credential.listing_problem()
    if listing is not None:
        return Unavailable(LISTING_NOT_APPLIED, listing)
    section = provider["tools"]
    items = section.get("items", [])
    default_posture = _text(section["defaultPosture"])
    tools = tuple(tool_policy(tool, default_posture) for tool in items)
    # Key-based add needs a usable tool listing. OAuth registration comes before
    # an account can list tools, so it must not depend on a declared allowlist.
    # This only admits configuration; it does not grant tool access. A tool
    # that runs after an approval is allowed (ToolPolicy.needs_approval).
    if (
        credential.kind != "oauth"
        and default_posture == "deny"
        and not any(tool.gateway_posture == ALLOW for tool in tools)
    ):
        return Unavailable(
            NO_TOOL_ALLOWED,
            "the catalog denies each of its tools it does not declare, and none "
            "it declares can run at the gateway, even after an approval (each "
            "is one the catalog denies or a generic tool), so adding it would "
            "expose no tool",
        )
    settings = provider.get("x-gateway", {})
    timeout_ms = settings.get("timeoutMs")
    if timeout_ms is not None and type(timeout_ms) is not int:
        raise TypeError("x-gateway.timeoutMs")
    forward_headers = tuple(_text(h) for h in settings.get("forwardHeaders", []))
    tier = _text(provider["quality"]["tier"])
    modes = declared_modes if credential.kind == "oauth" else (AGENT,)
    if org_app:
        # Each person's own account only: the organization's one account would
        # sign in through discovery, which such a server does not offer.
        modes = ("user",)
    card = {
        "id": provider["id"],
        "version": version,
        "catalog_ref": catalog_ref,
        "kind": kind,
        "publisher": _text(provider["publisher"]),
        "vendor_status": _text(provider["vendorStatus"]["label"]),
        "tier": tier,
        "lifecycle": _text(provider["lifecycle"]["status"]),
        "account_modes": list(modes),
        "transport": transport_kind,
        "era": era,
        "url_template": url_template,
        "url_variables": [
            {
                "name": v.name,
                "description": v.description,
                "pattern": v.pattern,
                "default": v.default,
            }
            for v in variables.values()
        ],
        "credential": credential.card(),
        "tools": [
            _tool_card(tool, policy) for tool, policy in zip(items, tools, strict=True)
        ],
    }
    if credential.kind == "oauth":
        card["connect"] = {"served": True, "configure_oauth": True}
    return Plan(
        catalog_ref=catalog_ref,
        kind=kind,
        tier=tier,
        declared_modes=declared_modes,
        modes=modes,
        transport=transport_kind,
        era=era,
        url_template=url_template,
        url_variables=variables,
        credential=credential,
        tools=tools,
        default_posture=default_posture,
        timeout_ms=timeout_ms,
        forward_headers=forward_headers,
        parts=parts,
        card=card,
    )


def _credential_plan(
    auth: Mapping, *, discovered: bool = False
) -> CredentialPlan | Unavailable:
    """``discovered``: the record is an ``oauth2`` one that signs each person
    in, as discovered vendor MCP OAuth (``oauth2_by_discovery``) or with the
    organization's own OAuth app (``oauth2_by_org_app``)."""
    kind = auth["kind"]
    if kind == "none":
        return CredentialPlan(mode="none")
    if kind == "mcp_oauth" or (kind == "oauth2" and discovered):
        return CredentialPlan(mode="per_user", kind="oauth", label="OAuth account")
    if kind == "oauth2":
        # Other declared OAuth (a pilot connector, Microsoft, or a shape
        # neither sign-in serves) remains separate from vendor MCP OAuth.
        return Unavailable(
            OAUTH_NOT_BUILT,
            "connecting over OAuth is not available yet; only an API key or no "
            "credential can be connected",
        )
    if kind != "api_key":
        return Unavailable(
            AUTH_NOT_SUPPORTED, f"the gateway cannot send a {kind} credential yet"
        )
    parts = auth["credentialParts"]
    secrets = [part for part in parts if part["secret"] is True]
    # The vault stores one secret per credential; one that stores several
    # (MCPCredentialSecret.parts, CN11 R5) lifts this rule.
    if len(secrets) != 1:
        return Unavailable(
            PARTS_NOT_APPLIED,
            "the credential has more than one secret part, and a stored "
            "credential holds one secret",
        )
    constants = []
    for part in parts:
        if part["secret"] is True:
            continue
        where, name = _text(part["in"]), _text(part["name"])
        value, prefix = part.get("value"), part.get("prefix", "")
        if not isinstance(value, str) or value == "":
            # Nothing carries a value the customer sets for a part that is
            # not secret (AM-C20 item 1), so the gateway refuses it too.
            return Unavailable(
                PARTS_NOT_APPLIED,
                f"the credential's {name} part is set by each customer, and "
                "the gateway adds only a fixed value from the catalog",
            )
        if prefix and where != mcp_credentials.HEADER:
            return Unavailable(PREFIX_NOT_APPLIED, _PREFIX_MESSAGE)
        constants.append((where, name, _text(prefix) + value))
    part = secrets[0]
    where, name, prefix = _text(part["in"]), _text(part["name"]), part.get("prefix", "")
    placement, header = where, ""
    if where != mcp_credentials.HEADER:
        # The vault puts an api_key in a query parameter or a URL variable
        # (CN11) and the gateway places it there (CN7); never with a prefix.
        if prefix:
            return Unavailable(PREFIX_NOT_APPLIED, _PREFIX_MESSAGE)
        vault_kind = mcp_credentials.Kind.API_KEY
    elif name.lower() == "authorization":
        # The gateway sends a vault bearer as "Authorization: Bearer <value>".
        if prefix != "Bearer ":
            return Unavailable(PREFIX_NOT_APPLIED, _PREFIX_MESSAGE)
        vault_kind = mcp_credentials.Kind.BEARER
    elif prefix:
        return Unavailable(PREFIX_NOT_APPLIED, _PREFIX_MESSAGE)
    else:
        vault_kind, header = mcp_credentials.Kind.API_KEY, name
    try:
        if placement == mcp_credentials.HEADER:
            mcp_credentials.new_secret(vault_kind, _HEADER_CHECK_VALUE, header or None)
        else:
            mcp_credentials.new_secret(
                vault_kind, _HEADER_CHECK_VALUE, placement=placement, name=name
            )
    except mcp_credentials.MCPCredentialError:
        if placement == mcp_credentials.HEADER:
            return Unavailable(
                AUTH_NOT_SUPPORTED,
                f"the gateway sets the {name} header itself, so a key cannot go there",
            )
        return Unavailable(
            PLACEMENT_NOT_APPLIED,
            f"the vault cannot store a key for the {placement} {name}",
        )
    return CredentialPlan(
        mode="shared",
        kind=str(vault_kind),
        header_name=header,
        placement=placement,
        name=(header or "Authorization")
        if placement == mcp_credentials.HEADER
        else name,
        constants=tuple(constants),
        label=_text(part.get("label", "")),
        help=_text(part.get("help", "")),
        help_url=_text(part.get("helpUrl", "")),
    )


def _placement_unavailable(problem: tuple[str, str]) -> Unavailable:
    field, reason = problem
    where = {"url": "the URL", "catalog_ref": "the catalog record"}.get(
        field, f"the credential's {field}"
    )
    return Unavailable(
        PLACEMENT_NOT_APPLIED,
        f"the gateway cannot place the key there: {where} {reason}",
    )


def _url_variables(
    url: str, declared: Mapping, secret: str | None = None
) -> dict[str, URLVariable] | Unavailable:
    """The org's URL variables. ``secret`` is the credential's URL variable,
    which the URL holds and the gateway fills (never an org value)."""
    if any(
        name not in declared and name != secret for name in _URL_VARIABLE.findall(url)
    ):
        return Unavailable(DATA_INVALID, "the URL names a variable it does not declare")
    variables = {}
    for name, spec in declared.items():
        if spec["scope"] != "org":
            return Unavailable(
                USER_URL_VARIABLE_NOT_BUILT,
                f"the URL variable {name} is set by each person, which needs "
                "per-user connections",
            )
        default = spec.get("default")
        regex = re.compile(_text(spec["pattern"]))
        if default is not None and not regex.fullmatch(_text(default)):
            return Unavailable(
                DATA_INVALID, f"the default of {name} breaks its pattern"
            )
        variables[name] = URLVariable(
            name, _text(spec["description"]), spec["pattern"], default, regex
        )
    return variables


def tool_policy(tool: Mapping, default_posture: str) -> ToolPolicy:
    """Whether the gateway lets a declared tool through (CONNECTORS-PLAN
    section 2.8). A read-only, non-destructive tool whose posture is
    ``auto`` is allowed. ``deny`` is denied. A generic tool is denied too,
    write or not: policy sees only its verb, never the operation in its
    arguments, so an approval could not say what it lets run. Any other
    write, destructive or ``ask`` tool runs after an approval."""
    posture = tool.get("posture", default_posture)
    hints = tool["annotations"]
    name = _text(tool["name"])
    if posture == "deny":
        return ToolPolicy(name, DENY, POSTURE_DENY)
    if tool.get("generic") is True:
        return ToolPolicy(name, DENY, GENERIC_TOOL)
    if (
        hints["destructiveHint"] is not False
        or hints["readOnlyHint"] is not True
        or posture != "auto"
    ):
        return ToolPolicy(name, ALLOW, APPROVAL_REQUIRED)
    return ToolPolicy(name, ALLOW, None)


def listed_tool_policy(
    tool: connector_tool_listing.ListedTool,
    declared: ToolPolicy | None,
    default_posture: str,
) -> ToolPolicy:
    """Whether the gateway lets a tool the provider listed at add through
    (CN-ADD, MCP-APPS-PLAN section 2.3). Its annotations decide: a read-only
    tool is allowed, and a write or destructive one runs after an approval
    (a missing hint is read by MCP's defaults).

    The catalog can only tighten that. A tool it declares keeps the declared
    posture when that denies (``tool_policy``: deny or generic) or needs an
    approval (a declared write, destructive or ask tool, even one listed as
    read-only), and a section whose ``defaultPosture`` is deny denies every
    tool it does not declare. The section's ``ask`` is EE's ask-the-user
    posture; under the ruling the annotations decide such an undeclared
    tool, so a read-only one is allowed with no approval, as before
    approvals. A name that breaks MCP's tool name rule is denied, and never
    written to the virtual server, where a "*" would be a glob."""
    if not tool.valid_name:
        return ToolPolicy(tool.name, DENY, INVALID_TOOL_NAME)
    if declared is not None and declared.gateway_posture == DENY:
        return ToolPolicy(tool.name, DENY, declared.reason)
    if declared is None and default_posture == "deny":
        return ToolPolicy(tool.name, DENY, POSTURE_DENY)
    if (
        tool.destructive
        or not tool.read_only
        or (declared is not None and declared.needs_approval)
    ):
        return ToolPolicy(tool.name, ALLOW, APPROVAL_REQUIRED)
    return ToolPolicy(tool.name, ALLOW, None)


def _listed_tool_card(
    tool: connector_tool_listing.ListedTool, policy: ToolPolicy
) -> dict:
    return {
        "name": tool.name,
        "title": tool.title,
        "description": tool.description,
        "read_only": tool.read_only,
        "destructive": tool.destructive,
        "idempotent": tool.idempotent,
        "open_world": tool.open_world,
        "gateway_posture": policy.gateway_posture,
        "reason": policy.reason,
    }


def _tool_card(tool: Mapping, policy: ToolPolicy) -> dict:
    hints = tool["annotations"]
    card = {
        "name": policy.name,
        "title": _text(tool.get("title", "")),
        "description": _text(tool.get("description", "")),
    }
    for key, hint in _HINTS:
        if type(hints[hint]) is not bool:
            raise TypeError(hint)
        card[key] = hints[hint]
    card.update(gateway_posture=policy.gateway_posture, reason=policy.reason)
    return card


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("expected a string")
    return value


# --- The bundle -------------------------------------------------------------


@dataclass(frozen=True)
class AppEntry:
    card: dict  # the app as the API shows it, without the org's state
    default_provider: str
    search_text: str
    plans: dict[str, Plan | Unavailable]  # by provider id, in bundle order


@dataclass(frozen=True)
class Bundle:
    version: str
    sha256: str
    source_commit: str
    apps: dict[str, AppEntry]  # by app id, in bundle order
    # Each provider's manifest record, by (app id, provider id): what the
    # connect flow reads of a provider's declared OAuth spec.
    providers: dict[tuple[str, str], dict] = dataclasses.field(default_factory=dict)


_cache_lock = threading.Lock()
_cache: tuple[tuple, Bundle] | None = None


def load_bundle() -> Bundle:
    """The bundle at ``BUNDLE_PATH``, parsed once per file change."""
    global _cache
    path = Path(BUNDLE_PATH)
    try:
        stat = path.stat()
        from agentcc.services.connector_k2 import runtime_url

        key = (str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino, runtime_url())
        with _cache_lock:
            if _cache is not None and _cache[0] == key:
                return _cache[1]
        raw = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        logger.warning(
            "connector_catalog_unavailable",
            path=str(path),
            error_type=type(exc).__name__,
        )
        raise _unavailable() from exc
    bundle = _parse_bundle(raw)
    with _cache_lock:
        _cache = (key, bundle)
    return bundle


def _parse_bundle(raw: Any) -> Bundle:
    if not isinstance(raw, dict) or raw.get("schema") != BUNDLE_SCHEMA:
        raise _malformed(f"it is not a {BUNDLE_SCHEMA} document")
    meta = [raw.get(key) for key in ("version", "sha256", "sourceCommit")]
    if not all(isinstance(value, str) and value for value in meta):
        raise _malformed("version, sha256 or sourceCommit is missing")
    apps: dict[str, dict] = {}
    for record in _records(raw, "apps"):
        app_id = record.get("id")
        if not _is_id(app_id, _APP_ID) or app_id in apps:
            raise _malformed("an app id is missing, malformed or repeated")
        apps[app_id] = record
    plans: dict[str, dict[str, Plan | Unavailable]] = {app_id: {} for app_id in apps}
    providers: dict[tuple[str, str], dict] = {}
    for record in _records(raw, "providers"):
        app_id, provider_id = record.get("app"), record.get("id")
        if app_id not in apps or not _is_id(provider_id, _PROVIDER_ID):
            raise _malformed("a provider names no app, or its id is malformed")
        if provider_id in plans[app_id]:
            raise _malformed(f"provider {app_id}/{provider_id} is repeated")
        if not _is_id(record.get("version"), _SEMVER):
            raise _malformed(f"provider {app_id}/{provider_id} has no semver version")
        plans[app_id][provider_id] = plan_for(apps[app_id], record)
        providers[(app_id, provider_id)] = record
    _records(raw, "platforms")
    entries = {}
    for app_id, record in apps.items():
        try:
            entries[app_id] = _app_entry(record, plans[app_id])
        except (AttributeError, KeyError, TypeError) as exc:
            raise _malformed(f"app {app_id} cannot be read") from exc
    return Bundle(*meta, apps=entries, providers=providers)


def _app_entry(app: Mapping, plans: dict[str, Plan | Unavailable]) -> AppEntry:
    vendor = app["vendor"]
    default = _text(app["defaultProvider"])
    icon = app.get("icon")
    card = {
        "id": app["id"],
        "name": _text(app["name"]),
        "description": _text(app["description"]),
        "vendor": {
            "name": _text(vendor["name"]),
            "domains": [_text(domain) for domain in vendor["domains"]],
        },
        "ee_category": _text(app["eeCategory"]),
        "group": _text(app["group"]),
        "categories": [_text(category) for category in app["categories"]],
        "tags": [_text(tag) for tag in app.get("tags", [])],
        "docs_url": _text(app["docsUrl"]),
        "icon": None
        if icon is None
        else {
            "light": _text(icon["light"]),
            "dark": _text(icon.get("dark", icon["light"])),
        },
        "lifecycle": _text(app["lifecycle"]["status"]),
        # The default provider only when the org can add it.
        "default_provider": default if isinstance(plans.get(default), Plan) else None,
    }
    words = [card["id"], card["name"], card["description"], card["vendor"]["name"]]
    return AppEntry(
        card=card,
        default_provider=default,
        search_text=" ".join(words + card["tags"]).casefold(),
        plans=plans,
    )


def _records(raw: dict, key: str) -> list[dict]:
    records = raw.get(key)
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise _malformed(f"{key} is not a list of records")
    return records


def _is_id(value: Any, pattern: re.Pattern) -> bool:
    return isinstance(value, str) and bool(pattern.fullmatch(value))


def _malformed(reason: str) -> CatalogUnavailable:
    logger.warning("connector_catalog_malformed", reason=reason)
    return _unavailable()


def _unavailable() -> CatalogUnavailable:
    return CatalogUnavailable(
        CATALOG_UNAVAILABLE, "The connector catalog is not available."
    )


# --- URL variables -------------------------------------------------------------


def render_url(
    template: str,
    variables: Mapping[str, URLVariable],
    supplied: Mapping[str, Any],
    secret: str | None = None,
) -> tuple[str, dict[str, str]]:
    """The provider URL with the org's values, and those values (a default
    fills a variable the org leaves out). Each value meets AM-C9's rule and
    the variable's pattern. In the host a value goes in as is and must leave a
    DNS host name that parses back unchanged; in the path or query it is
    percent-encoded (RFC 6570 simple expansion), so it can never add a path
    segment, a query parameter or a fragment. ``secret``, the credential's
    URL variable, stays ``{secret}`` for the gateway to fill (CN7); the org
    can never set it. Raises ``ConnectorInvalid``."""
    unknown = sorted(set(supplied) - set(variables))
    if unknown:
        raise ConnectorInvalid(
            URL_VARIABLE_UNKNOWN,
            f"url_variables: {', '.join(unknown)} is not a variable of this provider",
        )
    values = {}
    for name, variable in variables.items():
        value = supplied.get(name, variable.default)
        if value is None:
            raise ConnectorInvalid(
                URL_VARIABLE_MISSING,
                f"url_variables.{name} is required: {variable.description}",
            )
        if not (
            isinstance(value, str)
            and _URL_VARIABLE_VALUE.fullmatch(value)
            and variable.regex.fullmatch(value)
        ):
            raise _invalid_variable(name, variable.pattern)
        values[name] = value
    scheme, _, rest = template.partition("://")
    cut = min(
        (i for i in (rest.find("/"), rest.find("?")) if i >= 0), default=len(rest)
    )
    authority, tail = rest[:cut], rest[cut:]

    def host_value(match: re.Match) -> str:
        name = match.group(1)
        if not _HOST_VALUE.fullmatch(values[name]):
            raise _invalid_variable(name, variables[name].pattern)
        return values[name]

    rendered_authority = _URL_VARIABLE.sub(host_value, authority)
    tail = _URL_VARIABLE.sub(
        lambda m: (
            m.group(0)
            if m.group(1) == secret
            else quote(values[m.group(1)], safe="-._~")
        ),
        tail,
    )
    url = f"{scheme}://{rendered_authority}{tail}"
    if not _is_dns_host(url, rendered_authority):
        named = _URL_VARIABLE.findall(authority) or ["the host"]
        raise ConnectorInvalid(
            URL_VARIABLE_INVALID,
            f"url_variables.{named[0]} must leave the URL a DNS host name",
        )
    return url, values


def _invalid_variable(name: str, pattern: str) -> ConnectorInvalid:
    return ConnectorInvalid(
        URL_VARIABLE_INVALID,
        f"url_variables.{name} must be 1-253 printable ASCII characters with no "
        f"space and match {pattern}",
    )


def _is_dns_host(url: str, authority: str) -> bool:
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - a bad port raises ValueError here
    except ValueError:
        return False
    host = parts.hostname or ""
    if host != authority.rsplit(":", 1)[0].lower() or not _HOSTNAME.fullmatch(host):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        # WHATWG reads a host whose last label is numeric as an IPv4 address.
        return not host.rsplit(".", 1)[-1].isdigit()


# --- Reads -------------------------------------------------------------------


def catalog_for_org(
    org: Organization,
    user: User,
    *,
    category: str | None = None,
    q: str | None = None,
    kind: str | None = None,
    tier: str | None = None,
    connected: bool | None = None,
) -> tuple[Bundle, list[dict]]:
    """The apps the org can add or has added, filtered, sorted by name."""
    bundle = load_bundle()
    servers = _org_connectors(org, user)
    needle = q.casefold() if q else None
    apps = []
    for app_id, entry in bundle.apps.items():
        card = entry.card
        if category and category not in (card["ee_category"], *card["categories"]):
            continue
        if needle and needle not in entry.search_text:
            continue
        record = _app_record(
            entry, servers.get(app_id, []), kind=kind, tier=tier, org=org
        )
        if record is None:
            continue
        if connected is not None and record["connected"] != connected:
            continue
        apps.append(record)
    apps.sort(key=lambda record: (record["name"].casefold(), record["id"]))
    return bundle, apps


def app_for_org(org: Organization, user: User, app_id: str) -> dict:
    entry = load_bundle().apps.get(app_id)
    record = None
    if entry is not None:
        record = _app_record(entry, _org_connectors(org, user).get(app_id, []), org=org)
    if record is None:
        raise ConnectorNotFound(APP_NOT_FOUND, "No such app in the connector catalog.")
    return record


def _mcp_oauth(record: Mapping) -> bool:
    """Whether a provider record is vendor MCP OAuth, configured through its
    OAuth app, rather than a pilot connector's declared OAuth sign-in: auth kind
    ``mcp_oauth``, or an ``oauth2`` record ``oauth2_by_discovery`` or
    ``oauth2_by_org_app`` admits. Each plans as a per-person "oauth"
    credential."""
    auth = record.get("auth") if isinstance(record, Mapping) else None
    return isinstance(auth, Mapping) and (
        auth.get("kind") == "mcp_oauth"
        or oauth2_by_discovery(record)
        or oauth2_by_org_app(record)
    )


def _app_record(
    entry: AppEntry,
    servers: list[dict],
    *,
    kind: str | None = None,
    tier: str | None = None,
    org: Organization | None = None,
) -> dict | None:
    """An app as the API shows it, or None when it is not listed: it has no
    provider the org can add now that matches ``kind`` and ``tier``, and, with
    no such filter, the org has no server of it either. An app whose every
    plan is not built yet (_NOT_BUILT) or hidden by a sign-in flag stays
    visible as coming_soon without providers, whatever the flags: they decide
    what can be connected, never whether the catalog admits the app exists.
    An app with a plan the catalog's own tool policy refuses
    (NO_TOOL_ALLOWED) is not unfinished, so this rule leaves it absent.
    It still obeys the addable-provider filters (kind, tier)."""
    app_id = entry.card["id"]
    from agentcc.services import connector_catalog_oauth, microsoft_connect_gate as microsoft

    records = load_bundle().providers
    providers = []
    for plan in entry.plans.values():
        if (
            not isinstance(plan, Plan)
            or not mcp_features.provider_visible(
                app_id, records.get((app_id, plan.card["id"]), plan.card)
            )
            or (kind and plan.kind != kind)
            or (tier and plan.tier != tier)
        ):
            continue
        # A K2 pilot's sign-in card is per org; a discovered MCP OAuth
        # provider keeps its own card (configure_oauth).
        card = (
            connector_catalog_oauth.provider_card(org, plan)
            if org is not None and (
                (plan.credential.kind == "oauth" and microsoft.blocked(
                    records.get((app_id, plan.card["id"]), {}), app_id=app_id
                ))
                or (plan.credential.mode == "per_user"
                    and not _mcp_oauth(records.get((app_id, plan.card["id"]), {})))
            )
            else plan.card
        )
        if card is not None:
            providers.append(card)
    servers = [
        server
        for server in servers
        if mcp_features.provider_visible(
            app_id, records.get((app_id, server.get("provider")), {})
        )
        and (
            mcp_features.enabled(mcp_features.UPSTREAM_OAUTH)
            or server.get("credential_mode") != "per_user"
        )
    ]
    held = [
        (plan, records.get((app_id, provider_id), {}))
        for provider_id, plan in entry.plans.items()
    ]
    # A plan a sign-in flag hides is simply not connectable here. A plan the
    # catalog's tool policy refuses keeps the app out of Coming soon.
    coming_soon = (
        bool(held)
        and not any(
            isinstance(plan, Unavailable) and plan.code == NO_TOOL_ALLOWED
            for plan, _ in held
        )
        and all(
            isinstance(plan, Unavailable)
            or not mcp_features.provider_visible(app_id, record)
            for plan, record in held
        )
        and any(
            (not isinstance(plan, Unavailable) or plan.code in _NOT_BUILT)
            and (
                not _microsoft_catalog_provider(app_id, record)
                or mcp_features.microsoft_auth_shape_valid(record)
            )
            for plan, record in held
        )
    )
    coming_soon = coming_soon or any(
        card.get("connect", {}).get("served") is False
        and card.get("connect", {}).get("reason") == microsoft.CODE
        for card in providers
    ) or any(server.get("start", {}).get("reason") == microsoft.CODE for server in servers)
    if not providers and (kind or tier or (not servers and not coming_soon)):
        return None
    return {
        **entry.card,
        **({"status": "coming_soon", "default_provider": None} if coming_soon else {}),
        "providers": providers,
        "connected": any(server["connected"] for server in servers),
        "servers": servers,
    }


def _org_connectors(org: Organization, user: User) -> dict[str, list[dict]]:
    """The org's servers that name a catalog app, by app id, each with
    whether it is connected for ``user``: one that needs no credential while
    it is enabled; a shared one while the active config references an active
    vault credential (the one the push sends); a per-user one while ``user``
    has an active credential of their own for it."""
    mcp = _active_mcp(org)
    entries = {
        server_id: (ref, entry)
        for server_id, entry in _servers(mcp).items()
        if (ref := _catalog_ref(entry)) is not None
        and mcp_features.server_visible(entry)
    }
    if not entries:
        return {}
    rows = list(
        AgentccMCPCredential.objects.filter(
            Q(scope=AgentccMCPCredential.Scope.SHARED)
            | Q(scope=AgentccMCPCredential.Scope.USER, user=user),
            organization=org,
            server_id__in=list(entries),
            status=AgentccMCPCredential.Status.ACTIVE,
        ).defer("encrypted_secret", "encrypted_refresh")
    )
    virtual_servers = mcp.get("virtual_servers")
    virtual_servers = virtual_servers if isinstance(virtual_servers, dict) else {}
    by_app: dict[str, list[dict]] = {}
    for server_id, ((app_id, provider_id, version), entry) in sorted(entries.items()):
        credential = entry.get("credential")
        mode = (credential if isinstance(credential, dict) else {}).get(
            "mode"
        ) or "none"
        enabled = entry.get("enabled") is not False
        own = [row for row in rows if row.server_id == server_id]
        if mode == "none":
            connected = enabled
        elif mode == "shared":
            connected = enabled and any(
                mcp_credentials.pushes_credential(mcp, row) for row in own
            )
        elif mode == "per_user":
            connected = enabled and any(
                row.scope == AgentccMCPCredential.Scope.USER for row in own
            )
        else:
            connected = False
        vs = virtual_servers.get(server_id)
        own_vs = isinstance(vs, dict) and server_id in (vs.get("members") or [])
        by_app.setdefault(app_id, []).append(
            {
                "server_id": server_id,
                "catalog_ref": f"{app_id}/{provider_id}@{version}",
                "provider": provider_id,
                "version": version,
                "credential_mode": mode,
                "enabled": enabled,
                "connected": connected,
                "virtual_server": {"slug": server_id, "id": vs.get("id")}
                if own_vs
                else None,
            }
        )
    from agentcc.services import connector_catalog_oauth

    static_server_ids = {
        row.server_id
        for row in rows
        if row.scope == AgentccMCPCredential.Scope.USER
        and row.kind
        in {
            AgentccMCPCredential.Kind.BEARER,
            AgentccMCPCredential.Kind.API_KEY,
            AgentccMCPCredential.Kind.HEADER,
        }
    }
    shared_oauth_server_ids = {
        row.server_id for row in rows
        if row.scope == AgentccMCPCredential.Scope.SHARED
        and row.kind == AgentccMCPCredential.Kind.OAUTH
    }
    shared_oauth_server_ids.update(
        server_id for server_id, (_, entry) in entries.items()
        if isinstance(entry.get("credential"), Mapping)
        and entry["credential"].get("kind") in mcp_features.OAUTH_KINDS
    )
    connector_catalog_oauth.decorate_servers(
        org, user, by_app, static_server_ids=static_server_ids,
        shared_oauth_server_ids=shared_oauth_server_ids,
    )
    return by_app


# --- Writes ------------------------------------------------------------------


def add_connector(
    org: Organization,
    app_id: str,
    *,
    provider_id: str | None,
    mode: str,
    url_variables: Mapping[str, Any],
    actor: User,
    credential: str | None = None,
) -> dict:
    """Add ``app_id`` from the catalog with the org's ``credential``, the
    secret value (None for a provider that needs none).

    It lists the provider's tools through the gateway's probe with that
    credential (CN-ADD), and sets each tool's posture (``listed_tool_policy``).
    Then one new config version holds the server, the credential (stored in
    the vault and referenced from the server) and the connector's virtual
    server; the writer validates, saves and pushes it. The probe runs before
    the write, outside any transaction, so a slow provider holds no lock.

    Raises a ``ConnectorCatalogError`` or the writer's ``OrgConfigRejected``.
    Either way nothing is written and no credential is stored."""
    from agentcc.mcp_release_gates import require_catalog_connect

    require_catalog_connect()
    entry = load_bundle().apps.get(app_id)
    if entry is None:
        raise ConnectorNotFound(APP_NOT_FOUND, "No such app in the connector catalog.")
    if not mcp_features.app_visible(app_id, entry.card):
        raise ConnectorNotFound("not_found", "Not found.")
    provider_id = provider_id or entry.default_provider
    record = load_bundle().providers.get((app_id, provider_id), {})
    if not mcp_features.provider_visible(app_id, record):
        raise ConnectorNotFound("not_found", "Not found.")
    plan = entry.plans.get(provider_id)
    if plan is None:
        raise ConnectorNotFound(PROVIDER_NOT_FOUND, f"{app_id} has no such provider.")
    if isinstance(plan, Unavailable):
        raise ConnectorConflict(plan.code, f"{app_id}/{provider_id}: {plan.message}.")
    if plan.credential.kind == "oauth" and _mcp_oauth(record):
        raise ConnectorConflict(
            "oauth_configuration_required", "Configure the OAuth app before signing in."
        )
    if mode not in plan.declared_modes:
        raise ConnectorInvalid(
            MODE_NOT_DECLARED, f"{app_id}/{provider_id} has no {mode} account mode."
        )
    if mode not in plan.modes:
        raise ConnectorConflict(PER_USER_NOT_BUILT, f"{_PER_USER_MESSAGE}.")
    if plan.credential.mode == "per_user":
        from agentcc.services import connector_catalog_oauth

        return connector_catalog_oauth.add(
            org,
            app_id,
            entry,
            plan,
            mode=mode,
            values=url_variables,
            actor=actor,
            credential=credential,
        )
    url, values = render_url(
        plan.url_template,
        plan.url_variables,
        url_variables,
        plan.credential.url_variable,
    )
    # The org's values are in: the push's rule once more, on the URL written.
    problem = plan.credential.placement_problem(url, plan.catalog_ref, plan.parts)
    if problem is not None:
        unavailable = _placement_unavailable(problem)
        raise ConnectorConflict(
            unavailable.code, f"{app_id}/{provider_id}: {unavailable.message}."
        )
    secret = _credential_secret(plan.credential, credential)
    # Refused before the provider is called, and again under the org lock.
    _refuse_add(_active_mcp(org), app_id)
    listing = _listing(org, plan, url, secret)
    declared = {tool.name: tool for tool in plan.tools}
    listed = [
        (tool, listed_tool_policy(tool, declared.get(tool.name), plan.default_posture))
        for tool in listing.tools
    ]
    # A tool the catalog denies is denied by the rule even when the provider
    # does not list it yet, so a later listing cannot bring it in.
    virtual_server = _virtual_server(
        entry.card,
        [policy for _, policy in listed]
        + [tool for tool in plan.tools if tool.gateway_posture == DENY],
    )
    if not virtual_server["tools"]["include"]:
        raise ConnectorConflict(
            NO_TOOL_ALLOWED,
            f"{app_id}/{provider_id}: none of the {listing.tool_count} tools it "
            "lists can run at the gateway, even after an approval (each is one "
            "the catalog denies or a generic tool, or its name breaks MCP's "
            "rule), so adding it would expose no tool.",
        )
    server = {
        "url": url,
        "transport": plan.transport,
        "era": plan.era,
        "catalog_ref": plan.catalog_ref,
        "credential": {"mode": plan.credential.mode},
    }
    if values:
        server["url_variables"] = values
    if plan.timeout_ms is not None:
        server["timeout_ms"] = plan.timeout_ms
    if plan.forward_headers:
        server["forward_headers"] = list(plan.forward_headers)
    stored: list[AgentccMCPCredential] = []

    def updater(config: AgentccOrgConfig) -> None:
        require_catalog_connect()
        mcp = copy.deepcopy(config.mcp) if isinstance(config.mcp, dict) else {}
        _refuse_add(mcp, app_id)
        mcp["enabled"] = True
        mcp["servers"] = {**_servers(mcp), app_id: copy.deepcopy(server)}
        mcp["virtual_servers"] = {
            **_virtual_servers(mcp),
            app_id: copy.deepcopy(virtual_server),
        }
        # The listing is the baseline pinning holds every later change to
        # (M20): each tool is pinned, in this same version, to the revision
        # the gateway listed it at.
        mcp_revisions.pin_listing(mcp, app_id, listing.tools)
        config.mcp = mcp
        if secret is None:
            return
        # An active shared credential of this id is one no server
        # references (the id is free, above), left by a removed server:
        # nothing pushes it, so it goes (the vault's create does the same).
        mcp_credentials.revoke_server_credentials(
            org, app_id, scope=AgentccMCPCredential.Scope.SHARED
        )
        # Bound to the origin of the URL the server is added with, where the
        # key was just listed with: the push and the probe send it
        # only there, and an unbound credential is sent nowhere.
        row = mcp_credentials.store(
            org,
            app_id,
            secret,
            actor=actor,
            origin=mcp_credentials.origin_of(url),
        )
        mcp_credentials.set_reference(
            config, app_id, row, AgentccMCPCredential.Status.ACTIVE
        )
        stored.append(row)

    with mcp_credentials.vault_write():
        config, warnings = write_config_version_with(
            org,
            updater,
            # The baseline, recorded with the version that set the posture.
            description=(
                f"Add connector {plan.catalog_ref} from the catalog: "
                f"{listing.tool_count} tools listed, tools_hash {listing.tools_hash}"
            ),
            user=actor,
        )
    logger.info(
        "connector_added",
        org_id=str(org.pk),
        catalog_ref=plan.catalog_ref,
        version=config.version,
        tool_count=listing.tool_count,
        tools_allowed=len(virtual_server["tools"]["include"]),
        tools_need_approval=len(virtual_server.get("approval", {}).get("tools", [])),
        tools_hash=listing.tools_hash,
        credential_id=str(stored[0].pk) if stored else None,
        actor_id=str(actor.pk),
    )
    return {
        "app": app_id,
        "provider": provider_id,
        "catalog_ref": plan.catalog_ref,
        "server_id": app_id,
        "mode": mode,
        "url": url,
        "url_variables": values,
        "credential": {**plan.credential.card(), "connected": True},
        "virtual_server": {
            "slug": app_id,
            "id": config.mcp["virtual_servers"][app_id]["id"],
        },
        "tools": [
            _listed_tool_card(tool, policy) for tool, policy in listed if tool.name
        ],
        "baseline": {
            "tool_count": listing.tool_count,
            "tools_hash": listing.tools_hash,
        },
        "mcp_enabled": config.mcp.get("enabled") is True,
        "version": config.version,
        **_gateway_write_result(warnings),
    }


def _gateway_write_result(warnings, *, disconnected_app=None) -> dict:
    """Carry the revocation-only warning without hiding a failed config push."""
    from agentcc.services.mcp_shared_credential_lifecycle import (
        SHARED_CREDENTIAL_REVOCATION,
        SHARED_CREDENTIAL_REVOCATION_MESSAGE,
    )

    result = {"gateway_synced": is_gateway_synced(warnings)}
    unconfirmed = [w for w in warnings if w["code"] == GATEWAY_SYNC_FAILED]
    if not unconfirmed or any(
        w.get("operation") != SHARED_CREDENTIAL_REVOCATION for w in unconfirmed
    ):
        return result
    message = SHARED_CREDENTIAL_REVOCATION_MESSAGE
    if disconnected_app is not None:
        # Removal must still work when the app or the whole catalog is gone.
        app_name = disconnected_app
        try:
            entry = load_bundle().apps.get(disconnected_app)
        except ConnectorCatalogError:
            pass
        else:
            if entry is not None:
                app_name = entry.card["name"]
        # Applying the saved removal retires the pool entry and cancels its
        # clients (tenancy.Pool.apply/entry.retire). It does not acknowledge the
        # revision-purge obligation, which stays pending until its own retry.
        message = (
            f"{app_name} is disconnected. "
            "The gateway has not confirmed it revoked the stored key yet; "
            "it stops using it at its next config sync."
        )
    result["gateway_warning"] = message
    return result


def _credential_secret(
    credential: CredentialPlan, value: str | None
) -> mcp_credentials.Secret | None:
    """The vault secret the add stores and lists the tools with, checked as
    the vault checks it; None for a provider that needs no credential.
    Raises ``ConnectorInvalid``, naming no value."""
    if credential.mode == "none":
        if value is not None:
            raise ConnectorInvalid(
                CREDENTIAL_NOT_TAKEN,
                "credential: this provider needs no credential, so none is taken.",
            )
        return None
    if value is None:
        raise ConnectorInvalid(
            CREDENTIAL_REQUIRED,
            f"credential.value is required: the {credential.name} key, which "
            "lists the provider's tools now and is stored for the organization.",
        )
    try:
        return credential.secret(value)
    except mcp_credentials.MCPCredentialError as e:
        raise ConnectorInvalid(CREDENTIAL_INVALID, f"credential.value: {e}") from None


def _listing(
    org: Organization,
    plan: Plan,
    url: str,
    secret: mcp_credentials.Secret | None,
) -> connector_tool_listing.Listing:
    try:
        return connector_tool_listing.list_tools(
            str(org.pk),
            url,
            transport=plan.transport,
            auth=secret.push_form() if secret is not None else None,
        )
    except connector_tool_listing.ListingError as e:
        logger.warning(
            "connector_tool_listing_failed",
            org_id=str(org.pk),
            catalog_ref=plan.catalog_ref,
            code=e.code,
        )
        if e.status == 404:
            # MCP is off for the deployment: about the gateway, not the app.
            raise ConnectorNotFound(e.code, str(e)) from None
        kind = ConnectorInvalid if e.status == 400 else ConnectorListingFailed
        raise kind(e.code, f"{plan.catalog_ref}: {e}.") from None


def remove_connector(org: Organization, app_id: str, *, actor: User) -> dict:
    """Remove every org server whose ``catalog_ref`` names ``app_id``, revoke
    their credentials, drop them from every virtual server's members, and
    remove the connector's own virtual server once it has no member left.
    Needs no catalog, so a connector the catalog dropped can still be removed.
    Raises ``ConnectorNotFound`` when the org has none."""
    from agentcc.mcp_release_gates import require_catalog_connect

    require_catalog_connect()
    removed: dict[str, list] = {}

    def updater(config: AgentccOrgConfig) -> None:
        require_catalog_connect()
        mcp = copy.deepcopy(config.mcp) if isinstance(config.mcp, dict) else {}
        servers = _servers(mcp)
        server_ids = sorted(
            server_id
            for server_id, entry in servers.items()
            if _catalog_app(entry) == app_id
        )
        if not server_ids:
            raise ConnectorNotFound(
                NOT_ADDED, f"{app_id} is not added to this organization."
            )
        revoked = []
        for server_id in server_ids:
            del servers[server_id]
            revoked += mcp_credentials.revoke_server_credentials(org, server_id)
        virtual_servers = mcp.get("virtual_servers")
        virtual_servers = virtual_servers if isinstance(virtual_servers, dict) else {}
        removed_vs = []
        for slug, vs in virtual_servers.items():
            members = vs.get("members") if isinstance(vs, dict) else None
            if not isinstance(members, list):
                continue
            kept = [m for m in members if str(m).split("@", 1)[0] not in server_ids]
            if kept == members:
                continue
            if not kept and slug in server_ids:
                removed_vs.append(slug)
            else:
                vs["members"] = kept
        for slug in removed_vs:
            del virtual_servers[slug]
        mcp["servers"] = servers
        config.mcp = mcp
        removed.update(servers=server_ids, virtual_servers=removed_vs, revoked=revoked)

    config, warnings = write_config_version_with(
        org, updater, description=f"Remove connector {app_id}", user=actor
    )
    logger.info(
        "connector_removed",
        org_id=str(org.pk),
        app=app_id,
        servers=removed["servers"],
        version=config.version,
        actor_id=str(actor.pk),
    )
    return {
        "app": app_id,
        "removed_servers": removed["servers"],
        "removed_virtual_servers": removed["virtual_servers"],
        "revoked_credentials": [str(pk) for pk in removed["revoked"]],
        "version": config.version,
        **_gateway_write_result(warnings, disconnected_app=app_id),
    }


def _virtual_server(app_card: Mapping, tools: list[ToolPolicy]) -> dict:
    """The connector's own virtual server: open to every principal of the
    org (agent mode, one shared account), composing only the tools that run,
    allowed or after an approval. Its approval policy lists the second
    (require listed); a rule denies each tool the posture does not allow
    (a deny always wins, even over a later, wider include). A name that
    breaks MCP's tool name rule is written to no list, a name denied once is
    never included, and a name that needs an approval once always does."""
    app_id = app_card["id"]
    allowed, approved, denied = set(), set(), set()
    for tool in tools:
        if not connector_tool_listing.TOOL_NAME.fullmatch(tool.name):
            continue
        if tool.gateway_posture != ALLOW:
            target = denied
        elif tool.needs_approval:
            target = approved
        else:
            target = allowed
        target.add(f"{app_id}/{tool.name}")
    approved -= denied
    virtual_server = {
        "title": app_card["name"],
        "description": app_card["description"],
        "members": [app_id],
        "tools": {"include": sorted((allowed | approved) - denied)},
    }
    if approved:
        # Each approval lets one identical call run (validity once). No
        # ttl_seconds, so the gateway's default applies (900 s). The gateway
        # refuses every call on a server whose policy it cannot apply, such
        # as validity "step" or a TTL of 0 (approvalPolicy), so nothing else
        # is set here.
        virtual_server["approval"] = {
            "require": "listed",
            "tools": sorted(approved),
            "validity": "once",
        }
    if denied:
        virtual_server["access"] = {"rules": [{"match": {}, "deny": sorted(denied)}]}
    return virtual_server


def _refuse_add(mcp: dict, app_id: str) -> None:
    """Refuse an add the org's mcp section ``mcp`` does not take: another
    spelling of a section, the app already added, its server id taken, an
    access default of allow, or MCP left off (``_refuse_mcp_switch``)."""
    _refuse_alias_spellings(mcp)
    servers = _servers(mcp)
    if any(_catalog_app(entry) == app_id for entry in servers.values()):
        raise ConnectorConflict(
            ALREADY_ADDED,
            f"{app_id} is already added; remove it to add it with another provider.",
        )
    if app_id in servers or app_id in _virtual_servers(mcp):
        raise ConnectorConflict(
            SERVER_ID_TAKEN,
            f"mcp.servers.{app_id} or mcp.virtual_servers.{app_id} is already "
            "used by a server the organization added itself.",
        )
    _refuse_access_default_allow(mcp)
    _refuse_mcp_switch(mcp)


def _refuse_alias_spellings(mcp: dict) -> None:
    """The writer assigns virtual-server ids under the snake_case key only, so
    an mcp section saved under another spelling is left to its admin."""
    for field in ("servers", "virtual_servers", "managed_tools"):
        aliases = sorted(field_input_names(MCPOrgConfig, field) - {field})
        present = [alias for alias in aliases if alias in mcp]
        if present:
            raise ConnectorConflict(
                MCP_CONFIG_SPELLING,
                f"mcp.{present[0]} is saved under another spelling; save it as "
                f"mcp.{field} before adding a connector.",
            )


def _refuse_access_default_allow(mcp: dict) -> None:
    access = mcp.get("access")
    if isinstance(access, dict) and access.get("default") == "allow":
        raise ConnectorConflict(
            ACCESS_DEFAULT_ALLOW,
            "mcp.access.default is allow, so a key bound to no virtual server "
            "would reach every tool of the connector, the ones its posture denies "
            "included. Bind keys to virtual servers and set it to deny first.",
        )


def _refuse_mcp_switch(mcp: dict) -> None:
    """The gateway runs an org's MCP servers only while ``mcp.enabled`` is
    true, and an add sets it. A first connector in an org with no MCP setup
    switches it on. An explicit false is the org's off switch, and an org
    with servers, virtual servers or managed tools configured would have
    them switched on as well, so both are left to the admin."""
    enabled = mcp.get("enabled")
    if enabled is True:
        return
    if enabled is False:
        raise ConnectorConflict(
            MCP_DISABLED,
            "MCP is switched off for this organization (mcp.enabled is false); "
            "switch it on before adding a connector.",
        )
    managed = mcp.get("managed_tools")
    if (
        _servers(mcp)
        or mcp.get("virtual_servers")
        or (isinstance(managed, dict) and managed.get("enabled") is True)
    ):
        raise ConnectorConflict(
            MCP_DISABLED,
            "mcp.enabled is not set, so the gateway runs none of this "
            "organization's MCP servers, and adding a connector would switch on "
            "the ones already configured too. Set mcp.enabled first.",
        )


def _active_mcp(org: Organization) -> dict:
    mcp = (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=org, is_active=True, deleted=False
        )
        .values_list("mcp", flat=True)
        .first()
    )
    return mcp if isinstance(mcp, dict) else {}


def _servers(mcp: dict) -> dict[str, dict]:
    servers = mcp.get("servers")
    if not isinstance(servers, dict):
        return {}
    return {sid: entry for sid, entry in servers.items() if isinstance(entry, dict)}


def _virtual_servers(mcp: dict) -> dict:
    virtual_servers = mcp.get("virtual_servers")
    return virtual_servers if isinstance(virtual_servers, dict) else {}


# catalog_ref under every spelling the contract reads, catalog_ref first.
_CATALOG_REF_KEYS = tuple(
    sorted(
        field_input_names(MCPServerOrgConfig, "catalog_ref"),
        key=lambda key: key != "catalog_ref",
    )
)


def _catalog_ref(entry: Mapping) -> tuple[str, str, str] | None:
    """A server's catalog_ref as (app, provider, version), or None."""
    for key in _CATALOG_REF_KEYS:
        if key in entry:
            value = entry[key]
            match = _CATALOG_REF.fullmatch(value) if isinstance(value, str) else None
            return match.groups() if match else None
    return None


def provider_record(entry: Mapping) -> dict | None:
    """The manifest record of the provider a server's ``catalog_ref`` names,
    when the loaded bundle holds that provider at the same major version;
    None for a server added by URL, an unreadable ref, a provider the bundle
    no longer has, or no bundle at all."""
    ref = _catalog_ref(entry)
    if ref is None:
        return None
    app_id, provider_id, version = ref
    try:
        record = load_bundle().providers.get((app_id, provider_id))
    except ConnectorCatalogError:
        return None
    if (
        record is None
        or str(record.get("version", "")).split(".")[0] != version.split(".")[0]
    ):
        return None
    return record


def _catalog_app(entry: Mapping) -> str | None:
    ref = _catalog_ref(entry)
    return ref[0] if ref else None
