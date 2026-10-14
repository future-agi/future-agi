"""Expose the issue seeder only in the E2E backend image."""

from django.urls import path

from tfc.urls import urlpatterns as product_urlpatterns
from tracer.views.e2e_feed_issue import E2EFeedIssueView

urlpatterns = [
    *product_urlpatterns,
    path("tracer/e2e/feed-issue/", E2EFeedIssueView.as_view()),
]
