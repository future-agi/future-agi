"""SIEM export routes (G16-SIEM), under /agentcc/siem/.

Included ahead of the router in agentcc/urls.py.
"""

from django.urls import path

from agentcc.views.siem import (
    SIEMConnectionTestView,
    SIEMDeliveryDiscardView,
    SIEMDeliveryListView,
    SIEMDeliveryRetryView,
    SIEMDestinationDetailView,
    SIEMDestinationListView,
    SIEMDestinationRotateView,
)

urlpatterns = [
    path(
        "siem/destinations/test-connection/",
        SIEMConnectionTestView.as_view(),
        name="agentcc-siem-test-connection",
    ),
    path(
        "siem/destinations/",
        SIEMDestinationListView.as_view(),
        name="agentcc-siem-destinations",
    ),
    path(
        "siem/destinations/<uuid:pk>/",
        SIEMDestinationDetailView.as_view(),
        name="agentcc-siem-destination-detail",
    ),
    path(
        "siem/destinations/<uuid:pk>/rotate-secret/",
        SIEMDestinationRotateView.as_view(),
        name="agentcc-siem-destination-rotate-secret",
    ),
    path(
        "siem/deliveries/",
        SIEMDeliveryListView.as_view(),
        name="agentcc-siem-deliveries",
    ),
    path(
        "siem/deliveries/<uuid:pk>/retry/",
        SIEMDeliveryRetryView.as_view(),
        name="agentcc-siem-delivery-retry",
    ),
    path(
        "siem/deliveries/<uuid:pk>/discard/",
        SIEMDeliveryDiscardView.as_view(),
        name="agentcc-siem-delivery-discard",
    ),
]
