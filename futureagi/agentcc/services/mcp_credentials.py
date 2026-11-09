"""The MCP credential vault: upstream MCP secrets, encrypted and write-only (M14).

A customer's upstream MCP secret (a bearer token or an API key) lives only in
``AgentccMCPCredential``, encrypted through ``CredentialManager``. The org
config holds a reference to it, ``credential {mode, credential_id, state}``:
under ``mcp.servers.<id>`` for the org's own servers, and under
``mcp.managed_tools.credentials.<fagi.id>`` for a FutureAGI managed server
(MC.4). ``assemble_for_push`` decrypts the active shared secrets for
``config_push``, which adds them to the push payload only; a secret is never
stored in a config version, returned by a read, or logged.

Shared writes take the org's config lock first (``org_config_writer.lock_org``)
and push the org's config under it, so pushes follow the order of writes (the
S0 rule). Personal writes take the (org, user, server) identity lock instead:
they never push config and unrelated identities never wait on their I/O. A write that changes the reference goes through the config writer,
which writes a new version and pushes it; a rotation changes no reference, so
it pushes the active version as it is.

A lapsed credential (revoked, expired, needs_reauth) keeps its config entry and
``mode`` and changes only ``state`` (decision 27): the gateway then knows a
customer credential existed and answers ``auth_required`` instead of moving
the call to managed billing. Only ``use_managed`` removes the entry of a
``fagi.*`` server.

Placement (CN11, AM-C9). An API key goes in a header, as before, or in a
query parameter or a secret URL variable of its server's url (Browserbase
takes its key in the query). The pushed secret says where with ``in`` and
``name``; a header placement keeps the form it had, so its fingerprint does
not change. With no migration, the row records a non-header placement in
``header_name`` as ``<in>:<name>``; ``:`` is never in a header name.

Org OAuth clients (CN11). An org's own OAuth client for a catalog
connector's vendor is an ``oauth_client`` row, scope shared, keyed
``server:<id>`` or ``group:<group>`` (connector_oauth_clients.org_client_key),
which CN3's ``resolve`` reads. It stays in Django: ``assemble_for_push`` and
the probe take static kinds only, so it is never pushed, and the upstream
credential routes neither list it nor rotate or revoke it. Its secret is
write-only and is held as a ``SealedSecret`` from the request to encryption.

A shared credential of a customer server is bound to the origin (scheme, host
and port) its secret was entered for, ``origin``: only a write that enters the
secret together with its server's URL binds it (update-mcp-server, create, the
secret migration). The push and the probe send it only to its server at that
origin (``pushes_credential``), whatever path put the server elsewhere, and an
activation that would put it elsewhere is refused (``credentials_sent_elsewhere``,
IC-R1-12). A rotation keeps
the binding while a config sends the credential and clears it otherwise; an
unbound credential is sent nowhere until it is re-entered with its server.
"""

import copy
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import partial
from urllib.parse import urlsplit

import structlog
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Organization, User
from agentcc.contracts.gateway_admin import (
    MCPManagedToolCredential,
    MCPOrgConfig,
    MCPServerCredential,
)
from agentcc.models.mcp_credential import URL_MAX_LENGTH, AgentccMCPCredential
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import (
    config_push,
    connector_oauth_clients,
    mcp_credential_placement,
    org_config_secrets,
)
from agentcc.services.credential_manager import (
    ENCRYPTED_PREFIX,
    decrypt_token,
    encrypt_token,
)
from agentcc.services.mcp_credential_lifecycle import (
    with_current_lifetimes as with_current_lifetimes,
)
from agentcc.services.mcp_shared_credential_lifecycle import evict as evict_shared
from agentcc.services.mcp_shared_credential_lifecycle import (
    evict_after_commit as evict_shared_after_commit,
)
from agentcc.services.mcp_shared_credential_lifecycle import next_revision
from agentcc.services.mcp_user_credential_lifecycle import evict as evict_user
from agentcc.services.mcp_user_credential_lifecycle import lock_identity
from agentcc.services.org_config_writer import (
    is_gateway_synced,
    lock_org,
    write_config_version_with,
)

logger = structlog.get_logger(__name__)

Kind = AgentccMCPCredential.Kind
Scope = AgentccMCPCredential.Scope
Status = AgentccMCPCredential.Status

MANAGED_PREFIX = "fagi."
# A customer server id (MC.4), or a managed server's fagi.<id>.
SERVER_ID = re.compile(r"^(fagi\.)?[a-z0-9][a-z0-9_-]{0,47}$")
# The static secrets the gateway sends upstream (mcp/transport.go, probe.go).
STATIC_KINDS = (Kind.BEARER, Kind.API_KEY)
MAX_SECRET_CHARS = 8192
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$")
# Headers the gateway sets itself on an upstream hop (mcp_credential_placement,
# the gateway's own list, credtemplate/part.go): the vault refuses them at
# create, as the push and the gateway would.
_reserved_header = mcp_credential_placement.reserved_header
# Where an API key goes (AM-C9; connector-manifest/v1 credentialIn), and the
# manifest's patterns for a query parameter's and a URL variable's name.
HEADER = "header"
QUERY = "query"
URL_VARIABLE = "url_variable"
PLACEMENTS = (HEADER, QUERY, URL_VARIABLE)
_QUERY_NAME = re.compile(r"^[A-Za-z0-9._~-]{1,64}$")
_VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
# mcp.managed_tools under every spelling the contract reads, managed_tools first.
_MANAGED_TOOLS_KEYS = tuple(
    sorted(
        config_push.field_input_names(MCPOrgConfig, "managed_tools"),
        key=lambda key: key != "managed_tools",
    )
)


def _input_order(contract_model, field_name) -> tuple[str, ...]:
    """A field's spellings in the order the contract reads them: its
    validation alias choices, of which the first one present wins
    (``_normalize_input_aliases``)."""
    alias = contract_model.model_fields[field_name].validation_alias
    choices = getattr(alias, "choices", (alias,)) if alias is not None else ()
    return tuple(choice for choice in choices if isinstance(choice, str)) or (
        field_name,
    )


# A reference's credential_id under every spelling the contract reads, in the
# order it reads them: a tuple, so every worker resolves a reference that
# carries two spellings the same way (a set's order is hash-seeded).
_CREDENTIAL_ID_KEYS = tuple(
    dict.fromkeys(
        _input_order(MCPServerCredential, "credential_id")
        + _input_order(MCPManagedToolCredential, "credential_id")
    )
)
_DEFAULT_PORTS = {"http": 80, "https": 443}
# Set while a vault writer writes a config version: the generic write guard
# (vault_owned_changes) then lets it change the references it owns.
_VAULT_WRITE = ContextVar("agentcc_mcp_vault_write", default=False)


class MCPCredentialError(ValueError):
    """A credential write was refused. Messages never carry a secret."""

    code = "invalid_mcp_credential"


class MCPServerNotFound(MCPCredentialError):
    code = "mcp_server_not_found"


class MCPCredentialExists(MCPCredentialError):
    code = "mcp_credential_exists"


class MCPCredentialNotActive(MCPCredentialError):
    code = "mcp_credential_not_active"


class ManagedToolsDisabled(MCPCredentialError):
    code = "managed_tools_disabled"


class SecretInRequest(MCPCredentialError):
    """A request put a secret where only a reference belongs."""

    code = "secret_in_config"


class MCPProbeURLMismatch(MCPCredentialError):
    """A probe named a stored credential with a URL its server does not use."""

    code = "mcp_probe_url_mismatch"


class MCPProbePlacementUnsupported(MCPProbeURLMismatch):
    """A probe named a stored key placed in a query parameter or a URL
    variable: the probe sends a credential in a header only. A subclass of
    the URL refusal so the probe route answers it with the same 400."""

    code = "mcp_probe_credential_placement"


class MCPCredentialIsOAuthClient(MCPCredentialError):
    """An upstream credential route named an org OAuth client, which only
    /agentcc/connectors/oauth-clients/ rotates or revokes."""

    code = "mcp_credential_is_oauth_client"


class MCPServerSignsInOverOAuth(MCPCredentialError):
    """A stored key named a server whose one shared account an Owner or
    Admin signs in over OAuth (credential kind oauth, CN24): it takes none."""

    code = "mcp_server_signs_in_over_oauth"


class OrgOAuthClientInvalid(MCPCredentialError):
    """An org OAuth client write was refused; CN3's code for a client."""

    code = "invalid_connector_oauth_client"


class OrgOAuthClientExists(MCPCredentialExists):
    code = "connector_oauth_client_exists"


class MCPCredentialOriginChanged(MCPCredentialError):
    """A save would send a server's stored credential to another origin: its
    url changes scheme, host or port while the save sends no new secret (M14
    IR-9, IC-R1-9)."""

    code = "mcp_credential_origin_changed"


# The refusal's message: the editor re-enters the credential (it is
# write-only, so they cannot copy it) or saves the server without one.
ORIGIN_CHANGED_MESSAGE = (
    "moves the server to another origin (scheme, host or port), so its stored "
    "credential is not sent there: reconnect it by re-entering the credential (auth.token or "
    "auth.key in update-mcp-server, or /agentcc/mcp/credentials/), or save the "
    "server without one (auth {type: none}, or drop its credential reference)"
)


