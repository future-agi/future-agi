"""Independent deployment gates, never organization-writable configuration.

Keep the bundle immutable: visibility is evaluated for every request, not cached
in connector_catalog's parsed plans. Missing/invalid settings fail closed.
"""

import re

from django.conf import settings
from django.http import Http404, JsonResponse
from django.urls.converters import UUIDConverter
from django.views.defaults import page_not_found

from agentcc.services.siem_settings import export_enabled

UPSTREAM_OAUTH = "AGENTCC_MCP_UPSTREAM_OAUTH_ENABLED"
CLIENT_OAUTH = "AGENTCC_MCP_CLIENT_OAUTH_ENABLED"
K2_APPS = "AGENTCC_MCP_K2_APPS_ENABLED"
# Console-only: the Connect tab's key-based client setup. No route to hide.
CLIENT_INSTALLS = "AGENTCC_MCP_CLIENT_INSTALLS_ENABLED"
TOOL_CALL_EXPLORER = "AGENTCC_MCP_TOOL_CALL_EXPLORER_ENABLED"
TOOL_CALL_DOWNLOAD = "AGENTCC_MCP_TOOL_CALL_DOWNLOAD_ENABLED"
K2_GROUPS = frozenset({"google", "microsoft", "arcade"})
K2_IDS = frozenset(
    {
        "gmail",
        "google-calendar",
        "google-bigquery",
        "microsoft-outlook",
        "microsoft-teams",
        "arcade",
    }
)
OAUTH_KINDS = frozenset({"oauth", "oauth2", "mcp_oauth"})
# Match urls_siem's seven patterns, with exactly zero or one final slash.
# Reuse Django's UUID syntax: malformed/uppercase IDs and lookalikes continue
# through the ordinary chain, rather than broadening the SIEM refusal family.
_SIEM_ROUTE = re.compile(
    rf"/agentcc/siem/(?:destinations(?:/test-connection|/{UUIDConverter.regex}"
    rf"(?:/rotate-secret)?)?|deliveries(?:/{UUIDConverter.regex}/(?:retry|discard))?)/?"
)


def enabled(name):
    return getattr(settings, name, False) is True


def capabilities():
    return {
        "upstream_oauth": enabled(UPSTREAM_OAUTH),
        "client_oauth": enabled(CLIENT_OAUTH),
        "k2_apps": enabled(K2_APPS),
        "client_installs": enabled(CLIENT_INSTALLS),
        "tool_call_explorer": enabled(TOOL_CALL_EXPLORER),
        "tool_call_download": enabled(TOOL_CALL_EXPLORER)
        and enabled(TOOL_CALL_DOWNLOAD),
        "siem_export": export_enabled(),
    }


def k2_app(app_id, card=None):
    return app_id in K2_IDS


def app_visible(app_id, card=None):
    return enabled(K2_APPS) or not k2_app(app_id, card)


def provider_visible(app_id, record):
    if not app_visible(app_id):
        return False
    auth = record.get("auth") or record.get("credential") or {}
    if not enabled(K2_APPS) and (
        record.get("id") == "arcade"
        or (auth.get("oauth2") or {}).get("clientGroup") in K2_GROUPS
    ):
        return False
    return enabled(UPSTREAM_OAUTH) or auth.get("kind") not in OAUTH_KINDS


def server_visible(entry):
    if not isinstance(entry, dict):
        return True
    credential = entry.get("credential")
    credential = credential if isinstance(credential, dict) else {}
    if not enabled(UPSTREAM_OAUTH) and (
        credential.get("mode") == "per_user" or credential.get("kind") in OAUTH_KINDS
    ):
        return False
    ref = entry.get("catalog_ref") or entry.get("catalogRef") or ""
    ref = ref if isinstance(ref, str) else ""
    app_id = ref.split("/", 1)[0]
    if not app_visible(app_id):
        return False
    if ref:
        from agentcc.services.connector_catalog import provider_record

        record = provider_record(entry)
        if record is not None and not provider_visible(app_id, record):
            return False
    return True


