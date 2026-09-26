"""Contract tests for the capability service across all license states.

Tests the self-hosted EE image scenario with various license states:
active, grace, expired, trial_active, trial_expired, missing, invalid.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tfc.capabilities import service
from tfc.capabilities.errors import CapabilityDenied
from tfc.licensing.types import (
    DenialReason,
    DeploymentFlavor,
    DeploymentLocation,
    LicenseSnapshot,
    LicenseState,
    LicenseType,
)


class _FakeLicenseResolver:
    def __init__(self, snapshot: LicenseSnapshot):
        self._snapshot = snapshot

    def get_snapshot(self) -> LicenseSnapshot:
        return self._snapshot


def _make_active_snapshot(features: frozenset[str] | None = None) -> LicenseSnapshot:
    now = datetime.now(UTC)
    return LicenseSnapshot(
        state=LicenseState.ACTIVE,
        license_type=LicenseType.PRODUCTION,
        license_id="lic_test_001",
        customer_id="cus_test_001",
        issued_to="Test Corp",
        band="business",
        features=features
        or frozenset({"falcon_ai", "agentic_eval", "turing_models"}),
        limits={"traces_monthly": 1_000_000, "gateway_requests_monthly": 500_000},
        max_instances=3,
        issued_at=now - timedelta(days=180),
        expires_at=now + timedelta(days=180),
        grace_ends_at=now + timedelta(days=270),
        validated_at=now,
    )


@pytest.fixture()
def active_license():
    resolver = _FakeLicenseResolver(_make_active_snapshot())
    service.configure(
        flavor=DeploymentFlavor.SELF_HOSTED_EE,
        location=DeploymentLocation.SELF_HOSTED,
        license_resolver=resolver,
    )
    yield resolver
    service.configure(
        flavor=DeploymentFlavor.OSS,
        location=DeploymentLocation.SELF_HOSTED,
    )


class TestActiveLicense:
    def test_included_feature_allowed(self, active_license):
        decision = service.check("falcon_ai")
        assert decision.allowed is True
        assert decision.license_state == "active"

    def test_excluded_feature_denied(self, active_license):
        decision = service.check("protect")
        assert decision.allowed is False
        assert decision.reason_code == DenialReason.LICENSE_FEATURE_MISSING.value

    def test_oss_baseline_still_allowed(self, active_license):
        decision = service.check("knowledge_base")
        assert decision.allowed is True

    def test_managed_service_shows_network_required(self, active_license):
        decision = service.check("falcon_ai")
        assert decision.allowed is True
        assert decision.requires_network is True


class TestGraceLicense:
    @pytest.fixture(autouse=True)
    def grace_license(self):
        now = datetime.now(UTC)
        snapshot = LicenseSnapshot(
            state=LicenseState.GRACE,
            license_type=LicenseType.PRODUCTION,
            license_id="lic_test_002",
            band="business",
            features=frozenset({"falcon_ai", "agentic_eval", "optimization"}),
            expires_at=now - timedelta(days=5),
            grace_ends_at=now + timedelta(days=30),
            validated_at=now,
        )
        resolver = _FakeLicenseResolver(snapshot)
        service.configure(
            flavor=DeploymentFlavor.SELF_HOSTED_EE,
            location=DeploymentLocation.SELF_HOSTED,
            license_resolver=resolver,
        )
        yield
        service.configure(
            flavor=DeploymentFlavor.OSS,
            location=DeploymentLocation.SELF_HOSTED,
        )

    def test_grace_allows_included_feature(self):
        decision = service.check("falcon_ai")
        assert decision.allowed is True
        assert decision.license_state == "grace"

    def test_grace_denies_excluded_feature(self):
        decision = service.check("protect")
        assert decision.allowed is False
        assert decision.reason_code == DenialReason.LICENSE_FEATURE_MISSING.value

    def test_oss_baseline_allowed_during_grace(self):
        decision = service.check("knowledge_base")
        assert decision.allowed is True


class TestExpiredLicense:
    @pytest.fixture(autouse=True)
    def expired_license(self):
        snapshot = LicenseSnapshot(
            state=LicenseState.EXPIRED,
            license_type=LicenseType.PRODUCTION,
            license_id="lic_test_003",
            band="business",
            features=frozenset({"falcon_ai", "agentic_eval"}),
            validated_at=datetime(2025, 6, 1, tzinfo=UTC),
        )
        resolver = _FakeLicenseResolver(snapshot)
        service.configure(
            flavor=DeploymentFlavor.SELF_HOSTED_EE,
            location=DeploymentLocation.SELF_HOSTED,
            license_resolver=resolver,
        )
        yield
        service.configure(
            flavor=DeploymentFlavor.OSS,
            location=DeploymentLocation.SELF_HOSTED,
        )

    def test_expired_denies_all_paid_features(self):
        decision = service.check("falcon_ai")
        assert decision.allowed is False
        assert decision.reason_code == DenialReason.LICENSE_EXPIRED.value

    def test_expired_allows_oss_baseline(self):
        decision = service.check("knowledge_base")
        assert decision.allowed is True


class TestTrialActive:
    @pytest.fixture(autouse=True)
    def trial_license(self):
        now = datetime.now(UTC)
        snapshot = LicenseSnapshot(
            state=LicenseState.TRIAL_ACTIVE,
            license_type=LicenseType.TRIAL,
            license_id="lic_trial_001",
            band="business",
            features=frozenset({"falcon_ai", "agentic_eval"}),
            expires_at=now + timedelta(days=14),
            validated_at=now,
        )
        resolver = _FakeLicenseResolver(snapshot)
        service.configure(
            flavor=DeploymentFlavor.SELF_HOSTED_EE,
            location=DeploymentLocation.SELF_HOSTED,
            license_resolver=resolver,
        )
        yield
        service.configure(
            flavor=DeploymentFlavor.OSS,
            location=DeploymentLocation.SELF_HOSTED,
        )

    def test_trial_allows_included_feature(self):
        decision = service.check("falcon_ai")
        assert decision.allowed is True
        assert decision.license_state == "trial_active"

    def test_trial_denies_excluded_feature(self):
        decision = service.check("protect")
        assert decision.allowed is False


class TestTrialExpired:
    @pytest.fixture(autouse=True)
    def trial_expired_license(self):
        snapshot = LicenseSnapshot(
            state=LicenseState.TRIAL_EXPIRED,
            license_type=LicenseType.TRIAL,
            license_id="lic_trial_002",
            band="business",
            features=frozenset({"falcon_ai", "agentic_eval"}),
            validated_at=datetime(2025, 6, 1, tzinfo=UTC),
        )
        resolver = _FakeLicenseResolver(snapshot)
        service.configure(
            flavor=DeploymentFlavor.SELF_HOSTED_EE,
            location=DeploymentLocation.SELF_HOSTED,
            license_resolver=resolver,
        )
        yield
        service.configure(
            flavor=DeploymentFlavor.OSS,
            location=DeploymentLocation.SELF_HOSTED,
        )

    def test_trial_expired_denies_paid_features(self):
        decision = service.check("falcon_ai")
        assert decision.allowed is False
        assert decision.reason_code == DenialReason.LICENSE_TRIAL_EXPIRED.value

    def test_trial_expired_allows_oss_baseline(self):
        decision = service.check("knowledge_base")
        assert decision.allowed is True


class TestMissingLicense:
    @pytest.fixture(autouse=True)
    def missing_license(self):
        from tfc.licensing.types import MISSING_LICENSE

        resolver = _FakeLicenseResolver(MISSING_LICENSE)
        service.configure(
            flavor=DeploymentFlavor.SELF_HOSTED_EE,
            location=DeploymentLocation.SELF_HOSTED,
            license_resolver=resolver,
        )
        yield
        service.configure(
            flavor=DeploymentFlavor.OSS,
            location=DeploymentLocation.SELF_HOSTED,
        )

    def test_missing_denies_paid_features(self):
        decision = service.check("falcon_ai")
        assert decision.allowed is False
        assert decision.reason_code == DenialReason.LICENSE_MISSING.value

    def test_missing_allows_oss_baseline(self):
        decision = service.check("knowledge_base")
        assert decision.allowed is True


class TestInvalidLicense:
    @pytest.fixture(autouse=True)
    def invalid_license(self):
        from tfc.licensing.types import INVALID_LICENSE

        resolver = _FakeLicenseResolver(INVALID_LICENSE)
        service.configure(
            flavor=DeploymentFlavor.SELF_HOSTED_EE,
            location=DeploymentLocation.SELF_HOSTED,
            license_resolver=resolver,
        )
        yield
        service.configure(
            flavor=DeploymentFlavor.OSS,
            location=DeploymentLocation.SELF_HOSTED,
        )

    def test_invalid_denies_paid_features(self):
        decision = service.check("falcon_ai")
        assert decision.allowed is False
        assert decision.reason_code == DenialReason.LICENSE_INVALID.value

    def test_invalid_allows_oss_baseline(self):
        decision = service.check("knowledge_base")
        assert decision.allowed is True


class TestResolverFailure:
    def test_no_resolver_denies_paid_features(self):
        service.configure(
            flavor=DeploymentFlavor.SELF_HOSTED_EE,
            location=DeploymentLocation.SELF_HOSTED,
            license_resolver=None,
        )
        decision = service.check("falcon_ai")
        assert decision.allowed is False
        assert decision.reason_code == DenialReason.RESOLVER_UNAVAILABLE.value

    def test_no_resolver_allows_oss_baseline(self):
        service.configure(
            flavor=DeploymentFlavor.SELF_HOSTED_EE,
            location=DeploymentLocation.SELF_HOSTED,
            license_resolver=None,
        )
        decision = service.check("knowledge_base")
        assert decision.allowed is True


class _FakeCloudPlanResolver:
    def has_feature(self, org_id: str, feature_id: str) -> bool:
        return True

    def get_upgrade_cta(self, org_id: str, feature_id: str) -> dict | None:
        return None


class TestCloudResolverFailure:
    def test_cloud_denies_paid_feature_when_org_id_missing(self):
        service.configure(
            flavor=DeploymentFlavor.CLOUD,
            location=DeploymentLocation.CLOUD,
            cloud_plan_resolver=_FakeCloudPlanResolver(),
        )

        decision = service.check("falcon_ai", org_id=None)

        assert decision.allowed is False
        assert decision.reason_code == DenialReason.RESOLVER_UNAVAILABLE.value

    def test_cloud_allows_oss_baseline_when_org_id_missing(self):
        service.configure(
            flavor=DeploymentFlavor.CLOUD,
            location=DeploymentLocation.CLOUD,
            cloud_plan_resolver=_FakeCloudPlanResolver(),
        )

        decision = service.check("knowledge_base", org_id=None)

        assert decision.allowed is True


class TestCheckOrRaise:
    def test_raises_capability_denied_on_denial(self, active_license):
        with pytest.raises(CapabilityDenied) as exc_info:
            service.check_or_raise("protect")
        assert exc_info.value.reason_code == DenialReason.LICENSE_FEATURE_MISSING.value
        assert exc_info.value.feature_id == "protect"

    def test_returns_decision_on_allow(self, active_license):
        decision = service.check_or_raise("falcon_ai")
        assert decision.allowed is True

    def test_temporal_activity_raises_application_error(self, active_license):
        from temporalio.exceptions import ApplicationError

        with pytest.raises(ApplicationError) as exc_info:
            service.check_or_raise("protect", activity=True)
        assert exc_info.value.non_retryable is True
