"""``verify_providers`` — one-command E2E verification matrix across all providers.

Examples:
    python manage.py verify_providers                       # registry matrix (no I/O)
    python manage.py verify_providers --mode credentials    # which creds are present
    python manage.py verify_providers --mode connectivity   # real handshakes (creds)
    python manage.py verify_providers --mode registry --json # machine-readable

Credentials are read from SIM_VERIFY_<PROVIDER>_<FIELD> env vars (e.g.
SIM_VERIFY_DEEPGRAM_API_KEY). Connectivity probes make read-only API calls only — they
place no phone calls and start no billable sessions.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from simulate.services import provider_verification as pv

_STATUS_MARK = {
    pv.OK: "PASS",
    pv.MISSING: "MISS",
    pv.FAILED: "FAIL",
    pv.SKIPPED: "SKIP",
    pv.NOT_IMPL: "----",
}


class Command(BaseCommand):
    help = "Verify simulation providers across chat/voice and inbound/outbound."

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode", choices=pv.MODES, default=pv.MODE_REGISTRY,
            help="registry (default) | credentials | connectivity",
        )
        parser.add_argument(
            "--json", action="store_true", help="emit machine-readable JSON",
        )

    def handle(self, *args, **options):
        mode = options["mode"]
        try:
            report = pv.verify(mode)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        if options["json"]:
            self.stdout.write(json.dumps(report.to_dict(), indent=2))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(f"Provider verification — mode={mode}"))
        self.stdout.write(f"{'PROVIDER':16}{'MODALITY':10}{'DIRECTION':12}{'STATUS':8}DETAIL")
        self.stdout.write("-" * 88)
        for c in report.cells:
            mark = _STATUS_MARK.get(c.status, c.status)
            styler = (
                self.style.SUCCESS if c.status == pv.OK
                else self.style.ERROR if c.status in (pv.FAILED, pv.MISSING)
                else self.style.WARNING
            )
            self.stdout.write(
                f"{c.provider:16}{c.modality:10}{(c.direction or '-'):12}"
                f"{styler(mark):8}{c.detail}"
            )
        self.stdout.write("-" * 88)
        summary = ", ".join(f"{k}={v}" for k, v in sorted(report.summary().items()))
        self.stdout.write(self.style.MIGRATE_HEADING(f"Summary: {summary}"))
