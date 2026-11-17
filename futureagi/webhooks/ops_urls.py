"""Staff-only operations routes of the webhooks app (``tfc/urls.py`` includes them under ``webhooks/ops/``)."""

from django.urls import path

from webhooks.ops_views import OpsStatsView

urlpatterns = [path("stats/", OpsStatsView.as_view(), name="webhooks-ops-stats")]
