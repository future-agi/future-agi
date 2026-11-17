"""MCP tool-revision (pinning and quarantine) routes, under /agentcc/mcp/.

Included ahead of the router in agentcc/urls.py.
"""

from django.urls import path

from agentcc.views.mcp_revisions import (
    MCPRevisionApproveBaselineView,
    MCPRevisionApproveView,
    MCPRevisionListView,
    MCPRevisionRejectView,
)

urlpatterns = [
    path(
        "mcp/revisions/",
        MCPRevisionListView.as_view(),
        name="agentcc-mcp-revisions",
    ),
    path(
        "mcp/revisions/approve-baseline/",
        MCPRevisionApproveBaselineView.as_view(),
        name="agentcc-mcp-revisions-approve-baseline",
    ),
    path(
        "mcp/revisions/<int:revision_id>/approve/",
        MCPRevisionApproveView.as_view(),
        name="agentcc-mcp-revision-approve",
    ),
    path(
        "mcp/revisions/<int:revision_id>/reject/",
        MCPRevisionRejectView.as_view(),
        name="agentcc-mcp-revision-reject",
    ),
]
