from django.urls import include, path
from rest_framework.routers import DefaultRouter

from integrations.views.integration_connection import IntegrationConnectionViewSet
from integrations.views.slack import (
    SlackChannelsView,
    SlackInstallView,
    slack_oauth_callback,
)
from integrations.views.sync_log import SyncLogViewSet

router = DefaultRouter()
router.register(
    r"connections", IntegrationConnectionViewSet, basename="integration-connections"
)
router.register(r"sync-logs", SyncLogViewSet, basename="sync-logs")

urlpatterns = [
    path("slack/install/", SlackInstallView.as_view(), name="slack-install"),
    path("slack/callback/", slack_oauth_callback, name="slack-callback"),
    path(
        "connections/<uuid:connection_id>/slack/channels/",
        SlackChannelsView.as_view(),
        name="slack-channels",
    ),
    path("", include(router.urls)),
]
