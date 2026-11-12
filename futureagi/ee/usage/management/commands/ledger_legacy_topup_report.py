"""Report (never change) pre-2a top-ups the ledger cannot link to their PaymentIntent.

BL-LEDGER-2A-c, ruling 513.3. R's pre-2a ``apply_topup`` credited a Checkout
session under ``stripe:<cs>`` without recording the session's PaymentIntent.
When that PaymentIntent later credited on its own (``stripe_pi:<pi>``) before
any replay of the session linked the two, one payment is two credits. This
residual cannot be closed in the ledger: the session row names no
PaymentIntent, so nothing the ledger holds ties it to the PaymentIntent's
credit; only Stripe knows a session's ``payment_intent``. A later session
replay naming the PaymentIntent raises ``TopupMismatch`` (the caller alerts),
but if none comes the double stays silent. Since 2A-b every session and invoice
credit must name its PaymentIntent, so no new row of this kind can be written;
this command lists the old ones, once, for a person to check in Stripe.

What it lists:

- PAIR: a PI-less session row and a PaymentIntent credit that may be the same
  payment. Without ``--stripe-map`` these are candidates (same org, same
  amount, the PaymentIntent credited at or after the session). With a map of
  session id to PaymentIntent id exported from Stripe outside this command
  (it never calls Stripe), a mapped pair is confirmed, and a credit in another
  org is named as such.
- MAPPED_NOT_CREDITED: a mapped session whose PaymentIntent never credited (no
  double; a replay naming it would link it).
- UNPAIRED: a PI-less session row with no candidate.
- DOUBLE: an R PaymentIntent row usage/0033 left blank because another top-up
  already holds that PaymentIntent (credited twice before the link was known).

It only reads: no entry, account or event row is written, and any correction
(for example an adjustment) is a separate, deliberate step by a person.
"""

import csv
import json

from django.core.management.base import BaseCommand, CommandError

CLOSURE_NOTE = (
    "These pre-2a doubles cannot be closed in the ledger: a PI-less session row "
    "names no PaymentIntent, so only Stripe can say which PaymentIntent paid it. "
    "This report changes nothing."
)


class Command(BaseCommand):
    help = (
        "Read-only, one-time report of pre-2a top-ups (R's apply_topup) credited "
        "without their PaymentIntent whose PaymentIntent may have credited again. "
        "This residual cannot be closed in the ledger; confirm each pair in Stripe. "
        "Never writes and never calls Stripe."
    )

    def add_arguments(self, parser):
        parser.add_argument("--org-id", default="", help="Restrict the report to one org")
        parser.add_argument(
            "--stripe-map",
            default="",
            help=(
                "CSV with session_id and payment_intent_id columns, exported from "
                "Stripe outside this command; mapped pairs are reported as confirmed"
            ),
        )
        parser.add_argument("--json", action="store_true", help="Print one JSON object")

    def handle(self, *args, **options):
        from ee.usage.models.managed_credits import ManagedCreditEntry

        mapping = _read_map(options["stripe_map"]) if options["stripe_map"] else None
        topups = ManagedCreditEntry.objects.filter(kind=ManagedCreditEntry.Kind.TOPUP)
        if options["org_id"]:
            topups = topups.filter(organization_id=options["org_id"])
        report = {
            "read_only": True,
            "closable_in_ledger": False,
            "note": CLOSURE_NOTE,
            "mode": "heuristic" if mapping is None else "stripe_map",
            "pairs": [],
            "mapped_not_credited": [],
            "unpaired": [],
            "doubles": [],
        }
        sessions = topups.filter(
            idempotency_key__startswith="stripe:", stripe_payment_intent_id=""
        ).order_by("created_at", "id")
        for session in sessions.iterator():
            pi_id = mapping.get(session.stripe_ref) if mapping is not None else None
            if pi_id:
                credit = (
                    ManagedCreditEntry.objects.filter(
                        kind=ManagedCreditEntry.Kind.TOPUP, idempotency_key=f"stripe_pi:{pi_id}"
                    ).first()
                    or ManagedCreditEntry.objects.filter(
                        kind=ManagedCreditEntry.Kind.TOPUP, stripe_payment_intent_id=pi_id
                    ).first()
                )
                if credit is None:
                    report["mapped_not_credited"].append(
                        {**_session(session), "payment_intent": pi_id}
                    )
                    continue
                same_org = credit.organization_id == session.organization_id
                status = "confirmed" if same_org else "confirmed_other_org"
                report["pairs"].append(_pair(status, session, credit))
                continue
            candidates = topups.filter(
                organization_id=session.organization_id,
                idempotency_key__startswith="stripe_pi:",
                amount_micros=session.amount_micros,
                created_at__gte=session.created_at,
            ).order_by("created_at", "id")
            found = [_pair("candidate", session, credit) for credit in candidates]
            if found:
                report["pairs"].extend(found)
            else:
                report["unpaired"].append(_session(session))
        blank_pis = topups.filter(
            idempotency_key__startswith="stripe_pi:", stripe_payment_intent_id=""
        ).order_by("created_at", "id")
        for row in blank_pis.iterator():
            holder = (
                ManagedCreditEntry.objects.filter(
                    kind=ManagedCreditEntry.Kind.TOPUP, stripe_payment_intent_id=row.stripe_ref
                )
                .exclude(pk=row.pk)
                .first()
            )
            if holder is not None:
                report["doubles"].append(_double(row, holder))
        report["summary"] = _summary(report, sessions.count())
        if options["json"]:
            self.stdout.write(json.dumps(report, sort_keys=True))
            return
        self._write_text(report)

    def _write_text(self, report):
        out = self.stdout.write
        out(f"Legacy top-up report ({report['mode']}). {report['note']}")
        for p in report["pairs"]:
            out(
                f"PAIR {p['status']} org={p['org_id']} session_entry={p['session_entry']} "
                f"session={p['session']} amount_micros={p['amount_micros']} "
                f"session_credited_at={p['session_credited_at']} "
                f"payment_intent_entry={p['payment_intent_entry']} "
                f"payment_intent={p['payment_intent']} payment_intent_org={p['payment_intent_org_id']} "
                f"payment_intent_credited_at={p['payment_intent_credited_at']}"
            )
        for m in report["mapped_not_credited"]:
            out(
                f"MAPPED_NOT_CREDITED org={m['org_id']} session_entry={m['session_entry']} "
                f"session={m['session']} payment_intent={m['payment_intent']}"
            )
        for u in report["unpaired"]:
            out(
                f"UNPAIRED org={u['org_id']} session_entry={u['session_entry']} "
                f"session={u['session']} amount_micros={u['amount_micros']}"
            )
        for d in report["doubles"]:
            out(
                f"DOUBLE org={d['org_id']} payment_intent_entry={d['payment_intent_entry']} "
                f"payment_intent={d['payment_intent']} amount_micros={d['amount_micros']} "
                f"held_by_entry={d['held_by_entry']} held_by_key={d['held_by_key']} "
                f"held_by_org={d['held_by_org_id']}"
            )
        s = report["summary"]
        out(
            "summary: "
            + " ".join(f"{name}={s[name]}" for name in sorted(s))
        )


