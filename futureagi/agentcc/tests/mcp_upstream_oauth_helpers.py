"""Shared helpers for the per-user upstream OAuth tests (M25x).

``FakeVendor`` stands in for a vendor MCP server and its authorization
server, answered in place of ``mcp_safe_fetch.fetch``: protected-resource and
authorization-server metadata, client registration (DCR), and a token
endpoint that checks PKCE S256, single-use codes, the client, the redirect
URI and the resource as a real one does. A client registered with it in
advance (``confidential``) must prove its secret on every token, refresh and
revocation request, by HTTP Basic or in the form (RFC 6749 2.3.1). Nothing
here reaches a network.
"""

import base64
import hashlib
import secrets
import uuid
from urllib.parse import parse_qs, unquote_plus, urlsplit

import urllib3

from accounts.models import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.services import mcp_safe_fetch
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

SERVER_ID = "issues"
SERVER_URL = "https://mcp.vendor.example/mcp"
ISSUER = "https://auth.vendor.example"
CALLBACK = "https://api.example.test/agentcc/mcp/connect/callback/"
CLIENT_METADATA = "https://api.example.test/agentcc/mcp/connect/client.json"
CONSOLE = "https://app.example.test/dashboard/gateway/integrations"
INTERNAL_SECRET = "test-m25x-internal-secret-0123456789-not-real"
ADMIN_TOKEN = "test-m25x-admin-token-0123456789abcdef-not-real"
START = "/agentcc/mcp/connect/{}/start/"
CALLBACK_PATH = "/agentcc/mcp/connect/callback/"


def _response(status, body=None, raw=None):
    import json

    content = raw if raw is not None else json.dumps(body or {}).encode()
    return mcp_safe_fetch.FetchResponse(
        status=status, headers=urllib3.HTTPHeaderDict(), content=content, url=""
    )