# An activation must not silently drop a referenced credential, including one
# revoked when an earlier config removed the server (IC-R1-12, W4).
BOUND_ORIGIN_MESSAGE = (
    "does not reference an active credential bound to its current origin "
    "(scheme, host and port): reconnect its credential (re-enter it with the "
    "server using auth.token or auth.key in update-mcp-server, or sign in "
    "again for OAuth), or save the server without a credential reference"
)


@dataclass
class CredentialChange:
    """A credential write and whether the gateway took the push that
    followed it (None when nothing pushed changed, or the org has no active
    config yet)."""

    credential: AgentccMCPCredential
    gateway_synced: bool | None = None


@dataclass(frozen=True)
class Secret:
    """One static upstream secret; ``repr`` leaves the value out.

    ``placement`` is where it goes: in ``header_name`` (Authorization when
    empty), or in the query parameter or the URL variable ``name``."""

    kind: str
    value: str = field(repr=False)
    header_name: str = ""
    placement: str = HEADER
    name: str = ""

    def push_form(self) -> dict[str, str]:
        """MCPCredentialSecret as pushed: ``{type, header, value}`` for a
        header placement, exactly as before AM-C9, and ``{type, in, name,
        value}`` for a query parameter or a URL variable."""
        if self.placement == HEADER:
            header = self.header_name or "Authorization"
            return {"type": self.kind, "header": header, "value": self.value}
        return {
            "type": self.kind,
            "in": self.placement,
            "name": self.name,
            "value": self.value,
        }

    @property
    def stored_header_name(self) -> str:
        """The row's ``header_name``: the header, or ``<in>:<name>``."""
        if self.placement == HEADER:
            return self.header_name
        return f"{self.placement}:{self.name}"


def new_secret(kind, value, header_name=None, *, placement=None, name=None) -> Secret:
    """A checked ``Secret``. Raises ``MCPCredentialError`` naming the field.

    ``placement`` defaults to a header. A header placement may name its
    header as ``name`` or ``header_name``, not two different ones. A query
    or URL variable placement takes an api_key, a ``name`` that follows
    connector-manifest/v1's queryName or varName, and no ``header_name``;
    whether the server's url can take it is ``check_placement``'s."""
    if kind not in STATIC_KINDS:
        raise MCPCredentialError(
            "kind must be bearer or api_key; OAuth credentials are connected "
            "by each user, not stored here"
        )
    if not isinstance(value, str) or not value:
        raise MCPCredentialError("the secret value must be a non-empty string")
    if (
        value != value.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or len(value) > MAX_SECRET_CHARS
    ):
        raise MCPCredentialError(
            "the secret value must have no surrounding spaces or control "
            f"characters and at most {MAX_SECRET_CHARS} characters"
        )
    if value.startswith(ENCRYPTED_PREFIX):
        raise MCPCredentialError(
            f"the secret value must be the plain secret, not {ENCRYPTED_PREFIX} ciphertext"
        )
    if org_config_secrets.is_mask(value):
        # The release line refused a read mask typed as a secret (NG-01). A
        # read of the vault never returns one, so a mask names some other
        # field's secret, which must never become this one.
        raise MCPCredentialError(
            "the secret value must be the plain secret, not a read-back mask "
            f"({org_config_secrets.MASK_STEM}...); send no auth to keep the "
            "stored credential"
        )
    header_name = header_name or ""
    placement = placement or HEADER
    if placement not in PLACEMENTS:
        raise MCPCredentialError(f"placement must be one of {', '.join(PLACEMENTS)}")
    if placement != HEADER:
        return _placed_secret(kind, value, header_name, placement, name)
    if name:
        if header_name and header_name.lower() != str(name).lower():
            raise MCPCredentialError(
                "a header placement names its header once: name and header_name differ"
            )
        header_name = name
    if kind == Kind.API_KEY and not header_name:
        raise MCPCredentialError("an api_key credential needs header_name")
    if header_name and (
        not isinstance(header_name, str)
        or not _HEADER_NAME.fullmatch(header_name)
        or _reserved_header(header_name)
    ):
        raise MCPCredentialError(
            "header_name must be an HTTP header name the gateway does not set itself"
        )
    return Secret(str(kind), value, header_name)


def _placed_secret(kind, value, header_name, placement, name) -> Secret:
    if kind != Kind.API_KEY:
        raise MCPCredentialError(
            "only an api_key credential goes in a query parameter or a URL "
            "variable; a bearer token goes in Authorization"
        )
    if header_name:
        raise MCPCredentialError(
            "a credential in a query parameter or a URL variable has no header_name"
        )
    pattern = _QUERY_NAME if placement == QUERY else _VARIABLE_NAME
    if not isinstance(name, str) or not pattern.fullmatch(name):
        raise MCPCredentialError(f"name must match {pattern.pattern} for {placement}")
    return Secret(str(kind), value, "", placement, name)


def check_placement(secret: Secret, mcp, server_id: str, scope: str) -> None:
    """Refuse a placement the server cannot take: a query parameter or a URL
    variable for a per-user credential or a managed (``fagi.*``) server, or a
    URL variable whose ``{name}`` is not in the path or query of the
    server's url in ``mcp`` (``secret_variable_problem``)."""
    if secret.placement == HEADER:
        return
    if scope != Scope.SHARED:
        raise MCPCredentialError(
            "only a shared credential goes in a query parameter or a URL variable"
        )
    if is_managed(server_id):
        raise MCPCredentialError(
            f"{server_id} is a managed (fagi.*) server: its credential goes in a header"
        )
    if secret.placement == URL_VARIABLE:
        problem = secret_variable_problem(configured_url(mcp, server_id), secret.name)
        if problem is not None:
            raise MCPCredentialError(problem)


def secret_variable_problem(url, name: str) -> str | None:
    """Why ``url`` cannot take the secret URL variable ``{name}``, or None.

    It must hold ``{name}``, and only in its path or query: never in the
    scheme, user info, host, port or fragment, so a secret never sets where
    it is sent (IC-R1-9). Read as text, so a variable in the port, which no
    URL parser takes, is refused like the rest."""
    token = "{" + name + "}"
    problem = (
        f"the server's url has no {token} in its path or query: a secret URL "
        "variable fills a path or query only, never the scheme, user info, "
        "host, port or fragment, so a secret never sets where it is sent"
    )
    if not isinstance(url, str):
        return problem
    scheme, sep, rest = url.partition("://")
    end = min((i for i in map(rest.find, "/?#") if i >= 0), default=len(rest))
    path_and_query, _, fragment = rest[end:].partition("#")
    if (
        not sep
        or token not in path_and_query
        or any(token in part for part in (scheme, rest[:end], fragment))
    ):
        return problem
    return None


# The rule the push applies to a pushed secret (AM-C20 item 2; IR-CN7-2; the
# CW2 preamble, AM-C22): the gateway's own (credtemplate.ForCatalogSecret),
# held to it by the shared placement vectors. An org server's secret goes in a
# header, a query parameter or a URL variable of its url (and in parts, though
# no vault writer stores one yet, CN11 R5). When the server has a catalog_ref
# the provider's declared parts in the connector catalog decide it: every
# declared secret part carried with its prefix, no constant pushed, no
# undeclared part, the same major version. catalog defaults to None, which is
# no catalog loaded, so a caller that passes a catalog_ref without the catalog
# gets a catalog_ref refusal (fail closed, as the gateway with no catalog);
# config_push passes connector_catalog_parts' copy. A managed tool
# credential's secret (server_url None) goes in a header only.
pushed_placement_problem = mcp_credential_placement.pushed_placement_problem


def check_server_id(server_id) -> str:
    if not isinstance(server_id, str) or not SERVER_ID.fullmatch(server_id):
        raise MCPCredentialError(f"server_id must match {SERVER_ID.pattern}")
    return server_id


def is_managed(server_id: str) -> bool:
    return server_id.startswith(MANAGED_PREFIX)


def create(
    org: Organization,
    server_id: str,
    *,
    kind: str,
    value: str,
    header_name: str | None = None,
    placement: str | None = None,
    name: str | None = None,
    scope: str = Scope.SHARED,
    user: User | None = None,
    actor: User | None,
    label: str = "",
) -> CredentialChange:
    """Store a new credential for ``server_id``.

    A shared one also writes the server's reference (state ``active``) into a
    new config version, which the writer pushes. A per-user one changes
    nothing pushed. Raises ``MCPServerNotFound`` for a customer server the
    active config does not hold, and ``MCPCredentialExists`` when the config
    references an active one already (rotate or revoke it instead). An active
    shared one the config no longer references (its server was removed by a
    generic save) is revoked first: nothing pushes it.

    ``placement`` and ``name`` put an api_key in a query parameter or a
    secret URL variable of the server's url (``new_secret``,
    ``check_placement``). The push that follows refuses either until the
    gateway applies it (AM-C20), and the write rolls back with it.
    """
    check_server_id(server_id)
    secret = new_secret(kind, value, header_name, placement=placement, name=name)
    if scope not in Scope.values or (scope == Scope.USER) != (user is not None):
        raise MCPCredentialError("a user credential names its user; a shared one none")
    if not isinstance(label, str) or len(label) > 255:
        raise MCPCredentialError("label must be a string of at most 255 characters")
    with transaction.atomic():
        if scope == Scope.USER:
            lock_identity(org.pk, user.pk, server_id)
        else:
            lock_org(org.pk)
        if scope == Scope.USER:
            mcp = _active_mcp(org.pk)
            _require_server(mcp, server_id)
            _refuse_signed_in_server(mcp, server_id)
            check_placement(secret, mcp, server_id, scope)
            change = CredentialChange(
                store(org, server_id, secret, actor=actor, user=user, label=label)
            )
            change.gateway_synced = evict_user(change.credential)
        else:
            created = []

            def updater(config):
                _require_server(config.mcp, server_id)
                _refuse_signed_in_server(config.mcp, server_id)
                check_placement(secret, config.mcp, server_id, scope)
                _revoke_unreferenced(org, config.mcp, server_id)
                # Bound to the URL its server has in the version that
                # references it (none for a fagi.* server).
                origin = origin_of(configured_url(config.mcp, server_id))
                created.append(
                    store(
                        org, server_id, secret, actor=actor, label=label, origin=origin
                    )
                )
                set_reference(config, server_id, created[0], Status.ACTIVE)

            with vault_write():
                _, warnings = write_config_version_with(
                    org,
                    updater,
                    description=f"Add MCP credential for {server_id}",
                    user=actor,
                )
            change = CredentialChange(created[0], is_gateway_synced(warnings))
    _log("mcp_credential_created", change.credential, actor)
    return change


