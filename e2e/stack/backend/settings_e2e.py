"""Settings installed only into the E2E backend image."""

from dataclasses import replace

from tfc.settings.settings import *  # noqa: F403
from tfc.capabilities.registry import FEATURE_REGISTRY

ROOT_URLCONF = "tfc.urls_e2e"

# The managed mock stack has no EE license. This test-image-only registry
# replacement keeps the product capability decision and image untouched.
FEATURE_REGISTRY["error_feed"] = replace(
    FEATURE_REGISTRY["error_feed"], oss_baseline=True
)

# The licensed customer (e2e/lib/licensed-customer.ts): Instant eval and
# Insights stay Enterprise-only on this unlicensed stack, so OBS-E2E-053 and
# OBS-E2E-058 still see their upgrade page, except for an organization one
# of whose members signed up as e2e-licensed-…@futureagi.com. Only this
# test image's self-hosted licence step changes; every caller (the
# capabilities read, the views, the eval-task activity) still goes through
# tfc.capabilities.service.check with its org id.
from tfc.capabilities import service as _capability_service

# Insights starts a report only when its default Turing models are allowed
# (tfc/ee_gates.py insights_turing_denied), so a licence that includes
# Insights includes turing_models too. EVAL-E2E-011 runs on an ordinary org
# and still sees Turing models denied.
E2E_LICENSED_FEATURES = frozenset({"instant_eval", "insights", "turing_models"})
E2E_LICENSED_EMAIL_PREFIX = "e2e-licensed-"
_product_check_self_hosted_ee = _capability_service._check_self_hosted_ee


def _e2e_licensed_org(org_id):
    from accounts.models.organization_membership import OrganizationMembership

    return OrganizationMembership.objects.filter(
        organization_id=org_id, user__email__startswith=E2E_LICENSED_EMAIL_PREFIX
    ).exists()


def _e2e_check_self_hosted_ee(feature, org_id):
    if feature.id in E2E_LICENSED_FEATURES and org_id and _e2e_licensed_org(org_id):
        return _capability_service.CapabilityDecision(
            allowed=True, feature_id=feature.id
        )
    return _product_check_self_hosted_ee(feature, org_id)


_capability_service._check_self_hosted_ee = _e2e_check_self_hosted_ee

# Google fixture endpoints exist only in this E2E image. Production settings
# and accepted Google issuer policy are unchanged. The callback module imports
# these legacy constants directly from tfc.settings.settings.
from tfc.settings import settings as product_settings

product_settings.AUTH0_DOMAIN = AUTH0_DOMAIN = "mock-google:28443"
product_settings.AUTH0_CLIENT_ID = AUTH0_CLIENT_ID = "e2e-google-client"
product_settings.AUTH0_CLIENT_SECRET = AUTH0_CLIENT_SECRET = "e2e-google-secret"
GOOGLE_OAUTH_JWKS_URL = "https://mock-google:28443/certs"
