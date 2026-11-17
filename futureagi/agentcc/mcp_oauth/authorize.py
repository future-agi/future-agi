"""The authorization request, the consent and the authorization code.

The browser reaches ``/authorize`` on the API host, which knows no session, so
the request is checked there, kept as a pending request for 10 minutes and
handed to the console's consent page. The signed-in person opens it there:
the first person to open it owns it, nobody else can read or decide it, and
it is decided once. Approving writes the consent row for (person, client,
organization, resource, scopes) and mints a single-use code that lives 10
minutes; the redirect carries ``code``, ``state`` and ``iss``.
"""

import hashlib
import re
import secrets
from dataclasses import asdict, dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from agentcc.mcp_oauth import clients, config, resources
from agentcc.models.mcp_oauth import AgentccMCPOAuthConsent

_PKCE_CHALLENGE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
_REQUEST_KEY = "agentcc:mcpoauth:req:{}"
_REQUEST_OWNER_KEY = "agentcc:mcpoauth:req-owner:{}"
_REQUEST_DONE_KEY = "agentcc:mcpoauth:req-done:{}"
_CODE_KEY = "agentcc:mcpoauth:code:{}"
MAX_STATE_CHARS = 2048


class AuthorizeError(Exception):
    """``error`` is an RFC 6749 error code. ``redirect`` is False when the
    client or its redirect URI could not be trusted, so the error is shown to
    the person and never sent anywhere."""

    def __init__(self, error, description, *, redirect=True):
        super().__init__(description)
        self.error = error
        self.description = description
        self.redirect = redirect


class PendingNotFound(Exception):
    """No such pending request for this person: unknown, expired, decided or
    opened by someone else."""


class NoOrganization(Exception):
    """The person belongs to no organization that holds the resource."""


@dataclass
class Pending:
    client_id: str
    redirect_uri: str
    code_challenge: str
    resource: str
    scopes: list
    state: str | None


def parse_scopes(raw) -> list[str]:
    if raw is None or raw == "":
        return list(config.SCOPE_ORDER)
    asked = set(str(raw).split())
    if not asked or asked - set(config.SCOPES):
        raise AuthorizeError("invalid_scope", "Unknown scope requested.")
    return [s for s in config.SCOPE_ORDER if s in asked]


def validate(params) -> tuple[clients.Client, Pending]:
    """Check an authorization request (RFC 6749 4.1.1, RFC 7636, RFC 8707).
    The client and the redirect URI are checked first, so that nothing is
    ever sent to an address the client did not register."""
    client = clients.resolve(params.get("client_id"))
    if client is None:
        raise AuthorizeError("invalid_client", "Unknown client_id.", redirect=False)
    redirect_uri = params.get("redirect_uri")
    if not redirect_uri or not clients.redirect_uri_allowed(client, redirect_uri):
        raise AuthorizeError(
            "invalid_request",
            "redirect_uri is missing or not registered for this client.",
            redirect=False,
        )
    state = params.get("state")
    if state is not None and len(state) > MAX_STATE_CHARS:
        raise AuthorizeError("invalid_request", "state is too long.", redirect=False)
    if params.get("response_type") != "code":
        raise AuthorizeError(
            "unsupported_response_type", "Only response_type=code is supported."
        )
    challenge = params.get("code_challenge")
    if params.get("code_challenge_method") != "S256" or not challenge:
        raise AuthorizeError(
            "invalid_request", "PKCE with code_challenge_method=S256 is required."
        )
    if not _PKCE_CHALLENGE.fullmatch(challenge):
        raise AuthorizeError("invalid_request", "code_challenge is malformed.")
    resource = resources.parse(params.get("resource"))
    if resource is None:
        raise AuthorizeError(
            "invalid_target",
            "resource must be this gateway's MCP endpoint or one of its "
            "virtual servers.",
        )
    scopes = parse_scopes(params.get("scope"))
    return client, Pending(
        client_id=client.client_id,
        redirect_uri=redirect_uri,
        code_challenge=challenge,
        resource=resource.uri,
        scopes=scopes,
        state=state,
    )


def store(pending: Pending) -> str:
    request_id = secrets.token_urlsafe(32)
    cache.set(
        _REQUEST_KEY.format(request_id), asdict(pending), config.PENDING_REQUEST_SECONDS
    )
    return request_id


def _valid_id(request_id) -> bool:
    return isinstance(request_id, str) and 20 <= len(request_id) <= 128


def load(request_id, user) -> Pending:
    """The pending request, bound to ``user``: the first signed-in person to
    open it owns it."""
    if not _valid_id(request_id) or user is None:
        raise PendingNotFound()
    data = cache.get(_REQUEST_KEY.format(request_id))
    if not isinstance(data, dict):
        raise PendingNotFound()
    owner_key = _REQUEST_OWNER_KEY.format(request_id)
    cache.add(owner_key, str(user.pk), config.PENDING_REQUEST_SECONDS)
    if cache.get(owner_key) != str(user.pk):
        raise PendingNotFound()
    return Pending(**data)


