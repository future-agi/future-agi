"""Account-test fixtures shared across RBAC suites.

EE mocking strategy
-------------------
Account RBAC tests assert role/membership semantics, not billing entitlements.

Production gates custom-role edits through the EE entitlement layer
(``tfc.ee_gating.check_ee_feature`` / ``Entitlements.check_feature``). Test
settings use a fake EE license key, so those gates can return 402 before RBAC
assertions run.

These autouse fixtures keep RBAC suites green with *and without* a real EE
checkout:

1. ``_allow_custom_role_gate_for_accounts_tests`` — force-allow CUSTOM_ROLES via
   ``tfc.ee_gating`` (always present in this repo).
2. ``_bypass_plan_entitlement_check_for_accounts_tests`` — if ``ee.usage`` is
   importable, patch ``Entitlements.check_feature`` to allow custom roles.

Non-custom-role product behavior is covered in ``ee/usage`` tests, not here.
Live outbound calls (HubSpot / SMTP / Slack) must be mocked in the test module
that exercises them (see ``test_post_registration.py``).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _allow_custom_role_gate_for_accounts_tests(monkeypatch):
    """Allow CUSTOM_ROLES through tfc.ee_gating for all accounts tests."""
    import tfc.ee_gating as ee_gating

    original_check = ee_gating.check_ee_feature

    def check_ee_feature(feature, *args, **kwargs):
        if ee_gating._feature_name(feature) == ee_gating.EEFeature.CUSTOM_ROLES.value:
            return None
        return original_check(feature, *args, **kwargs)

    monkeypatch.setattr(ee_gating, "check_ee_feature", check_ee_feature)


@pytest.fixture(autouse=True)
def _bypass_plan_entitlement_check_for_accounts_tests():
    """Bypass plan-gating on role-update endpoints when ee.usage is present.

    ``MemberRoleUpdateAPIView`` / workspace role updates call
    ``Entitlements.check_feature("has_custom_roles")``. Without this patch, free
    test orgs get 402 before permission-matrix assertions run.

    No-op when the ``ee`` package is not installed (OSS checkout).
    """
    try:
        from ee.usage.schemas.events import CheckResult
    except ImportError:
        yield
        return

    from ee.usage.services.entitlements import Entitlements

    original_check_feature = Entitlements.check_feature

    def check_feature(org_id, feature):
        if feature == "has_custom_roles":
            return CheckResult(allowed=True)
        return original_check_feature(org_id, feature)

    with patch(
        "ee.usage.services.entitlements.Entitlements.check_feature",
        side_effect=check_feature,
    ):
        yield
