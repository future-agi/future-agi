"""Consent regressions, written before implementation; VM execution is owed.

No payment network: the main-owned claim seam feeds a mock charge requester.
The main-integration tests are deliberately not xfailed: they must block release
until ee.usage.services.auto_topup.claim_auto_topup calls the consent guard.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.db import DatabaseError

from agentcc.tests import test_managed_credits_api as wallet

# The consent rules of a build that runs auto-reload; this release refuses
# turning it on (test_auto_reload_unavailable).
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("auto_reload_available")]
auto_reload_available = wallet.auto_reload_available
admin_client = wallet.admin_client
make_client = wallet.make_client
member_client = wallet.member_client
other_org = wallet.other_org
ledger = wallet.ledger

# Pin the published client value, rather than deriving every test input from it.
VERSION = "auto-reload-v1"
URL = f"{wallet.BASE}settings/"


@pytest.fixture
def saved_payment_method(ledger, organization):
    """A server-persisted method must precede the user's acknowledgement."""
    return ledger.ManagedCreditAccount.objects.create(
        organization=organization,
        autoreload_payment_method_id="pm_synthetic_test",
        autoreload_daily_count_cap=2,
        autoreload_daily_amount_cap_micros=50_000_000,
    )


def body(**auto):
    return {
        "low_balance_threshold_usd": "5",
        "auto_reload": {
            "enabled": True,
            "threshold_usd": "10",
            "amount_usd": "25",
            "consent_version": VERSION,
            **auto,
        },
    }


def receipts():
    from agentcc.models.auto_reload_consent import AgentccAutoReloadConsent

    return AgentccAutoReloadConsent.objects


@pytest.mark.parametrize(
    "version,code",
    [
        (None, "auto_reload_consent_required"),
        ("", "auto_reload_consent_required"),
        ("old-terms", "auto_reload_consent_outdated"),
        (f" {VERSION} ", "auto_reload_consent_outdated"),
    ],
)
def test_enable_refuses_missing_or_stale_consent(
    ledger, organization, admin_client, version, code
):
    payload = body(consent_version=version)
    if version is None:
        del payload["auto_reload"]["consent_version"]
    response = admin_client.put(URL, payload, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == code
    assert ledger.get_account(organization) is None


def test_enable_records_version_server_time_actor_and_accepted_amounts(
    ledger, organization, admin_client, saved_payment_method
):
    now = datetime(2026, 10, 28, tzinfo=UTC)
    # The client's actor and timestamp cannot override the authenticated actor.
    payload = body(actor_id="not-the-user", accepted_at="2000-01-01T00:00:00Z")
    with patch("agentcc.views.managed_credits.timezone.now", return_value=now):
        response = admin_client.put(URL, payload, format="json")
    assert response.status_code == 200, response.content
    receipt = receipts().get(organization=organization)
    account = ledger.get_account(organization)
    assert receipt.consent_version == VERSION == account.autoreload_consent_version
    assert receipt.accepted_at == now == account.autoreload_consent_at
    assert receipt.actor_id == admin_client.handler._force_user.pk
    assert receipt.actor_identifier == receipt.actor_id
    assert receipt.threshold_micros == account.autoreload_threshold_micros == 10_000_000
    assert receipt.amount_micros == account.autoreload_amount_micros == 25_000_000
    assert ledger.ledger_entries(organization).count() == 0


@pytest.mark.parametrize("change", [{}, {"threshold_usd": "12"}, {"amount_usd": "50"}])
def test_every_enabled_save_requires_fresh_acknowledgement(
    ledger, organization, admin_client, change, saved_payment_method
):
    assert admin_client.put(URL, body(), format="json").status_code == 200
    accounts = ledger.ManagedCreditAccount.objects.filter(organization=organization)
    before = list(accounts.values())
    payload = body(**change)
    del payload["auto_reload"]["consent_version"]
    response = admin_client.put(URL, payload, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "auto_reload_consent_required"
    assert list(accounts.values()) == before
    assert receipts().filter(organization=organization).count() == 1


def test_reconfirmation_keeps_previous_receipt(
    ledger, organization, admin_client, saved_payment_method
):
    start = datetime(2026, 10, 28, tzinfo=UTC)
    with patch("agentcc.views.managed_credits.timezone.now", return_value=start):
        assert admin_client.put(URL, body(), format="json").status_code == 200
    later = start + timedelta(seconds=1)
    with patch("agentcc.views.managed_credits.timezone.now", return_value=later):
        response = admin_client.put(URL, body(amount_usd="50"), format="json")
        assert response.status_code == 200
    rows = list(receipts().filter(organization=organization).order_by("accepted_at"))
    assert [(r.accepted_at, r.amount_micros) for r in rows] == [
        (start, 25_000_000),
        (later, 50_000_000),
    ]
    assert ledger.get_account(organization).autoreload_consent_at == later


def test_disable_needs_no_consent_and_reenable_cannot_reuse_it(
    ledger, organization, admin_client, saved_payment_method
):
    assert admin_client.put(URL, body(), format="json").status_code == 200
    payload = {"low_balance_threshold_usd": None, "auto_reload": {"enabled": False}}
    assert admin_client.put(URL, payload, format="json").status_code == 200
    account = ledger.get_account(organization)
    assert account.autoreload_enabled is False
    assert account.autoreload_consent_version == ""
    assert account.autoreload_consent_at is None
    assert receipts().filter(organization=organization).count() == 1
    payload = body()
    del payload["auto_reload"]["consent_version"]
    assert admin_client.put(URL, payload, format="json").status_code == 400
    assert ledger.get_account(organization).autoreload_enabled is False


def test_receipt_failure_rolls_back_settings(
    ledger, organization, admin_client, saved_payment_method
):
    manager = receipts()
    before = list(
        ledger.ManagedCreditAccount.objects.filter(organization=organization).values()
    )
    with patch.object(
        manager, "create", side_effect=DatabaseError("synthetic failure")
    ):
        response = admin_client.put(URL, body(), format="json")
    assert response.status_code == 503
    assert (
        list(
            ledger.ManagedCreditAccount.objects.filter(
                organization=organization
            ).values()
        )
        == before
    )
    assert not receipts().filter(organization=organization).exists()


def test_member_cannot_record_consent(ledger, organization, member_client):
    assert member_client.put(URL, body(), format="json").status_code == 403
    assert not receipts().filter(organization=organization).exists()
    assert ledger.get_account(organization) is None


def test_direct_settings_call_cannot_bypass_consent(ledger, organization):
    from agentcc.views.managed_credits import ManagedCreditsApiError, save_settings

    data = {
        "low_balance_threshold_usd": None,
        "auto_reload": {"enabled": True, "amount_usd": Decimal(25)},
    }
    with pytest.raises(ManagedCreditsApiError) as refusal:
        save_settings(ledger, organization, data, Decimal(25), False, actor=None)
    assert refusal.value.code == "auto_reload_consent_required"
    assert ledger.get_account(organization) is None
    data["auto_reload"]["consent_version"] = VERSION
    with pytest.raises(ManagedCreditsApiError) as refusal:
        save_settings(ledger, organization, data, Decimal(25), False, actor=None)
    assert refusal.value.code == "auto_reload_consent_actor_required"


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "stale",
        "no_time",
        "different_amount",
        "different_threshold",
        "disabled",
        "foreign_org",
        "no_actor",
    ],
)
def test_guard_requires_matching_current_receipt(
    ledger, organization, other_org, admin_client, invalid, saved_payment_method
):
    from agentcc.services.auto_reload_consent import has_current_auto_reload_consent

    assert admin_client.put(URL, body(), format="json").status_code == 200
    account = ledger.get_account(organization)
    assert has_current_auto_reload_consent(account)
    if invalid == "missing":
        receipts().filter(organization=organization).delete()
    elif invalid == "stale":
        account.autoreload_consent_version = "old-terms"
    elif invalid == "no_time":
        account.autoreload_consent_at = None
    elif invalid == "different_amount":
        account.autoreload_amount_micros += 1
    elif invalid == "different_threshold":
        account.autoreload_threshold_micros += 1
    elif invalid == "disabled":
        account.autoreload_enabled = False
    elif invalid == "no_actor":
        receipts().filter(organization=organization).update(actor=None)
    else:
        receipts().filter(organization=organization).update(organization=other_org)
    assert not has_current_auto_reload_consent(account)


