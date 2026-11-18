"""Enforcement matrix: parametrized test for every paid feature across states.

Verifies the plan requirement: 'Every registered paid feature is enforced
across all execution surfaces.'
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tfc.capabilities import service
from tfc.capabilities.registry import OSS_LOCKED_FEATURES, PAID_FEATURES
from tfc.ee_gating import FeatureUnavailable, check_ee_feature
from tfc.licensing.types import (
    DeploymentFlavor,
    DeploymentLocation,
    LicenseSnapshot,
    LicenseState,
    LicenseType,
)


class _FakeResolver:
    def __init__(self, snapshot: LicenseSnapshot):
        self._snapshot = snapshot

    def get_snapshot(self) -> LicenseSnapshot:
        return self._snapshot


ALL_PAID = sorted(PAID_FEATURES)
# Two-tier gating: only oss_locked features need a license off-cloud; the
# rest of the paid set is free on self-hosted deployments.
LOCKED = sorted(OSS_LOCKED_FEATURES)
UNLOCKED = sorted(PAID_FEATURES - OSS_LOCKED_FEATURES)


@pytest.fixture()
def oss_deployment():
    service.configure(
        flavor=DeploymentFlavor.OSS,
        location=DeploymentLocation.SELF_HOSTED,
    )
    yield
    service._configured = False


@pytest.fixture()
def ee_active_all_features():
    snapshot = LicenseSnapshot(
        state=LicenseState.ACTIVE,
        license_type=LicenseType.PRODUCTION,
        license_id="lic_matrix",
        band="enterprise_plus",
        features=frozenset(PAID_FEATURES),
        validated_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    service.configure(
        flavor=DeploymentFlavor.SELF_HOSTED_EE,
        location=DeploymentLocation.SELF_HOSTED,
        license_resolver=_FakeResolver(snapshot),
    )
    yield
    service._configured = False


@pytest.fixture()
def ee_active_no_features():
    snapshot = LicenseSnapshot(
        state=LicenseState.ACTIVE,
        license_type=LicenseType.PRODUCTION,
        license_id="lic_matrix_empty",
        band="team",
        features=frozenset(),
        validated_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    service.configure(
        flavor=DeploymentFlavor.SELF_HOSTED_EE,
        location=DeploymentLocation.SELF_HOSTED,
        license_resolver=_FakeResolver(snapshot),
    )
    yield
    service._configured = False


@pytest.fixture()
def ee_expired():
    snapshot = LicenseSnapshot(
        state=LicenseState.EXPIRED,
        license_type=LicenseType.PRODUCTION,
        license_id="lic_matrix_exp",
        band="enterprise_plus",
        features=frozenset(PAID_FEATURES),
        validated_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    service.configure(
        flavor=DeploymentFlavor.SELF_HOSTED_EE,
        location=DeploymentLocation.SELF_HOSTED,
        license_resolver=_FakeResolver(snapshot),
    )
    yield
    service._configured = False


class TestOSSImageDeniesLockedFeatures:
    @pytest.mark.parametrize("feature_id", LOCKED)
    def test_locked_feature_denied(self, oss_deployment, feature_id):
        with pytest.raises(FeatureUnavailable):
            check_ee_feature(feature_id, org_id="org_1")

    @pytest.mark.parametrize("feature_id", UNLOCKED)
    def test_unlocked_feature_allowed(self, oss_deployment, feature_id):
        check_ee_feature(feature_id, org_id="org_1")


class TestActiveAllFeaturesIncluded:
    @pytest.mark.parametrize("feature_id", ALL_PAID)
    def test_paid_feature_allowed(self, ee_active_all_features, feature_id):
        check_ee_feature(feature_id, org_id="org_1")


class TestActiveNoFeaturesIncluded:
    @pytest.mark.parametrize("feature_id", LOCKED)
    def test_locked_feature_denied_when_excluded(
        self, ee_active_no_features, feature_id
    ):
        with pytest.raises(FeatureUnavailable):
            check_ee_feature(feature_id, org_id="org_1")

    @pytest.mark.parametrize("feature_id", UNLOCKED)
    def test_unlocked_feature_allowed_when_excluded(
        self, ee_active_no_features, feature_id
    ):
        check_ee_feature(feature_id, org_id="org_1")


class TestExpiredDeniesLockedFeatures:
    @pytest.mark.parametrize("feature_id", LOCKED)
    def test_locked_feature_denied_when_expired(self, ee_expired, feature_id):
        with pytest.raises(FeatureUnavailable):
            check_ee_feature(feature_id, org_id="org_1")

    @pytest.mark.parametrize("feature_id", UNLOCKED)
    def test_unlocked_feature_allowed_when_expired(self, ee_expired, feature_id):
        check_ee_feature(feature_id, org_id="org_1")
