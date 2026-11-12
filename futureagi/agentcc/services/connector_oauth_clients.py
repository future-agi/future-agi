"""OAuth client sources for catalog connectors (CN3).

When the gateway connects a user (or an admin, for a shared account) to a
catalog connector's vendor, it identifies itself to the vendor's authorization
server with an OAuth client. ``resolve`` picks that client, in this order:

1. The org's own client (bring your own): an active ``AgentccMCPCredential`` of
   kind ``oauth_client`` and scope ``shared``, keyed ``server:<id>`` or
   ``group:<group>`` (``org_client_key``). The vault keeps it encrypted, Django-side:
   ``assemble_for_push`` pushes only static kinds, so this one never leaves.
2. FutureAGI's platform client, an operator secret. It is never a vault row
   (the vault's organization is required) and never in the repository or the
   catalog. It comes from ``AGENTCC_CONNECTOR_CLIENT_<GROUP>_ID`` and
   ``..._SECRET``, else from the JSON secrets file the operator names in
   ``AGENTCC_CONNECTOR_CLIENTS_FILE`` (a mounted Kubernetes Secret, or a file a
   secret manager's agent writes). Both are read at each connect, so a secret
   rotated in the file applies on the next connect; the environment changes
   only when the process restarts.
3. Otherwise ``None``.

Never the platform client where an organization would choose where
FutureAGI's secret goes. A client secret is sent to the provider's
``tokenUrl`` and, on disconnect, to its ``revocation.url``. Where either takes
its host from a URL variable (Snowflake's ``https://{accountUrl}/...``, filled
in by the org that adds the connector), the platform client is not a source,
however it is configured, and is not even read: only the org's own client is,
then CIMD or DCR, which send no secret of ours.

``client_source`` then decides how the connect identifies itself: an org
client keyed to the server always; a client for the whole group, the org's or
the platform's, where the server's authorization server accepts pre-registered
clients; then CIMD, then DCR. Passing over an org's client is logged as a
warning, and with nothing acceptable the connect is refused: with
``OrgClientRequired`` where only the org's own client could serve.

A client group names the apps that share one client (the catalog's
``auth.oauth2.clientGroup``: ``google`` for Gmail, Calendar and Drive); an app
without one is its own group. Nothing else is a source: not Django settings,
not another org's or another group's client, not Omega EE's env names.

Fail closed. A half-configured platform client or a malformed secrets file
raises ``PlatformClientMisconfigured``, and an org client that cannot be read
raises ``OrgClientUnreadable``. Neither falls through to the next source, so
the users of an org whose own client is unreadable are never sent to ours.

Secrets. A secret leaves only through ``ClientRef.token_exchange_secret``, for
the token request; it is never in a repr, a log line, an error or anything
serialisable. Nor is it in an error report: the app's Sentry reports every
frame's local variables and redacts only some names. So a function that holds
a plaintext secret, or the secrets file's contents, returns a result or the
reason for a refusal and never raises; only its caller, whose locals are names
and reasons, raises, outside any exception handler. No frame on a refusal's
traceback holds a secret under any name, and the module chains no exception to
a refusal (a caller's own exception can still be its context).
"""

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import structlog

from accounts.models import Organization
from agentcc.models.mcp_credential import URL_MAX_LENGTH, AgentccMCPCredential
from agentcc.services.credential_manager import ENCRYPTED_PREFIX, decrypt_token
from agentcc.services import microsoft_connect_gate as microsoft
from agentcc.services import microsoft_client_identity as identity

logger = structlog.get_logger(__name__)

Kind = AgentccMCPCredential.Kind
Scope = AgentccMCPCredential.Scope
Status = AgentccMCPCredential.Status

