"""Grant, adjust or show an org's managed-model credit.

Every change is an entry in the managed credit ledger (the money system of
record). When the entry commits, the gateway's copy of the granted total
follows through a versioned push. A failed push is logged
(``managed_credits_push_failed``) and not retried. The next grant or
adjustment for the org sends the full
total again, and ``--show`` prints the version the gateway acked:

    python manage.py agentcc_managed_credits <org_id> --show
    python manage.py agentcc_managed_credits <org_id> --grant 25 --note "pilot credit"
    python manage.py agentcc_managed_credits <org_id> --adjust -2.50 --reason "refund"
"""

import importlib
import uuid
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Organization
from agentcc.services.gateway_admin.credits import get_credits
from agentcc.services.gateway_client import GatewayClientError

CREDITS_MODULE = "ee.usage.services.managed_credits"
MICROS_PER_USD = Decimal(1_000_000)
RECENT_ENTRIES = 20


def load_ledger():
    """The ledger service, or ``CommandError`` when this build has none (OSS)."""
    try:
        return importlib.import_module(CREDITS_MODULE)
    except ImportError as exc:
        raise CommandError(
            "billing_unavailable: the managed credit ledger is not installed"
        ) from exc


def load_org(org_id):
    try:
        org = Organization.objects.filter(id=org_id).first()
    except ValidationError:  # not a UUID
        org = None
    if org is None:
        raise CommandError(f"No organization {org_id}")
    return org


def format_usd(micros):
    sign = "-" if micros < 0 else ""
    return f"{sign}${abs(Decimal(micros)) / MICROS_PER_USD:.6f}"


def _micros(raw, flag):
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise CommandError(f"{flag} takes a USD amount, got {raw!r}") from exc
    if not amount.is_finite():
        raise CommandError(f"{flag} takes a finite USD amount, got {raw!r}")
    return int((amount * MICROS_PER_USD).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _text(value, flag, action):
    if not value or not value.strip():
        raise CommandError(f"{action} needs {flag} TEXT for the ledger entry")
    return value.strip()


class Command(BaseCommand):
    help = "Grant, adjust or show an org's managed-model credit (USD) in the ledger."

    def add_arguments(self, parser):
        # No abbreviations: "--set" would otherwise parse as Django's own
        # "--settings".
        parser.allow_abbrev = False
        parser.add_argument("org_id")
        action = parser.add_mutually_exclusive_group()
        action.add_argument("--grant", metavar="USD", help="grant this much credit")
        action.add_argument(
            "--adjust", metavar="±USD", help="add (+) or remove (-) credit"
        )
        action.add_argument(
            "--show",
            action="store_true",
            help="print the ledger and gateway state (the default)",
        )
        parser.add_argument("--note", help="why the grant was made (with --grant)")
        parser.add_argument(
            "--reason", help="why the adjustment was made (with --adjust)"
        )

    def handle(self, *args, **options):
        ledger = load_ledger()
        org = load_org(options["org_id"])
        # A one-off key per run: running the command twice grants twice.
        run_key = f"cli-{uuid.uuid4()}"
        try:
            if options["grant"] is not None:
                micros = _micros(options["grant"], "--grant")
                note = _text(options["note"], "--note", "--grant")
                ledger.grant(
                    org, micros, idempotency_key=run_key, actor=None, note=note
                )
                self._report(ledger, org, f"granted {format_usd(micros)}")
            elif options["adjust"] is not None:
                micros = _micros(options["adjust"], "--adjust")
                reason = _text(options["reason"], "--reason", "--adjust")
                ledger.adjust(
                    org, micros, idempotency_key=run_key, reason=reason, actor=None
                )
                self._report(ledger, org, f"adjusted {format_usd(micros)}")
            else:
                self._show(ledger, org)
        except ledger.ManagedCreditError as exc:
            raise CommandError(str(exc)) from exc

    def _report(self, ledger, org, what):
        account = ledger.get_account(org)
        pushed = (
            f"gateway acked version {account.version}"
            if account.gateway_acked_version >= account.version
            else (
                f"gateway push pending: version {account.version} not acked yet "
                "(a failed push is logged and not retried; the next grant or "
                "adjustment sends the full total again)"
            )
        )
        self.stdout.write(
            f"org {org.id}: {what}; ledger granted {format_usd(account.granted_total_micros)}, "
            f"settled spend {format_usd(account.settled_spend_micros)}; {pushed}"
        )

    def _show(self, ledger, org):
        totals = ledger.balance(org)
        account = ledger.get_account(org)
        version = account.version if account else 0
        acked = account.gateway_acked_version if account else 0
        self.stdout.write(
            f"org {org.id}\n"
            f"ledger: granted {format_usd(totals['granted_total_micros'])}, "
            f"settled spend {format_usd(totals['settled_spend_micros'])}, "
            f"available {format_usd(totals['ledger_available_micros'])}, "
            f"version {version} (gateway acked {acked})"
        )
        try:
            state = get_credits(str(org.id))
        except GatewayClientError as exc:
            self.stdout.write(f"gateway: unavailable ({exc})")
        else:
            self.stdout.write(
                f"gateway: granted {format_usd(state.get('granted_total_micros', 0))} "
                f"(version {state.get('granted_version')}), "
                f"spent {format_usd(state.get('spent_micros', 0))}, "
                f"held {format_usd(state.get('held_micros', 0))}, "
                f"available {format_usd(state.get('available_micros', 0))}, "
                f"outbox pending {state.get('outbox_pending')}, "
                f"ledger {state.get('ledger')}"
            )
        recent = ledger.ledger_entries(org).reverse()[:RECENT_ENTRIES]
        self.stdout.write(f"latest {RECENT_ENTRIES} entries, newest first:")
        for entry in recent:
            self.stdout.write(
                f"  {entry.occurred_at.isoformat()}  {entry.kind:<10} "
                f"{format_usd(entry.amount_micros):>16}  {entry.idempotency_key}"
                f"  {entry.request_id or entry.note}"
            )