def rotate(
    credential: AgentccMCPCredential,
    value: str,
    actor: User | None,
    *,
    header_name: str | None = None,
    placement: str | None = None,
    name: str | None = None,
) -> CredentialChange:
    """Replace an active credential's secret in place: same id, ``version``
    plus one, ``rotated_at`` now.

    The reference in the config does not change, so no version is written; the
    org's active config is pushed under the lock in the same transaction. Until
    that push lands the gateway keeps the previous secret, and a failed push
    (``gateway_synced`` False) leaves it there until the gateway's next sync.

    With none of ``header_name``, ``placement`` and ``name`` the credential
    keeps where it goes; with any of them it goes where they say, checked as
    ``create`` checks it (a bare ``placement="header"`` keeps the row's
    header).

    A rotation never moves a shared credential's origin (IC-R1-12): it keeps it
    while the active config sends the credential (to its server at that
    origin), and clears it otherwise, since nothing then says which server the
    new secret is for; it is sent nowhere until re-entered with its server.
    """
    with transaction.atomic():
        if credential.scope == Scope.USER:
            lock_identity(
                credential.organization_id, credential.user_id, credential.server_id
            )
        else:
            lock_org(credential.organization_id)
        row = _locked(credential)
        _refuse_oauth_client(row)
        if row.status != Status.ACTIVE:
            raise MCPCredentialNotActive("only an active credential can be rotated")
        kept = _decoded_placement(row)
        if header_name is None and placement is None and name is None:
            if kept is None:
                raise MCPCredentialError(
                    "where this credential goes cannot be read: revoke it and add it again"
                )
            placement, header_name, name = kept
        elif placement == HEADER and header_name is None and name is None:
            # A header placement that names no header keeps the row's own,
            # if it has one; a key placed elsewhere must name it.
            header_name = kept[1] if kept is not None and kept[0] == HEADER else None
        secret = new_secret(
            row.kind, value, header_name, placement=placement, name=name
        )
        check_placement(
            secret, _active_mcp(row.organization_id), row.server_id, row.scope
        )
        origin = row.origin
        if row.scope == Scope.SHARED and not pushes_credential(
            _active_mcp(row.organization_id), row
        ):
            origin = ""
        rotate_in_place(row, secret, origin=origin)
        if row.scope == Scope.SHARED:
            synced = push_active_config(row.organization_id)
            # The replaced secret's revision stops on every gateway replica.
            if evict_shared(row) is False:
                synced = False
        else:
            synced = evict_user(row)
    _log("mcp_credential_rotated", row, actor)
    return CredentialChange(row, synced)


def revoke(credential: AgentccMCPCredential, actor: User | None) -> CredentialChange:
    """Revoke a credential. Its config entry keeps ``mode`` and gets
    ``state: revoked``; the push no longer carries its secret."""
    return mark_lapsed(credential, Status.REVOKED, actor)


def mark_lapsed(
    credential: AgentccMCPCredential, state: str, actor: User | None = None
) -> CredentialChange:
    """Set an active credential to ``revoked``, ``expired`` or ``needs_reauth``
    (decision 27). A shared one's config entry keeps its ``mode`` and
    ``credential_id`` and takes the new ``state``."""
    if state not in (Status.REVOKED, Status.EXPIRED, Status.NEEDS_REAUTH):
        raise MCPCredentialError(
            "a credential lapses to revoked, expired or needs_reauth"
        )
    with transaction.atomic():
        if credential.scope == Scope.USER:
            lock_identity(
                credential.organization_id, credential.user_id, credential.server_id
            )
        else:
            lock_org(credential.organization_id)
        row = _locked(credential)
        _refuse_oauth_client(row)
        if (
            state == Status.REVOKED
            and row.scope == Scope.USER
            and row.kind == Kind.OAUTH
        ):
            raise MCPCredentialError(
                "Use the personal account Disconnect endpoint to revoke this OAuth account and finish cache cleanup."
            )
        if row.status != Status.ACTIVE:
            raise MCPCredentialNotActive(f"the credential is already {row.status}")
        row.status = state
        row.save(update_fields=["status"])
        if row.scope == Scope.SHARED:
            synced = _write_lapsed_state(row, state, actor)
            # As a personal credential is evicted: every gateway replica stops
            # the lapsed grant, including one whose config still holds it.
            if evict_shared(row) is False:
                synced = False
        else:
            synced = evict_user(row)
    _log("mcp_credential_lapsed", row, actor)
    return CredentialChange(row, synced)


def use_managed(org: Organization, server_id: str, actor: User | None) -> dict:
    """Move a ``fagi.*`` server from the org's own credential to managed
    billing: revoke its shared credential and remove its
    ``managed_tools.credentials`` entry, in a config version the actor
    writes. Refused unless the org has ``managed_tools.enabled``. Users' own
    per-user credentials for the server are theirs and stay (decision 27
    resolves a caller's own credential first)."""
    check_server_id(server_id)
    if not is_managed(server_id):
        raise MCPCredentialError("only a fagi.* server can move to managed billing")
    with transaction.atomic():
        lock_org(org.pk)
        revoked = []

        def updater(config):
            managed = _managed_tools(config.mcp)
            if managed is None or managed.get("enabled") is not True:
                raise ManagedToolsDisabled(
                    "managed tools are off for this org: turn on mcp.managed_tools.enabled first"
                )
            credentials = managed.get("credentials")
            if not isinstance(credentials, dict) or server_id not in credentials:
                raise MCPServerNotFound(
                    f"{server_id} has no customer credential recorded"
                )
            del credentials[server_id]
            revoked.extend(
                revoke_server_credentials(org, server_id, scope=Scope.SHARED)
            )

        with vault_write():
            config, warnings = write_config_version_with(
                org,
                updater,
                description=f"Use managed billing for {server_id}",
                user=actor,
            )
    logger.info(
        "mcp_credential_use_managed",
        org_id=str(org.pk),
        server_id=server_id,
        revoked=[str(pk) for pk in revoked],
        config_version=config.version,
        actor_id=str(actor.pk) if actor is not None else None,
    )
    return {
        "server_id": server_id,
        "version": config.version,
        "revoked": [str(pk) for pk in revoked],
        "gateway_synced": is_gateway_synced(warnings),
    }


def assemble_for_push(org_id) -> dict[str, dict[str, str]]:
    """``{server_id: push_form}`` for the org's active shared static
    credentials that the active config references (``{type, header, value}``,
    or ``{type, in, name, value}`` for a key placed in a query parameter or a
    URL variable); a ``fagi.*`` one keyed by its ``fagi.<id>``, the key of
    its ``managed_tools.credentials`` entry.

    A row is returned only when its server's entry in the active config
    (``mcp.servers.<id>.credential``, or ``managed_tools.credentials.<id>``)
    names it by ``credential_id`` with ``state: active``, so a push never
    carries a secret the stored config does not show (the writers save the
    version they push as the active one before pushing it, in the same
    transaction), and a customer server's only while its URL is at the origin
    the credential is bound to (``pushes_credential``, IC-R1-12).

    Called only by ``config_push`` while it builds a push payload (the M2
    hook), never at save time. A row that cannot be decrypted is left out and
    logged by id and error type, so one bad row does not drop the others.
    """
    mcp = _active_mcp(org_id)
    rows = AgentccMCPCredential.objects.filter(
        organization_id=org_id,
        scope=Scope.SHARED,
        status=Status.ACTIVE,
        kind__in=STATIC_KINDS,
    ).order_by("server_id")
    secrets = {}
    for row in rows:
        if is_shared_oauth(_server_entries(mcp).get(row.server_id)):
            # Its token is fetched when it is used (CN24): never pushed.
            if _references(mcp, row):
                logger.warning(
                    "mcp_credential_not_sent",
                    org_id=str(org_id),
                    credential_id=str(row.pk),
                    server_id=row.server_id,
                    reason="signs_in_over_oauth",
                )
            continue
        if row.token_expires_at is not None and timezone.now() >= row.token_expires_at:
            continue
        if not pushes_credential(mcp, row):
            if _references(mcp, row):
                # Referenced, but its server is not at the origin the secret
                # was entered for, or it is bound to none: held back.
                logger.warning(
                    "mcp_credential_not_sent",
                    org_id=str(org_id),
                    credential_id=str(row.pk),
                    server_id=row.server_id,
                    reason="origin",
                )
            continue
        placed = _decoded_placement(row)
        if placed is None:
            _log_unreadable(row, "UnknownPlacement")
            continue
        value = _decrypted(row)
        if value is not None:
            secrets[row.server_id] = _row_secret(row, value, placed).push_form()
    return secrets


