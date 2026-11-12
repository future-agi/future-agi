"""Auto-reload is not available in this release.

Nothing in this build runs a managed-credit auto-reload, so the settings save
refuses to turn it on (before writing anything: no account change, no consent
receipt) and every credit summary says it is not available, whatever the
organization has stored. Turning it off and saving the low-balance alert still
work without any agreement. No payment network: a charge would have to start
with a claim, and Stripe's PaymentIntent create is watched.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import transaction

from agentcc.models.auto_reload_consent import AgentccAutoReloadConsent
from agentcc.services.auto_reload_consent import (
    has_current_auto_reload_consent,
    record_auto_reload_consent,
)
from agentcc.tests import test_managed_credits_api as wallet

pytestmark = [pytest.mark.integration, pytest.mark.django_db]
admin_client = wallet.admin_client
make_client = wallet.make_client
gateway = wallet.gateway
ledger = wallet.ledger

# Written out, not imported, so a change to what the person reads or what a
# client matches on is a deliberate change to these tests.
CODE = "auto_reload_unavailable"
SENTENCE = "Automatic reloads are not available in this release."
VERSION = "auto-reload-v1"
METHOD = "pm_synthetic_unavailable"
ACCEPTED_AT = datetime(2026, 11, 6, tzinfo=UTC)
MICROS = wallet.MICROS
SUMMARY_URL = wallet.BASE
SETTINGS_URL = f"{wallet.BASE}settings/"


def accounts(ledger, organization):
    return ledger.ManagedCreditAccount.objects.filter(organization=organization)


def receipts(organization):
    return AgentccAutoReloadConsent.objects.filter(organization_id=organization.pk)


def store(ledger, organization, stored, user=None):
    """The organization's saved settings: none, auto-reload off, or on.

    Every stored account has a saved method and both daily caps, so nothing
    but the auto-reload setting itself stands between it and a claim.
    """
    if stored == "missing":
        return None
    on = stored.startswith("on")
    account = ledger.ManagedCreditAccount.objects.create(
        organization=organization,
        low_balance_threshold_micros=2 * MICROS,
        autoreload_enabled=on,
        autoreload_threshold_micros=10 * MICROS if on else None,
        autoreload_amount_micros=25 * MICROS if on else None,
        autoreload_consent_version=VERSION if on else "",
        autoreload_consent_at=ACCEPTED_AT if on else None,
        autoreload_payment_method_id=METHOD,
        autoreload_daily_count_cap=2,
        autoreload_daily_amount_cap_micros=50 * MICROS,
    )
    if stored == "on_with_current_receipt":
        with transaction.atomic():
            record_auto_reload_consent(
                account, version=VERSION, actor=user, accepted_at=ACCEPTED_AT
            )
        assert has_current_auto_reload_consent(account)
    return account


def enable_body(consent):
    auto_reload = {"enabled": True, "threshold_usd": "10", "amount_usd": "25"}
    if consent == "current":
        auto_reload["consent_version"] = VERSION
    elif consent == "outdated":
        auto_reload["consent_version"] = "old-terms"
    return {"low_balance_threshold_usd": "5", "auto_reload": auto_reload}


def actor(client):
    return client.handler._force_user


@pytest.mark.parametrize("consent", ["current", "missing", "outdated"])
@pytest.mark.parametrize("stored", ["missing", "off", "on"])
def test_enabling_is_refused_with_the_sentence_and_writes_nothing(
    ledger, organization, admin_client, consent, stored
):
    store(ledger, organization, stored)
    before = list(accounts(ledger, organization).values())

    response = admin_client.put(SETTINGS_URL, enable_body(consent), format="json")

    assert response.status_code == 400, response.content
    assert response.json()["code"] == CODE
    assert response.json()["result"] == SENTENCE
    # Not even the low-balance alert in the same body is saved.
    assert list(accounts(ledger, organization).values()) == before
    assert not receipts(organization).exists()


def test_direct_settings_save_refuses_enabling_and_records_no_receipt(
    ledger, organization, admin_client
):
    from agentcc.views.managed_credits import ManagedCreditsApiError, save_settings

    store(ledger, organization, "off")
    before = list(accounts(ledger, organization).values())
    data = {
        "low_balance_threshold_usd": Decimal(5),
        "auto_reload": {
            "enabled": True,
            "consent_version": VERSION,
            "threshold_usd": Decimal(10),
            "amount_usd": Decimal(25),
        },
    }

    with pytest.raises(ManagedCreditsApiError) as refusal:
        save_settings(
            ledger, organization, data, Decimal(25), False, actor=actor(admin_client)
        )

    assert refusal.value.status_code == 400
    assert refusal.value.code == CODE
    assert str(refusal.value) == SENTENCE
    assert list(accounts(ledger, organization).values()) == before
    assert not receipts(organization).exists()


@pytest.mark.parametrize("stored", ["missing", "off", "on_with_current_receipt"])
def test_turning_off_and_the_low_balance_alert_still_save_without_consent(
    ledger, organization, admin_client, stored
):
    store(ledger, organization, stored, actor(admin_client))
    kept = receipts(organization).count()
    body = {"low_balance_threshold_usd": "7", "auto_reload": {"enabled": False}}

    response = admin_client.put(SETTINGS_URL, body, format="json")

    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["low_balance_threshold_usd"] == 7.0
    assert result["auto_reload"]["enabled"] is False
    assert result["auto_reload"]["disabled_reason"] == CODE
    account = ledger.get_account(organization)
    assert account.low_balance_threshold_micros == 7 * MICROS
    assert account.autoreload_enabled is False
    # Turning it off records no agreement and deletes no earlier one.
    assert receipts(organization).count() == kept


@pytest.mark.parametrize(
    "stored", ["missing", "off", "on_without_receipt", "on_with_current_receipt"]
)
def test_every_summary_says_auto_reload_is_not_available(
    ledger, organization, admin_client, gateway, stored
):
    store(ledger, organization, stored, actor(admin_client))
    before = list(accounts(ledger, organization).values())
    kept = list(receipts(organization).values())

    summary = admin_client.get(SUMMARY_URL)
    settings = admin_client.get(SETTINGS_URL)

    assert summary.status_code == 200, summary.content
    assert settings.status_code == 200, settings.content
    on = stored.startswith("on")
    for read in (summary, settings):
        auto_reload = read.json()["result"]["auto_reload"]
        assert auto_reload["disabled_reason"] == CODE
        # The stored preference is reported as stored, never rewritten.
        assert auto_reload["enabled"] is on
    assert list(accounts(ledger, organization).values()) == before
    assert list(receipts(organization).values()) == kept


def test_a_refused_enable_leaves_nothing_to_claim_or_charge(
    ledger, organization, admin_client
):
    # The balance (zero) is at or below the requested threshold, the saved
    # method and both daily caps are set: only the refusal stops a claim.
    store(ledger, organization, "off")
    balance = ledger.ledger_balance(organization).balance_micros
    entries = ledger.ledger_entries(organization).count()

    with patch("stripe.PaymentIntent.create") as create_payment_intent:
        response = admin_client.put(SETTINGS_URL, enable_body("current"), format="json")
        claim = ledger.claim_auto_topup(organization)

    assert claim is None
    assert response.status_code == 400, response.content
    assert response.json()["code"] == CODE
    create_payment_intent.assert_not_called()
    assert ledger.ledger_balance(organization).balance_micros == balance
    assert ledger.ledger_entries(organization).count() == entries
    account = ledger.get_account(organization)
    assert account.autoreload_in_flight_seq is None
    assert account.autoreload_seq == 0
