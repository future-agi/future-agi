"""
Phase 6a: LicenseValidator + Unified Feature Check Tests
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from django.test import override_settings

from tfc.capabilities import service
from tfc.licensing.types import (
    DeploymentFlavor,
    DeploymentLocation,
    LicenseSnapshot,
    LicenseState,
)

from ee.licensing import state
from ee.usage.deployment import _detect_mode
from ee.usage.services.license import LicenseResult, LicenseValidator


@pytest.fixture(autouse=True)
def reset_license_state():
    state.set_snapshot(LicenseSnapshot(state=LicenseState.MISSING))
    service.configure(
        flavor=DeploymentFlavor.OSS,
        location=DeploymentLocation.SELF_HOSTED,
    )
    yield
    _detect_mode.cache_clear()


@pytest.mark.unit
class TestLicenseValidatorAdapter:
    def test_empty_key_returns_invalid(self):
        result = LicenseValidator.validate("")

        assert result.valid is False
        assert result.error == "No license key provided"

    def test_hash_key_is_deterministic(self):
        assert LicenseValidator.hash_key("test-key") == LicenseValidator.hash_key(
            "test-key"
        )
        assert LicenseValidator.hash_key("test-key") != LicenseValidator.hash_key(
            "other-key"
        )

    def test_cached_result_reads_canonical_snapshot(self):
        state.set_snapshot(
            LicenseSnapshot(
                state=LicenseState.GRACE,
                band="business",
                features=frozenset({"scim"}),
                limits={"traces_monthly": 25_000_000},
                issued_to="Test Corp",
                validated_at=datetime.now(UTC),
            )
        )

        result = LicenseValidator.get_cached_license()

        assert result == LicenseResult(
            valid=True,
            band="business",
            features=["scim"],
            max_traces_monthly=25_000_000,
            issued_to="Test Corp",
        )

    def test_invalidate_cache_resets_canonical_snapshot(self):
        state.set_snapshot(LicenseSnapshot(state=LicenseState.ACTIVE))

        LicenseValidator.invalidate_cache()

        assert state.get_snapshot().state == LicenseState.MISSING


@pytest.mark.unit
class TestUnifiedFeatureCheck:
    def test_oss_allows_baseline_feature(self):
        from ee.usage.services.entitlements import Entitlements

        assert Entitlements.has_feature_unified("org-1", "knowledge_base") is True

    def test_oss_denies_locked_feature(self):
        from ee.usage.services.entitlements import Entitlements

        assert Entitlements.has_feature_unified("org-1", "protect") is False

    def test_oss_allows_unlocked_paid_feature(self):
        from ee.usage.services.entitlements import Entitlements

        assert Entitlements.has_feature_unified("org-1", "scim") is True

    def test_unregistered_feature_is_not_gated(self):
        from ee.usage.services.entitlements import Entitlements

        assert Entitlements.has_feature_unified("org-1", "tracing") is True

    def test_self_hosted_ee_reads_canonical_snapshot(self):
        from ee.usage.services.entitlements import Entitlements

        state.set_snapshot(
            LicenseSnapshot(
                state=LicenseState.ACTIVE,
                features=frozenset({"protect"}),
            )
        )
        service.configure(
            flavor=DeploymentFlavor.SELF_HOSTED_EE,
            location=DeploymentLocation.SELF_HOSTED,
            license_resolver=state.get_resolver(),
        )

        assert Entitlements.has_feature_unified("org-1", "protect") is True
        assert Entitlements.has_feature_unified("org-1", "error_feed") is False

    def test_cloud_uses_plan_resolver(self):
        from ee.usage.services.entitlements import Entitlements

        class Resolver:
            def has_feature(self, org_id, feature_id):
                return org_id == "org-1" and feature_id == "scim"

            def get_upgrade_cta(self, org_id, feature_id):
                return None

        service.configure(
            flavor=DeploymentFlavor.CLOUD,
            location=DeploymentLocation.CLOUD,
            cloud_plan_resolver=Resolver(),
        )

        assert Entitlements.has_feature_unified("org-1", "scim") is True
        assert Entitlements.has_feature_unified("org-1", "audit_logs") is False


@pytest.mark.unit
class TestPhoneHome:

    @override_settings(CLOUD_DEPLOYMENT="US", EE_LICENSE_KEY="")
    @patch("ee.usage.deployment._validate_cloud_secret", return_value=True)
    def test_phone_home_skips_on_cloud(self, _mock_secret):
        _detect_mode.cache_clear()
        from ee.usage.tasks.phone_home import phone_home

        result = phone_home()
        assert result["skipped"] is True

    @override_settings(CLOUD_DEPLOYMENT="", EE_LICENSE_KEY="")
    def test_phone_home_skips_on_oss(self):
        _detect_mode.cache_clear()
        from ee.usage.tasks.phone_home import phone_home

        result = phone_home()
        assert result["skipped"] is True
