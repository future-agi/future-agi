"""Contract tests for the capability service with the EE package absent.

These tests prove that the capability service returns correct decisions
when ee/ is stripped (OSS image scenario). No Django DB needed.
"""

from __future__ import annotations

import pytest

from tfc.capabilities import service
from tfc.capabilities.errors import CapabilityDenied
from tfc.capabilities.registry import (
    FEATURE_REGISTRY,
    MANAGED_SERVICE_FEATURES,
    OSS_BASELINE_FEATURES,
    PAID_FEATURES,
)
from tfc.licensing.types import (
    DenialReason,
    DeploymentFlavor,
    DeploymentLocation,
)


@pytest.fixture(autouse=True)
def oss_flavor():
    """Configure the capability service as an OSS image (no EE code)."""
    service.configure(
        flavor=DeploymentFlavor.OSS,
        location=DeploymentLocation.SELF_HOSTED,
        license_resolver=None,
        cloud_plan_resolver=None,
    )
    yield
    service.configure(
        flavor=DeploymentFlavor.OSS,
        location=DeploymentLocation.SELF_HOSTED,
        license_resolver=None,
        cloud_plan_resolver=None,
    )


class TestOSSBaseline:
    """OSS baseline features are always allowed regardless of deployment."""

    @pytest.mark.parametrize("feature_id", sorted(OSS_BASELINE_FEATURES))
    def test_oss_baseline_always_allowed(self, feature_id: str):
        decision = service.check(feature_id)
        assert decision.allowed is True
        assert decision.feature_id == feature_id

    @pytest.mark.parametrize(
        "feature_id", sorted(MANAGED_SERVICE_FEATURES | {"error_feed"})
    )
    def test_locked_features_denied_on_oss_image(self, feature_id: str):
        decision = service.check(feature_id)
        assert decision.allowed is False
        assert decision.reason_code == DenialReason.EE_CODE_UNAVAILABLE.value

    @pytest.mark.parametrize(
        "feature_id",
        sorted(PAID_FEATURES - MANAGED_SERVICE_FEATURES - {"error_feed"}),
    )
    def test_unlocked_paid_features_allowed_on_oss_image(self, feature_id: str):
        decision = service.check(feature_id)
        assert decision.allowed is True


class TestUnknownFeature:
    def test_unknown_feature_always_denied(self):
        decision = service.check("totally_nonexistent_feature_xyz")
        assert decision.allowed is False
        assert decision.reason_code == DenialReason.FEATURE_UNKNOWN.value

    def test_check_or_raise_on_unknown_feature(self):
        with pytest.raises(CapabilityDenied) as exc_info:
            service.check_or_raise("bogus_feature_not_in_registry")
        assert exc_info.value.reason_code == DenialReason.FEATURE_UNKNOWN.value


class TestRegistryConsistency:
    def test_all_oss_features_are_in_registry(self):
        for feature_id in OSS_BASELINE_FEATURES:
            assert feature_id in FEATURE_REGISTRY

    def test_all_paid_features_are_in_registry(self):
        for feature_id in PAID_FEATURES:
            assert feature_id in FEATURE_REGISTRY

    def test_oss_and_paid_are_disjoint(self):
        assert OSS_BASELINE_FEATURES & PAID_FEATURES == frozenset()

    def test_managed_services_are_paid(self):
        assert MANAGED_SERVICE_FEATURES <= PAID_FEATURES

    def test_no_feature_in_both_categories(self):
        for f in FEATURE_REGISTRY.values():
            if f.oss_baseline:
                assert not f.requires_license
            if f.requires_license:
                assert not f.oss_baseline
