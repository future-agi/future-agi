"""Tests verifying check_ee_feature delegates to the capability service."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tfc.capabilities import service
from tfc.ee_gating import EEFeature, FeatureUnavailable, check_ee_feature
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


@pytest.fixture(autouse=True)
def configure_capability_service():
    """Configure capability service so delegation is active."""
    snapshot = LicenseSnapshot(
        state=LicenseState.ACTIVE,
        license_type=LicenseType.PRODUCTION,
        license_id="lic_test",
        band="enterprise",
        features=frozenset({"voice_sim", "falcon_ai", "optimization"}),
        validated_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    resolver = _FakeResolver(snapshot)
    service.configure(
        flavor=DeploymentFlavor.SELF_HOSTED_EE,
        location=DeploymentLocation.SELF_HOSTED,
        license_resolver=resolver,
    )
    yield
    service._configured = False
    service.configure(
        flavor=DeploymentFlavor.OSS,
        location=DeploymentLocation.SELF_HOSTED,
        license_resolver=None,
    )
    service._configured = False


class TestDelegation:
    def test_included_feature_passes(self):
        check_ee_feature(EEFeature.FALCON_AI, org_id="org_1")

    def test_excluded_feature_raises(self):
        with pytest.raises(FeatureUnavailable):
            check_ee_feature(EEFeature.PROTECT, org_id="org_1")

    def test_oss_baseline_always_passes(self):
        check_ee_feature("knowledge_base", org_id="org_1")
        check_ee_feature("review_workflow", org_id="org_1")

    def test_activity_raises_application_error(self):
        from temporalio.exceptions import ApplicationError

        with pytest.raises(ApplicationError) as exc_info:
            check_ee_feature(EEFeature.PROTECT, org_id="org_1", activity=True)
        assert exc_info.value.non_retryable is True

    def test_unknown_feature_passes_through(self):
        check_ee_feature("totally_unknown_not_in_enum", org_id="org_1")


class TestFallbackWhenNotConfigured:
    def test_falls_back_when_not_configured(self, monkeypatch):
        service._configured = False
        monkeypatch.setattr("tfc.ee_gating.is_oss", lambda: True)
        with pytest.raises(FeatureUnavailable):
            check_ee_feature(EEFeature.FALCON_AI)