class FakeVendor:
    def __init__(
        self,
        *,
        cimd=True,
        dcr=False,
        iss=True,
        s256=True,
        revocation=True,
        issuer=ISSUER,
        resource=SERVER_URL,
        expires_in=900,
        confidential=None,
        auth_methods=None,
    ):
        self.cimd, self.dcr, self.iss, self.s256 = cimd, dcr, iss, s256
        # Pre-registered clients, {client_id: secret}, and the token endpoint
        # auth methods the metadata advertises (None: the field is absent).
        self.confidential = dict(confidential or {})
        self.auth_methods = auth_methods
        # How each token endpoint and revocation request authenticated:
        # (endpoint, method, client_id, secret_ok), method "basic", "post" or
        # "none".
        self.client_auths = []
        self.revocation = revocation
        self.issuer = issuer
        self.resource = resource
        self.expires_in = expires_in
        self.requests = []
        self.codes = {}
        self.registrations = []
        self.revoked = []
        self.issued = []
        self.refresh_fails = False
        self.refreshes = []
        self._used_refresh = set()
        self._n = 0

    # -- the fetcher's stand-in --
    def fetch(
        self, url, *, method="GET", data=None, json_body=None, headers=None, **kwargs
    ):
        self.requests.append((method, url, data, json_body, dict(headers or {})))
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if method == "GET" and url == (
            "https://mcp.vendor.example/.well-known/oauth-protected-resource/mcp"
        ):
            return _response(
                200,
                {
                    "resource": self.resource,
                    "authorization_servers": [ISSUER],
                    "scopes_supported": ["mcp"],
                },
            )
        if (
            method == "GET"
            and origin == ISSUER
            and parts.path == ("/.well-known/oauth-authorization-server")
        ):
            return _response(200, self.metadata())
        if method == "POST" and url == ISSUER + "/register" and self.dcr:
            self._n += 1
            self.registrations.append(json_body)
            return _response(
                201,
                {
                    "client_id": f"dcr-client-{self._n}",
                    "token_endpoint_auth_method": "none",
                },
            )
        if method == "POST" and url == ISSUER + "/token":
            form, refused = self._authenticated("token", data or {}, headers or {})
            return refused or self._token(form)
        if method == "POST" and url == ISSUER + "/revoke":
            form, refused = self._authenticated("revoke", data or {}, headers or {})
            if refused:
                return refused
            self.revoked.append(form)
            return _response(200, {})
        return _response(404, {"error": "not_found"})

    def metadata(self):
        meta = {
            "issuer": self.issuer,
            "authorization_endpoint": ISSUER + "/authorize",
            "token_endpoint": ISSUER + "/token",
            "code_challenge_methods_supported": ["S256"] if self.s256 else ["plain"],
            "client_id_metadata_document_supported": self.cimd,
            "authorization_response_iss_parameter_supported": self.iss,
        }
        if self.dcr:
            meta["registration_endpoint"] = ISSUER + "/register"
        if self.revocation:
            meta["revocation_endpoint"] = ISSUER + "/revoke"
        if self.auth_methods is not None:
            meta["token_endpoint_auth_methods_supported"] = list(self.auth_methods)
        return meta

    def _authenticated(self, endpoint, form, headers):
        """The form with the client it authenticated as in ``client_id``, or
        a 401 ``invalid_client``. A pre-registered client must prove its
        secret, by exactly one method; any other client is public."""
        form = dict(form)
        basic = headers.get("Authorization", "")
        method, secret = "none", None
        if basic.startswith("Basic "):
            raw = base64.b64decode(basic[len("Basic ") :]).decode()
            client_id, _, secret = raw.partition(":")
            client_id, secret = unquote_plus(client_id), unquote_plus(secret)
            if "client_secret" in form or form.get("client_id", client_id) != client_id:
                return form, _response(401, {"error": "invalid_client"})
            form["client_id"] = client_id
            method = "basic"
        elif "client_secret" in form:
            secret = form.pop("client_secret")
            method = "post"
        client_id = form.get("client_id")
        expected = self.confidential.get(client_id)
        ok = expected is None or (secret is not None and secret == expected)
        self.client_auths.append((endpoint, method, client_id, ok))
        if not ok or (expected is None and secret is not None):
            return form, _response(401, {"error": "invalid_client"})
        return form, None

    # -- the vendor's sign-in page --
    def authorize(self, authorize_url):
        """The member signs in at the vendor: a code for the authorize
        request, and the state to send back."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        code = "test-code-" + secrets.token_hex(8)
        self.codes[code] = query
        return code, query["state"]

    def _token(self, form):
        grant = form.get("grant_type")
        if grant == "authorization_code":
            asked = self.codes.pop(form.get("code"), None)
            if asked is None:
                return _response(400, {"error": "invalid_grant"})
            verifier = form.get("code_verifier", "")
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .rstrip(b"=")
                .decode()
            )
            if (
                asked.get("code_challenge_method") != "S256"
                or challenge != asked.get("code_challenge")
                or form.get("client_id") != asked.get("client_id")
                or form.get("redirect_uri") != asked.get("redirect_uri")
                or form.get("resource") != asked.get("resource")
            ):
                return _response(400, {"error": "invalid_grant"})
            return self._issue()
        if grant == "refresh_token":
            presented = form.get("refresh_token")
            self.refreshes.append(dict(form))
            if (
                self.refresh_fails
                or presented in self._used_refresh
                or presented not in [t["refresh_token"] for t in self.issued]
            ):
                return _response(400, {"error": "invalid_grant"})
            # Refresh tokens rotate: each works once.
            self._used_refresh.add(presented)
            return self._issue()
        return _response(400, {"error": "unsupported_grant_type"})

    def grant(self):
        """Tokens as a completed sign-in would get them."""
        self._issue()
        return self.issued[-1]

    def _issue(self):
        self._n += 1
        tokens = {
            "access_token": f"test-upstream-at-{self._n}-not-real",
            "token_type": "Bearer",
            "expires_in": self.expires_in,
            "refresh_token": f"test-upstream-rt-{self._n}-not-real",
            "scope": "mcp",
        }
        if self.expires_in is None:
            del tokens["expires_in"]
        self.issued.append(tokens)
        return _response(200, tokens)


def seed_config(org, servers=None):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        mcp={
            "servers": servers
            if servers is not None
            else {
                SERVER_ID: {
                    "url": SERVER_URL,
                    "transport": "http",
                    "credential": {"mode": "per_user"},
                },
                "shared-one": {
                    "url": "https://mcp.shared.example/mcp",
                    "transport": "http",
                    "credential": {"mode": "shared"},
                },
            }
        },
    )


def move_server(org, url):
    config = AgentccOrgConfig.no_workspace_objects.get(organization=org, is_active=True)
    config.mcp["servers"][SERVER_ID]["url"] = url
    config.save(update_fields=["mcp"])


def member(org, workspace, level=Level.MEMBER):
    """A new member of ``org``, and an API client signed in as them."""
    user = User.objects.create_user(
        email=f"m25x-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="M25x Member",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.MEMBER,
        level=level,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return user, client
