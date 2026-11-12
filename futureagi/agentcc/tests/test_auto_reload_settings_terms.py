"""API clients can obtain the exact authorization they must show for auto-reload."""

import pytest

from agentcc.auto_reload_terms import (
    AUTO_RELOAD_CONSENT_TERMS,
    AUTO_RELOAD_CONSENT_VERSION,
)
from agentcc.serializers.managed_credits import ManagedCreditAutoReloadSerializer
from agentcc.tests import test_managed_credits_api as wallet

# The terms of a build that runs auto-reload; this release refuses turning it
# on (test_auto_reload_unavailable).
pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("auto_reload_available")]
auto_reload_available = wallet.auto_reload_available
admin_client = wallet.admin_client
make_client = wallet.make_client
ledger = wallet.ledger
gateway = wallet.gateway

URL = f"{wallet.BASE}settings/"


def test_settings_get_publishes_exact_current_terms(ledger, organization, admin_client):
    response = admin_client.get(URL)
    assert response.status_code == 200, response.content
    auto_reload = response.json()["result"]["auto_reload"]
    assert auto_reload["consent_version"] == AUTO_RELOAD_CONSENT_VERSION
    assert auto_reload["consent_terms"] == AUTO_RELOAD_CONSENT_TERMS
    assert ledger.get_account(organization) is None


def test_consent_response_fields_are_read_only():
    fields = ManagedCreditAutoReloadSerializer().fields
    assert fields["consent_version"].read_only is True
    assert fields["consent_terms"].read_only is True


def test_served_version_enables_reload_and_summary_publishes_terms(
    ledger, organization, admin_client, gateway
):
    wallet._opt_in(organization)
    response = admin_client.get(URL)
    assert response.status_code == 200, response.content
    version = response.json()["result"]["auto_reload"]["consent_version"]
    # c2.6 binds explicit consent to the server-persisted payment method.
    ledger.ManagedCreditAccount.objects.create(
        organization=organization,
        autoreload_payment_method_id="pm_synthetic_settings_terms",
    )
    saved = admin_client.put(
        URL,
        {
            "low_balance_threshold_usd": "5",
            "auto_reload": {
                "enabled": True,
                "threshold_usd": "10",
                "amount_usd": "25",
                "consent_version": version,
            },
        },
        format="json",
    )
    assert saved.status_code == 200, saved.content
    assert ledger.get_account(organization).autoreload_enabled is True
    summary = admin_client.get(wallet.BASE)
    assert summary.status_code == 200, summary.content
    for result in (saved.json()["result"], summary.json()["result"]):
        assert result["auto_reload"]["consent_version"] == AUTO_RELOAD_CONSENT_VERSION
        assert result["auto_reload"]["consent_terms"] == AUTO_RELOAD_CONSENT_TERMS


@pytest.mark.parametrize(
    "version,code",
    [
        (None, "auto_reload_consent_required"),
        ("old-terms", "auto_reload_consent_outdated"),
    ],
    ids=["missing", "stale"],
)
def test_publishing_terms_does_not_imply_consent(
    ledger, organization, admin_client, version, code
):
    wallet._opt_in(organization)
    assert admin_client.get(URL).status_code == 200
    auto_reload = {"enabled": True, "threshold_usd": "10", "amount_usd": "25"}
    if version is not None:
        auto_reload["consent_version"] = version
    response = admin_client.put(
        URL,
        {"low_balance_threshold_usd": "5", "auto_reload": auto_reload},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["code"] == code
    assert ledger.get_account(organization) is None