def redirect_url(redirect_uri: str, params: dict) -> str:
    """``redirect_uri`` with ``params`` added to its own query, ``iss`` always
    among them (RFC 9207)."""
    parts = urlsplit(redirect_uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query += [(k, v) for k, v in params.items() if v is not None]
    query.append(("iss", config.issuer()))
    return urlunsplit(parts._replace(query=urlencode(query)))


def error_redirect(pending_or_params, error, description=None) -> str:
    getter = (
        pending_or_params.get
        if isinstance(pending_or_params, dict)
        else lambda k: getattr(pending_or_params, k, None)
    )
    return redirect_url(
        getter("redirect_uri"),
        {"error": error, "error_description": description, "state": getter("state")},
    )


def describe(request_id, user) -> dict:
    """What the consent page shows: the client, the redirect, the
    organizations the person may grant it in, and the scopes."""
    pending = load(request_id, user)
    client = clients.resolve(pending.client_id)
    resource = resources.parse(pending.resource)
    if client is None or resource is None:
        raise PendingNotFound()
    orgs = resources.organizations_for(user, resource)
    if not orgs:
        raise NoOrganization()
    redirect = urlsplit(pending.redirect_uri)
    return {
        "client": {
            "client_id": client.client_id,
            "name": client.name,
            "kind": client.kind,
            "verified": client.verified,
            "first_party": client.first_party,
        },
        "redirect_uri": pending.redirect_uri,
        "redirect_host": redirect.hostname or f"{redirect.scheme}:",
        "resource": pending.resource,
        "virtual_server": resource.virtual_server,
        "organizations": [{"id": str(o.id), "name": o.name} for o in orgs],
        "scopes": [
            {"name": s, "description": config.SCOPES[s]} for s in pending.scopes
        ],
        "pre_approved": clients.pre_approved(client, pending.redirect_uri),
    }


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def decide(request_id, user, *, approve: bool, organization_id=None) -> str:
    """Record the person's decision; returns where the browser goes next.
    A request is decided once."""
    pending = load(request_id, user)
    client = clients.resolve(pending.client_id)
    resource = resources.parse(pending.resource)
    if client is None or resource is None:
        raise PendingNotFound()
    if approve:
        orgs = resources.organizations_for(user, resource)
        if not orgs:
            raise NoOrganization()
        if organization_id is None and len(orgs) == 1:
            org = orgs[0]
        else:
            org = next((o for o in orgs if str(o.id) == str(organization_id)), None)
        if org is None:
            raise ValueError("Choose one of the organizations offered.")
    if not cache.add(
        _REQUEST_DONE_KEY.format(request_id), 1, config.PENDING_REQUEST_SECONDS
    ):
        raise PendingNotFound()
    cache.delete(_REQUEST_KEY.format(request_id))
    if not approve:
        return error_redirect(pending, "access_denied", "The person declined.")
    consent = record_consent(user, client, org, pending.resource, pending.scopes)
    code = secrets.token_urlsafe(32)
    cache.set(
        _CODE_KEY.format(_code_hash(code)),
        {
            "client_id": client.client_id,
            "redirect_uri": pending.redirect_uri,
            "code_challenge": pending.code_challenge,
            "resource": pending.resource,
            "scopes": pending.scopes,
            "user_id": str(user.pk),
            "organization_id": str(org.id),
            "consent_id": consent.pk,
        },
        config.AUTHORIZATION_CODE_SECONDS,
    )
    return redirect_url(pending.redirect_uri, {"code": code, "state": pending.state})


@transaction.atomic
def record_consent(user, client, org, resource, scopes) -> AgentccMCPOAuthConsent:
    """One live consent per (person, client, organization, resource); a new
    approval replaces its scopes."""
    live = (
        AgentccMCPOAuthConsent.objects.select_for_update()
        .filter(
            user=user,
            client=client.row,
            organization=org,
            resource=resource,
            revoked_at__isnull=True,
        )
        .first()
    )
    now = timezone.now()
    if live is not None:
        live.scopes = list(scopes)
        live.granted_at = now
        live.save(update_fields=["scopes", "granted_at"])
        return live
    return AgentccMCPOAuthConsent.objects.create(
        user=user,
        client=client.row,
        organization=org,
        resource=resource,
        scopes=list(scopes),
        granted_at=now,
    )


def take_code(code) -> tuple[dict | None, bool]:
    """``(grant, reused)``: the code's grant the first time it is presented;
    on any later presentation within its life, the grant with ``reused`` set,
    so the caller refuses it and revokes what the first exchange issued
    (RFC 6749 section 4.1.2)."""
    if not isinstance(code, str) or not code or len(code) > 256:
        return None, False
    key = _CODE_KEY.format(_code_hash(code))
    data = cache.get(key)
    if not isinstance(data, dict):
        return None, False
    first = cache.add(key + ":used", 1, config.AUTHORIZATION_CODE_SECONDS)
    return data, not first
