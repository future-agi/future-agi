from __future__ import annotations

from tfc.licensing.types import (
    DenialReason,
    DeploymentFlavor,
    DeploymentLocation,
    DisplayMode,
    LicenseSnapshot,
    LicenseState,
    derive_display_mode,
)


class TestLicenseSnapshotProperties:
    def test_active_is_usable(self):
        snap = LicenseSnapshot(state=LicenseState.ACTIVE)
        assert snap.is_active is True
        assert snap.is_usable is True
        assert snap.is_expired is False
        assert snap.is_grace is False

    def test_grace_is_usable(self):
        snap = LicenseSnapshot(state=LicenseState.GRACE)
        assert snap.is_active is False
        assert snap.is_usable is True
        assert snap.is_grace is True

    def test_trial_active_is_usable(self):
        snap = LicenseSnapshot(state=LicenseState.TRIAL_ACTIVE)
        assert snap.is_active is True
        assert snap.is_usable is True

    def test_expired_is_not_usable(self):
        snap = LicenseSnapshot(state=LicenseState.EXPIRED)
        assert snap.is_active is False
        assert snap.is_usable is False
        assert snap.is_expired is True

    def test_trial_expired_is_not_usable(self):
        snap = LicenseSnapshot(state=LicenseState.TRIAL_EXPIRED)
        assert snap.is_usable is False
        assert snap.is_expired is True

    def test_missing_is_not_usable(self):
        snap = LicenseSnapshot(state=LicenseState.MISSING)
        assert snap.is_usable is False

    def test_invalid_is_not_usable(self):
        snap = LicenseSnapshot(state=LicenseState.INVALID)
        assert snap.is_usable is False


class TestDeriveDisplayMode:
    def test_cloud_location(self):
        assert derive_display_mode(
            DeploymentFlavor.CLOUD,
            DeploymentLocation.CLOUD,
            LicenseState.NOT_APPLICABLE,
        ) == DisplayMode.CLOUD

    def test_cloud_flavor_overrides(self):
        assert derive_display_mode(
            DeploymentFlavor.CLOUD,
            DeploymentLocation.SELF_HOSTED,
            LicenseState.MISSING,
        ) == DisplayMode.CLOUD

    def test_ee_active_is_enterprise(self):
        assert derive_display_mode(
            DeploymentFlavor.SELF_HOSTED_EE,
            DeploymentLocation.SELF_HOSTED,
            LicenseState.ACTIVE,
        ) == DisplayMode.ENTERPRISE

    def test_ee_grace_is_enterprise(self):
        assert derive_display_mode(
            DeploymentFlavor.SELF_HOSTED_EE,
            DeploymentLocation.SELF_HOSTED,
            LicenseState.GRACE,
        ) == DisplayMode.ENTERPRISE

    def test_ee_trial_active_is_enterprise(self):
        assert derive_display_mode(
            DeploymentFlavor.SELF_HOSTED_EE,
            DeploymentLocation.SELF_HOSTED,
            LicenseState.TRIAL_ACTIVE,
        ) == DisplayMode.ENTERPRISE

    def test_ee_expired_is_oss_locked(self):
        assert derive_display_mode(
            DeploymentFlavor.SELF_HOSTED_EE,
            DeploymentLocation.SELF_HOSTED,
            LicenseState.EXPIRED,
        ) == DisplayMode.OSS_LOCKED

    def test_ee_missing_is_oss_locked(self):
        assert derive_display_mode(
            DeploymentFlavor.SELF_HOSTED_EE,
            DeploymentLocation.SELF_HOSTED,
            LicenseState.MISSING,
        ) == DisplayMode.OSS_LOCKED

    def test_ee_invalid_is_oss_locked(self):
        assert derive_display_mode(
            DeploymentFlavor.SELF_HOSTED_EE,
            DeploymentLocation.SELF_HOSTED,
            LicenseState.INVALID,
        ) == DisplayMode.OSS_LOCKED

    def test_oss_image_is_oss(self):
        assert derive_display_mode(
            DeploymentFlavor.OSS,
            DeploymentLocation.SELF_HOSTED,
            LicenseState.MISSING,
        ) == DisplayMode.OSS


class TestDenialReasonValues:
    def test_all_reason_codes_are_uppercase(self):
        for reason in DenialReason:
            assert reason.value == reason.value.upper()

    def test_reason_codes_are_unique(self):
        values = [r.value for r in DenialReason]
        assert len(values) == len(set(values))
