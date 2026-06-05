"""``provision_sim_phone_numbers`` — populate the SimulationPhoneNumber pool (TH-5642).

Outbound voice sims acquire an idle number from the system-level ``SimulationPhoneNumber``
pool. When the pool is empty the call never connects (the acquire activity now fails fast
with a clear error — see voice_small.py). This command fills the pool from a Twilio
account's owned numbers so outbound voice simulations can run.

    python manage.py provision_sim_phone_numbers --direction outbound
    python manage.py provision_sim_phone_numbers --direction outbound --numbers +12175696753,+12068956991
    python manage.py provision_sim_phone_numbers --direction inbound --dry-run

Twilio creds are read from TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN (or --sid/--token).
Idempotent: a number already in the pool (by provider_phone_id) is skipped. provider_phone_id
defaults to the Twilio number SID (stable, unique); use --provider-id e164 to key by number.
"""

from __future__ import annotations

import os

import requests
from django.core.management.base import BaseCommand, CommandError

from simulate.models.simulation_phone_number import SimulationPhoneNumber

_TWILIO_NUMBERS_URL = (
    "https://api.twilio.com/2010-04-01/Accounts/{sid}/IncomingPhoneNumbers.json"
)


class Command(BaseCommand):
    help = "Provision Twilio phone numbers into the SimulationPhoneNumber pool."

    def add_arguments(self, parser):
        parser.add_argument(
            "--direction", choices=["inbound", "outbound"], required=True,
            help="call_direction to assign the provisioned numbers",
        )
        parser.add_argument(
            "--numbers", default="",
            help="comma-separated E.164 numbers to provision (default: all on the account)",
        )
        parser.add_argument("--sid", default=os.getenv("TWILIO_ACCOUNT_SID", ""))
        parser.add_argument("--token", default=os.getenv("TWILIO_AUTH_TOKEN", ""))
        parser.add_argument(
            "--provider-id", choices=["sid", "e164"], default="sid",
            help="what to store as provider_phone_id (Twilio number SID or the E.164 number)",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        sid, token = opts["sid"], opts["token"]
        if not sid or not token:
            raise CommandError(
                "Twilio creds required: set TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN or pass --sid/--token."
            )
        direction = opts["direction"]
        wanted = {n.strip() for n in opts["numbers"].split(",") if n.strip()}

        resp = requests.get(
            _TWILIO_NUMBERS_URL.format(sid=sid),
            params={"PageSize": 100}, auth=(sid, token), timeout=30,
        )
        if resp.status_code != 200:
            raise CommandError(f"Twilio list numbers failed ({resp.status_code}): {resp.text}")
        account_numbers = resp.json().get("incoming_phone_numbers", []) or []

        created = skipped = filtered = 0
        for n in account_numbers:
            e164 = n.get("phone_number")
            number_sid = n.get("sid")
            if wanted and e164 not in wanted:
                filtered += 1
                continue
            provider_phone_id = number_sid if opts["provider_id"] == "sid" else e164
            if SimulationPhoneNumber.objects.filter(provider_phone_id=provider_phone_id).exists():
                self.stdout.write(f"  skip (exists): {e164} [{provider_phone_id}]")
                skipped += 1
                continue
            if opts["dry_run"]:
                self.stdout.write(self.style.WARNING(f"  would add: {e164} [{provider_phone_id}] {direction}"))
                created += 1
                continue
            SimulationPhoneNumber.objects.create(
                phone_number=e164,
                provider_phone_id=provider_phone_id,
                call_direction=direction,
                status=SimulationPhoneNumber.PhoneStatus.IDLE,
            )
            self.stdout.write(self.style.SUCCESS(f"  added: {e164} [{provider_phone_id}] {direction} (idle)"))
            created += 1

        verb = "would add" if opts["dry_run"] else "added"
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Done: {verb}={created}, skipped(existing)={skipped}, filtered_out={filtered}. "
            f"Pool now has {SimulationPhoneNumber.objects.filter(call_direction=direction).count()} "
            f"{direction} number(s)."
        ))