# An app id (the catalog's app id rule) or a client group, which follows it too:
# no upper case and no "_", so a group's env names (upper case, "-" to "_")
# belong to that group alone.
CATALOG_NAME = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
ENV_PREFIX = "AGENTCC_CONNECTOR_CLIENT_"
CLIENTS_FILE_ENV = "AGENTCC_CONNECTOR_CLIENTS_FILE"
MAX_CLIENTS_FILE_BYTES = 1 << 20
# The vault's own limits: oauth_client_id's column, and M14's secret length.
MAX_CLIENT_ID_CHARS = URL_MAX_LENGTH
MAX_SECRET_CHARS = 8192
# An org client's vault key lives in its own namespace, never the bare server
# id: the vault allows one active shared row per server id whatever its kind,
# and that slot holds the server's own shared secret or token. Server ids never
# contain ":" (M14's SERVER_ID), so neither namespace can name a server.
SERVER_KEY_PREFIX = "server:"
GROUP_KEY_PREFIX = "group:"
_SERVER_KEY_CHARS = AgentccMCPCredential._meta.get_field("server_id").max_length
# What an authorization server accepts, as the catalog's
# auth.oauth2.registration lists it.
REGISTRATION_METHODS = frozenset({"preregistered", "cimd", "dcr"})
# A URL variable as the catalog writes one (connector-manifest/v1 urlTemplate).
TEMPLATE_VARIABLE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]{0,31}\}")
# A secrets-file entry's fields, each with its limit.
_FIELD_LIMITS = {"client_id": MAX_CLIENT_ID_CHARS, "client_secret": MAX_SECRET_CHARS}


class ConnectorOAuthClientError(ValueError):
    """No client could be resolved. Messages name settings, never values."""

    code = "invalid_connector_oauth_client"


class PlatformClientMisconfigured(ConnectorOAuthClientError):
    code = "connector_oauth_client_misconfigured"


class OrgClientUnreadable(ConnectorOAuthClientError):
    code = "connector_oauth_client_unreadable"


class OAuthClientUnavailable(ConnectorOAuthClientError):
    """No client the authorization server accepts: the connect is refused."""

    code = "oauth_client_unavailable"


class OrgClientRequired(OAuthClientUnavailable):
    """Only the org's own client could serve, and it has none: the provider
    takes pre-registered clients only, and a URL variable sets the host of an
    endpoint its secret is sent to, so the platform client is never used."""

    code = "connector_oauth_org_client_required"


class _Sealed:
    """A client secret kept out of every repr and every serialiser."""

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


@dataclass(frozen=True)
class ClientRef:
    """The OAuth client the connect flow uses.

    ``client_id`` is not a secret: it travels in the authorize URL. Record it
    on the token row, so a refresh uses the client that issued the token.
    """

    source: str  # "org" or "platform"
    origin: str  # org: "server" or "group" (the key that matched); platform: "env" or "file"
    client_group: str
    client_id: str
    _secret: _Sealed = field(repr=False, compare=False)
    credential_id: str | None = None  # the org client's vault row

    def token_exchange_secret(self) -> str:
        """The client secret, for the request to the token endpoint only.

        Pass it straight into that request: a local variable that holds it is
        one exception away from an error report's local variables."""
        identity.deny_if_excluded(self.client_id)
        return self._secret._value


def env_names(client_group: str) -> tuple[str, str]:
    """``AGENTCC_CONNECTOR_CLIENT_<GROUP>_ID`` and ``..._SECRET`` for a group."""
    _check_name("client_group", client_group)
    stem = ENV_PREFIX + client_group.upper().replace("-", "_")
    return f"{stem}_ID", f"{stem}_SECRET"


def org_client_key(
    *, client_group: str | None = None, server_id: str | None = None
) -> str:
    """The vault key (``server_id`` column) of an org's ``oauth_client`` row:
    ``server:<server_id>`` for a client of one server, ``group:<client_group>``
    for a client of a whole group. Writers store org clients under this key."""
    if (client_group is None) == (server_id is None):
        raise ConnectorOAuthClientError("name one of client_group and server_id")
    if server_id is not None:
        return _server_key(server_id)
    _check_name("client_group", client_group)
    return GROUP_KEY_PREFIX + client_group


