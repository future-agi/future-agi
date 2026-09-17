"""Tests for EEFeatureMiddleware's deployment-mode guard.

Regression context: the cloud early-return was removed in "enterprise
licensing P0/P1 hardening" on the assumption that the unified resolver could
handle cloud too. It can — but only when given an organization, and
``request.organization`` is assigned by APIKeyAuthentication during DRF
dispatch, after every middleware has run. On cloud the middleware therefore
passed a blank org id into a UUID lookup, raising ValidationError before
authentication and turning every request under a gated prefix into a 500.

The guard is what keeps the org-dependent branch off the cloud path.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from ee.usage.middleware.ee import EE_FEATURE_PATHS, EEFeatureMiddleware

GATED_PATH = "/falcon-ai/conversations/"


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def middleware():
    return EEFeatureMiddleware(lambda request: HttpResponse("OK", status=200))


class TestCloudGuard:
    """On cloud the middleware must defer to the view layer, untouched."""

    def test_cloud_passes_through_without_consulting_entitlements(self, middleware, rf):
        request = rf.get(GATED_PATH)

        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch(
                "ee.usage.services.entitlements.Entitlements.has_feature_unified"
            ) as has_feature,
        ):
            response = middleware(request)

        assert response.status_code == 200
        # The load-bearing assertion: on cloud this middleware must not ask an
        # org-scoped question it has no org to answer with.
        has_feature.assert_not_called()

    def test_cloud_pass_through_covers_every_gated_prefix(self, middleware, rf):
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch(
                "ee.usage.services.entitlements.Entitlements.has_feature_unified"
            ) as has_feature,
        ):
            for prefix in EE_FEATURE_PATHS:
                response = middleware(rf.get(f"{prefix}anything/"))
                assert response.status_code == 200

        has_feature.assert_not_called()


class TestSelfHostedStillEnforces:
    """Off cloud the licence check is org-independent, so it must still run."""

    def test_self_hosted_allows_when_licensed(self, middleware, rf):
        request = rf.get(GATED_PATH)

        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
            patch(
                "ee.usage.services.entitlements.Entitlements.has_feature_unified",
                return_value=True,
            ) as has_feature,
        ):
            response = middleware(request)

        assert response.status_code == 200
        has_feature.assert_called_once()

    def test_self_hosted_denies_with_402_when_unlicensed(self, middleware, rf):
        request = rf.get(GATED_PATH)

        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
            patch("ee.usage.deployment.DeploymentMode.is_ee", return_value=True),
            patch("ee.usage.deployment.DeploymentMode.get_mode", return_value="ee"),
            patch(
                "ee.usage.services.entitlements.Entitlements.has_feature_unified",
                return_value=False,
            ),
        ):
            response = middleware(request)

        assert response.status_code == 402

    def test_ungated_path_is_never_checked(self, middleware, rf):
        request = rf.get("/api/capabilities/")

        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
            patch(
                "ee.usage.services.entitlements.Entitlements.has_feature_unified"
            ) as has_feature,
        ):
            response = middleware(request)

        assert response.status_code == 200
        has_feature.assert_not_called()


class TestBlankOrgIdNeverReachesTheResolver:
    """Belt and braces: even off cloud, the org id handed over stays blank-safe.

    ``request.organization`` is unset at middleware time in every deployment
    mode, so the middleware always passes "". That is only safe because the
    self-hosted branch of the capability service ignores org_id — this test
    pins the input so a future change to that branch surfaces here.
    """

    def test_self_hosted_receives_blank_org_id(self, middleware, rf):
        request = rf.get(GATED_PATH)

        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
            patch(
                "ee.usage.services.entitlements.Entitlements.has_feature_unified",
                return_value=True,
            ) as has_feature,
        ):
            middleware(request)

        org_id, feature = has_feature.call_args[0]
        assert org_id == ""
        assert feature == "falcon_ai"

    def test_org_on_request_is_still_honoured_when_present(self, middleware, rf):
        """If a future middleware does resolve the org, pass it through."""
        request = rf.get(GATED_PATH)
        request.organization = MagicMock(id="11111111-1111-1111-1111-111111111111")

        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
            patch(
                "ee.usage.services.entitlements.Entitlements.has_feature_unified",
                return_value=True,
            ) as has_feature,
        ):
            middleware(request)

        org_id, _ = has_feature.call_args[0]
        assert org_id == "11111111-1111-1111-1111-111111111111"
