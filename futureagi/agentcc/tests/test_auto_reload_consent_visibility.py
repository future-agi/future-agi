"""Legacy unbound receipts and changed methods must not look charge-ready."""

from decimal import Decimal

import pytest

from agentcc.models.auto_reload_consent import AgentccAutoReloadConsent
from agentcc.tests import test_auto_reload_payment_binding as binding
from agentcc.tests import test_managed_credits_api as wallet
from agentcc.views import managed_credits as views

# What a build that runs auto-reload shows; this release says it is not
# available for every organization (test_auto_reload_unavailable).
pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db,
    pytest.mark.usefixtures("auto_reload_available"),
]
auto_reload_available = wallet.auto_reload_available
account = binding.account
admin_client = wallet.admin_client
make_client = wallet.make_client


def settings(account):
    account.low_balance_threshold_micros = None
    return views._settings(account, Decimal(25))["auto_reload"]


@pytest.mark.parametrize("invalid", ["legacy", "changed_method", "absent"])
def test_enabled_but_unbound_preference_discloses_need_for_fresh_consent(
    account, admin_client, invalid
):
    account.autoreload_disabled_reason = ""
    if invalid != "absent":
        receipt = binding.record(account, admin_client)
        if invalid == "legacy":
            assert hasattr(receipt, "payment_method_id")
            AgentccAutoReloadConsent.objects.filter(pk=receipt.pk).update(
                payment_method_id=""
            )
        else:
            account.autoreload_payment_method_id = "pm_synthetic_changed"
    displayed = settings(account)
    assert displayed["enabled"] is True
    assert displayed["disabled_reason"] == "auto_reload_consent_required"


def test_matching_current_consent_does_not_show_a_false_refusal(account, admin_client):
    account.autoreload_disabled_reason = ""
    binding.record(account, admin_client)
    assert settings(account)["disabled_reason"] == ""


def test_existing_billing_reason_is_preserved(account):
    account.autoreload_disabled_reason = "synthetic_billing_reason"
    assert settings(account)["disabled_reason"] == "synthetic_billing_reason"


def test_disabled_preference_does_not_require_new_consent(account):
    account.autoreload_enabled = False
    account.autoreload_disabled_reason = ""
    assert settings(account)["disabled_reason"] == ""
