"""Where the authorization server lives and what it hands out.

Every value is read when it is used, from Django settings first and the
environment second, so a deployment sets it once (Helm env) and a test
overrides it with ``override_settings``.
"""

import os

from django.conf import settings

ISSUER_PATH = "/agentcc/mcp-oauth"

# Access tokens live at most 15 minutes (decision 37): revocation, consent and
# org membership are re-checked at every refresh, so nothing outlives a
# removal by more than this.
ACCESS_TOKEN_SECONDS = 900
AUTHORIZATION_CODE_SECONDS = 600
PENDING_REQUEST_SECONDS = 600
REFRESH_TOKEN_SECONDS = 30 * 24 * 3600

SCOPES = {
    "mcp:tools": "List and call the tools of this MCP server",
    "mcp:resources": "Read the resources of this MCP server",
    "mcp:prompts": "Use the prompts of this MCP server",
}
SCOPE_ORDER = ("mcp:tools", "mcp:resources", "mcp:prompts")

ACCESS_TOKEN_TYPE = "futureagi-mcp-access"
SIGNING_ALG = "ES256"


def _setting(name, default=""):
    value = getattr(settings, name, None)
    if value is None:
        value = os.environ.get(name, default)
    return value


def issuer() -> str:
    """``https://<api host>/agentcc/mcp-oauth``: AGENTCC_MCP_OAUTH_ISSUER, else
    BASE_URL plus the issuer path."""
    explicit = str(_setting("AGENTCC_MCP_OAUTH_ISSUER") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    base = str(getattr(settings, "BASE_URL", "") or "http://localhost:8000")
    return base.rstrip("/") + ISSUER_PATH


def endpoint(name: str) -> str:
    return f"{issuer()}/{name}"


def mcp_endpoint_path() -> str:
    path = str(_setting("AGENTCC_MCP_ENDPOINT_PATH") or "/mcp").strip() or "/mcp"
    return "/" + path.strip("/")


def resource_bases() -> list[str]:
    """The gateway MCP endpoints a token may be bound to, without a trailing
    slash: AGENTCC_MCP_OAUTH_RESOURCE_BASES (comma separated), else the public
    gateway URL plus the MCP endpoint path."""
    listed = str(_setting("AGENTCC_MCP_OAUTH_RESOURCE_BASES") or "")
    bases = [b.strip().rstrip("/") for b in listed.split(",") if b.strip()]
    if bases:
        return bases
    gateway = str(
        _setting("AGENTCC_GATEWAY_PUBLIC_URL")
        or _setting("AGENTCC_GATEWAY_URL")
        or "http://localhost:8090"
    ).rstrip("/")
    return [gateway + mcp_endpoint_path()]


def consent_page_url() -> str:
    """The console page that shows the consent screen to a signed-in person."""
    explicit = str(_setting("AGENTCC_MCP_OAUTH_CONSENT_URL") or "").strip()
    if explicit:
        return explicit
    frontend = str(getattr(settings, "FRONTEND_BASE_URL", "") or "")
    return (frontend or "http://localhost:3031").rstrip("/") + "/gateway/mcp/authorize"


def cimd_allowed_hosts() -> set[str]:
    """Hosts whose client ID metadata documents are accepted; empty means any
    public host."""
    listed = str(_setting("AGENTCC_MCP_OAUTH_CIMD_ALLOWED_HOSTS") or "")
    return {h.strip().lower() for h in listed.split(",") if h.strip()}


def first_party_client() -> dict | None:
    """The pre-registered first-party client (Future AGI Agents, EE), or None
    when its redirect URIs are not configured."""
    uris = str(_setting("AGENTCC_MCP_OAUTH_EE_REDIRECT_URIS") or "")
    redirect_uris = [u.strip() for u in uris.split(",") if u.strip()]
    if not redirect_uris:
        return None
    client_id = (
        str(_setting("AGENTCC_MCP_OAUTH_EE_CLIENT_ID") or "").strip() or "futureagi-ee"
    )
    name = str(_setting("AGENTCC_MCP_OAUTH_EE_CLIENT_NAME") or "").strip()
    return {
        "client_id": client_id,
        "client_name": name or "Future AGI Agents",
        "redirect_uris": redirect_uris,
    }
