"""Expose synthetic-data seeders only in the E2E backend image."""

from importlib.util import find_spec

from accounts.views.e2e_clean_signup import clean_signup
from tfc.views.e2e_google import GoogleFixtureDeploymentInfoView
from django.urls import path

from sdk.views.e2e_legacy_eval import E2ELegacyEvalView
from tfc.urls import urlpatterns as product_urlpatterns

urlpatterns = [
    path("api/deployment-info/", GoogleFixtureDeploymentInfoView.as_view()),
    *product_urlpatterns,
    path("accounts/e2e/clean-signup/", clean_signup),
    path("sdk/e2e/legacy-eval/", E2ELegacyEvalView.as_view()),
]
if find_spec("tracer.services.feed_alerts") is not None:
    from tracer.views.e2e_feed_issue import E2EFeedIssueView

    urlpatterns.append(path("tracer/e2e/feed-issue/", E2EFeedIssueView.as_view()))