def describe(credential: AgentccMCPCredential) -> dict:
    """What a read may show of a credential: never its value. ``in`` and
    ``name`` say where it goes; ``header_name`` is its header, or empty for
    a query parameter or a URL variable."""
    placement, header, name = _decoded_placement(credential) or ("", "", "")
    if placement == HEADER:
        name = header or "Authorization"
    return {
        "id": str(credential.pk),
        "server_id": credential.server_id,
        "scope": credential.scope,
        "kind": credential.kind,
        "header_name": header or "",
        "in": placement,
        "name": name or "",
        "status": credential.status,
        "version": credential.version,
        "label": credential.label,
        "created_at": credential.created_at,
        "rotated_at": credential.rotated_at,
        "last_used_at": credential.last_used_at,
        "has_value": True,
    }


def probe_auth(
    org: Organization, credential_id, user: User | None, url: str
) -> dict | None:
    """The inline ``auth`` a probe sends for one of the org's active
    credentials: a shared one, or the requesting user's own. None when the id
    names nothing of that (another org's credential included).

    A stored secret is probed only where the org's active config sends it,
    both read from one copy of that config:

    - a shared credential only while its server's entry references it with
      state ``active`` (``pushes_credential``, the rule the push follows), so
      one a save disconnected, or whose server is gone, is probed nowhere,
      even after its server id is added again; a per-user credential is not
      referenced by id and is checked by its URL alone;
    - and ``url`` must be the URL the active config gives the server
      (``same_upstream``). A ``fagi.*`` credential has no URL here (the
      managed catalog is the gateway's), so it is refused too.

    Raises ``MCPProbeURLMismatch``; nothing is decrypted then."""
    row = AgentccMCPCredential.objects.filter(
        pk=credential_id, organization=org, status=Status.ACTIVE, kind__in=STATIC_KINDS
    ).first()
    if row is None or (
        row.scope == Scope.USER and (user is None or row.user_id != user.pk)
    ):
        return None
    mcp = _active_mcp(org.pk)
    if row.scope == Scope.SHARED and not pushes_credential(mcp, row):
        raise MCPProbeURLMismatch(
            "this stored credential is not the one its server's active config "
            "sends, so it is probed nowhere; to test a URL, send the secret as auth"
        )
    configured = configured_url(mcp, row.server_id)
    if configured is None or not same_upstream(url, configured):
        raise MCPProbeURLMismatch(
            "a stored credential is probed only against the URL its server is "
            "configured with; to test another URL, send the secret as auth"
        )
    placed = _decoded_placement(row)
    if placed is None or placed[0] != HEADER:
        raise MCPProbePlacementUnsupported(
            "this stored credential goes in a query parameter or a URL "
            "variable, and the probe sends a credential in a header only, so "
            "it is not probed: probe the server without it"
        )
    value = _decrypted(row)
    if value is None:
        return None
    return _row_secret(row, value, placed).push_form()


def pushes_credential(mcp, credential: AgentccMCPCredential) -> bool:
    """Whether the mcp section ``mcp`` sends ``credential``: its server's
    entry (``mcp.servers.<id>.credential``, or
    ``managed_tools.credentials.<fagi.id>``) names it by ``credential_id``
    with ``state: active``, and a customer server's URL is at the origin the
    credential is bound to (``at_bound_origin``, IC-R1-12). The only case in
    which a push carries its secret, and so the only one in which a probe may
    send it."""
    return _references(mcp, credential) and at_bound_origin(mcp, credential)


def at_bound_origin(mcp, credential: AgentccMCPCredential) -> bool:
    """Whether ``credential``'s server is, in the mcp section ``mcp``, at the
    origin the credential is bound to. Always for a ``fagi.*`` server (the
    gateway's catalog holds its URL); never for a credential bound to none."""
    if is_managed(credential.server_id):
        return True
    return bool(credential.origin) and credential.origin == origin_of(
        configured_url(mcp, credential.server_id)
    )


def credentials_sent_elsewhere(mcp, org_id) -> list[str]:
    """References an activation cannot use without silently dropping a grant.

    Called under the org-write lock. Every named credential must still be an
    active shared grant of this org and server, at its bound origin. Looking
    up active rows alone and skipping missing ones would let a rollback
    resurrect a reference revoked by removal (W4) with no secret in its push.
    Validate OAuth references too, although their tokens are fetched on use.
    Managed slots retain their catalog-owned origin rule; servers with no
    reference, including per-user sign-in configurations, are unaffected.
    """
    references = [
        (server_id, entry.get("credential"), f"mcp.servers.{server_id}.url")
        for server_id, entry in _server_entries(mcp).items()
    ] + [
        (server_id, reference, f"mcp.managed_tools.credentials.{server_id}")
        for server_id, reference in _managed_entries(mcp).items()
    ]
    paths = []
    for server_id, reference, path in references:
        if not isinstance(reference, dict):
            continue
        reference_id = _reference_id(reference)
        if reference_id is None:
            continue
        try:
            pk = uuid.UUID(str(reference_id))
        except (TypeError, ValueError, AttributeError):
            paths.append(path)
            continue
        oauth = reference.get("kind") == "oauth"
        row = (
            AgentccMCPCredential.objects.filter(
                pk=pk,
                organization_id=org_id,
                server_id=server_id,
                scope=Scope.SHARED,
                status=Status.ACTIVE,
                kind__in=(Kind.OAUTH,) if oauth else STATIC_KINDS,
            )
            .only("pk", "server_id", "origin", "token_expires_at")
            .first()
        )
        if (
            row is None
            or reference.get("mode") != "shared"
            or reference.get("state", Status.ACTIVE if oauth else None) != Status.ACTIVE
            or not at_bound_origin(mcp, row)
            or (
                not oauth
                and row.token_expires_at is not None
                and timezone.now() >= row.token_expires_at
            )
        ):
            paths.append(path)
    return sorted(paths)


def origin_of(url) -> str:
    """The origin a credential is bound to for ``url``: ``scheme://host:port``,
    scheme and host in lower case and the default port filled in (an IPv6 host
    in brackets); "" for a URL that does not parse, which binds nothing."""
    key = _upstream_key(url)
    if key is None or key[2] is None:
        return ""
    scheme, host, port = key[:3]
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def configured_url(mcp, server_id: str) -> str | None:
    """The URL the mcp section ``mcp`` gives customer server ``server_id``;
    None for a ``fagi.*`` server or a server the section does not hold."""
    if is_managed(server_id):
        return None
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    entry = servers.get(server_id) if isinstance(servers, dict) else None
    url = entry.get("url") if isinstance(entry, dict) else None
    return url if isinstance(url, str) and url else None


def same_upstream(url: str, configured: str) -> bool:
    """Whether ``url`` names the same upstream as ``configured``: scheme,
    host, port (default ports filled in), path (a trailing slash ignored) and
    query all equal, case-insensitive in scheme and host."""
    left, right = _upstream_key(url), _upstream_key(configured)
    return left is not None and left == right


def moves_origin(stored_url, new_url) -> bool:
    """Whether a save moves a server from ``stored_url`` to another origin:
    scheme, host or port (default ports filled in, scheme and host compared
    case-insensitively) differ. A path or query change is not a move. A URL
    whose origin cannot be read (it does not parse, or names no scheme or
    host) names no origin, so a change to or from one is a move, as on the
    release line (NG-01's origin rule); a URL left exactly as stored is not."""
    if stored_url == new_url:
        return False
    old, new = _upstream_key(stored_url), _upstream_key(new_url)
    return old is None or new is None or old[:3] != new[:3]


def _upstream_key(url):
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return None
    return (
        scheme,
        host,
        port or _DEFAULT_PORTS.get(scheme),
        parts.path.rstrip("/"),
        parts.query,
    )


def credentials_for_org(org: Organization, server_id: str | None = None, *, user=None):
    """Shared metadata plus only this caller's personal metadata. A service
    credential has no personal projection. Encrypted fields are not loaded."""
    rows = (
        AgentccMCPCredential.objects.filter(organization=org)
        .filter(
            Q(scope=Scope.SHARED) | Q(scope=Scope.USER, user=user, user__isnull=False)
        )
        .exclude(kind=Kind.OAUTH_CLIENT)
        .defer("encrypted_secret", "encrypted_refresh")
    )
    if server_id:
        rows = rows.filter(server_id=server_id)
    return list(rows.order_by("server_id", "-created_at"))


# --- Org OAuth clients (CN11) ----------------------------------------------------
# A function here that holds a client secret in clear returns and never raises,
# and its caller raises outside any handler: the app's error reports carry
# every frame's local variables (connector_oauth_clients' module docstring).


