"""Expose synthetic-data seeders only in the E2E backend image."""

from importlib.util import find_spec

from accounts.views.e2e_clean_signup import clean_signup
from django.urls import path

from tfc.urls import urlpatterns as product_urlpatterns

urlpatterns = [
    *product_urlpatterns,
    path("accounts/e2e/clean-signup/", clean_signup),
]
# The Google sign-in fixture and the legacy-eval seeder are mounted only where the
# stack serves them (the Distributed backend image copies both; the Standalone
# overlay carries no Google fixture since mock-google is not a stack service).
if find_spec("tfc.views.e2e_google") is not None:
    from tfc.views.e2e_google import GoogleFixtureDeploymentInfoView

    urlpatterns.insert(0, path("api/deployment-info/", GoogleFixtureDeploymentInfoView.as_view()))
if find_spec("sdk.views.e2e_legacy_eval") is not None:
    from sdk.views.e2e_legacy_eval import E2ELegacyEvalView

    urlpatterns.append(path("sdk/e2e/legacy-eval/", E2ELegacyEvalView.as_view()))
if find_spec("tracer.services.feed_alerts") is not None:
    from tracer.views.e2e_feed_issue import E2EFeedIssueView

    urlpatterns.append(path("tracer/e2e/feed-issue/", E2EFeedIssueView.as_view()))
