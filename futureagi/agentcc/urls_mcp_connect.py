"""Per-user upstream OAuth connect routes, under /agentcc/mcp/connect/ (M25x).

Also the gateway's internal per-user token endpoint,
/agentcc/internal/mcp/upstream-token/, and the personal-key toggle,
/agentcc/mcp/keys/{key_id}/personal/, and the member's own accounts,
GET /agentcc/mcp/connect/. Included ahead of the router in
agentcc/urls.py. The fixed paths come before the ones that take a server id.
"""

from django.urls import path

from agentcc.views.mcp_connect import (
    MCPConnectStartView,
    MCPDisconnectView,
    MCPOwnConnectionListView,
    client_metadata_document,
    connect_callback,
)
from agentcc.views.mcp_internal_tokens import upstream_token
from agentcc.views.mcp_personal_keys import MCPPersonalKeyView

urlpatterns = [
    path(
        "internal/mcp/upstream-token/",
        upstream_token,
        name="agentcc-mcp-internal-upstream-token",
    ),
    path(
        "mcp/connect/",
        MCPOwnConnectionListView.as_view(),
        name="agentcc-mcp-connect-list",
    ),
    path(
        "mcp/connect/callback/",
        connect_callback,
        name="agentcc-mcp-connect-callback",
    ),
    path(
        "mcp/connect/client.json",
        client_metadata_document,
        name="agentcc-mcp-connect-client-metadata",
    ),
    path(
        "mcp/connect/<str:server_id>/start/",
        MCPConnectStartView.as_view(),
        name="agentcc-mcp-connect-start",
    ),
    path(
        "mcp/connect/<str:server_id>/",
        MCPDisconnectView.as_view(),
        name="agentcc-mcp-connect-disconnect",
    ),
    path(
        "mcp/keys/<uuid:key_id>/personal/",
        MCPPersonalKeyView.as_view(),
        name="agentcc-mcp-personal-key",
    ),
]
