"""OAuth 2.1 authorization-server routes for MCP clients, under /agentcc/mcp-oauth/.

Included ahead of the router in agentcc/urls.py. The endpoint paths carry no
trailing slash: clients call exactly the URLs the metadata advertises.
"""

from django.urls import path

from agentcc.mcp_oauth import views
from agentcc.mcp_oauth.consent_api import (
    ConsentDetailView,
    ConsentListView,
    ConsentRequestView,
)

urlpatterns = [
    path(
        "mcp-oauth/.well-known/openid-configuration",
        views.metadata_view,
        name="agentcc-mcp-oauth-metadata-appended",
    ),
    path(
        "mcp-oauth/authorize", views.authorize_view, name="agentcc-mcp-oauth-authorize"
    ),
    path("mcp-oauth/token", views.token_view, name="agentcc-mcp-oauth-token"),
    path("mcp-oauth/jwks.json", views.jwks_view, name="agentcc-mcp-oauth-jwks"),
    path("mcp-oauth/register", views.register_view, name="agentcc-mcp-oauth-register"),
    path("mcp-oauth/revoke", views.revoke_view, name="agentcc-mcp-oauth-revoke"),
    path(
        "mcp-oauth/consent/<str:request_id>/",
        ConsentRequestView.as_view(),
        name="agentcc-mcp-oauth-consent",
    ),
    path(
        "mcp-oauth/consents/",
        ConsentListView.as_view(),
        name="agentcc-mcp-oauth-consents",
    ),
    path(
        "mcp-oauth/consents/<int:consent_id>/",
        ConsentDetailView.as_view(),
        name="agentcc-mcp-oauth-consent-detail",
    ),
]
