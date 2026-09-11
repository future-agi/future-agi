"""
Phase 6a: DeploymentMode + EE Feature Gating Tests
"""

import os
from unittest.mock import patch

import pytest
from django.test import override_settings

from ee.usage.deployment import EE_FEATURES, DeploymentMode, _detect_mode
from tfc.capabilities.registry import PAID_FEATURES

_TEST_SECRET = os.environ.get("CLOUD_DEPLOYMENT_SECRET", "")


@pytest.mark.unit
class TestDeploymentMode:

    def setup_method(self):
        _detect_mode.cache_clear()

    def teardown_method(self):
        _detect_mode.cache_clear()

    @override_settings(CLOUD_DEPLOYMENT="US", EE_LICENSE_KEY="")
    @patch("ee.usage.deployment._validate_cloud_secret", return_value=True)
    def test_cloud_mode_with_valid_secret(self, _mock):
        assert _detect_mode() == "cloud"

    @override_settings(CLOUD_DEPLOYMENT="EU", EE_LICENSE_KEY="")
    @patch("ee.usage.deployment._validate_cloud_secret", return_value=True)
    def test_cloud_eu_with_valid_secret(self, _mock):
        _detect_mode.cache_clear()
        assert _detect_mode() == "cloud"

    @override_settings(CLOUD_DEPLOYMENT="DEV", EE_LICENSE_KEY="")
    @patch("ee.usage.deployment._validate_cloud_secret", return_value=True)
    def test_cloud_dev_with_valid_secret(self, _mock):
        _detect_mode.cache_clear()
        assert _detect_mode() == "cloud"

    @override_settings(
        CLOUD_DEPLOYMENT="US", CLOUD_DEPLOYMENT_SECRET="", EE_LICENSE_KEY=""
    )
    def test_cloud_without_secret_falls_to_oss(self):
        """CLOUD_DEPLOYMENT set but no secret → OSS (the bypass this PR fixes)."""
        _detect_mode.cache_clear()
        assert _detect_mode() == "oss"

    @override_settings(
        CLOUD_DEPLOYMENT="DEV",
        CLOUD_DEPLOYMENT_SECRET="wrong-secret",
        EE_LICENSE_KEY="",
    )
    def test_cloud_with_wrong_secret_falls_to_oss(self):
        """Invalid secret → OSS."""
        _detect_mode.cache_clear()
        assert _detect_mode() == "oss"

    @override_settings(
        CLOUD_DEPLOYMENT="US",
        CLOUD_DEPLOYMENT_SECRET="wrong-secret",
        EE_LICENSE_KEY="eyJhbGciOiJSUzI1NiJ9.test",
    )
    def test_invalid_secret_falls_through_to_ee(self):
        """CLOUD_DEPLOYMENT set with wrong secret but EE key present → EE."""
        _detect_mode.cache_clear()
        assert _detect_mode() == "ee"

    @override_settings(
        CLOUD_DEPLOYMENT="US",
        EE_LICENSE_KEY="eyJhbGciOiJSUzI1NiJ9.test",
    )
    @patch("ee.usage.deployment._validate_cloud_secret", return_value=True)
    def test_cloud_takes_precedence_over_ee(self, _mock):
        """If both CLOUD_DEPLOYMENT (with valid secret) and EE_LICENSE_KEY are set, cloud wins."""
        _detect_mode.cache_clear()
        assert _detect_mode() == "cloud"

    @override_settings(CLOUD_DEPLOYMENT="", EE_LICENSE_KEY="eyJhbGciOiJSUzI1NiJ9.test")
    def test_ee_mode(self):
        _detect_mode.cache_clear()
        assert _detect_mode() == "ee"

    @override_settings(CLOUD_DEPLOYMENT="", EE_LICENSE_KEY="")
    def test_oss_mode(self):
        _detect_mode.cache_clear()
        assert _detect_mode() == "oss"

    @override_settings(CLOUD_DEPLOYMENT="INVALID", EE_LICENSE_KEY="")
    def test_invalid_cloud_deployment_is_oss(self):
        _detect_mode.cache_clear()
        assert _detect_mode() == "oss"

    @override_settings(CLOUD_DEPLOYMENT="US", EE_LICENSE_KEY="")
    @patch("ee.usage.deployment._validate_cloud_secret", return_value=True)
    def test_is_cloud(self, _mock):
        _detect_mode.cache_clear()
        assert DeploymentMode.is_cloud() is True
        assert DeploymentMode.is_ee() is False
        assert DeploymentMode.is_oss() is False

    @override_settings(CLOUD_DEPLOYMENT="", EE_LICENSE_KEY="")
    def test_is_oss(self):
        _detect_mode.cache_clear()
        assert DeploymentMode.is_oss() is True
        assert DeploymentMode.is_cloud() is False


@pytest.mark.unit
class TestCloudSecretValidation:
    """Tests for the secret validation function itself."""

    def setup_method(self):
        _detect_mode.cache_clear()

    def teardown_method(self):
        _detect_mode.cache_clear()

    @pytest.mark.skipif(not _TEST_SECRET, reason="CLOUD_DEPLOYMENT_SECRET not set")
    def test_validate_cloud_secret_valid(self):
        """Real secret from env matches the hardcoded hash."""
        from ee.usage.deployment import _validate_cloud_secret

        assert _validate_cloud_secret(_TEST_SECRET) is True

    def test_validate_cloud_secret_empty(self):
        from ee.usage.deployment import _validate_cloud_secret

        assert _validate_cloud_secret("") is False

    def test_validate_cloud_secret_wrong(self):
        from ee.usage.deployment import _validate_cloud_secret

        assert _validate_cloud_secret("not-the-right-secret") is False

    def test_validate_cloud_secret_hash_as_secret(self):
        """Setting the secret to the hash itself should not pass."""
        from ee.usage.deployment import _EXPECTED_SECRET_HASH, _validate_cloud_secret

        assert _validate_cloud_secret(_EXPECTED_SECRET_HASH) is False


@pytest.mark.unit
class TestEEFeatures:

    def test_ee_features_set_exists(self):
        assert len(EE_FEATURES) > 0

    def test_uses_canonical_paid_feature_registry(self):
        assert EE_FEATURES == PAID_FEATURES

    def test_oss_baseline_features_are_not_ee(self):
        assert "knowledge_base" not in EE_FEATURES
        assert "review_workflow" not in EE_FEATURES

    def test_scim_is_ee(self):
        assert "scim" in EE_FEATURES

    def test_audit_logs_is_ee(self):
        assert "audit_logs" in EE_FEATURES

    def test_core_features_not_in_ee_set(self):
        """Core features should NOT be in the EE set."""
        assert "tracing" not in EE_FEATURES
        assert "datasets" not in EE_FEATURES
        assert "monitors" not in EE_FEATURES