@pytest.mark.parametrize("consent", ["absent", "version_only", "legacy", "stale"])
def test_main_claim_without_current_receipt_never_requests_a_charge(
    ledger, organization, consent
):
    """Integration gate owed to main: do not mock or bypass the real claim seam."""
    account = ledger.ManagedCreditAccount.objects.create(
        organization=organization,
        autoreload_enabled=True,
        autoreload_threshold_micros=10_000_000,
        autoreload_amount_micros=25_000_000,
        autoreload_payment_method_id="pm_synthetic_test",
        autoreload_daily_count_cap=2,
        autoreload_daily_amount_cap_micros=50_000_000,
        autoreload_consent_version=(
            "old-terms"
            if consent == "stale"
            else VERSION
            if consent != "absent"
            else ""
        ),
        autoreload_consent_at=(
            datetime(2026, 10, 28, tzinfo=UTC)
            if consent in ("legacy", "stale")
            else None
        ),
    )
    request_charge = MagicMock()
    claim = ledger.claim_auto_topup(organization)
    if claim is not None:
        request_charge(claim)
    request_charge.assert_not_called()
    account.refresh_from_db()
    assert account.autoreload_in_flight_seq is None
    assert account.autoreload_seq == 0


def test_main_claim_with_current_consent_preserves_charge_path(
    ledger, organization, admin_client, saved_payment_method
):
    assert admin_client.put(URL, body(), format="json").status_code == 200
    ledger.ManagedCreditAccount.objects.filter(organization=organization).update(
        autoreload_payment_method_id="pm_synthetic_test",
        autoreload_daily_count_cap=2,
        autoreload_daily_amount_cap_micros=50_000_000,
    )
    claim = ledger.claim_auto_topup(organization)
    assert claim is not None
    assert claim.amount_micros == 25_000_000
    assert ledger.claim_auto_topup(organization) is None