class SealedSecret:
    """An OAuth client secret on its way into the vault: never in a repr, a
    str, a copy or a pickle, so a frame or a serialiser that holds one shows
    ``<redacted>``."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        raise TypeError("an OAuth client secret is not serialisable")


def create_oauth_client(
    org: Organization,
    *,
    client_group: str | None = None,
    server_id: str | None = None,
    client_id: str,
    client_secret: SealedSecret,
    actor: User | None,
    label: str = "",
) -> AgentccMCPCredential:
    """Store the org's own OAuth client for a client group, or for one of
    the org's MCP servers, as an active ``oauth_client`` row. Nothing is
    pushed. Raises ``OrgOAuthClientInvalid`` for a bad key, client id or
    secret, ``MCPServerNotFound`` for a server the active config does not
    hold, and ``OrgOAuthClientExists`` when the key has an active client
    (rotate or revoke it)."""
    key = _oauth_client_key(client_group, server_id)
    problem = (
        _client_id_problem(client_id)
        or _sealed_problem(client_secret)
        or _label_problem(label)
    )
    if problem is not None:
        raise OrgOAuthClientInvalid(problem)
    with transaction.atomic():
        lock_org(org.pk)
        if server_id is not None:
            _require_server(_active_mcp(org.pk), server_id)
        if _active_oauth_client(org.pk, key).exists():
            raise OrgOAuthClientExists(
                "an active OAuth client is stored for this key: rotate or revoke it"
            )
        encrypted, error_type = _sealed_ciphertext(client_secret)
        if encrypted is None:
            # A server fault, not the request's: fail closed, as a 500.
            raise RuntimeError(
                f"the client secret could not be encrypted ({error_type})"
            )
        row = AgentccMCPCredential.objects.create(
            organization=org,
            server_id=key,
            scope=Scope.SHARED,
            kind=Kind.OAUTH_CLIENT,
            oauth_client_id=client_id,
            encrypted_secret=encrypted,
            label=label,
            created_by=actor,
        )
    _log_oauth_client("connector_oauth_client_created", row, actor)
    return row


def rotate_oauth_client(
    credential: AgentccMCPCredential, client_secret: SealedSecret, actor: User | None
) -> AgentccMCPCredential:
    """Replace an active org client's secret in place: same id and client id,
    ``version`` plus one. A new client id is a new client: revoke this one
    and create it."""
    problem = _sealed_problem(client_secret)
    if problem is not None:
        raise OrgOAuthClientInvalid(problem)
    with transaction.atomic():
        lock_org(credential.organization_id)
        row = _locked_oauth_client(credential)
        if row.status != Status.ACTIVE:
            raise MCPCredentialNotActive("only an active OAuth client can be rotated")
        encrypted, error_type = _sealed_ciphertext(client_secret)
        if encrypted is None:
            # A server fault, not the request's: fail closed, as a 500.
            raise RuntimeError(
                f"the client secret could not be encrypted ({error_type})"
            )
        row.encrypted_secret = encrypted
        row.version += 1
        row.rotated_at = timezone.now()
        row.save(update_fields=["encrypted_secret", "version", "rotated_at"])
    _log_oauth_client("connector_oauth_client_rotated", row, actor)
    return row


def revoke_oauth_client(
    credential: AgentccMCPCredential, actor: User | None
) -> AgentccMCPCredential:
    """Revoke an active org client: the next connect resolves the next
    source (CN3's order)."""
    with transaction.atomic():
        lock_org(credential.organization_id)
        row = _locked_oauth_client(credential)
        if row.status != Status.ACTIVE:
            raise MCPCredentialNotActive(f"the OAuth client is already {row.status}")
        row.status = Status.REVOKED
        row.save(update_fields=["status"])
    _log_oauth_client("connector_oauth_client_revoked", row, actor, reason="revoked")
    return row


def oauth_clients_for_org(org: Organization) -> list:
    """The org's OAuth clients by key, newest first; secrets not loaded."""
    return list(
        AgentccMCPCredential.objects.filter(organization=org, kind=Kind.OAUTH_CLIENT)
        .defer("encrypted_secret", "encrypted_refresh")
        .order_by("server_id", "-created_at")
    )


def oauth_client_for_org(
    org: Organization, credential_id
) -> AgentccMCPCredential | None:
    """One of the org's OAuth clients, or None (another org's included)."""
    return (
        AgentccMCPCredential.objects.filter(
            pk=credential_id, organization=org, kind=Kind.OAUTH_CLIENT
        )
        .defer("encrypted_secret", "encrypted_refresh")
        .first()
    )


def describe_oauth_client(credential: AgentccMCPCredential) -> dict:
    """What an admin may read of an org client: its key and client id, never
    its secret."""
    key = credential.server_id
    group_prefix = connector_oauth_clients.GROUP_KEY_PREFIX
    server_prefix = connector_oauth_clients.SERVER_KEY_PREFIX
    return {
        "id": str(credential.pk),
        "client_group": key[len(group_prefix) :]
        if key.startswith(group_prefix)
        else None,
        "server_id": key[len(server_prefix) :]
        if key.startswith(server_prefix)
        else None,
        "client_id": credential.oauth_client_id,
        "label": credential.label,
        "status": credential.status,
        "version": credential.version,
        "created_at": credential.created_at,
        "rotated_at": credential.rotated_at,
        "has_secret": True,
    }


def _oauth_client_key(client_group, server_id) -> str:
    """The vault key for an org client (CN3's ``org_client_key``), for a
    client group or for one of the org's own MCP servers."""
    if server_id is not None and (
        not isinstance(server_id, str)
        or not SERVER_ID.fullmatch(server_id)
        or is_managed(server_id)
    ):
        raise OrgOAuthClientInvalid(
            "server_id must be one of the org's MCP server ids, never a managed "
            f"(fagi.*) one, and match {SERVER_ID.pattern}"
        )
    try:
        return connector_oauth_clients.org_client_key(
            client_group=client_group, server_id=server_id
        )
    except connector_oauth_clients.ConnectorOAuthClientError as e:
        raise OrgOAuthClientInvalid(str(e)) from None


def _client_id_problem(client_id) -> str | None:
    if _plain_text(client_id, URL_MAX_LENGTH):
        return None
    return (
        f"client_id must be a non-empty string of at most {URL_MAX_LENGTH} "
        "characters, with no surrounding spaces or control characters"
    )


def _sealed_problem(sealed) -> str | None:
    """Why a sealed client secret is refused, or None. It reads the
    plaintext, so it returns and never raises."""
    if isinstance(sealed, SealedSecret) and _plain_text(
        sealed._value, MAX_SECRET_CHARS
    ):
        return None
    return (
        "client_secret must be a non-empty string of at most "
        f"{MAX_SECRET_CHARS} characters, with no surrounding spaces, control "
        f"characters or {ENCRYPTED_PREFIX} prefix"
    )


def _label_problem(label) -> str | None:
    if isinstance(label, str) and len(label) <= 255:
        return None
    return "label must be a string of at most 255 characters"


def _plain_text(value, max_chars: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
        and len(value) <= max_chars
        and not value.startswith(ENCRYPTED_PREFIX)
    )


def _sealed_ciphertext(sealed: SealedSecret) -> tuple[str | None, str]:
    """``(ciphertext, "")``, or ``(None, error type)``. Any error is kept by
    type only and never raised from here or chained, so no report carries
    this frame or encrypt_token's, where the plaintext is a local."""
    try:
        return encrypt_token(sealed._value), ""
    except Exception as e:  # any type: the caller refuses the write (500)
        return None, type(e).__name__


def _active_oauth_client(org_id, key: str):
    return AgentccMCPCredential.objects.filter(
        organization_id=org_id,
        server_id=key,
        kind=Kind.OAUTH_CLIENT,
        scope=Scope.SHARED,
        user__isnull=True,
        account_label="",
        status=Status.ACTIVE,
    )


def _locked_oauth_client(credential: AgentccMCPCredential) -> AgentccMCPCredential:
    row = _locked(credential)
    if row.kind != Kind.OAUTH_CLIENT:
        raise MCPCredentialError("this credential is not an org OAuth client")
    return row


def _revoke_server_clients(org: Organization, server_ids, reason: str) -> list:
    """Revoke the active org client keyed to each of ``server_ids``; returns
    their ids. An id no client can be keyed to (a managed one, or one a
    forged config holds) has none. The caller holds the lock, and each
    revocation is logged once its transaction commits."""
    keys = [
        connector_oauth_clients.org_client_key(server_id=server_id)
        for server_id in server_ids
        if isinstance(server_id, str)
        and SERVER_ID.fullmatch(server_id)
        and not is_managed(server_id)
    ]
    if not keys:
        return []
    rows = list(
        AgentccMCPCredential.objects.filter(
            organization=org,
            server_id__in=keys,
            kind=Kind.OAUTH_CLIENT,
            status=Status.ACTIVE,
        ).only("pk", "organization_id", "server_id", "status", "version")
    )
    if not rows:
        return []
    AgentccMCPCredential.objects.filter(pk__in=[row.pk for row in rows]).update(
        status=Status.REVOKED
    )
    for row in rows:
        row.status = Status.REVOKED
        transaction.on_commit(
            partial(
                _log_oauth_client,
                "connector_oauth_client_revoked",
                row,
                None,
                reason=reason,
            )
        )
    return [row.pk for row in rows]


def _log_oauth_client(event: str, row, actor, **extra) -> None:
    logger.info(
        event,
        org_id=str(row.organization_id),
        credential_id=str(row.pk),
        key=row.server_id,
        status=row.status,
        version=row.version,
        actor_id=str(actor.pk) if actor is not None else None,
        **extra,
    )


# --- Building blocks for writers that already hold the org lock -------------
# The console's update-mcp-server and remove-mcp-server updaters and the secret
# migration call these inside a config write, so the credential row and the
# config version commit or roll back together.


def store(
    org: Organization,
    server_id: str,
    secret: Secret,
    *,
    actor: User | None,
    user: User | None = None,
    label: str = "",
    status: str = Status.ACTIVE,
    origin: str = "",
) -> AgentccMCPCredential:
    """Insert one credential row, bound to ``origin`` (``origin_of`` its
    server's URL; "" binds it to none). The caller holds the org lock."""
    scope = Scope.USER if user is not None else Scope.SHARED
    if status == Status.ACTIVE and _active(org.pk, server_id, scope, user).exists():
        raise MCPCredentialExists(
            f"{server_id} already has an active credential: rotate or revoke it"
        )
    return AgentccMCPCredential.objects.create(
        grant_revision=next_revision(org.pk, server_id) if scope == Scope.SHARED else 0,
        organization=org,
        server_id=server_id,
        scope=scope,
        user=user,
        kind=secret.kind,
        header_name=secret.stored_header_name,
        encrypted_secret=encrypt_token(secret.value),
        status=status,
        label=label,
        origin=origin,
        created_by=actor,
    )


def rotate_in_place(row: AgentccMCPCredential, secret: Secret, *, origin: str) -> None:
    """Give a locked row a new secret, bound to ``origin``: ``version`` plus
    one, ``rotated_at`` now, and for a shared one its slot's next grant
    revision. The caller evicts the replaced one (``evict_shared``)."""
    row.kind = secret.kind
    row.header_name = secret.stored_header_name
    row.encrypted_secret = encrypt_token(secret.value)
    row.origin = origin
    row.version += 1
    row.rotated_at = timezone.now()
    if row.scope == Scope.SHARED:
        row.grant_revision = next_revision(row.organization_id, row.server_id)
    row.save(
        update_fields=[
            "kind",
            "header_name",
            "encrypted_secret",
            "origin",
            "version",
            "rotated_at",
            "grant_revision",
        ]
    )


def put_shared_secret(
    org: Organization,
    server_id: str,
    secret: Secret,
    *,
    actor: User | None,
    origin: str,
) -> AgentccMCPCredential:
    """The org's active shared credential for ``server_id`` with ``secret``,
    bound to ``origin``: rotated in place when one exists, else created. The
    caller holds the lock."""
    row = _active(org.pk, server_id, Scope.SHARED, None).select_for_update().first()
    if row is None:
        return store(org, server_id, secret, actor=actor, origin=origin)
    rotate_in_place(row, secret, origin=origin)
    # Inside a config write: the replaced revision is evicted once it commits.
    evict_shared_after_commit([row])
    return row


def active_shared(org_id, server_id: str) -> AgentccMCPCredential | None:
    return _active(org_id, server_id, Scope.SHARED, None).first()


def revoke_server_credentials(
    org: Organization, server_id: str, *, scope: str | None = None
) -> list:
    """Revoke every active credential of ``server_id`` (shared and per-user,
    or only ``scope``'s); returns their ids. The caller holds the lock and
    edits the config. Without ``scope`` (the server is removed) the org
    OAuth client added for the server goes too; a group's client stays."""
    rows = AgentccMCPCredential.objects.filter(
        organization=org, server_id=server_id, status=Status.ACTIVE
    )
    if scope is not None:
        rows = rows.filter(scope=scope)
    ids = list(rows.values_list("pk", flat=True))
    shared_static = list(rows.filter(scope=Scope.SHARED, kind__in=STATIC_KINDS))
    from agentcc.models.mcp_upstream_oauth import AgentccMCPConnectState
    from agentcc.services.mcp_connection_cleanup import mark_revoked

    # OAuth cleanup cannot be hidden by a status-only bulk update. Record it
    # before removing the issuing app; the person's list retains these rows.
    oauth_rows = (
        AgentccMCPCredential.objects.select_for_update()
        .filter(
            organization=org,
            server_id=server_id,
            kind=Kind.OAUTH,
        )
        .exclude(status=Status.REVOKED)
    )
    if scope is not None:
        oauth_rows = oauth_rows.filter(scope=scope)
    for row in oauth_rows:
        mark_revoked(row, trigger="server_removed", record=True)
        if row.pk not in ids:
            ids.append(row.pk)
    states = AgentccMCPConnectState.objects.filter(
        organization=org, server_id=server_id
    )
    if scope is not None:
        states = states.filter(mode=scope)
    states.delete()
    rows.update(status=Status.REVOKED)
    # Every gateway replica stops these grants once the config write commits.
    evict_shared_after_commit(shared_static)
    if scope is None and not is_managed(server_id):
        ids.extend(_revoke_server_clients(org, [server_id], "server_removed"))
    return ids


def revoke_server_clients_left_behind(org: Organization, value, stored) -> list:
    """Revoke the org OAuth client of each server a config write removes,
    moves to another origin (``moves_origin``) or adds (a client keyed to an
    id no stored server holds was left by a write that removed it), from the
    mcp section ``stored`` to ``value``; returns their ids. A server's client
    never follows it to another origin (IC-R1-9). The caller holds the lock
    and writes the config in the same transaction.

    update-mcp-server and remove-mcp-server call this rule through
    ``console_server_entry`` and ``revoke_server_credentials``; the generic
    writers (``org_config_writer.write_config_version_with``, which
    update-config and POST org-configs/ use, and ``activate_config_version``)
    call it for every write (R3)."""
    old, new = _server_entries(stored), _server_entries(value)
    removed = sorted(set(old) - set(new))
    moved = sorted(
        server_id
        for server_id in set(old) & set(new)
        if moves_origin(old[server_id].get("url"), new[server_id].get("url"))
    )
    added = sorted(set(new) - set(old))
    revoked = (
        _revoke_server_clients(org, removed, "server_removed")
        + _revoke_server_clients(org, moved, "server_moved")
        + _revoke_server_clients(org, added, "server_added")
    )
    return [str(pk) for pk in revoked]


def console_server_entry(
    org: Organization, server_id: str, sent: dict, stored, *, actor: User | None
) -> dict:
    """The ``mcp.servers.<server_id>`` entry the console's update-mcp-server
    writes, inside its config write (the caller holds the org lock).

    A new ``auth.token`` or ``auth.key`` is stored in the vault (rotating the
    server's active shared credential in place, or creating one), bound to the
    origin of the url this save gives the server (IC-R1-12), and only its
    reference ``credential {mode: shared, credential_id, state: active}`` is
    written. ``auth: {type: none}`` revokes the shared credential and writes
    ``credential {mode: none}``. With no new value the stored reference and
    transport are kept, so the console can leave the secret blank to keep it;
    a stored clear-text ``auth`` secret then moves into the vault. Keeping
    does not follow an origin change: a blank secret with a url of another
    scheme, host or port than the stored one, while a stored credential would
    be sent, is refused (MCPCredentialOriginChanged, M14 IR-9); a path or
    query change keeps it. A ``credential.secret`` in the request, or a
    ``credential_id`` or ``state`` that differs from the stored one, is
    refused: the vault owns them.

    The org OAuth client added for this server never follows it: a save
    that moves the server to another origin, or adds it while a client
    keyed to its id is left from before, revokes that client in this write
    (``revoke_server_clients_left_behind``'s rule).
    """
    entry = copy.deepcopy(sent)
    stored = stored if isinstance(stored, dict) else {}
    if not stored:
        _revoke_server_clients(org, [server_id], "server_added")
    elif moves_origin(stored.get("url"), entry.get("url")):
        _revoke_server_clients(org, [server_id], "server_moved")
    held = (
        stored.get("credential") if isinstance(stored.get("credential"), dict) else {}
    )
    held = {key: value for key, value in held.items() if key != "secret"}
    credential = _sent_credential(entry.get("credential"), held)
    new = _sent_secret(entry.pop("auth", None))
    if new is not None and new is not _REMOVE and credential.get("kind") == "oauth":
        raise MCPServerSignsInOverOAuth(
            f"mcp.servers.{server_id}: its account is signed in over OAuth by "
            "an Owner or Admin, so it takes no stored key"
        )
    if "transport" not in entry and "transport" in stored:
        entry["transport"] = stored["transport"]
    if new is _REMOVE:
        revoke_server_credentials(org, server_id, scope=Scope.SHARED)
        for key in ("credential_id", "state", "kind"):
            credential.pop(key, None)
        entry["credential"] = {**credential, "mode": "none"}
        return entry
    if new is None:
        shared = active_shared(org.pk, server_id)
        new = _legacy_secret_as_pushed(server_id, stored) if shared is None else None
        # Kept: the legacy secret this save moves into the vault, or the
        # stored reference to the active credential the push sends.
        keeps = new is not None or (
            shared is not None
            and _reference_id(credential) == str(shared.pk)
            and credential.get("state") == Status.ACTIVE
        )
        if keeps and moves_origin(stored.get("url"), entry.get("url")):
            raise MCPCredentialOriginChanged(
                f"mcp.servers.{server_id}.url {ORIGIN_CHANGED_MESSAGE}."
            )
    if new is not None:
        row = put_shared_secret(
            org, server_id, new, actor=actor, origin=origin_of(entry.get("url"))
        )
        credential.update(_reference(row, Status.ACTIVE))
    if credential:
        entry["credential"] = credential
    return entry


def set_reference(config, server_id: str, credential, state: str) -> None:
    """Point ``server_id``'s config entry at ``credential`` with ``state``,
    dropping any legacy inline ``auth`` and any ``secret`` there."""
    mcp = config.mcp if isinstance(config.mcp, dict) else {}
    reference = _reference(credential, state)
    if is_managed(server_id):
        managed = _managed_tools(mcp, create=True)
        credentials = managed.get("credentials")
        if not isinstance(credentials, dict):
            credentials = managed["credentials"] = {}
        credentials[server_id] = reference
    else:
        entry = mcp["servers"][server_id]
        entry.pop("auth", None)
        current = entry.get("credential")
        current = current if isinstance(current, dict) else {}
        current.pop("secret", None)
        entry["credential"] = {**current, **reference}
    config.mcp = mcp


def config_secrets(mcp) -> dict[str, object]:
    """Every push-only secret an mcp section holds, by path: a server's
    legacy ``auth.token`` and ``auth.key``, its ``credential.secret``, and a
    managed server's ``managed_tools.credentials.<id>.secret`` (under each
    spelling the contract reads). Empty values count as none."""
    found = {}
    if not isinstance(mcp, dict):
        return found
    servers = mcp.get("servers")
    for server_id, entry in (servers if isinstance(servers, dict) else {}).items():
        if not isinstance(entry, dict):
            continue
        auth = entry.get("auth")
        for key in ("token", "key"):
            if isinstance(auth, dict) and auth.get(key) not in (None, ""):
                found[f"mcp.servers.{server_id}.auth.{key}"] = auth[key]
        credential = entry.get("credential")
        if isinstance(credential, dict) and credential.get("secret") not in (None, {}):
            found[f"mcp.servers.{server_id}.credential.secret"] = credential["secret"]
    for key in _MANAGED_TOOLS_KEYS:
        managed = mcp.get(key)
        credentials = managed.get("credentials") if isinstance(managed, dict) else None
        for managed_id, entry in (
            credentials if isinstance(credentials, dict) else {}
        ).items():
            if isinstance(entry, dict) and entry.get("secret") not in (None, {}):
                found[f"mcp.{key}.credentials.{managed_id}.secret"] = entry["secret"]
    return found


@contextmanager
def vault_write():
    """Mark the config writes inside as the vault's own, so
    ``vault_owned_changes`` lets them set or change credential references.
    Wrap the whole ``write_config_version_with`` call: the writer reruns the
    MCP rules after the updater returns."""
    token = _VAULT_WRITE.set(True)
    try:
        yield
    finally:
        _VAULT_WRITE.reset(token)


def vault_owned_changes(value, stored) -> list[str]:
    """The paths where a generic config write changes what the credential
    vault owns, against the locked stored version; empty inside
    ``vault_write``.

    - A ``managed_tools.credentials.<fagi.id>`` entry the vault references
      (it has a ``credential_id``) may not be removed or have its
      ``credential_id``, ``mode`` or ``state`` changed: only use-managed
      moves a managed server to managed billing (decision 27). A legacy entry
      with no ``credential_id`` is the secret migration's.
    - A server's ``credential.credential_id`` and ``credential.state``, and
      those of a managed entry with no ``credential_id``, may not be set or
      changed; one a write leaves as the stored version has it passes, and
      dropping a server's reference (or the server) is allowed and pushes
      nothing for it.
    """
    if _VAULT_WRITE.get():
        return []
    changed = []
    old_servers, new_servers = _server_entries(stored), _server_entries(value)
    for server_id, entry in new_servers.items():
        if _sets_owned_pair(
            entry.get("credential"),
            (old_servers.get(server_id) or {}).get("credential"),
        ):
            changed.append(f"mcp.servers.{server_id}.credential")
    old_managed, new_managed = _managed_entries(stored), _managed_entries(value)
    for key, old in old_managed.items():
        if _reference_id(old) is None:
            continue
        new = new_managed.get(key)
        if (
            not isinstance(new, dict)
            or _owned_pair(new) != _owned_pair(old)
            or new.get("mode") != old.get("mode")
        ):
            changed.append(f"mcp.managed_tools.credentials.{key}")
    for key, new in new_managed.items():
        old = old_managed.get(key)
        if old is not None and _reference_id(old) is not None:
            continue  # compared in full above
        if _sets_owned_pair(new, old):
            changed.append(f"mcp.managed_tools.credentials.{key}")
    return sorted(set(changed))


def credential_origin_moves(value, stored) -> list[str]:
    """The ``mcp.servers.<id>.url`` paths where a generic config write moves
    a server to another origin (``moves_origin``) while it keeps a credential
    the push would send there: the stored entry's active reference
    (``credential_id`` with ``state: active``), left as stored, or a legacy
    clear-text secret the stored entry holds, left in place (M14 IR-9,
    IC-R1-9). A write that moves the server and drops its reference passes.
    Empty inside ``vault_write``: the console checks its own saves
    (``console_server_entry``), where a re-entered secret rotates the
    credential in place and keeps its id."""
    if _VAULT_WRITE.get():
        return []
    moved = []
    old_servers, new_servers = _server_entries(stored), _server_entries(value)
    for server_id, entry in new_servers.items():
        old = old_servers.get(server_id)
        if old is None or not moves_origin(old.get("url"), entry.get("url")):
            continue
        old_pair = _owned_pair(old.get("credential"))
        keeps_reference = (
            old_pair[0] is not None
            and old_pair[1] == Status.ACTIVE
            and _owned_pair(entry.get("credential")) == old_pair
        )
        held = _legacy_secret_as_pushed(server_id, old)
        if keeps_reference or (
            held is not None and _legacy_secret_as_pushed(server_id, entry) == held
        ):
            moved.append(f"mcp.servers.{server_id}.url")
    return sorted(moved)


def _sets_owned_pair(new, old) -> bool:
    """Whether reference ``new`` holds a (credential_id, state) that the
    stored reference ``old`` does not: a set or changed pair. Dropping both
    sets nothing."""
    pair = _owned_pair(new)
    return pair != (None, None) and pair != _owned_pair(old)


def _owned_pair(reference):
    """A reference's vault-owned (credential_id, state)."""
    if not isinstance(reference, dict):
        return (None, None)
    return (_reference_id(reference), reference.get("state"))


def _server_entries(mcp) -> dict:
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    return {
        server_id: entry
        for server_id, entry in (servers if isinstance(servers, dict) else {}).items()
        if isinstance(entry, dict)
    }


def _managed_entries(mcp) -> dict:
    """managed_tools.credentials entries by key, under the spelling the
    section uses."""
    managed = _managed_tools(mcp)
    credentials = managed.get("credentials") if managed else None
    return {
        key: entry
        for key, entry in (credentials if isinstance(credentials, dict) else {}).items()
        if isinstance(entry, dict)
    }


def legacy_secret(entry) -> Secret | None:
    """The secret a stored server entry holds in clear and the gateway used:
    ``auth`` bearer with a token, ``auth`` api_key with a key and a header, or
    a ``credential.secret``. None when it holds none the gateway would send."""
    if not isinstance(entry, dict):
        return None
    credential = entry.get("credential")
    secret = credential.get("secret") if isinstance(credential, dict) else None
    if isinstance(secret, dict):
        return _usable(secret.get("type"), secret.get("value"), secret.get("header"))
    auth = entry.get("auth")
    if not isinstance(auth, dict):
        return None
    if auth.get("type") == Kind.BEARER:
        return _usable(Kind.BEARER, auth.get("token"), None)
    if auth.get("type") == Kind.API_KEY:
        return _usable(Kind.API_KEY, auth.get("key"), auth.get("header"))
    return None


def _legacy_secret_as_pushed(server_id: str, entry) -> Secret | None:
    """``legacy_secret`` of a stored server entry as the push sends it: an
    NG-01 ``enc::`` leaf (one a pod on the release code wrote during the
    rollout, before migration 0056 or ``agentcc_mcp_migrate_secrets --apply``
    moved it) decrypted, exactly once (``org_config_secrets.clear_mcp_secrets``),
    and one this deployment cannot decrypt read as none, since the push drops
    it. ``legacy_secret`` itself reads a leaf literally: its other callers read
    entries that are already clear, and a second decryption would turn a saved
    value that is itself some field's ciphertext into that field's secret."""
    if not isinstance(entry, dict):
        return None
    clear = org_config_secrets.clear_mcp_secrets({"servers": {server_id: entry}})
    return legacy_secret(clear["servers"][server_id])


def push_active_config(org_id) -> bool | None:
    """Push the org's active config; the caller holds the org lock. None when
    the org has no active config (its first push carries the secrets)."""
    config = AgentccOrgConfig.no_workspace_objects.filter(
        organization_id=org_id, is_active=True
    ).first()
    if config is None:
        return None
    return config_push.push_org_config(str(org_id), config)


# update-mcp-server's explicit "no credential" (auth: {type: none}).
_REMOVE = object()


def _sent_secret(auth):
    """A new secret from a request's legacy-shaped ``auth``: a Secret, _REMOVE
    for ``{type: none}``, or None when it sends no value (keep the stored one)."""
    if not isinstance(auth, dict):
        return None
    value = auth.get("token") or auth.get("key")
    if not value:
        return _REMOVE if auth.get("type") == "none" else None
    kind = auth.get("type")
    if kind == Kind.BEARER and auth.get("key") and not auth.get("token"):
        raise MCPCredentialError("a bearer credential sends auth.token")
    if kind == Kind.API_KEY and auth.get("token") and not auth.get("key"):
        raise MCPCredentialError("an api_key credential sends auth.key")
    return new_secret(kind, value, auth.get("header"))


def _sent_credential(sent, held: dict) -> dict:
    """The request's ``credential`` over the stored one. It may not carry a
    secret, or a vault-owned ``credential_id`` or ``state`` unlike the stored."""
    if sent is None:
        return dict(held)
    if not isinstance(sent, dict):
        raise MCPCredentialError("credential must be a JSON object")
    if sent.get("secret") is not None:
        raise SecretInRequest(
            "send a new credential as auth.token or auth.key; credential.secret "
            "is never stored"
        )
    for key in ("credential_id", "state"):
        if key in sent and sent[key] != held.get(key):
            raise MCPCredentialError(f"credential.{key} is set by the credential vault")
    return {**held, **{k: v for k, v in sent.items() if k != "secret"}}


def _reference(credential, state: str) -> dict:
    return {"mode": "shared", "credential_id": str(credential.pk), "state": state}


def _usable(kind, value, header) -> Secret | None:
    try:
        return new_secret(kind, value, header)
    except MCPCredentialError:
        return None


def managed_tools_sections(mcp) -> list[dict]:
    """mcp.managed_tools under each spelling the contract reads that is an
    object, managed_tools first."""
    if not isinstance(mcp, dict):
        return []
    return [mcp[key] for key in _MANAGED_TOOLS_KEYS if isinstance(mcp.get(key), dict)]


def _managed_tools(mcp, *, create=False) -> dict | None:
    """mcp.managed_tools under the spelling the section uses (managed_tools
    when creating it)."""
    if not isinstance(mcp, dict):
        return None
    for key in _MANAGED_TOOLS_KEYS:
        if isinstance(mcp.get(key), dict):
            return mcp[key]
    if not create:
        return None
    mcp["managed_tools"] = {}
    return mcp["managed_tools"]


def is_shared_oauth(entry) -> bool:
    """Whether a server's config entry is a shared server whose one account
    is signed in over OAuth (``credential {mode: shared, kind: oauth}``,
    CN24): its token is fetched when it is used, never stored by hand or
    pushed."""
    credential = entry.get("credential") if isinstance(entry, dict) else None
    return (
        isinstance(credential, dict)
        and credential.get("mode") == "shared"
        and credential.get("kind") == "oauth"
    )


def _refuse_signed_in_server(mcp, server_id: str) -> None:
    if is_shared_oauth(_server_entries(mcp).get(server_id)):
        raise MCPServerSignsInOverOAuth(
            f"{server_id}'s account is signed in over OAuth by an Owner or "
            "Admin, so it takes no stored key"
        )


def write_reference_state(row: AgentccMCPCredential, state: str, actor) -> bool | None:
    """Set ``state`` on the config entry that names the shared ``row``, in a
    new version the writer pushes (``mark_lapsed``'s rule, for a row whose
    status the caller set under the org lock). Returns whether the gateway
    took the push."""
    return _write_lapsed_state(row, state, actor)


def _require_server(mcp, server_id: str) -> None:
    if is_managed(server_id):
        return
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    if not isinstance(servers, dict) or not isinstance(servers.get(server_id), dict):
        raise MCPServerNotFound(f"the org has no MCP server {server_id}")


def _active_mcp(org_id) -> dict:
    mcp = (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization_id=org_id, is_active=True, deleted=False
        )
        .values_list("mcp", flat=True)
        .first()
    )
    return mcp if isinstance(mcp, dict) else {}


