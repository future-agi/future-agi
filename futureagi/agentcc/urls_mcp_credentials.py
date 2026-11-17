"""MCP credential vault and connection-probe routes, under /agentcc/mcp/.

Included ahead of the router in agentcc/urls.py.
"""

from django.urls import path

from agentcc.views.mcp_credentials import (
    MCPCredentialDetailView,
    MCPCredentialListView,
    MCPCredentialRotateView,
    MCPCredentialUseManagedView,
)
from agentcc.views.mcp_probe import MCPProbeView

urlpatterns = [
    path(
        "mcp/credentials/",
        MCPCredentialListView.as_view(),
        name="agentcc-mcp-credentials",
    ),
    path(
        "mcp/credentials/<uuid:credential_id>/",
        MCPCredentialDetailView.as_view(),
        name="agentcc-mcp-credential-detail",
    ),
    path(
        "mcp/credentials/<uuid:credential_id>/rotate/",
        MCPCredentialRotateView.as_view(),
        name="agentcc-mcp-credential-rotate",
    ),
    path(
        "mcp/credentials/<str:server_id>/use-managed/",
        MCPCredentialUseManagedView.as_view(),
        name="agentcc-mcp-credential-use-managed",
    ),
    path("mcp/probe/", MCPProbeView.as_view(), name="agentcc-mcp-probe"),
]
