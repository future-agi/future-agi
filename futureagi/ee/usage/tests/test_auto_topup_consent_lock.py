"""Automatic top-up consent is checked while the account row is locked."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.db import OperationalError, connection, connections, transaction

from accounts.models.organization import Organization
from agentcc.services import auto_reload_consent
from ee.usage.models.managed_credits import ManagedCreditAccount
from ee.usage.services import auto_topup
from ee.usage.tests.ledger_support import migrate_usage_to_latest


@pytest.mark.django_db(transaction=True)
def test_claim_refuses_missing_consent_while_holding_the_account_lock():
    migrate_usage_to_latest()
    org = Organization.objects.create(name="Automatic top-up consent lock")
    account = ManagedCreditAccount.objects.create(
        organization=org,
        autoreload_enabled=True,
        autoreload_threshold_micros=10_000,
        autoreload_amount_micros=5_000,
        autoreload_payment_method_id="pm_synthetic_test",
        autoreload_daily_count_cap=2,
        autoreload_daily_amount_cap_micros=10_000,
    )
    predicate = auto_reload_consent.has_current_auto_reload_consent

    def competing_lock():
        try:
            with transaction.atomic():
                ManagedCreditAccount.objects.select_for_update(nowait=True).get(
                    pk=account.pk
                )
        except OperationalError as exc:
            # PostgreSQL lock_not_available, not a connection or schema error.
            return exc.__cause__.sqlstate
        finally:
            connections.close_all()
        return "acquired"

    def observe(locked_account):
        assert connection.in_atomic_block
        assert locked_account.pk == account.pk
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(competing_lock).result(timeout=5) == "55P03"
        return predicate(locked_account)

    with patch.object(
        auto_reload_consent, "has_current_auto_reload_consent", side_effect=observe
    ) as checked:
        assert auto_topup.claim_auto_topup(org) is None
        checked.assert_called_once()
    account.refresh_from_db()
    assert account.autoreload_in_flight_seq is None
    assert account.autoreload_seq == 0
