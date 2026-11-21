from django.urls import include, path
from rest_framework.routers import DefaultRouter

from integrations.views.github import (
    GitHubInstallCompleteView,
    GitHubInstallView,
    SourceBranchesView,
    SourceRepositoriesView,
    github_install_callback,
)
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
    path(
        "github/install/",
        GitHubInstallView.as_view(),
        name="integrations-github-install",
    ),
    path(
        "github/install/complete/",
        GitHubInstallCompleteView.as_view(),
        name="integrations-github-install-complete",
    ),
    path(
        "github/callback/", github_install_callback, name="integrations-github-callback"
    ),
    path(
        "connections/<uuid:connection_id>/repositories/",
        SourceRepositoriesView.as_view(),
        name="source-repositories",
    ),
    path(
        "connections/<uuid:connection_id>/repositories/<int:repository_id>/branches/",
        SourceBranchesView.as_view(),
        name="source-branches",
    ),
    path("", include(router.urls)),
]
