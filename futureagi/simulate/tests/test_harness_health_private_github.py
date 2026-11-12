"""The harness health endpoint tells the source panel whether to offer private GitHub.

``private_github_available`` comes from the same ``platform_github_app_refused()``
that refuses a private GitHub source at job creation. As in the containment matrix,
the App settings are set per case and the organization query answers one or two
synthetic ids, so each case reaches the real tenancy check.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from simulate.services import github_app_tenancy
from simulate.views import harness_job as harness_job_view
from simulate.views.harness_job import HarnessJobViewSet

APP_ID = "12345"
APP_KEY = "synthetic-app-private-key"
ONE_ORGANIZATION = ("synthetic-org-a",)
TWO_ORGANIZATIONS = ("synthetic-org-a", "synthetic-org-b")
PROVIDER_HEALTH = {
    "configured": True,
    "provider": "synthetic",
    "sandbox_provider": "synthetic",
    "public_ingress": True,
}


class _Provider:
    def health(self):
        return dict(PROVIDER_HEALTH)


def _health(settings, monkeypatch, user, *, orgs, app=True, deployment=""):
    settings.CLOUD_DEPLOYMENT = deployment
    settings.GITHUB_APP_ID = APP_ID if app else ""
    settings.GITHUB_APP_PRIVATE_KEY = APP_KEY if app else ""
    monkeypatch.setattr(github_app_tenancy, "_live_org_ids", lambda: list(orgs))
    monkeypatch.setattr(harness_job_view, "get_harness_provider", lambda: _Provider())
    request = APIRequestFactory().get("/simulate/api/harness-jobs/health/")
    force_authenticate(request, user=user)
    response = HarnessJobViewSet.as_view({"get": "health"})(request)
    assert response.status_code == 200
    # The provider's own health fields come back unchanged beside the new one.
    assert {
        key: value
        for key, value in response.data.items()
        if key != "private_github_available"
    } == PROVIDER_HEALTH
    return response.data["private_github_available"]


def test_private_github_is_unavailable_where_creation_refuses_it(
    settings, monkeypatch, user
):
    assert _health(settings, monkeypatch, user, orgs=TWO_ORGANIZATIONS) is False


@pytest.mark.parametrize("deployment", ["US", "EU", "DEV"])
def test_private_github_is_unavailable_on_a_cloud_region(
    settings, monkeypatch, user, deployment
):
    assert (
        _health(
            settings, monkeypatch, user, orgs=ONE_ORGANIZATION, deployment=deployment
        )
        is False
    )


def test_private_github_is_available_on_a_single_organization_self_hosted_app(
    settings, monkeypatch, user
):
    assert _health(settings, monkeypatch, user, orgs=ONE_ORGANIZATION) is True


def test_private_github_is_available_without_the_platform_app(
    settings, monkeypatch, user
):
    assert (
        _health(settings, monkeypatch, user, orgs=TWO_ORGANIZATIONS, app=False) is True
    )