def resolve(
    org: Organization,
    app_id: str,
    client_group: str | None,
    *,
    token_url: str,
    revocation_url: str | None,
    server_id: str | None = None,
    platform: bool = True,
    issuing_client_id: str | None = None,
) -> ClientRef | None:
    """The org's own client, else the platform client, else ``None``. With
    ``platform=False`` only the org's own client is a source, and the platform
    client is not even read (a K2 grant, ``connector_k2``).

    ``token_url`` and ``revocation_url`` are where a client secret is sent: the
    provider's ``tokenUrl`` and ``revocation.url`` as the catalog writes them,
    variables unfilled, never after an org's values are filled in nor as
    discovery found them. ``revocation_url`` is ``None`` when the provider
    declares none. Where a URL variable sets either one's host, the platform
    client is not a source (module docstring). A provider whose endpoints are
    discovered (``mcp_oauth``) passes the URL discovery starts from, its
    ``transport.url``, as ``token_url``.

    With ``server_id``, an org client keyed to that server wins over the org's
    client for the group. Raises ``OrgClientUnreadable`` and
    ``PlatformClientMisconfigured`` (module docstring) rather than falling
    through to the next source.
    """
    # Checked first, so a missing organization is refused the same way however
    # the deployment is configured. An Organization's pk is a default UUID, set
    # before it is saved, so "saved" is Django's _state.adding.
    if not isinstance(org, Organization) or org.pk is None or org._state.adding:
        raise ConnectorOAuthClientError("org must be a saved Organization")
    _check_name("app_id", app_id)
    group = app_id if client_group is None else client_group
    _check_name("client_group", group)
    microsoft.require_microsoft_connect_enabled(
        app_id=app_id, client_group=group, server_id=server_id
    )
    restricted = not microsoft.platform_allowed(
        app_id=app_id, client_group=group, server_id=server_id
    )
    platform = platform and not restricted
    if issuing_client_id is not None:
        identity.deny_if_excluded(issuing_client_id)
    _check_endpoints(token_url, revocation_url)
    keys = [] if server_id is None else [_server_key(server_id)]
    keys.append(GROUP_KEY_PREFIX + group)
    ref = _org_client(
        org, group, keys, issuing_client_id=issuing_client_id, restricted=restricted
    )
    if ref is None and platform:
        set_by_variable = _endpoint_set_by_variable(token_url, revocation_url)
        if set_by_variable is None:
            ref = _platform_client(group)
        else:
            # Never read: the platform client's secret stays where it is.
            endpoint, variable = set_by_variable
            logger.info(
                "connector_oauth_platform_client_not_used",
                org_id=str(org.pk),
                client_group=group,
                endpoint=endpoint,
                variable=variable,
            )
    if ref is not None:
        logger.debug(
            "connector_oauth_client_resolved",
            org_id=str(org.pk),
            client_group=group,
            source=ref.source,
            origin=ref.origin,
            credential_id=ref.credential_id,
        )
    return ref


def server_client(
    org: Organization, server_id: str, *, issuing_client_id: str | None = None
) -> ClientRef | None:
    """The org's own client keyed to ``server_id``, for a server the org
    added by URL: it has no client group, so no group's client and never the
    platform client is a source for it. Raises ``OrgClientUnreadable`` for a
    row that cannot be read, as ``resolve`` does."""
    if not isinstance(org, Organization) or org.pk is None or org._state.adding:
        raise ConnectorOAuthClientError("org must be a saved Organization")
    from agentcc.services.mcp_upstream_oauth import server_entry

    entry = server_entry(org.pk, server_id, include_hidden=True)
    microsoft.require_microsoft_connect_enabled(entry, server_id=server_id)
    restricted = not microsoft.platform_allowed(entry, server_id=server_id)
    if issuing_client_id is not None:
        identity.deny_if_excluded(issuing_client_id)
    return _org_client(
        org, "", [_server_key(server_id)], issuing_client_id=issuing_client_id,
        restricted=restricted,
    )


