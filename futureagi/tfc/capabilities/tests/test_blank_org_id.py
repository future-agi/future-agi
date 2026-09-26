"""A blank org id must never reach the cloud plan resolver.

``check()`` is the single entry point for capability decisions, and several
callers derive ``org_id`` from an optional organization. When that resolves to
"" the value is not an identity — forwarding it reaches
``PlanEntitlement.objects.filter(organization_id="")``, which raises
ValidationError against a UUID column and 500s the request instead of denying
it. Normalising to None routes it to the existing "no org" denial.
"""

import pytest
from tfc.capabilities import service
from tfc.licensing.types import (
    DenialReason,
    DeploymentFlavor,
    DeploymentLocation,
)

FEATURE = "falcon_ai"


class _AllowingResolver:
    """Allows everything, and fails loudly if handed a blank org id."""

    def has_feature(self, org_id: str, feature_id: str) -> bool:
        assert org_id, "cloud resolver must never be called with a blank org id"
        return True

    def get_upgrade_cta(self, org_id: str, feature_id: str) -> dict | None:
        return None


@pytest.fixture
def cloud_service():
    service.configure(
        flavor=DeploymentFlavor.CLOUD,
        location=DeploymentLocation.CLOUD,
        cloud_plan_resolver=_AllowingResolver(),
    )


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_org_id_denies_without_reaching_resolver(cloud_service, blank):
    decision = service.check(FEATURE, org_id=blank)

    assert decision.allowed is False
    assert decision.reason_code == DenialReason.RESOLVER_UNAVAILABLE.value


def test_none_org_id_still_denies(cloud_service):
    decision = service.check(FEATURE, org_id=None)

    assert decision.allowed is False
    assert decision.reason_code == DenialReason.RESOLVER_UNAVAILABLE.value


def test_real_org_id_is_passed_through_untouched(cloud_service):
    decision = service.check(FEATURE, org_id="11111111-1111-1111-1111-111111111111")

    assert decision.allowed is True