def hidden_server(org_id, server_id):
    if enabled(UPSTREAM_OAUTH) and enabled(K2_APPS):
        return False
    from django.core.exceptions import ValidationError

    from agentcc.services.mcp_upstream_oauth import server_entry

    try:
        entry = server_entry(org_id, server_id, include_hidden=True)
    except (ValueError, ValidationError):
        return False  # The ordinary endpoint still validates malformed ids.
    return entry is not None and not server_visible(entry)


def catalog_app_visible(app_id):
    if not app_visible(app_id):
        return False
    from agentcc.services import connector_catalog

    try:
        bundle = connector_catalog.load_bundle()
    except connector_catalog.CatalogUnavailable:
        return True  # Preserve the ordinary unavailable response.
    records = [record for (app, _), record in bundle.providers.items() if app == app_id]
    return not records or any(provider_visible(app_id, record) for record in records)


def not_found():
    response = JsonResponse(
        {"status": False, "code": "not_found", "message": "Not found."}, status=404
    )
    response["Cache-Control"] = "no-store"
    return response


def tool_call_unavailable(*, download=False):
    """NEW explorer/downloads only; no ingestion or legacy MCP side effects."""
    if not enabled(TOOL_CALL_EXPLORER):
        code = "mcp_tool_call_explorer_disabled"
        # NEW: minimal off-state copy; site gate review required.
        message = "The MCP tool-call explorer is off on this deployment."
    elif download and not enabled(TOOL_CALL_DOWNLOAD):
        code = "mcp_tool_call_download_disabled"
        # NEW: minimal off-state copy; site gate review required.
        message = "MCP tool-call downloads are off on this deployment."
    else:
        return None
    response = JsonResponse(
        {"status": False, "code": code, "message": message}, status=404
    )
    response["Cache-Control"] = "no-store"
    return response


def _catalog_oauth_app_route(directory):
    """A catalogue app's MCP OAuth app configuration or discovery route,
    /agentcc/connectors/apps/<app_id>/oauth/... (upstream OAuth). An app that
    also has a key-based provider stays visible, so the app check alone does
    not hide these routes."""
    prefix = "/agentcc/connectors/apps/"
    if not directory.startswith(prefix):
        return False
    parts = directory[len(prefix) :].split("/")
    return len(parts) > 2 and bool(parts[0]) and parts[1] == "oauth"


class MCPSubfeatureMiddleware:
    """Hide routes before authentication, body validation, redirects or I/O."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if _SIEM_ROUTE.fullmatch(path) and not export_enabled():
            # Refuse before CommonMiddleware, session auth and EE-key lookups.
            # Keep Django's canonical 404 builder, not the MCP JSON envelope.
            response = page_not_found(request, Http404())
            response["Cache-Control"] = "no-store"
            return response
        directory = path.rstrip("/") + "/"
        # Explorer/download gates exclude ingestion, shared traces and other
        # exports. SIEM has its own independent early gate above.
        # Exact route family, including slashless links before CommonMiddleware.
        prefix = "/agentcc/mcp/tool-calls/"
        if directory.startswith(prefix):
            item = directory[len(prefix) :].rstrip("/")
            if item in {"", "export"} or (item.isascii() and item.isdigit()):
                refusal = tool_call_unavailable(download=item == "export")
                if refusal is not None:
                    return refusal
        if not enabled(UPSTREAM_OAUTH) and (
            directory.startswith("/agentcc/mcp/connect/")
            or directory.startswith("/agentcc/connectors/oauth-clients/")
            or path.rstrip("/") == "/agentcc/internal/mcp/upstream-token"
            or _catalog_oauth_app_route(directory)
        ):
            return not_found()
        if not enabled(CLIENT_OAUTH) and (
            directory.startswith("/agentcc/mcp-oauth/")
            or path.rstrip("/")
            in {
                "/.well-known/oauth-authorization-server/agentcc/mcp-oauth",
                "/.well-known/openid-configuration/agentcc/mcp-oauth",
            }
        ):
            return not_found()
        # Direct catalog/add links must be indistinguishable from an unknown app,
        # even for unauthenticated callers. No org data is needed for this check.
        for prefix in ("/agentcc/connectors/catalog/", "/agentcc/connectors/apps/"):
            if path.startswith(prefix):
                app_id = path[len(prefix) :].split("/", 1)[0]
                if app_id and not catalog_app_visible(app_id):
                    return not_found()
        return self.get_response(request)
