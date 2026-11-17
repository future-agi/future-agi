"""Saved-method binding: synthetic IDs, real receipt table, no payment network."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import transaction

from agentcc.auto_reload_terms import AutoReloadConsentError
from agentcc.models.auto_reload_consent import AgentccAutoReloadConsent
from agentcc.services.auto_reload_consent import (
    has_current_auto_reload_consent,
    record_auto_reload_consent,
)
from agentcc.tests import test_managed_credits_api as wallet

# The binding rules of a build that runs auto-reload; this release refuses
# turning it on (test_auto_reload_unavailable).
pytestmark = [
    pytest.mark.integration,
    pytest.mark.django_db,
    pytest.mark.usefixtures("auto_reload_available"),
]
auto_reload_available = wallet.auto_reload_available
admin_client = wallet.admin_client
make_client = wallet.make_client
ledger = wallet.ledger
VERSION = "auto-reload-v1"
METHOD = "pm_synthetic_consent_bound"


@pytest.fixture
def account(organization):
    return SimpleNamespace(
        organization_id=organization.pk,
        autoreload_enabled=True,
        autoreload_consent_version=VERSION,
        autoreload_consent_at=datetime(2026, 11, 6, tzinfo=UTC),
        autoreload_threshold_micros=10_000_000,
        autoreload_amount_micros=25_000_000,
        autoreload_payment_method_id=METHOD,
    )


def record(account, admin_client):
    with transaction.atomic():
        return record_auto_reload_consent(
            account,
            version=VERSION,
            actor=admin_client.handler._force_user,
            accepted_at=account.autoreload_consent_at,
        )


def test_receipt_snapshots_the_persisted_payment_method(account, admin_client):
    receipt = record(account, admin_client)
    assert getattr(receipt, "payment_method_id", None) == METHOD
    assert has_current_auto_reload_consent(account)


@pytest.mark.parametrize("replacement", ["pm_synthetic_replacement", "", None])
def test_changed_or_removed_method_invalidates_old_receipt(
    account, admin_client, replacement
):
    record(account, admin_client)
    account.autoreload_payment_method_id = replacement
    assert not has_current_auto_reload_consent(account)


def test_new_acknowledgement_binds_new_method_without_rewriting_history(
    account, admin_client
):
    first = record(account, admin_client)
    account.autoreload_payment_method_id = "pm_synthetic_replacement"
    assert not has_current_auto_reload_consent(account)
    second = record(account, admin_client)
    assert has_current_auto_reload_consent(account)
    first.refresh_from_db()
    assert first.payment_method_id == METHOD
    assert second.payment_method_id == "pm_synthetic_replacement"
    assert AgentccAutoReloadConsent.objects.count() == 2


@pytest.mark.parametrize("missing", ["", None, " ", 123, "x" * 256])
def test_cannot_record_consent_without_a_usable_saved_method(
    account, admin_client, missing
):
    account.autoreload_payment_method_id = missing
    with pytest.raises(AutoReloadConsentError) as refusal:
        record(account, admin_client)
    assert refusal.value.code == "auto_reload_payment_method_required"
    assert not AgentccAutoReloadConsent.objects.exists()


def test_blank_legacy_receipt_is_never_upgraded_implicitly(account, admin_client):
    receipt = record(account, admin_client)
    assert hasattr(receipt, "payment_method_id"), "receipt needs a payment binding"
    AgentccAutoReloadConsent.objects.filter(pk=receipt.pk).update(payment_method_id="")
    assert not has_current_auto_reload_consent(account)
    receipt.refresh_from_db()
    assert receipt.payment_method_id == ""


def test_client_cannot_supply_the_payment_binding(ledger, organization, admin_client):
    from agentcc.views.managed_credits import save_settings

    ledger.ManagedCreditAccount.objects.create(
        organization=organization, autoreload_payment_method_id=METHOD
    )
    data = {
        "low_balance_threshold_usd": None,
        "auto_reload": {
            "enabled": True,
            "consent_version": VERSION,
            "threshold_usd": Decimal(10),
            "amount_usd": Decimal(25),
            "payment_method_id": "pm_synthetic_client_injection",
        },
    }
    save_settings(
        ledger,
        organization,
        data,
        Decimal(25),
        False,
        actor=admin_client.handler._force_user,
    )
    receipt = AgentccAutoReloadConsent.objects.get(organization=organization)
    assert getattr(receipt, "payment_method_id", None) == METHOD


def test_missing_saved_method_refuses_enable_and_rolls_back_account(
    ledger, organization, admin_client
):
    response = admin_client.put(
        wallet.BASE + "settings/",
        {
            "low_balance_threshold_usd": None,
            "auto_reload": {
                "enabled": True,
                "consent_version": VERSION,
                "threshold_usd": "10",
                "amount_usd": "25",
            },
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == "auto_reload_payment_method_required"
    assert ledger.get_account(organization) is None
    assert not AgentccAutoReloadConsent.objects.exists()


def test_revoke_is_available_after_payment_method_removed(
    ledger, organization, admin_client
):
    ledger.ManagedCreditAccount.objects.create(
        organization=organization,
        autoreload_enabled=True,
        autoreload_payment_method_id="",
        autoreload_consent_version=VERSION,
        autoreload_consent_at=datetime(2026, 11, 6, tzinfo=UTC),
    )
    with patch("agentcc.views.managed_credits._checkout") as checkout:
        response = admin_client.put(
            wallet.BASE + "settings/",
            {"low_balance_threshold_usd": None, "auto_reload": {"enabled": False}},
            format="json",
        )
    assert response.status_code == 200
    checkout.assert_not_called()
    saved = ledger.get_account(organization)
    assert not saved.autoreload_enabled
    assert saved.autoreload_consent_at is None
    assert not AgentccAutoReloadConsent.objects.exists()