def client_source(
    resolved: ClientRef | None,
    *,
    registration: Iterable[str],
    client_group: str,
    token_url: str,
    revocation_url: str | None,
) -> str:
    """How the connect flow identifies itself: ``org``, ``platform``, ``cimd``
    or ``dcr``.

    ``registration`` lists what the server's authorization server accepts, as
    the catalog records it for the provider; ``token_url`` and
    ``revocation_url`` are the ones given to ``resolve``. A probe can see CIMD
    and DCR support in the server's metadata, but never whether pre-registered
    clients are accepted. In order:

    1. An org client keyed to this server, always: the org registered it for
       this server, whatever ``registration`` says.
    2. A client for the whole group, the org's or the platform's, where
       ``preregistered`` is accepted. A group spans providers, and a vendor's
       MCP server can run its own authorization server that takes only CIMD
       or DCR clients. Passing over an org's client is logged as a warning.
    3. CIMD, then DCR, each where accepted.

    With none acceptable the connect is refused with ``OAuthClientUnavailable``,
    never sent with any other client: with its subclass ``OrgClientRequired``
    where only pre-registered clients are accepted and a URL variable sets an
    endpoint's host, since only the org's own client may go there. A platform
    client is never returned for such a provider. Reads no database and no
    environment.
    """
    if isinstance(registration, str):
        raise ConnectorOAuthClientError("registration is a list of methods, not one")
    accepted = set(registration)
    if not accepted <= REGISTRATION_METHODS:
        raise ConnectorOAuthClientError(
            f"unknown registration methods {sorted(accepted - REGISTRATION_METHODS)}; "
            f"expected some of {sorted(REGISTRATION_METHODS)}"
        )
    microsoft.require_microsoft_connect_enabled(client_group=client_group)
    if resolved is not None:
        microsoft.require_org_client_identity(resolved.client_id, client_group=client_group)
    if not microsoft.platform_allowed(client_group=client_group) and (
        resolved is None or resolved.source != "org"
    ):
        raise OrgClientRequired(microsoft.ORG_CLIENT_MESSAGE)
    id_name, secret_name = env_names(client_group)
    _check_endpoints(token_url, revocation_url)
    set_by_variable = _endpoint_set_by_variable(token_url, revocation_url)
    if resolved is not None:
        if resolved.client_group != client_group:
            raise ConnectorOAuthClientError(
                f"the client resolved for {resolved.client_group!r} cannot serve {client_group!r}"
            )
        if resolved.source == "platform" and set_by_variable is not None:
            # resolve never returns one here: it was resolved for other endpoints.
            raise ConnectorOAuthClientError(
                f"a platform client cannot serve {client_group!r}: "
                f"{_unfixed(set_by_variable)}, so FutureAGI's platform client is "
                "never sent there"
            )
        keyed_to_server = resolved.source == "org" and resolved.origin == "server"
        if keyed_to_server or "preregistered" in accepted:
            return resolved.source
        if resolved.source == "org":
            logger.warning(
                "connector_oauth_org_client_passed_over",
                client_group=client_group,
                credential_id=resolved.credential_id,
                registration=sorted(accepted),
            )
    for method in ("cimd", "dcr"):
        if method in accepted:
            return method
    if "preregistered" in accepted and set_by_variable is not None:
        raise OrgClientRequired(
            f"{client_group!r} needs the organization's own OAuth client: its "
            "authorization server accepts only pre-registered clients, and "
            f"{_unfixed(set_by_variable)}, so FutureAGI's platform client is never "
            "sent there"
        )
    if "preregistered" in accepted:
        raise OAuthClientUnavailable(
            f"no OAuth client for {client_group!r}: its authorization server needs "
            "a pre-registered client. Add the organization's own OAuth client, or "
            f"set {id_name} and {secret_name} on the server."
        )
    raise OAuthClientUnavailable(
        f"no OAuth client for {client_group!r}: its authorization server accepts "
        "none of a pre-registered client, CIMD or DCR"
    )


# --- The org's own client ----------------------------------------------------