def _read_map(path: str) -> dict[str, str]:
    from ee.usage.services.managed_credits import _CLIENT_SECRET, _PAYMENT_INTENT_ID

    try:
        with open(path, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise CommandError(f"cannot read --stripe-map: {exc.strerror}") from exc
    mapping = {}
    for n, row in enumerate(rows, start=2):
        session_id = (row.get("session_id") or "").strip()
        pi_id = (row.get("payment_intent_id") or "").strip()
        if not session_id or not pi_id:
            raise CommandError(f"--stripe-map line {n}: needs session_id and payment_intent_id")
        if (
            len(pi_id) > 255
            or not _PAYMENT_INTENT_ID.fullmatch(pi_id)
            or _CLIENT_SECRET.search(pi_id)
        ):
            # Not echoed: it may be a client secret.
            raise CommandError(f"--stripe-map line {n}: payment_intent_id is not a PaymentIntent id")
        mapping[session_id] = pi_id
    return mapping


def _when(entry) -> str:
    return entry.created_at.isoformat() if entry.created_at else ""


def _session(entry) -> dict:
    return {
        "org_id": str(entry.organization_id),
        "session_entry": str(entry.pk),
        "session": entry.stripe_ref,
        "amount_micros": entry.amount_micros,
        "session_credited_at": _when(entry),
    }


def _pair(status: str, session, credit) -> dict:
    return {
        "status": status,
        **_session(session),
        "payment_intent_entry": str(credit.pk),
        "payment_intent": credit.stripe_payment_intent_id or credit.stripe_ref,
        "payment_intent_org_id": str(credit.organization_id),
        "payment_intent_amount_micros": credit.amount_micros,
        "payment_intent_credited_at": _when(credit),
    }


def _double(row, holder) -> dict:
    return {
        "org_id": str(row.organization_id),
        "payment_intent_entry": str(row.pk),
        "payment_intent": row.stripe_ref,
        "amount_micros": row.amount_micros,
        "held_by_entry": str(holder.pk),
        "held_by_key": holder.idempotency_key,
        "held_by_org_id": str(holder.organization_id),
    }


def _summary(report: dict, sessions: int) -> dict:
    paired = {p["session_entry"]: p["amount_micros"] for p in report["pairs"]}
    statuses = [p["status"] for p in report["pairs"]]
    return {
        "sessions_without_payment_intent": sessions,
        "candidate_pairs": statuses.count("candidate"),
        "confirmed_pairs": len(statuses) - statuses.count("candidate"),
        "mapped_not_credited": len(report["mapped_not_credited"]),
        "unpaired": len(report["unpaired"]),
        "doubles": len(report["doubles"]),
        "micros_at_risk": sum(paired.values()) + sum(d["amount_micros"] for d in report["doubles"]),
    }
