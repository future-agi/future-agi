"""MCP tool-call ingestion and explorer routes, under /agentcc/mcp/."""

from django.urls import path

from agentcc.views.mcp_tool_calls import (
    MCPToolCallDetailView,
    MCPToolCallExportView,
    MCPToolCallListView,
)

urlpatterns = [
    path(
        "mcp/tool-calls/",
        MCPToolCallListView.as_view(),
        name="agentcc-mcp-tool-calls",
    ),
    path(
        "mcp/tool-calls/export/",
        MCPToolCallExportView.as_view(),
        name="agentcc-mcp-tool-calls-export",
    ),
    path(
        "mcp/tool-calls/<int:pk>/",
        MCPToolCallDetailView.as_view(),
        name="agentcc-mcp-tool-call-detail",
    ),
]