def _org_client(
    org: Organization, group: str, keys: list[str], *, issuing_client_id: str | None = None,
    restricted: bool = False,
) -> ClientRef | None:
    rows = {
        row.server_id: row
        for row in AgentccMCPCredential.objects.filter(
            organization=org,
            server_id__in=keys,
            kind=Kind.OAUTH_CLIENT,
            scope=Scope.SHARED,
            user__isnull=True,
            account_label="",
            status=Status.ACTIVE,
        ).defer("encrypted_secret", "encrypted_refresh")
    }
    for key in keys:  # the server's own client first, then the group's
        row = rows.get(key)
        if row is None:
            continue
        if issuing_client_id is not None and row.oauth_client_id != issuing_client_id:
            # The winning registration is different: do not decrypt its secret
            # merely to reject a token issued by an excluded platform client.
            return None
        if restricted:
            microsoft.require_org_client_identity(row.oauth_client_id, client_group="microsoft")
        identity.deny_if_excluded(row.oauth_client_id)
        sealed, error_type = _org_secret(row)
        if sealed is None:
            raise _unreadable(row, error_type)
        origin = "group" if key.startswith(GROUP_KEY_PREFIX) else "server"
        return ClientRef("org", origin, group, row.oauth_client_id, sealed, str(row.pk))
    return None


def _org_secret(row: AgentccMCPCredential) -> tuple[_Sealed | None, str]:
    """The row's client secret, sealed, or ``None`` and the error type that
    makes the row unreadable. It holds the plaintext, so it returns and never
    raises (module docstring); the caller refuses, never moving on to the next
    source. The identity preflight can refuse before any secret is loaded."""
    identity.deny_if_excluded(row.oauth_client_id)
    stored = row.encrypted_secret
    if not row.oauth_client_id or not stored.startswith(ENCRYPTED_PREFIX):
        return None, "NoClientIdOrNotEncrypted"
    secret = ""
    try:
        secret = decrypt_token(stored)
    except ValueError as e:  # CredentialManager turns InvalidToken into ValueError
        # Kept by type only, as the vault logs it: its traceback could quote
        # the ciphertext.
        return None, type(e).__name__
    if not isinstance(secret, str) or not secret or secret.startswith(ENCRYPTED_PREFIX):
        return None, "EmptyOrNotDecrypted"
    return _Sealed(secret), ""


def _unreadable(row: AgentccMCPCredential, error_type: str) -> OrgClientUnreadable:
    logger.warning(
        "connector_oauth_client_unreadable",
        org_id=str(row.organization_id),
        credential_id=str(row.pk),
        key=row.server_id,
        error_type=error_type,
    )
    return OrgClientUnreadable(
        f"the organization's OAuth client {row.pk} cannot be read: replace it, "
        "or revoke it to use the platform client"
    )


# --- FutureAGI's platform client ---------------------------------------------


def _platform_client(group: str) -> ClientRef | None:
    """The client from the environment, else the secrets file's entry for the
    group, else ``None``. Refusals are raised here, from a frame that holds
    names and reasons only (module docstring)."""
    if not microsoft.platform_allowed(client_group=group, app_id=group):
        return None
    id_name, secret_name = env_names(group)
    configured_id = os.environ.get(id_name)
    configured_path = os.environ.get(CLIENTS_FILE_ENV)
    if configured_path:
        # Both configured identity sources must be readable, but malformed
        # metadata is still a deployment error, not an org-client requirement.
        # The ID/offset scan does not decode or seal any client secret.
        _, problem = identity.file_metadata(configured_path)
        if problem is not None:
            raise _misconfigured(group, f"{CLIENTS_FILE_ENV} {problem}", path=configured_path)
    identity.deny_if_excluded(configured_id)
    if not configured_id and configured_path:
        identity.file_client_id(configured_path, group)
    ref, problem = _env_client(group, id_name, secret_name)
    if problem is not None:
        raise _misconfigured(group, problem)
    if ref is not None:
        return ref
    path = os.environ.get(CLIENTS_FILE_ENV) or ""
    if not path:
        return None
    identity.file_client_id(path, group)
    entries, problem = _clients_file(path)
    if problem is not None:
        raise _misconfigured(group, f"{CLIENTS_FILE_ENV} {problem}", path=path)
    if group not in entries:
        return None
    client_id, sealed = entries[group]
    return ClientRef("platform", "file", group, client_id, sealed)