def _write_lapsed_state(row: AgentccMCPCredential, state: str, actor) -> bool | None:
    """Set the lapsed state on the config entry that references ``row``, in a
    new version; with no such entry, push the active config so the secret
    leaves the gateway. Returns whether the gateway took the push."""
    mcp = _active_mcp(row.organization_id)
    if _reference_id(_entry_reference(mcp, row.server_id)) != str(row.pk):
        return push_active_config(row.organization_id)

    def updater(config):
        _entry_reference(config.mcp, row.server_id)["state"] = state

    with vault_write():
        _, warnings = write_config_version_with(
            row.organization,
            updater,
            description=f"MCP credential for {row.server_id} is {state}",
            user=actor,
        )
    return is_gateway_synced(warnings)


def _references(mcp, credential: AgentccMCPCredential) -> bool:
    """Whether ``credential``'s server entry in ``mcp`` names it by
    ``credential_id`` with ``state: active``."""
    reference = _entry_reference(mcp, credential.server_id)
    return (
        _reference_id(reference) == str(credential.pk)
        and reference.get("state") == Status.ACTIVE
    )


def _referenced_shared(org_id, server_id: str, reference):
    """The org's active shared static credential of ``server_id`` that
    ``reference`` names with ``state: active``, or None (a malformed id
    included: it names nothing)."""
    if not isinstance(reference, dict) or reference.get("state") != Status.ACTIVE:
        return None
    try:
        pk = uuid.UUID(str(_reference_id(reference)))
    except (TypeError, ValueError, AttributeError):
        return None
    return (
        AgentccMCPCredential.objects.filter(
            pk=pk,
            organization_id=org_id,
            server_id=server_id,
            scope=Scope.SHARED,
            status=Status.ACTIVE,
            kind__in=STATIC_KINDS,
        )
        .only("pk", "server_id", "origin")
        .first()
    )


