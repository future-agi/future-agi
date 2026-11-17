"""Partner lifecycle routes; every route is 404 while vendors are disabled."""

from django.urls import path

from agentcc.views.mcp_internal_vendor_credentials import vendor_credential
from agentcc.views.mcp_vendor_connections import (
    VendorBindingDetailView,
    VendorBindingsView,
    VendorConnectionsView,
    VendorConnectView,
    vendor_connect_page,
)

urlpatterns = [
    path(
        "mcp/vendor-bindings/<uuid:binding_id>/",
        VendorBindingDetailView.as_view(),
        name="mcp-vendor-binding-detail",
    ),
    path(
        "mcp/vendor-bindings/", VendorBindingsView.as_view(), name="mcp-vendor-bindings"
    ),
    path(
        "mcp/vendor-connections/",
        VendorConnectionsView.as_view(),
        name="mcp-vendor-connections",
    ),
    path(
        "mcp/vendor-connections/<str:server_id>/connect/",
        VendorConnectView.as_view(),
        name="mcp-vendor-connect",
    ),
    path(
        "mcp/vendor-connections/<str:server_id>/start/",
        vendor_connect_page,
        name="mcp-vendor-start",
    ),
    path(
        "mcp/vendors/internal/credential/",
        vendor_credential,
        name="mcp-vendor-internal",
    ),
]
