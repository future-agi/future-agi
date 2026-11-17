"""The authorization server's HTTP surface.

The OAuth protocol endpoints are plain Django views: their bodies, errors and
headers are fixed by the RFCs (form-encoded requests, ``{"error": ...}``
answers, ``Cache-Control: no-store``), not by the console's envelope.
"""

import base64
import binascii
import json
from urllib.parse import unquote, urlencode

from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from agentcc.mcp_oauth import (
    authorize,
    clients,
    config,
    dcr,
    keys,
    revocation,
    tokens,
)
from agentcc.mcp_oauth.metadata import authorization_server_metadata


def _json(body, status=200, *, cache=None):
    response = JsonResponse(body, status=status)
    response["Cache-Control"] = cache or "no-store"
    if cache is None:
        response["Pragma"] = "no-cache"
    return response


@require_GET
def metadata_view(request):
    return _json(authorization_server_metadata(), cache="public, max-age=300")


def _no_frames(response):
    response["X-Frame-Options"] = "DENY"
    response["Content-Security-Policy"] = "frame-ancestors 'none'"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response


class _Redirect(HttpResponseRedirect):
    # A client's redirect URI may use its own app scheme (cursor://, vscode://).
    allowed_schemes = None

    def __init__(self, url):
        self.allowed_schemes = [url.split(":", 1)[0]]
        super().__init__(url)


def _error_page(error, description):
    body = (
        "<!doctype html><meta charset=utf-8><title>Sign-in request refused</title>"
        f"<h1>This sign-in request was refused</h1><p>{escape(description)}</p>"
        f"<p><code>{escape(error)}</code></p>"
    )
    return HttpResponse(body, status=400, content_type="text/html; charset=utf-8")


@require_GET
def authorize_view(request):
    """GET /agentcc/mcp-oauth/authorize: check the request, then send the
    browser to the console's consent page with a pending request id."""
    params = request.GET
    try:
        _client, pending = authorize.validate(params)
    except authorize.AuthorizeError as e:
        if not e.redirect:
            return _no_frames(_error_page(e.error, e.description))
        return _no_frames(
            _Redirect(authorize.error_redirect(params, e.error, e.description))
        )
    request_id = authorize.store(pending)
    target = f"{config.consent_page_url()}?{urlencode({'request': request_id})}"
    return _no_frames(HttpResponseRedirect(target))


@require_GET
def jwks_view(request):
    return _json(keys.jwks(), cache="public, max-age=300")


def _oauth_error(error, description, status=400, *, basic=False):
    response = _json({"error": error, "error_description": description}, status)
    if status == 401 and basic:
        response["WWW-Authenticate"] = 'Basic realm="agentcc-mcp-oauth"'
    return response


def _client_credentials(request):
    """``(client_id, secret, used_basic)`` from HTTP Basic (RFC 6749 2.3.1,
    each part form-url-encoded) or the form body."""
    header = request.META.get("HTTP_AUTHORIZATION", "")
    if header[:6].lower() == "basic ":
        try:
            decoded = base64.b64decode(header[6:].strip(), validate=True).decode()
        except (binascii.Error, UnicodeDecodeError):
            return None, None, True
        client_id, sep, secret = decoded.partition(":")
        if not sep:
            return None, None, True
        return unquote(client_id), unquote(secret), True
    return request.POST.get("client_id"), request.POST.get("client_secret"), False


def authenticated_client(request):
    """The calling client, or an ``_oauth_error`` response."""
    client_id, secret, basic = _client_credentials(request)
    body_id = request.POST.get("client_id")
    if basic and body_id and body_id != client_id:
        return None, _oauth_error("invalid_request", "client_id mismatch.")
    client = clients.resolve(client_id)
    if client is None or not clients.authenticate(client, secret):
        return None, _oauth_error(
            "invalid_client", "Client authentication failed.", 401, basic=basic
        )
    return client, None


@csrf_exempt
@require_POST
def token_view(request):
    """POST /agentcc/mcp-oauth/token: authorization_code and refresh_token."""
    client, refused = authenticated_client(request)
    if refused is not None:
        return refused
    grant_type = request.POST.get("grant_type")
    try:
        if grant_type == "authorization_code":
            body = tokens.exchange_code(client, request.POST)
        elif grant_type == "refresh_token":
            body = tokens.refresh(client, request.POST)
        else:
            return _oauth_error("unsupported_grant_type", "Unsupported grant_type.")
    except tokens.TokenError as e:
        return _oauth_error(e.error, e.description, e.status)
    return _json(body)


@csrf_exempt
@require_POST
def register_view(request):
    """POST /agentcc/mcp-oauth/register (RFC 7591): JSON in, 201 with the new
    client_id (and a secret, once, for a confidential client)."""
    if len(request.body) > dcr.MAX_BODY_BYTES:
        return _oauth_error("invalid_client_metadata", "The request is too large.")
    content_type = request.content_type or ""
    if content_type != "application/json":
        return _oauth_error("invalid_client_metadata", "Send application/json.")
    try:
        body = json.loads(request.body or b"null")
    except (ValueError, UnicodeDecodeError):
        return _oauth_error("invalid_client_metadata", "The body is not JSON.")
    try:
        answer = dcr.register(body)
    except dcr.RegistrationError as e:
        return _oauth_error(e.error, e.description)
    return _json(answer, status=201)


@csrf_exempt
@require_POST
def revoke_view(request):
    """POST /agentcc/mcp-oauth/revoke (RFC 7009): 200 whenever the client
    authenticates and names a token, whatever the token was."""
    client, refused = authenticated_client(request)
    if refused is not None:
        return refused
    token = request.POST.get("token")
    if not token:
        return _oauth_error("invalid_request", "token is required.")
    revocation.revoke_token(client, token)
    return _json({})
