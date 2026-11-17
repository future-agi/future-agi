"""OAuth and MCP discovery documents under /.well-known/.

Included at the site root from tfc/urls.py, not from agentcc/urls.py. The
authorization server for MCP clients has the issuer
``https://<host>/agentcc/mcp-oauth``, so its metadata sits at the
path-inserted locations (RFC 8414 section 3.1). tfc/asgi.py hands exactly
these two paths to Django; every other /.well-known/oauth-* path stays with
the platform's own MCP server.
"""

from django.urls import path

from agentcc.mcp_oauth.views import metadata_view

urlpatterns = [
    path(
        ".well-known/oauth-authorization-server/agentcc/mcp-oauth",
        metadata_view,
        name="agentcc-mcp-oauth-metadata",
    ),
    path(
        ".well-known/openid-configuration/agentcc/mcp-oauth",
        metadata_view,
        name="agentcc-mcp-oauth-openid-configuration",
    ),
]