def _entry_reference(mcp, server_id: str) -> dict:
    """The config entry that holds ``server_id``'s credential reference, or {}."""
    if is_managed(server_id):
        managed = _managed_tools(mcp)
        credentials = managed.get("credentials") if managed else None
        entry = credentials.get(server_id) if isinstance(credentials, dict) else None
    else:
        servers = mcp.get("servers") if isinstance(mcp, dict) else None
        server = servers.get(server_id) if isinstance(servers, dict) else None
        entry = server.get("credential") if isinstance(server, dict) else None
    return entry if isinstance(entry, dict) else {}


def _reference_id(reference: dict):
    """A reference's credential_id as the contract reads it: the value of the
    first spelling present, in the contract's alias order (a null there wins
    over a later spelling, as ``_normalize_input_aliases`` has it)."""
    for key in _CREDENTIAL_ID_KEYS:
        if key in reference:
            return reference[key]
    return None


def _revoke_unreferenced(org: Organization, mcp, server_id: str) -> None:
    """Revoke ``server_id``'s active shared credential when ``mcp`` does not
    reference it. The caller holds the lock."""
    row = active_shared(org.pk, server_id)
    if row is not None and _reference_id(_entry_reference(mcp, server_id)) != str(
        row.pk
    ):
        revoke_server_credentials(org, server_id, scope=Scope.SHARED)


