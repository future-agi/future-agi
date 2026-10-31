"""Expose the issue seeder only in the E2E backend image."""

from importlib.util import find_spec

from accounts.views.e2e_clean_signup import clean_signup
from django.urls import path

from tfc.urls import urlpatterns as product_urlpatterns

urlpatterns = [*product_urlpatterns, path("accounts/e2e/clean-signup/", clean_signup)]
if find_spec("tracer.services.feed_alerts") is not None:
    from tracer.views.e2e_feed_issue import E2EFeedIssueView

    urlpatterns.append(path("tracer/e2e/feed-issue/", E2EFeedIssueView.as_view()))
