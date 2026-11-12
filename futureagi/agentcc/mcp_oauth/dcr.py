"""Dynamic client registration (RFC 7591), the path Claude still takes by
default (decision MO4: on, consent required).

A registered client is never trusted for anything but its own redirect URIs:
it gets no consent shortcut, and the consent screen marks it unverified.
``application_type`` is taken when sent and otherwise derived from the
redirect URIs (``native`` when any is loopback or an app scheme), because the
common MCP clients do not send it; a ``web`` client registers https redirects
only. ``token_endpoint_auth_method`` defaults to ``client_secret_basic`` as
RFC 7591 says; a secret is returned once and stored only as its SHA-256.
Registrations that never led to a consent are removed after a day.
"""

import secrets
import unicodedata
from datetime import timedelta

from django.db.models import Exists, OuterRef
from django.utils import timezone

from agentcc.mcp_oauth import clients, config
from agentcc.models.mcp_oauth import (
    AgentccMCPOAuthClient,
    AgentccMCPOAuthConsent,
    AgentccMCPOAuthRefreshToken,
)

MAX_BODY_BYTES = 16 * 1024
MAX_REDIRECT_URIS = 10
AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")
GRANT_TYPES = ("authorization_code", "refresh_token")
UNUSED_REGISTRATION_TTL = timedelta(days=1)


class RegistrationError(Exception):
    def __init__(self, error, description):
        super().__init__(description)
        self.error = error
        self.description = description


def _text(value, name, limit=200):
    if value is None:
        return None
    if not isinstance(value, str):
        raise RegistrationError("invalid_client_metadata", f"{name} must be a string.")
    cleaned = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C").strip()
    if len(cleaned) > limit:
        raise RegistrationError("invalid_client_metadata", f"{name} is too long.")
    return cleaned or None


def _string_list(body, name, default):
    value = body.get(name, default)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RegistrationError(
            "invalid_client_metadata", f"{name} must be a list of strings."
        )
    return value


def check_redirect_uris(value) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MAX_REDIRECT_URIS
        or not all(isinstance(u, str) for u in value)
    ):
        raise RegistrationError(
            "invalid_redirect_uri",
            f"redirect_uris must list 1 to {MAX_REDIRECT_URIS} URIs.",
        )
    for uri in value:
        if clients.redirect_uri_kind(uri) is None:
            raise RegistrationError(
                "invalid_redirect_uri",
                "Each redirect URI must be https, loopback http, or an app "
                "scheme, without a fragment.",
            )
    return list(dict.fromkeys(value))


def application_type_for(redirect_uris, declared) -> str:
    kinds = {clients.redirect_uri_kind(u) for u in redirect_uris}
    if declared is None:
        return "native" if kinds & {"loopback", "private"} else "web"
    if declared not in ("web", "native"):
        raise RegistrationError(
            "invalid_client_metadata", "application_type must be web or native."
        )
    if declared == "web" and kinds != {"https"}:
        raise RegistrationError(
            "invalid_redirect_uri", "A web client registers https redirect URIs only."
        )
    return declared


def check_grants(body):
    grant_types = _string_list(body, "grant_types", ["authorization_code"])
    if "authorization_code" not in grant_types or set(grant_types) - set(GRANT_TYPES):
        raise RegistrationError(
            "invalid_client_metadata",
            "grant_types must include authorization_code and may add refresh_token.",
        )
    response_types = _string_list(body, "response_types", ["code"])
    if response_types != ["code"]:
        raise RegistrationError(
            "invalid_client_metadata", 'response_types must be ["code"].'
        )
    return list(dict.fromkeys(grant_types)), response_types


def check_scope(value):
    if value is None:
        return None
    if not isinstance(value, str) or set(value.split()) - set(config.SCOPES):
        raise RegistrationError("invalid_client_metadata", "Unknown scope.")
    return value


def _purge_unused():
    cutoff = timezone.now() - UNUSED_REGISTRATION_TTL
    AgentccMCPOAuthClient.objects.filter(
        kind=AgentccMCPOAuthClient.Kind.DCR, created_at__lt=cutoff
    ).exclude(
        Exists(AgentccMCPOAuthConsent.objects.filter(client=OuterRef("pk")))
    ).exclude(
        Exists(AgentccMCPOAuthRefreshToken.objects.filter(client=OuterRef("pk")))
    ).delete()


def register(body) -> dict:
    if not isinstance(body, dict):
        raise RegistrationError("invalid_client_metadata", "Send a JSON object.")
    if "client_id" in body or "client_secret" in body:
        raise RegistrationError(
            "invalid_client_metadata", "client_id and client_secret are assigned here."
        )
    redirect_uris = check_redirect_uris(body.get("redirect_uris"))
    application_type = application_type_for(redirect_uris, body.get("application_type"))
    auth_method = body.get("token_endpoint_auth_method", "client_secret_basic")
    if auth_method not in AUTH_METHODS:
        raise RegistrationError(
            "invalid_client_metadata",
            "token_endpoint_auth_method must be none, client_secret_post or "
            "client_secret_basic.",
        )
    grant_types, response_types = check_grants(body)
    scope = check_scope(body.get("scope"))
    name = _text(body.get("client_name"), "client_name")
    client_uri = _text(body.get("client_uri"), "client_uri", 2048)
    if client_uri and clients.redirect_uri_kind(client_uri) != "https":
        client_uri = None
    metadata = {
        "client_name": name,
        "client_uri": client_uri,
        "token_endpoint_auth_method": auth_method,
        "grant_types": grant_types,
        "response_types": response_types,
        "scope": scope,
        "software_id": _text(body.get("software_id"), "software_id"),
        "software_version": _text(body.get("software_version"), "software_version"),
    }
    secret = None
    if auth_method != "none":
        secret = secrets.token_urlsafe(32)
        metadata["client_secret_hash"] = clients.secret_hash(secret)
    _purge_unused()
    row = AgentccMCPOAuthClient.objects.create(
        client_id=f"mcpc_{secrets.token_urlsafe(24)}",
        kind=AgentccMCPOAuthClient.Kind.DCR,
        metadata={k: v for k, v in metadata.items() if v is not None},
        redirect_uris=redirect_uris,
        application_type=application_type,
    )
    answer = {
        "client_id": row.client_id,
        "client_id_issued_at": int(row.created_at.timestamp()),
        "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": auth_method,
        "grant_types": grant_types,
        "response_types": response_types,
        "application_type": application_type,
    }
    if name:
        answer["client_name"] = name
    if scope:
        answer["scope"] = scope
    if secret is not None:
        answer["client_secret"] = secret
        answer["client_secret_expires_at"] = 0
    return answer
