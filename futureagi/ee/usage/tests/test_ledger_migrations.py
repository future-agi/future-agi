"""BL-LEDGER usage/0031 and 0032 on a ledger that already holds R's rows.

0031 replaces the entry sign constraint (gap G1): every row the 0030
constraint admitted must still be admitted. 0032 seeds each existing
account's buckets by replaying its rows, and I5 then holds on the migrated
ledger.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ee.usage.services import reconcile

BEFORE = [("usage", "0030_managed_credit_ledger")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_existing_rows_survive_0031_and_seed_buckets_in_0032():
    apps = _migrate(BEFORE)
    Organization = apps.get_model("accounts", "Organization")
    Account = apps.get_model("usage", "ManagedCreditAccount")
    Entry = apps.get_model("usage", "ManagedCreditEntry")
    org = Organization.objects.create(name="Pre-ledger org")
    t0 = datetime(2026, 10, 20, tzinfo=UTC)
    rows = [
        ("grant", 3_000),
        ("topup", 10_000),
        ("spend", -5_000),
        ("spend", -9_000),
        ("refund", 2_000),
        ("adjustment", -500),
        ("adjustment", 400),
        ("expiry", -1_000),
    ]
    for n, (kind, amount) in enumerate(rows):
        Entry.objects.create(
            id=uuid.uuid4(), organization=org, kind=kind, amount_micros=amount,
            idempotency_key=f"legacy:{n}", occurred_at=t0 + timedelta(minutes=n),
        )
    credits = sum(a for k, a in rows if k != "spend")
    spent = -sum(a for k, a in rows if k == "spend")
    Account.objects.create(
        organization=org, granted_total_micros=credits, settled_spend_micros=spent, version=6
    )

    try:
        _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes("usage"))
        from accounts.models.organization import Organization as LiveOrg
        from ee.usage.models.managed_credits import ManagedCreditAccount

        live = LiveOrg.objects.get(pk=org.pk)
        account = ManagedCreditAccount.objects.get(organization=live)
        # grant 3000 promo; topup 10000 purchased; spend 5000: promo 3000 + purchased 2000;
        # spend 9000: purchased 8000 + debt 1000; refund 2000: repays 1000 debt, 1000 purchased;
        # adjust -500: purchased; adjust +400: promo; expiry 1000: promo 400 + debt 600.
        assert (
            account.promo_balance_micros,
            account.commit_balance_micros,
            account.purchased_balance_micros,
            account.debt_micros,
        ) == (0, 0, 500, 600)
        assert (account.promo_total_micros, account.purchased_total_micros, account.entry_seq) == (
            3_400,
            10_000,
            len(rows),
        )
        assert account.promo_balance_micros + account.purchased_balance_micros - account.debt_micros == (
            credits - spent
        )
        result = reconcile.check_i5(live)
        assert result.ok, result.failures
    finally:
        _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes("usage"))