def _env_client(
    group: str, id_name: str, secret_name: str
) -> tuple[ClientRef | None, str | None]:
    """The platform client from the environment, ``(None, None)`` when neither
    name is set, or ``(None, reason)`` to refuse it. Returns and never raises:
    the plaintext stays in ``secret`` until it is sealed."""
    client_id = os.environ.get(id_name) or ""
    identity.deny_if_excluded(client_id)
    secret = os.environ.get(secret_name) or ""
    if not (client_id or secret):
        return None, None
    if not (client_id and secret):
        missing = secret_name if client_id else id_name
        return None, f"{id_name} and {secret_name} must both be set; {missing} is not"
    problem = _value_problem(id_name, client_id, MAX_CLIENT_ID_CHARS) or (
        _value_problem(secret_name, secret, MAX_SECRET_CHARS)
    )
    if problem is not None:
        return None, problem
    return ClientRef("platform", "env", group, client_id, _Sealed(secret)), None


def _clients_file(path: str) -> tuple[dict[str, tuple[str, _Sealed]], str | None]:
    """The operator's secrets file, ``{group: {client_id, client_secret}}``, as
    ``{group: (client_id, sealed secret)}``, or ``({}, reason)`` to refuse it.
    Checked whole: one bad entry refuses the file. Like every helper below
    that sees the file, it returns and never raises (module docstring)."""
    if not os.path.isabs(path):
        return {}, "must be an absolute path"
    document, problem = _parse_clients_file(path)
    if problem is not None:
        return {}, problem
    if not isinstance(document, dict):
        return {}, "must hold one JSON object"
    entries = {}
    for name, entry in document.items():
        # A key that is not a group name is not echoed: it could be a pasted secret.
        if not CATALOG_NAME.fullmatch(name):
            return {}, "has a key that is not a client group"
        if not isinstance(entry, dict) or entry.keys() != _FIELD_LIMITS.keys():
            return {}, f"entry {name!r} must hold exactly client_id and client_secret"
        client_id, sealed = entry["client_id"], entry["client_secret"]
        problem = _value_problem(
            f"entry {name!r} client_id", client_id, MAX_CLIENT_ID_CHARS
        ) or _value_problem(
            f"entry {name!r} client_secret",
            sealed._value if isinstance(sealed, _Sealed) else sealed,
            MAX_SECRET_CHARS,
        )
        if problem is not None:
            return {}, problem
        entries[name] = (client_id, sealed)
    return entries, None


def _parse_clients_file(path: str) -> tuple[object, str | None]:
    """The file's JSON document, each ``client_secret`` sealed as it is parsed,
    or ``(None, reason)``. Fails closed and keeps an error by its type only:
    a traceback, or a JSONDecodeError's ``doc``, could quote the document."""
    return identity.platform_file(path, _Sealed)


def _sealed_object(repeated: list[str], pairs: list[tuple[str, object]]) -> dict:
    """``json.loads``'s object_pairs_hook for the secrets file. It seals each
    ``client_secret`` as it is parsed, and notes a repeated key, which
    json.loads would settle by keeping the last. It never raises: an exception
    here would unwind through json's own frames, which hold the document."""
    obj: dict = {}
    for key, value in pairs:
        if key in obj and not repeated:
            # Named only when it is a field or a group, never a stray key that
            # could be a pasted secret.
            known = key in _FIELD_LIMITS or CATALOG_NAME.fullmatch(key)
            repeated.append(f"has the key {key!r} twice" if known else "repeats a key")
        if key == "client_secret" and isinstance(value, str):
            value = _Sealed(value)
        obj[key] = value
    return obj


