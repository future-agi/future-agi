"""The webhooks API, mounted at ``webhooks/`` (DESIGN.md section 10). The ``cli_listen`` routes are WH-4b's."""

from django.urls import path

from webhooks import api_views as a
from webhooks import views as v

urlpatterns = [
    path("endpoints/", v.EndpointListView.as_view(), name="webhooks-endpoints"),
    path(
        "endpoints/<str:endpoint_id>/",
        v.EndpointDetailView.as_view(),
        name="webhooks-endpoint",
    ),
    path(
        "endpoints/<str:endpoint_id>/enable/",
        v.EndpointEnableView.as_view(),
        name="webhooks-endpoint-enable",
    ),
    path(
        "endpoints/<str:endpoint_id>/disable/",
        v.EndpointDisableView.as_view(),
        name="webhooks-endpoint-disable",
    ),
    path(
        "endpoints/<str:endpoint_id>/resume/",
        v.EndpointResumeView.as_view(),
        name="webhooks-endpoint-resume",
    ),
    path(
        "endpoints/<str:endpoint_id>/rotate-secret/",
        v.EndpointRotateView.as_view(),
        name="webhooks-endpoint-rotate-secret",
    ),
    path(
        "endpoints/<str:endpoint_id>/revoke-previous-secret/",
        v.EndpointRevokePreviousView.as_view(),
        name="webhooks-endpoint-revoke-previous-secret",
    ),
    path(
        "endpoints/<str:endpoint_id>/test/",
        v.EndpointTestView.as_view(),
        name="webhooks-endpoint-test",
    ),
    path(
        "endpoints/<str:endpoint_id>/deliveries/",
        a.DeliveryListView.as_view(),
        name="webhooks-endpoint-deliveries",
    ),
    path(
        "endpoints/<str:endpoint_id>/activity/",
        a.ActivityView.as_view(),
        name="webhooks-endpoint-activity",
    ),
    path("preview/", a.PreviewView.as_view(), name="webhooks-preview"),
    path("deliveries/", a.DeliveryListView.as_view(), name="webhooks-deliveries"),
    path(
        "deliveries/redeliver/",
        a.BulkRedeliverView.as_view(),
        name="webhooks-deliveries-redeliver",
    ),
    path(
        "deliveries/<str:delivery_id>/",
        a.DeliveryDetailView.as_view(),
        name="webhooks-delivery",
    ),
    path(
        "deliveries/<str:delivery_id>/redeliver/",
        a.RedeliverView.as_view(),
        name="webhooks-delivery-redeliver",
    ),
    path("events/", a.EventListView.as_view(), name="webhooks-events"),
    path("event-types/", a.EventTypesView.as_view(), name="webhooks-event-types"),
    path("egress-ips/", a.EgressIpsView.as_view(), name="webhooks-egress-ips"),
    path("policy/", a.PolicyView.as_view(), name="webhooks-policy"),
    path(
        "policy/workspace/<uuid:workspace_id>/",
        a.WorkspacePolicyView.as_view(),
        name="webhooks-policy-workspace",
    ),
]
