"""Seed the wallet buckets of accounts that existed before usage/0031.

Entries written before 0031 carry no funding split, and they are never edited
(append-only). So each account's bucket balances, gross bucket totals and
entry sequence are computed once here by replaying its entries in order with
the legacy rule, which ``services/funding.py`` keeps as ``legacy_allocation``:
a grant or positive adjustment fills promo, a top-up or refund fills purchased
(each repaying debt first), a spend or negative adjustment draws promo, commit,
purchased then debt, and an expiry draws promo with any shortfall as debt.

The rule is copied here, frozen, so later changes to the service never change
what this migration did. Reverse is a no-op (0031's reverse drops the columns).
"""

from django.db import DEFAULT_DB_ALIAS, migrations

from tfc.migration_database import executor_alias_scope


def _seed(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        db = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        Account = apps.get_model("usage", "ManagedCreditAccount")
        Entry = apps.get_model("usage", "ManagedCreditEntry")
        for account in Account.objects.using(db).all().iterator():
            promo = commit = purchased = debt = 0
            promo_total = purchased_total = 0
            seq = 0
            entries = Entry.objects.using(db).filter(organization_id=account.organization_id).order_by(
                "occurred_at", "created_at", "id"
            )
            for kind, amount in entries.values_list("kind", "amount_micros").iterator():
                seq += 1
                if amount == 0:
                    continue
                if amount > 0:
                    repay = min(debt, amount)
                    debt -= repay
                    rest = amount - repay
                    if kind in ("grant", "adjustment"):
                        promo += rest
                        promo_total += amount
                    else:  # topup, refund
                        purchased += rest
                        if kind == "topup":
                            purchased_total += amount
                    continue
                need = -amount
                if kind == "expiry":
                    take = min(promo, need)
                    promo -= take
                    debt += need - take
                    continue
                for name in ("promo", "commit", "purchased"):
                    have = {"promo": promo, "commit": commit, "purchased": purchased}[name]
                    take = min(have, need)
                    need -= take
                    if name == "promo":
                        promo -= take
                    elif name == "commit":
                        commit -= take
                    else:
                        purchased -= take
                debt += need
            Account.objects.using(db).filter(pk=account.pk).update(
                promo_balance_micros=promo,
                commit_balance_micros=commit,
                purchased_balance_micros=purchased,
                debt_micros=debt,
                promo_total_micros=promo_total,
                purchased_total_micros=purchased_total,
                entry_seq=seq,
            )


class Migration(migrations.Migration):
    dependencies = [("usage", "0031_ledger_phase2a")]

    operations = [migrations.RunPython(_seed, migrations.RunPython.noop)]
