"""Deliver pending FA -> Omega EE revocation events now (READINESS lane 1.2a).

    python manage.py drain_omega_host_events            # one drain, prints counts
    python manage.py drain_omega_host_events --status   # backlog only, exit 1 if behind
    python manage.py drain_omega_host_events --watch 3  # drain every 3 s until stopped

The Temporal schedule ``omega-host-events-drain`` normally does this every 3 s.
"""

import json
import time

from django.core.management.base import BaseCommand

from accounts.host_events import relay
from accounts.models.host_event_outbox import HostEventOutbox, HostEventStatus


class Command(BaseCommand):
    help = "Deliver pending Omega host revocation events, or report the backlog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--status", action="store_true", help="Report the backlog without sending."
        )
        parser.add_argument(
            "--watch",
            type=float,
            default=0.0,
            help="Drain every N seconds until interrupted.",
        )

    def handle(self, *args, **options):
        if options["status"]:
            pending = HostEventOutbox.objects.filter(status=HostEventStatus.PENDING)
            lag = relay.backlog_seconds()
            purge_lag = relay.backlog_seconds(purges=True)
            report = {
                "channel": relay.current_config().state,
                "pending": pending.count(),
                "backlog_seconds": int(lag),
                # Workspace and organization purges (READINESS lane 3.22), alerted at 45 min.
                "purge_backlog_seconds": int(purge_lag),
                "last_errors": sorted(
                    set(
                        pending.exclude(last_error="").values_list(
                            "last_error", flat=True
                        )[:20]
                    )
                ),
            }
            self.stdout.write(json.dumps(report))
            if (
                lag > relay.BACKLOG_ALERT_SECONDS
                or purge_lag > relay.PURGE_ALERT_SECONDS
            ):
                raise SystemExit(1)
            return
        while True:
            self.stdout.write(json.dumps(relay.drain()))
            if options["watch"] <= 0:
                return
            time.sleep(options["watch"])
