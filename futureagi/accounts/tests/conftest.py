"""Account-test fixtures shared across RBAC suites."""

import pytest


@pytest.fixture(autouse=True)
def _allow_custom_role_gate_for_accounts_tests(monkeypatch):
    """Account RBAC tests assert role semantics, not billing entitlements.

    The production endpoints correctly gate custom role edits through the EE
    entitlement layer. Test settings use a fake EE license key, so the gate can
    return 402 before RBAC assertions run. Keep non-custom-role gates unchanged
    and let ee/usage tests cover entitlement denial directly.
    """
    import tfc.ee_gating as ee_gating

    original_check = ee_gating.check_ee_feature

    def check_ee_feature(feature, *args, **kwargs):
        if ee_gating._feature_name(feature) == ee_gating.EEFeature.CUSTOM_ROLES.value:
            return None
        return original_check(feature, *args, **kwargs)

    monkeypatch.setattr(ee_gating, "check_ee_feature", check_ee_feature)
