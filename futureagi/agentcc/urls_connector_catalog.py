"""Connector catalog routes (CN9), under /agentcc/connectors/.

agentcc/urls.py includes this module ahead of the router (CN-ADD); its
comment there says what is addable on this build.
"""

from django.urls import path

from agentcc.views.connector_catalog import (
    ConnectorAddView,
    ConnectorCatalogAppView,
    ConnectorCatalogListView,
)
from agentcc.views.connector_mcp_oauth_app import MCPOAuthAppView, MCPOAuthDiscoveryView

urlpatterns = [
    path(
        "connectors/apps/<str:app_id>/oauth/",
        MCPOAuthAppView.as_view(),
        name="agentcc-connector-mcp-oauth",
    ),
    path(
        "connectors/apps/<str:app_id>/oauth/discovery/",
        MCPOAuthDiscoveryView.as_view(),
        name="agentcc-connector-mcp-oauth-discovery",
    ),
    path(
        "connectors/catalog/",
        ConnectorCatalogListView.as_view(),
        name="agentcc-connector-catalog",
    ),
    path(
        "connectors/catalog/<str:app_id>/",
        ConnectorCatalogAppView.as_view(),
        name="agentcc-connector-catalog-app",
    ),
    path(
        "connectors/apps/<str:app_id>/add/",
        ConnectorAddView.as_view(),
        name="agentcc-connector-add",
    ),
]