def _value_problem(where: str, value: object, max_chars: int) -> str | None:
    """Why a client id or secret is refused, or ``None``. Refuses, never
    repairs: a trimmed value would be another client. It is handed the
    plaintext, so it returns and never raises."""
    if (
        isinstance(value, str)
        and value
        and value == value.strip()
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
        and len(value) <= max_chars
        and not value.startswith(ENCRYPTED_PREFIX)
    ):
        return None
    return (
        f"{where} must be a non-empty string of at most {max_chars} characters, "
        "with no surrounding spaces, control characters or encryption prefix"
    )


def _misconfigured(
    group: str, reason: str, **context: str
) -> PlatformClientMisconfigured:
    """The refusal. Its message, which a caller may show a member, names
    settings only; the log line, for the operator, also carries ``context``
    (the secrets file's path)."""
    logger.warning(
        "connector_oauth_client_misconfigured",
        client_group=group,
        reason=reason,
        **context,
    )
    return PlatformClientMisconfigured(
        f"the platform OAuth client for {group!r} is misconfigured: {reason}"
    )


# --- Checks -------------------------------------------------------------------


def _check_name(kind: str, value) -> None:
    if not isinstance(value, str) or not CATALOG_NAME.fullmatch(value):
        raise ConnectorOAuthClientError(f"{kind} must match {CATALOG_NAME.pattern}")


def _check_endpoints(token_url, revocation_url) -> None:
    """Where a client secret goes is always stated: no default stands in."""
    if not isinstance(token_url, str) or not token_url:
        raise ConnectorOAuthClientError(
            "token_url must be the provider's tokenUrl, as the catalog writes it"
        )
    if revocation_url is not None and (
        not isinstance(revocation_url, str) or not revocation_url
    ):
        raise ConnectorOAuthClientError(
            "revocation_url must be the provider's revocation.url, as the catalog "
            "writes it, or None where it declares none"
        )


def _endpoint_set_by_variable(
    token_url: str, revocation_url: str | None
) -> tuple[str, str | None] | None:
    """The first endpoint a client secret is sent to whose host a URL variable
    sets, as ``(catalog field, variable)``, or ``None`` when the catalog's
    text fixes both hosts."""
    for endpoint, url in (("tokenUrl", token_url), ("revocation.url", revocation_url)):
        if url is None:
            continue
        # The scheme and the authority (userinfo, host and port) are fixed when
        # they are literal and end at "/", "?" or "#" before the first "{" or
        # "}"; with no variable at all, "scheme://" and a host will do. So a
        # variable in the host or port leaves the host open, and so does one
        # straight after the host: its value could be "@elsewhere.example".
        cut = min(
            (i for i in (url.find("{"), url.find("}")) if i >= 0), default=len(url)
        )
        scheme, sep, rest = url[:cut].partition("://")
        end = min((i for i in map(rest.find, "/?#") if i >= 0), default=-1)
        if scheme and sep and (end > 0 or (end < 0 and cut == len(url) and rest)):
            continue
        variable = TEMPLATE_VARIABLE.match(url, cut)
        return endpoint, variable.group(0) if variable else None
    return None


def _unfixed(set_by_variable: tuple[str, str | None]) -> str:
    endpoint, variable = set_by_variable
    if variable is None:
        return f"its {endpoint} has no host the catalog fixes"
    return f"its {endpoint} leaves its host to the URL variable {variable}"


def _server_key(server_id) -> str:
    """``server:<server_id>``, the vault key of an org client for one server."""
    max_chars = _SERVER_KEY_CHARS - len(SERVER_KEY_PREFIX)
    if (
        not isinstance(server_id, str)
        or not server_id
        or ":" in server_id
        or len(server_id) > max_chars
    ):
        raise ConnectorOAuthClientError(
            f"server_id must be an MCP server id: no ':', at most {max_chars} characters"
        )
    return SERVER_KEY_PREFIX + server_id
