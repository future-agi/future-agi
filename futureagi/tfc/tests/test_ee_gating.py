"""Tests for the EE gating layer in `tfc.ee_gating`.

Covers:
- `FeatureUnavailable` surfaces as HTTP 402 via `custom_exception_handler`.
- `is_oss()` probes via `has_ee("ee.usage")`.
- `require_ee_feature` denies when ee is absent.
- `_ee_stub` and `_ee_activity_stub` raise the right exception types.
- `EEFeature` enum mirrors the ee-side `EE_FEATURES` set (skipped when
  ee/ is absent).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from rest_framework import status as drf_status

from tfc.ee_gating import (
    EE_FEATURES_OSS,
    EEFeature,
    EEResource,
    FeatureUnavailable,
    check_ee_can_create,
    check_ee_feature,
    is_oss,
    require_ee_feature,
)
from tfc.ee_loader import has_ee


# ── FeatureUnavailable ────────────────────────────────────────────────────


class TestFeatureUnavailable:
    def test_status_code_is_402(self):
        exc = FeatureUnavailable(EEFeature.KNOWLEDGE_BASE)
        assert exc.status_code == drf_status.HTTP_402_PAYMENT_REQUIRED

    def test_carries_feature_name(self):
        exc = FeatureUnavailable(EEFeature.CUSTOM_ROLES)
        assert exc.feature == "custom_roles"

    def test_accepts_plain_string(self):
        exc = FeatureUnavailable("ad_hoc_feature")
        assert exc.feature == "ad_hoc_feature"

    def test_default_code_is_entitlement_denied(self):
        exc = FeatureUnavailable(EEFeature.SCIM)
        assert exc.default_code == "ENTITLEMENT_DENIED"

    def test_custom_exception_handler_returns_402(self):
        """FeatureUnavailable should reach custom_exception_handler as 402
        with the structured payload the frontend expects."""
        from accounts.authentication import custom_exception_handler

        exc = FeatureUnavailable(EEFeature.AUDIT_LOGS)
        response = custom_exception_handler(exc, context={})

        assert response.status_code == 402
        assert response.data["status"] is False
        assert response.data["error"]["code"] == "ENTITLEMENT_DENIED"
        assert response.data["error"]["detail"]["feature"] == "audit_logs"
        assert response.data["upgrade_required"] is True
        # upgrade_cta should not appear unless explicitly provided.
        assert "upgrade_cta" not in response.data

    def test_upgrade_cta_threaded_through_to_response(self):
        """Cloud denials carry upgrade_cta — the exception handler must
        surface it so the FE upsell banner can render targeted copy."""
        from accounts.authentication import custom_exception_handler

        cta = {"plan": "scale", "url": "/billing/upgrade", "text": "Upgrade to Scale"}
        exc = FeatureUnavailable(EEFeature.OPTIMIZATION, upgrade_cta=cta)
        response = custom_exception_handler(exc, context={})

        assert response.status_code == 402
        assert response.data["upgrade_cta"] == cta


# ── is_oss ────────────────────────────────────────────────────────────────


class TestIsOss:
    def setup_method(self):
        is_oss.cache_clear()

    def teardown_method(self):
        is_oss.cache_clear()

    def test_oss_when_ee_not_installed(self):
        with patch("tfc.ee_gating.has_ee", return_value=False):
            assert is_oss() is True

    def test_delegates_to_deployment_mode_when_ee_present(self):
        """When ee/ is installed, is_oss() must defer to DeploymentMode."""
        if not has_ee("ee.usage"):
            pytest.skip("ee/ not present — only meaningful with ee/ installed")

        from ee.usage.deployment import DeploymentMode

        # Force DeploymentMode.is_oss() to return True; is_oss() should too.
        with patch.object(DeploymentMode, "is_oss", return_value=True):
            assert is_oss() is True

        is_oss.cache_clear()
        with patch.object(DeploymentMode, "is_oss", return_value=False):
            assert is_oss() is False


# ── check_ee_feature / require_ee_feature ─────────────────────────────────


class TestCheckEEFeature:
    def setup_method(self):
        is_oss.cache_clear()

    def test_oss_denies(self):
        with patch("tfc.ee_gating.is_oss", return_value=True):
            with pytest.raises(FeatureUnavailable) as exc_info:
                check_ee_feature(EEFeature.KNOWLEDGE_BASE)
            assert exc_info.value.feature == "knowledge_base"

    def test_oss_denies_with_activity_flag(self):
        """activity=True must raise a Temporal ApplicationError (non-retryable)."""
        from temporalio.exceptions import ApplicationError

        with patch("tfc.ee_gating.is_oss", return_value=True):
            with pytest.raises(ApplicationError) as exc_info:
                check_ee_feature(EEFeature.VOICE_SIM, activity=True)
            assert exc_info.value.non_retryable is True
            assert exc_info.value.type == "FeatureUnavailable"

    def test_non_oss_without_org_id_passes(self):
        with patch("tfc.ee_gating.is_oss", return_value=False):
            check_ee_feature(EEFeature.SCIM)  # no-op: no org_id, no deny

    def test_non_ee_feature_passes_in_oss(self):
        """A string that isn't in EE_FEATURES_OSS must not be denied.
        Prevents footgun where a typo (e.g. 'tracing') would 402 users on
        deployments without ee/.
        """
        with patch("tfc.ee_gating.is_oss", return_value=True):
            check_ee_feature("tracing")  # must not raise
            check_ee_feature("datasets")  # core feature, not EE


class TestRaiseDeniedLogs:
    def test_denial_emits_structured_log(self, caplog):
        """Ops/analytics need visibility into which EE features users are
        hitting. _raise_denied must emit a structured log on every deny.
        """
        import logging

        from tfc.ee_gating import _raise_denied

        caplog.set_level(logging.INFO, logger="tfc.ee_gating")
        with pytest.raises(FeatureUnavailable):
            _raise_denied("knowledge_base", activity=False)

        matches = [
            r for r in caplog.records if r.message == "ee_feature_denied"
        ]
        assert matches, "expected ee_feature_denied log record"
        assert matches[0].feature == "knowledge_base"
        assert matches[0].activity is False


class TestRequireEEFeatureDecorator:
    def setup_method(self):
        is_oss.cache_clear()

    def test_sync_fn_denied_in_oss(self):
        @require_ee_feature(EEFeature.OPTIMIZATION)
        def endpoint():
            return "ok"

        with patch("tfc.ee_gating.is_oss", return_value=True):
            with pytest.raises(FeatureUnavailable):
                endpoint()

    def test_sync_fn_passes_in_ee(self):
        @require_ee_feature(EEFeature.OPTIMIZATION)
        def endpoint():
            return "ok"

        with patch("tfc.ee_gating.is_oss", return_value=False):
            assert endpoint() == "ok"


# ── Stubs ─────────────────────────────────────────────────────────────────


class TestEEStub:
    def test_ee_stub_raises_feature_unavailable(self):
        from tfc.ee_stub import _ee_stub

        Stub = _ee_stub("FakeThing")
        with pytest.raises(FeatureUnavailable) as exc_info:
            Stub(1, 2, keyword="x")
        assert exc_info.value.feature == "FakeThing"

    def test_ee_activity_stub_raises_application_error(self):
        from temporalio.exceptions import ApplicationError

        from tfc.ee_stub import _ee_activity_stub

        Stub = _ee_activity_stub("FakeActivity")
        with pytest.raises(ApplicationError) as exc_info:
            Stub()
        assert exc_info.value.non_retryable is True


# ── EEFeature enum <-> EE_FEATURES parity ────────────────────────────────


class TestEEFeatureMirror:
    def test_ee_features_oss_mirror_matches_enum(self):
        assert EE_FEATURES_OSS == frozenset(f.value for f in EEFeature)

    def test_ee_features_oss_matches_ee_source_when_present(self):
        """The local mirror must stay in sync with the ee-side source."""
        if not has_ee("ee.usage"):
            pytest.skip("ee/ not present")

        from ee.usage.deployment import EE_FEATURES as EE_FEATURES_SOURCE

        assert EE_FEATURES_OSS == EE_FEATURES_SOURCE, (
            "tfc.ee_gating.EEFeature has drifted from ee.usage.deployment."
            "EE_FEATURES. Add/remove members to keep them in sync."
        )


# ── check_ee_can_create ───────────────────────────────────────────────────


class TestCheckEECanCreate:
    def setup_method(self):
        is_oss.cache_clear()

    def test_oss_denies(self):
        with patch("tfc.ee_gating.is_oss", return_value=True):
            with pytest.raises(FeatureUnavailable):
                check_ee_can_create(
                    EEResource.GATEWAY_WEBHOOKS, org_id="org-1", current_count=0
                )
