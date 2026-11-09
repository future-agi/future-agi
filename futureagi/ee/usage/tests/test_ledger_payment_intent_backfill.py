"""BL-LEDGER-2A-c (ruling 513.3): usage/0033 records the PaymentIntent on R's rows.

R's apply_topup_pi credited a PaymentIntent under ``stripe_pi:<pi>`` with
``stripe_ref`` <pi>, and usage/0031 added ``stripe_payment_intent_id`` with no
backfill. A session or invoice naming that PaymentIntent then credited the same
money again (the 2A-b verify's adv6 rows). 0033 fills the column from
``stripe_ref`` where the key starts ``stripe_pi:`` and the column is blank, and
its reverse is a no-op. The rows are seeded at 0032 with the historical models,
as R's code left them.
"""

import uuid

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ee.usage.services import managed_credits as mc

BEFORE = [("usage", "0032_ledger_opening_buckets")]
AFTER = [("usage", "0033_backfill_topup_payment_intent")]
AMOUNT = 5_000_000


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def _leaf():
    _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes("usage"))


@pytest.mark.django_db(transaction=True)
def test_0033_fills_r_payment_intent_rows_and_its_reverse_is_a_no_op():
    apps = _migrate(BEFORE)
    Organization = apps.get_model("accounts", "Organization")
    Entry = apps.get_model("usage", "ManagedCreditEntry")
    org = Organization.objects.create(name=f"m33 {uuid.uuid4().hex[:6]}")
    other = Organization.objects.create(name=f"m33 other {uuid.uuid4().hex[:6]}")

    def row(owner, key, ref, pi=""):
        return Entry.objects.create(
            id=uuid.uuid4(), organization=owner, kind="topup", amount_micros=AMOUNT,
            idempotency_key=key, stripe_ref=ref, stripe_payment_intent_id=pi,
        ).pk

    rows = {
        # R's apply_topup_pi rows: filled.
        "r_pi": row(org, "stripe_pi:pi_3M33a", "pi_3M33a"),
        "r_pi_other_org": row(other, "stripe_pi:pi_3M33h", "pi_3M33h"),
        # Already recorded: unchanged.
        "recorded": row(org, "stripe_pi:pi_3M33b", "pi_3M33b", pi="pi_3M33b"),
        # A session row R's apply_topup wrote: not a PaymentIntent row.
        "r_session": row(org, "stripe:cs_test_m33c", "cs_test_m33c"),
        # Credited twice before the link was known: the session holds the PI,
        # so the fill would break the partial unique. Left blank.
        "held_by_session": row(org, "stripe_pi:pi_3M33d", "pi_3M33d"),
        "the_session": row(org, "stripe:cs_test_m33d", "cs_test_m33d", pi="pi_3M33d"),
        # Not a PaymentIntent id, or not the key's own id: left blank.
        "client_secret": row(org, "stripe_pi:pi_3M33e_secret_x", "pi_3M33e_secret_x"),
        "not_an_id": row(org, "stripe_pi:junk-33", "junk-33"),
        "ref_differs": row(org, "stripe_pi:pi_3M33f", "pi_3M33g"),
    }

    try:
        apps = _migrate(AFTER)
        Entry = apps.get_model("usage", "ManagedCreditEntry")
        recorded = dict(
            Entry.objects.filter(pk__in=rows.values()).values_list("pk", "stripe_payment_intent_id")
        )
        assert {name: recorded[pk] for name, pk in rows.items()} == {
            "r_pi": "pi_3M33a",
            "r_pi_other_org": "pi_3M33h",
            "recorded": "pi_3M33b",
            "r_session": "",
            "held_by_session": "",
            "the_session": "pi_3M33d",
            "client_secret": "",
            "not_an_id": "",
            "ref_differs": "",
        }
        # Only that column moved: amounts, keys and refs are as seeded.
        assert sorted(Entry.objects.filter(pk__in=rows.values()).values_list("amount_micros", flat=True)) == (
            [AMOUNT] * len(rows)
        )

        _leaf()
        from accounts.models.organization import Organization as LiveOrg
        from ee.usage.models.managed_credits import ManagedCreditEntry

        live, live_other = LiveOrg.objects.get(pk=org.pk), LiveOrg.objects.get(pk=other.pk)
        # adv6: the session or invoice naming an R PaymentIntent now finds it.
        by_session = mc.fulfil_topup(
            live,
            payment=mc.TopupPayment(
                mc.TopupSource.CHECKOUT_SESSION, "cs_test_m33a", AMOUNT, mc.StripeMode.TEST,
                payment_intent_id="pi_3M33a",
            ),
        )
        by_invoice = mc.fulfil_topup(
            live_other,
            payment=mc.TopupPayment(
                mc.TopupSource.INVOICE, "in_1M33h", AMOUNT, mc.StripeMode.TEST,
                payment_intent_id="pi_3M33h",
            ),
        )
        assert (by_session.entry.pk, by_session.created) == (rows["r_pi"], False)
        assert (by_invoice.entry.pk, by_invoice.created) == (rows["r_pi_other_org"], False)
        assert ManagedCreditEntry.objects.filter(kind="topup").count() == len(rows)

        # Reverse: a no-op that keeps the recorded ids (removing them would
        # reopen the double credit).
        apps = _migrate(BEFORE)
        Entry = apps.get_model("usage", "ManagedCreditEntry")
        assert Entry.objects.get(pk=rows["r_pi"]).stripe_payment_intent_id == "pi_3M33a"
        assert Entry.objects.get(pk=rows["held_by_session"]).stripe_payment_intent_id == ""
    finally:
        _leaf()


@pytest.mark.django_db(transaction=True)
def test_0033_on_a_ledger_with_no_r_rows_changes_nothing():
    apps = _migrate(BEFORE)
    Organization = apps.get_model("accounts", "Organization")
    Entry = apps.get_model("usage", "ManagedCreditEntry")
    org = Organization.objects.create(name=f"m33 none {uuid.uuid4().hex[:6]}")
    pk = Entry.objects.create(
        id=uuid.uuid4(), organization=org, kind="topup", amount_micros=AMOUNT,
        idempotency_key="stripe:cs_test_m33n", stripe_ref="cs_test_m33n",
        stripe_payment_intent_id="pi_3M33n",
    ).pk
    try:
        apps = _migrate(AFTER)
        Entry = apps.get_model("usage", "ManagedCreditEntry")
        assert list(Entry.objects.filter(organization_id=org.pk).values_list("pk", "stripe_payment_intent_id")) == [
            (pk, "pi_3M33n")
        ]
    finally:
        _leaf()
