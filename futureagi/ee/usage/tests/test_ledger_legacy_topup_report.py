"""BL-LEDGER-2A-c: the one-time report of pre-2a top-up pairs.

R's pre-2a apply_topup credited a Checkout session with no PaymentIntent. If
that PaymentIntent then credited on its own before any session replay linked
the two, one payment is two credits, and the ledger cannot close it: the
session row names no PaymentIntent. ``manage.py ledger_legacy_topup_report``
lists such pairs (candidates by org, amount and order; confirmed with a
session-to-PaymentIntent map exported from Stripe elsewhere) and the R
PaymentIntent rows usage/0033 had to leave blank. It never writes, and these
rows prove that with the SQL it ran.
"""

import json
import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models.organization import Organization
from ee.usage.models.managed_credits import ManagedCreditAccount, ManagedCreditEntry
from ee.usage.services import managed_credits as mc
from ee.usage.services.funding import Bucket

Kind = ManagedCreditEntry.Kind
pytestmark = pytest.mark.integration
COMMAND = "ledger_legacy_topup_report"
WRITES = ("insert ", "update ", "delete ", "truncate ", "alter ", "create ", "drop ")


def _org(tag):
    return Organization.objects.create(name=f"c4 {tag} {uuid.uuid4().hex[:6]}")


def _legacy_session(org, cs, amount):
    """The row R's pre-2a apply_topup wrote: key stripe:<cs>, no PaymentIntent."""
    entry, created = mc._write(
        org, Kind.TOPUP, amount, mc._system_key("stripe", cs),
        bucket=Bucket.PURCHASED, stripe_ref=cs,
    )
    assert created and entry.stripe_payment_intent_id == ""
    return entry


def _r_pi_row(org, pi, amount):
    """The row R's apply_topup_pi wrote, as usage/0033 leaves a held one."""
    entry, created = mc._write(
        org, Kind.TOPUP, amount, mc._system_key("stripe_pi", pi),
        bucket=Bucket.PURCHASED, stripe_ref=pi,
    )
    assert created and entry.stripe_payment_intent_id == ""
    return entry


def _pi(org, pi, amount):
    return mc.fulfil_topup(
        org, payment=mc.TopupPayment(mc.TopupSource.PAYMENT_INTENT, pi, amount, mc.StripeMode.TEST)
    ).entry


def _cs(org, cs, amount, pi):
    return mc.fulfil_topup(
        org,
        payment=mc.TopupPayment(
            mc.TopupSource.CHECKOUT_SESSION, cs, amount, mc.StripeMode.TEST, payment_intent_id=pi
        ),
    ).entry


def _snapshot():
    return (
        sorted(ManagedCreditEntry.objects.values_list(
            "pk", "kind", "amount_micros", "idempotency_key", "stripe_payment_intent_id"
        )),
        sorted(ManagedCreditAccount.objects.values_list(
            "organization_id", "version", "entry_seq", "purchased_balance_micros", "debt_micros"
        )),
    )


def _run(*args):
    out = StringIO()
    before = _snapshot()
    with CaptureQueriesContext(connection) as queries:
        call_command(COMMAND, *args, stdout=out)
    statements = [q["sql"].lstrip().lower() for q in queries.captured_queries]
    assert statements, "the report ran no query"
    assert [s for s in statements if s.startswith(WRITES) or " for update" in s] == []
    assert _snapshot() == before
    return out.getvalue()


@pytest.fixture
def ledger():
    a, b = _org("a"), _org("b")
    rows = {
        "pi_before": _pi(a, "pi_3C4zero", 5_000_000),  # credited before the session
        "cs_paired": _legacy_session(a, "cs_test_c4one", 5_000_000),
        "pi_paired": _pi(a, "pi_3C4one", 5_000_000),
        "cs_alone": _legacy_session(a, "cs_test_c4two", 7_000_000),
        "cs_other_org": _legacy_session(b, "cs_test_c4three", 9_000_000),
        "pi_other_org": _pi(a, "pi_3C4three", 9_000_000),
        "cs_linked": _cs(a, "cs_test_c4four", 3_000_000, "pi_3C4four"),
        # Credited twice before 0033: the session holds the PI, R's PI row blank.
        "cs_holder": _cs(a, "cs_test_c4nine", 2_000_000, "pi_3C4nine"),
        "r_pi_held": _r_pi_row(a, "pi_3C4nine", 2_000_000),
    }
    return a, b, rows


