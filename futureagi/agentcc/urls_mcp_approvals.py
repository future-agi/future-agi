"""Console approvals, included ahead of the generic router."""

from django.urls import path

from agentcc.views.mcp_approvals import (
    MCPApprovalDecisionView,
    MCPApprovalDetailView,
    MCPApprovalListView,
)

urlpatterns = [
    path("mcp/approvals/", MCPApprovalListView.as_view(), name="mcp-approval-list"),
    path(
        "mcp/approvals/<str:approval_id>/",
        MCPApprovalDetailView.as_view(),
        name="mcp-approval-detail",
    ),
    path(
        "mcp/approvals/<str:approval_id>/decision/",
        MCPApprovalDecisionView.as_view(),
        name="mcp-approval-decision",
    ),
]