def _active(org_id, server_id, scope, user):
    return AgentccMCPCredential.objects.filter(
        organization_id=org_id,
        server_id=server_id,
        scope=scope,
        user=user,
        account_label="",
        status=Status.ACTIVE,
    )


def _locked(credential: AgentccMCPCredential) -> AgentccMCPCredential:
    return AgentccMCPCredential.objects.select_for_update().get(pk=credential.pk)


def _refuse_oauth_client(row: AgentccMCPCredential) -> None:
    if row.kind == Kind.OAUTH_CLIENT:
        raise MCPCredentialIsOAuthClient(
            "this is an org OAuth client: rotate or revoke it under "
            "/agentcc/connectors/oauth-clients/"
        )


def _decoded_placement(row: AgentccMCPCredential):
    """``(placement, header_name, name)`` from the row's ``header_name``:
    ``(header, <header>, None)``, or ``(query | url_variable, None,
    <name>)`` for ``<in>:<name>``; None for a value no writer stores."""
    stored = row.header_name or ""
    placement, sep, name = stored.partition(":")
    if not sep:
        return HEADER, stored, None
    if placement in (QUERY, URL_VARIABLE) and name:
        return placement, None, name
    return None


def _row_secret(row: AgentccMCPCredential, value: str, placed) -> Secret:
    placement, header_name, name = placed
    return Secret(row.kind, value, header_name or "", placement, name or "")


def _decrypted(row: AgentccMCPCredential) -> str | None:
    try:
        value = decrypt_token(row.encrypted_secret)
    except Exception as e:
        # Logged by type only: a message could quote ciphertext or a value.
        _log_unreadable(row, type(e).__name__)
        return None
    if not isinstance(value, str) or not value or value.startswith(ENCRYPTED_PREFIX):
        _log_unreadable(row, "EmptyOrNotDecrypted")
        return None
    return value


def _log_unreadable(row, error_type: str) -> None:
    logger.warning(
        "mcp_credential_unreadable",
        org_id=str(row.organization_id),
        credential_id=str(row.pk),
        server_id=row.server_id,
        error_type=error_type,
    )


def _log(event: str, row: AgentccMCPCredential, actor) -> None:
    logger.info(
        event,
        org_id=str(row.organization_id),
        credential_id=str(row.pk),
        server_id=row.server_id,
        scope=row.scope,
        status=row.status,
        version=row.version,
        actor_id=str(actor.pk) if actor is not None else None,
    )
