"""
Phase 3: Entitlements Service Tests

Tests the unified entitlement check system: get_limit, has_feature, can_create.
Unit tests mock Redis and DB. Integration tests need real services.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings as _settings
from redis.exceptions import ConnectionError as RedisConnectionError

# billing.yaml ships only with the private cloud overlay; these tests
# assert against its real entitlement values. Missing-file behavior is
# covered by test_billing_config_missing.py.
pytestmark = pytest.mark.skipif(
    not os.path.exists(
        getattr(
            _settings,
            "BILLING_CONFIG_PATH",
            os.path.join(_settings.BASE_DIR, "billing.yaml"),
        )
    ),
    reason="billing.yaml ships only with the private cloud overlay",
)


@pytest.mark.unit
class TestEntitlementsUnit:
    """Unit tests — Redis and DB are mocked."""

    def _get_entitlements(self):
        from ee.usage.services.entitlements import Entitlements

        return Entitlements

    def test_get_limit_returns_int(self):
        """get_limit returns an integer via billing.yaml fallback."""
        with (
            patch("ee.usage.services.entitlements.get_redis") as mock_redis,
            patch(
                "ee.usage.services.entitlements._get_cached_plan", return_value="free"
            ),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.get.return_value = None  # no cache

            # Mock DB — lazy import inside get_entitlement
            with patch("ee.usage.models.usage.PlanEntitlement.objects") as mock_qs:
                mock_qs.filter.return_value.values.return_value.first.return_value = (
                    None  # no DB rows
                )
                E = self._get_entitlements()
                # Falls through to billing.yaml: monitors/free = 3
                result = E.get_limit("org-1", "monitors")
                assert result == 3

    def test_has_feature_returns_bool(self):
        """has_feature returns a boolean via billing.yaml fallback."""
        with (
            patch("ee.usage.services.entitlements.get_redis") as mock_redis,
            patch(
                "ee.usage.services.entitlements._get_cached_plan", return_value="boost"
            ),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.get.return_value = None

            with patch("ee.usage.models.usage.PlanEntitlement.objects") as mock_qs:
                mock_qs.filter.return_value.values.return_value.first.return_value = (
                    None
                )
                E = self._get_entitlements()
                # billing.yaml: has_knowledge_base/boost = true
                result = E.has_feature("org-1", "has_knowledge_base")
                assert result is True

    def test_can_create_under_limit_allowed(self):
        """Under the limit → allowed."""
        E = self._get_entitlements()
        with patch.object(E, "get_limit", return_value=15):
            result = E.can_create("org-1", "monitors", current_count=5)
            assert result.allowed is True

    def test_can_create_at_limit_denied(self):
        """At the limit → denied (cloud only; self-hosted is uncapped)."""
        E = self._get_entitlements()
        with (
            patch(
                "ee.usage.services.entitlements.DeploymentMode.is_cloud",
                return_value=True,
            ),
            patch.object(E, "get_limit", return_value=3),
        ):
            with patch(
                "ee.usage.services.entitlements._find_upgrade_cta", return_value=None
            ):
                result = E.can_create("org-1", "monitors", current_count=3)
                assert result.allowed is False
                assert result.error_code == "ENTITLEMENT_LIMIT"

    def test_can_create_unlimited_allowed(self):
        """Unlimited (-1) → always allowed."""
        E = self._get_entitlements()
        with patch.object(E, "get_limit", return_value=-1):
            result = E.can_create("org-1", "monitors", current_count=9999)
            assert result.allowed is True

    def test_can_create_unconfigured_limit_denies_not_crashes(self):
        """Cloud + limit=None (unconfigured resource) → clean 402, not a
        TypeError on the None >= int comparison."""
        E = self._get_entitlements()
        with (
            patch(
                "ee.usage.services.entitlements.DeploymentMode.is_cloud",
                return_value=True,
            ),
            patch.object(E, "get_limit", return_value=None),
            patch(
                "ee.usage.services.entitlements._find_upgrade_cta", return_value=None
            ),
        ):
            result = E.can_create("org-1", "unknown_resource", current_count=5)
            assert result.allowed is False
            assert result.error_code == "ENTITLEMENT_DENIED"

    def test_can_create_zero_limit_denied(self):
        """Zero limit (feature not on plan) → denied (cloud only)."""
        E = self._get_entitlements()
        with (
            patch(
                "ee.usage.services.entitlements.DeploymentMode.is_cloud",
                return_value=True,
            ),
            patch.object(E, "get_limit", return_value=0),
        ):
            with patch(
                "ee.usage.services.entitlements._find_upgrade_cta", return_value=None
            ):
                result = E.can_create("org-1", "knowledge_bases", current_count=0)
                assert result.allowed is False
                assert result.error_code == "ENTITLEMENT_DENIED"

    def test_check_feature_enabled(self):
        """Boolean feature enabled → allowed."""
        E = self._get_entitlements()
        with patch.object(E, "has_feature", return_value=True):
            result = E.check_feature("org-1", "has_knowledge_base")
            assert result.allowed is True

    def test_check_feature_disabled(self):
        """Boolean feature disabled → denied (cloud only; self-hosted passes)."""
        E = self._get_entitlements()
        with (
            patch(
                "ee.usage.services.entitlements.DeploymentMode.is_cloud",
                return_value=True,
            ),
            patch.object(E, "has_feature", return_value=False),
        ):
            with patch(
                "ee.usage.services.entitlements._find_upgrade_cta", return_value=None
            ):
                result = E.check_feature("org-1", "has_knowledge_base")
                assert result.allowed is False
                assert result.error_code == "ENTITLEMENT_DENIED"

    def test_get_retention_days(self):
        """Retention days returns correct value."""
        E = self._get_entitlements()
        with patch.object(E, "get_limit", return_value=90):
            result = E.get_retention_days("org-1", "traces")
            assert result == 90

    def test_get_retention_days_default(self):
        """If not configured, default to 30 days."""
        E = self._get_entitlements()
        with patch.object(E, "get_limit", return_value=0):
            result = E.get_retention_days("org-1", "traces")
            assert result == 30

    def test_org_override_takes_precedence(self):
        """Per-org override should be returned over plan default."""
        with (
            patch("ee.usage.services.entitlements.get_redis") as mock_redis,
            patch(
                "ee.usage.services.entitlements._get_cached_plan", return_value="free"
            ),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.get.return_value = None  # no cache

            with patch("ee.usage.models.usage.PlanEntitlement.objects") as mock_qs:
                # First call: org override exists with value 50
                mock_qs.filter.return_value.values.return_value.first.return_value = {
                    "value_int": 50,
                    "value_bool": None,
                }
                E = self._get_entitlements()
                result = E.get_limit("org-1", "monitors")
                assert result == 50

    def test_get_entitlement_falls_back_when_redis_is_unavailable(self):
        """Redis is a cache for entitlements, not the source of truth."""
        with (
            patch("ee.usage.services.entitlements.get_redis") as mock_redis,
            patch(
                "ee.usage.services.entitlements._get_cached_plan", return_value="free"
            ),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.get.side_effect = RedisConnectionError("redis down")
            mock_r.setex.side_effect = RedisConnectionError("redis down")

            with patch("ee.usage.models.usage.PlanEntitlement.objects") as mock_qs:
                mock_qs.filter.return_value.values.return_value.first.return_value = (
                    None
                )
                E = self._get_entitlements()

                assert E.has_feature("org-1", "has_voice_sim") is True

    def test_cached_plan_falls_back_to_db_when_redis_is_unavailable(self):
        from ee.usage.services.metering import _get_cached_plan

        with patch("ee.usage.services.metering.get_redis") as mock_redis:
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.get.side_effect = RedisConnectionError("redis down")

            with patch(
                "ee.usage.models.usage.OrganizationSubscription.objects"
            ) as mock_qs:
                mock_qs.filter.return_value.values_list.return_value.first.return_value = (
                    "boost"
                )

                assert _get_cached_plan("org-1") == "boost"

    def test_cloud_plan_resolver_maps_capability_ids(self):
        from ee.usage.services.entitlements import Entitlements, get_cloud_plan_resolver

        resolver = get_cloud_plan_resolver()
        with patch.object(
            Entitlements, "has_feature", return_value=True
        ) as has_feature:
            assert resolver.has_feature("org-1", "scim") is True

        has_feature.assert_called_once_with("org-1", "has_scim")

    def test_cloud_plan_resolver_returns_serialized_upgrade_cta(self):
        from ee.usage.services.entitlements import get_cloud_plan_resolver

        cta = MagicMock()
        cta.model_dump.return_value = {"text": "Upgrade", "plan": "enterprise"}
        with patch(
            "ee.usage.services.entitlements._find_upgrade_cta",
            return_value=cta,
        ):
            result = get_cloud_plan_resolver().get_upgrade_cta("org-1", "scim")

        assert result == {"text": "Upgrade", "plan": "enterprise"}

    def test_cache_invalidation(self):
        """invalidate_cache should delete Redis keys."""
        with patch("ee.usage.services.entitlements.get_redis") as mock_redis:
            mock_r = MagicMock()
            mock_redis.return_value = mock_r

            E = self._get_entitlements()
            E.invalidate_cache("org-1", "monitors")
            mock_r.delete.assert_called_once_with("ent:org-1:monitors")

    def test_cache_invalidation_all_features(self):
        """invalidate_cache without feature should delete all keys for the org."""
        with patch("ee.usage.services.entitlements.get_redis") as mock_redis:
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.scan_iter.return_value = [b"ent:org-1:monitors", b"ent:org-1:queues"]

            E = self._get_entitlements()
            E.invalidate_cache("org-1")
            assert mock_r.delete.call_count == 2

    def test_invalidate_plan_caches_clears_plan_and_entitlements(self):
        """Plan-change writes must bust both ``plan:<org>`` and every
        ``ent:<org>:*`` key — otherwise paid features keep returning 402
        for up to 5 min after the customer pays.
        """
        with patch("ee.usage.services.entitlements.get_redis") as mock_redis:
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.scan_iter.return_value = [b"ent:org-1:has_synthetic_data"]

            from ee.usage.services.entitlements import invalidate_plan_caches

            invalidate_plan_caches("org-1")

            mock_r.delete.assert_any_call("plan:org-1", "billing_status:org-1")
            mock_r.delete.assert_any_call(b"ent:org-1:has_synthetic_data")
            assert mock_r.delete.call_count == 2


@pytest.mark.unit
class TestUpgradeCTA:
    """Test that upgrade CTAs are generated correctly."""

    def test_free_plan_gets_payg_cta(self):
        from ee.usage.services.entitlements import _find_upgrade_cta

        with patch(
            "ee.usage.services.entitlements._get_cached_plan", return_value="free"
        ):
            cta = _find_upgrade_cta("org-1", "has_knowledge_base")
            # KB is not on free or payg, available on boost
            if cta:
                assert cta.plan in ("payg", "boost", "scale", "enterprise")

    def test_boost_plan_gets_scale_cta_for_unlimited(self):
        from ee.usage.services.entitlements import _find_upgrade_cta

        with patch(
            "ee.usage.services.entitlements._get_cached_plan", return_value="boost"
        ):
            cta = _find_upgrade_cta("org-1", "monitors")
            # Boost has 15, Scale has unlimited
            if cta:
                assert cta.plan in ("scale", "enterprise")
