"""billing.yaml defines every key product code bills or gates on.

Nothing fails loudly when one is missing. check_usage allows an event type
billing.yaml doesn't list and the billing consumer drops its events, so the
usage goes unbilled and the credit cap checks nothing. A capability's Cloud
entitlement (``has_<feature>``) that isn't there resolves to False, so every
Cloud org loses the feature. These tests load the real file through
BillingConfig and look the keys up.

billing.yaml ships only with the private cloud overlay (future-agi/ee checked
out at ee/cloud/), so the module skips without it, as test_billing_config.py
does. Backend CI's internal runs check that repo out on the branch named like
the PR's head, else its base, else dev, and run it there.

The overlay and this repo merge separately. An overlay that has none of the
Jev / Instant eval / Insights keys yet (it predates the change that adds them)
skips those keys' cases instead of turning every other branch red; one that
has some of them but not all fails, since that is a real gap. A run for main
(a push to it, or a PR into it) never skips: main's code deploys with the
overlay's main, and without the keys Cloud bills no Jev or Insights usage and
grants no org the three features.
"""

import os

import pytest
from django.conf import settings

from ee.agenthub.insights import billing as insights_billing
from ee.usage.schemas.event_types import BillingEventType
from ee.usage.services.config import BillingConfig
from tfc.capabilities.registry import (
    FEATURE_INSIGHTS,
    FEATURE_INSTANT_EVAL,
    FEATURE_JEV_MODELS,
    FEATURE_REGISTRY,
)

pytestmark = [
    pytest.mark.requires_ee,
    pytest.mark.skipif(
        not os.path.exists(
            getattr(
                settings,
                "BILLING_CONFIG_PATH",
                os.path.join(settings.BASE_DIR, "billing.yaml"),
            )
        ),
        reason="billing.yaml ships only with the private cloud overlay",
    ),
]

# What Jev evaluations (ee/jev/metering.py) and Insights reports
# (ee/agenthub/insights/billing.py) emit and check credits against.
CREDIT_EVENTS = [BillingEventType.JEV_EVALUATOR.value, insights_billing.EVENT]

# On Cloud a capability is the plan entitlement ``has_<id>``
# (ee/usage/services/entitlements.py _CloudPlanResolver), read for every
# feature that isn't OSS baseline (tfc/capabilities/service.py check).
CLOUD_GATED = sorted(f.id for f in FEATURE_REGISTRY.values() if not f.oss_baseline)


# The keys this change adds to billing.yaml.
NEW_CALL_TYPES = set(CREDIT_EVENTS)
NEW_ENTITLEMENT_FEATURES = {
    FEATURE_JEV_MODELS.id,
    FEATURE_INSTANT_EVAL.id,
    FEATURE_INSIGHTS.id,
}


@pytest.fixture(scope="module")
def billing():
    return BillingConfig.reload()


def _overlay_has_any_new_key(billing):
    if NEW_CALL_TYPES & set(billing.get_all_call_types()):
        return True
    return any(
        billing.get_entitlement_default(f"has_{feature_id}", plan) is not None
        for feature_id in NEW_ENTITLEMENT_FEATURES
        for plan in billing.get_all_plans()
    )


def _testing_main():
    # Set by backend-ci.yml: the branch under test and the PR's base.
    return "main" in (os.environ.get("EE_REPO_BRANCH"), os.environ.get("EE_BASE_REF"))


def _skip_if_overlay_predates_new_keys(billing, key_is_new):
    if key_is_new and not _testing_main() and not _overlay_has_any_new_key(billing):
        pytest.skip(
            "the cloud overlay checked out predates the Jev / Instant eval / "
            "Insights billing keys"
        )


@pytest.mark.parametrize("event_type", list(BillingEventType), ids=lambda e: e.value)
def test_every_event_type_is_an_api_call_type(billing, event_type):
    _skip_if_overlay_predates_new_keys(billing, event_type.value in NEW_CALL_TYPES)
    assert event_type.value in billing.get_all_call_types()


@pytest.mark.parametrize("event_type", CREDIT_EVENTS)
def test_jev_and_insights_events_are_charged_in_ai_credits(billing, event_type):
    _skip_if_overlay_predates_new_keys(billing, True)
    assert billing.get_call_type(event_type).dimension == "ai_credits"


def test_jev_instant_eval_and_insights_are_cloud_gated():
    assert {
        FEATURE_JEV_MODELS.id,
        FEATURE_INSTANT_EVAL.id,
        FEATURE_INSIGHTS.id,
    } <= set(CLOUD_GATED)


@pytest.mark.parametrize("feature_id", CLOUD_GATED)
def test_every_cloud_gated_capability_is_a_boolean_entitlement_on_every_plan(
    billing, feature_id
):
    _skip_if_overlay_predates_new_keys(billing, feature_id in NEW_ENTITLEMENT_FEATURES)
    # Entitlements.has_feature treats anything but a bool as not included.
    values = {
        plan: billing.get_entitlement_default(f"has_{feature_id}", plan)
        for plan in billing.get_all_plans()
    }
    assert values
    assert all(isinstance(value, bool) for value in values.values()), values


class _OverlayWithout:
    """An overlay with none of the new keys; ``call_types`` and
    ``entitlements`` add some back."""

    def __init__(self, call_types=(), entitlements=()):
        self.call_types = dict.fromkeys(call_types)
        self.entitlements = set(entitlements)

    def get_all_call_types(self):
        return self.call_types

    def get_all_plans(self):
        return {"free": None}

    def get_entitlement_default(self, feature, plan):
        return True if feature in self.entitlements else None


def _skips(overlay, key_is_new):
    # The rule's tests assert on this rather than calling the rule in their
    # body, where a wrong skip would report them skipped and leave CI green.
    try:
        _skip_if_overlay_predates_new_keys(overlay, key_is_new)
    except pytest.skip.Exception:
        return True
    return False


def test_an_overlay_predating_every_new_key_skips_them_off_main(monkeypatch):
    monkeypatch.setenv("EE_REPO_BRANCH", "dev")
    monkeypatch.setenv("EE_BASE_REF", "dev")
    assert _skips(_OverlayWithout(), True)
    # The keys that were there before are always checked.
    assert not _skips(_OverlayWithout(), False)


@pytest.mark.parametrize("variable", ["EE_REPO_BRANCH", "EE_BASE_REF"])
def test_a_run_for_main_never_skips_a_missing_key(monkeypatch, variable):
    monkeypatch.setenv("EE_REPO_BRANCH", "feat/x")
    monkeypatch.setenv("EE_BASE_REF", "dev")
    monkeypatch.setenv(variable, "main")
    assert not _skips(_OverlayWithout(), True)


@pytest.mark.parametrize(
    "overlay",
    [
        _OverlayWithout(call_types=[BillingEventType.JEV_EVALUATOR.value]),
        _OverlayWithout(entitlements=[f"has_{FEATURE_INSIGHTS.id}"]),
    ],
    ids=["call_type", "entitlement"],
)
def test_an_overlay_with_some_new_keys_never_skips(monkeypatch, overlay):
    monkeypatch.delenv("EE_REPO_BRANCH", raising=False)
    monkeypatch.delenv("EE_BASE_REF", raising=False)
    assert not _skips(overlay, True)
