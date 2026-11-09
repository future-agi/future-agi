"""The org's own OAuth client routes (CN11), under /agentcc/connectors/.

Mounted by agentcc/urls.py as the other modules are (``path("",
include(...))``), with the connect flow that resolves these clients
(``mcp_connect_clients``, CN3's resolver).
"""

from django.urls import path

from agentcc.views.connector_oauth_clients import (
    OAuthClientDetailView,
    OAuthClientListView,
    OAuthClientRotateView,
)

urlpatterns = [
    path(
        "connectors/oauth-clients/",
        OAuthClientListView.as_view(),
        name="agentcc-connector-oauth-clients",
    ),
    path(
        "connectors/oauth-clients/<uuid:credential_id>/",
        OAuthClientDetailView.as_view(),
        name="agentcc-connector-oauth-client-detail",
    ),
    path(
        "connectors/oauth-clients/<uuid:credential_id>/rotate/",
        OAuthClientRotateView.as_view(),
        name="agentcc-connector-oauth-client-rotate",
    ),
]