@pytest.mark.django_db
def test_the_report_lists_candidate_pairs_doubles_and_unpaired_sessions(ledger):
    a, b, rows = ledger

    text = _run()

    pair_lines = [line for line in text.splitlines() if line.startswith("PAIR ")]
    assert len(pair_lines) == 1, text
    assert "candidate" in pair_lines[0]
    assert "cs_test_c4one" in pair_lines[0] and "pi_3C4one" in pair_lines[0]
    assert str(rows["cs_paired"].pk) in pair_lines[0] and str(rows["pi_paired"].pk) in pair_lines[0]
    assert "pi_3C4zero" not in text  # credited before the session: not its PaymentIntent
    unpaired = [line for line in text.splitlines() if line.startswith("UNPAIRED ")]
    assert sorted("cs_test_c4two" in u or "cs_test_c4three" in u for u in unpaired) == [True, True]
    doubles = [line for line in text.splitlines() if line.startswith("DOUBLE ")]
    assert len(doubles) == 1 and "pi_3C4nine" in doubles[0]
    assert str(rows["r_pi_held"].pk) in doubles[0] and str(rows["cs_holder"].pk) in doubles[0]
    assert "cs_test_c4four" not in text  # linked: nothing to report
    assert "cannot be closed in the ledger" in text
    assert "sessions_without_payment_intent=3" in text
    assert "candidate_pairs=1" in text and "confirmed_pairs=0" in text and "doubles=1" in text


@pytest.mark.django_db
def test_a_stripe_map_confirms_pairs_and_names_another_orgs_credit(ledger, tmp_path):
    a, b, rows = ledger
    mapping = tmp_path / "sessions.csv"
    mapping.write_text(
        "session_id,payment_intent_id\n"
        "cs_test_c4one,pi_3C4one\n"
        "cs_test_c4three,pi_3C4three\n"
        "cs_test_c4two,pi_3C4never\n"
    )

    text = _run("--stripe-map", str(mapping))

    pairs = {line.split()[1]: line for line in text.splitlines() if line.startswith("PAIR ")}
    assert set(pairs) == {"confirmed", "confirmed_other_org"}, text
    assert "cs_test_c4one" in pairs["confirmed"] and "pi_3C4one" in pairs["confirmed"]
    assert "cs_test_c4three" in pairs["confirmed_other_org"]
    assert str(a.pk) in pairs["confirmed_other_org"] and str(b.pk) in pairs["confirmed_other_org"]
    assert any(
        line.startswith("MAPPED_NOT_CREDITED ") and "cs_test_c4two" in line
        for line in text.splitlines()
    ), text
    assert "confirmed_pairs=2" in text and "candidate_pairs=0" in text


@pytest.mark.django_db
def test_the_report_as_json_and_for_one_org(ledger):
    a, b, rows = ledger

    body = json.loads(_run("--json"))
    assert body["read_only"] is True and body["closable_in_ledger"] is False
    assert body["mode"] == "heuristic"
    assert [(p["status"], p["session_entry"], p["payment_intent_entry"]) for p in body["pairs"]] == [
        ("candidate", str(rows["cs_paired"].pk), str(rows["pi_paired"].pk))
    ]
    assert [d["payment_intent_entry"] for d in body["doubles"]] == [str(rows["r_pi_held"].pk)]
    assert body["summary"]["micros_at_risk"] == 5_000_000 + 2_000_000

    only_b = json.loads(_run("--json", "--org-id", str(b.pk)))
    assert only_b["pairs"] == [] and only_b["doubles"] == []
    assert [u["session_entry"] for u in only_b["unpaired"]] == [str(rows["cs_other_org"].pk)]


@pytest.mark.django_db
def test_a_stripe_map_value_that_is_not_a_payment_intent_id_is_refused_unechoed(ledger, tmp_path):
    from django.core.management.base import CommandError

    mapping = tmp_path / "bad.csv"
    mapping.write_text("session_id,payment_intent_id\ncs_test_c4one,pi_3C4one_secret_C4abc\n")
    with pytest.raises(CommandError) as info:
        call_command(COMMAND, "--stripe-map", str(mapping), stdout=StringIO())
    assert "C4abc" not in str(info.value)
