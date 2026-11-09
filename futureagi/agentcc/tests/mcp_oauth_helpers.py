"""Shared fakes for the MCP authorization server tests (M24, M24x). Every value
here is a fake; nothing is sent to any outside host."""

import base64
import hashlib
import secrets
import uuid
from urllib.parse import parse_qs, urlsplit

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models.mcp_oauth import AgentccMCPOAuthClient
from agentcc.models.org_config import AgentccOrgConfig
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

ISSUER = "https://api.example.test/agentcc/mcp-oauth"
GATEWAY = "https://gw.example.test"
RESOURCE_BASE = f"{GATEWAY}/mcp"
CONSENT_URL = "https://app.example.test/gateway/mcp/authorize"
VS_A = "vs_01JZ0000000000000000000AAA"
VS_B = "vs_01JZ0000000000000000000BBB"
CLIENT_ID = "fake-preregistered-client"
REDIRECT = "https://client.example.test/oauth/callback"


def configure(settings):
    settings.AGENTCC_MCP_OAUTH_ISSUER = ISSUER
    settings.AGENTCC_MCP_OAUTH_RESOURCE_BASES = RESOURCE_BASE
    settings.AGENTCC_MCP_OAUTH_CONSENT_URL = CONSENT_URL
    settings.AGENTCC_MCP_OAUTH_EE_REDIRECT_URIS = ""
    settings.AGENTCC_MCP_OAUTH_CIMD_ALLOWED_HOSTS = ""


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def preregistered(client_id=CLIENT_ID, redirect_uris=(REDIRECT,), name="Fake Client"):
    return AgentccMCPOAuthClient.objects.create(
        client_id=client_id,
        kind=AgentccMCPOAuthClient.Kind.PREREGISTERED,
        metadata={"client_name": name},
        redirect_uris=list(redirect_uris),
    )


def org_with_virtual_server(org, vs_id, slug="crm"):
    AgentccOrgConfig.no_workspace_objects.filter(
        organization=org, is_active=True
    ).update(is_active=False)
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=(AgentccOrgConfig.all_objects.filter(organization=org).count() + 1),
        is_active=True,
        mcp={"virtual_servers": {slug: {"id": vs_id, "members": ["github"]}}},
    )


def member(org, *, level=Level.MEMBER, name="Member"):
    """A user who is an active member of ``org``, with an API client signed
    in as them."""
    person = User.objects.create_user(
        email=f"m24-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name=name,
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=person,
        organization=org,
        role=OrganizationRoles.MEMBER,
        level=level,
        is_active=True,
    )
    workspace = Workspace.objects.filter(organization=org, is_default=True).first()
    if workspace is None:
        workspace = Workspace.objects.create(
            name=f"{org.name} default",
            organization=org,
            is_default=True,
            is_active=True,
            created_by=person,
        )
    WorkspaceMembership.no_workspace_objects.create(
        user=person,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        organization_membership=membership,
        is_active=True,
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=person)
    client.set_workspace(workspace)
    return person, client


def new_org(name="Other Org"):
    return Organization.objects.create(name=name)


def authorize_params(challenge, **overrides):
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": f"{RESOURCE_BASE}/v/{VS_A}",
        "scope": "mcp:tools",
        "state": "fake-state-123",
    }
    params.update(overrides)
    return {k: v for k, v in params.items() if v is not None}


def query_of(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def start(client, challenge, **overrides):
    """GET /authorize; returns the pending request id from the consent-page
    redirect."""
    response = client.get(
        "/agentcc/mcp-oauth/authorize", authorize_params(challenge, **overrides)
    )
    assert response.status_code == 302, response.content
    location = response["Location"]
    assert location.startswith(CONSENT_URL + "?")
    return query_of(location)["request"]


def consent_path(request_id):
    return f"/agentcc/mcp-oauth/consent/{request_id}/"


def approve(api_client, request_id, organization_id=None):
    body = {"decision": "approve"}
    if organization_id is not None:
        body["organization_id"] = str(organization_id)
    response = api_client.post(consent_path(request_id), body, format="json")
    assert response.status_code == 200, response.content
    return response.json()["result"]["redirect_to"]


def sign_in(web_client, api_client, challenge, **overrides):
    """The whole browser leg: authorize, consent as ``api_client``'s user;
    returns the authorization code."""
    request_id = start(web_client, challenge, **overrides)
    redirect_to = approve(api_client, request_id)
    query = query_of(redirect_to)
    assert query["iss"] == ISSUER
    return query["code"]
