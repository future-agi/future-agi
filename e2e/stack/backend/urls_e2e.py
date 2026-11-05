"""Expose the issue seeder only in the E2E backend image."""

from importlib.util import find_spec

from django.urls import path

from tfc.urls import urlpatterns as product_urlpatterns

urlpatterns = [*product_urlpatterns]
if find_spec("tracer.services.feed_alerts") is not None:
    from tracer.views.e2e_feed_issue import E2EFeedIssueView

    urlpatterns.append(path("tracer/e2e/feed-issue/", E2EFeedIssueView.as_view()))
