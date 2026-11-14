"""Read-only Temporal health sentinel; see tfc/temporal/health.py for deployment options."""

from django.core.management.base import BaseCommand, CommandError

from tfc.temporal.health import ping_heartbeat, run_health_checks, send_health_alert


class Command(BaseCommand):
    help = "Check Temporal queue pollers and schedules; alert to Slack on problems"

    def add_arguments(self, parser):
        parser.add_argument(
            "--heartbeat-url",
            type=str,
            default=None,
            help="URL to GET after every check attempt, including failed checks",
        )

    def handle(self, *args, **options):
        try:
            try:
                report = run_health_checks()
            except Exception as exc:
                # Do not include exception text: connection errors can carry secrets.
                send_health_alert(
                    "HC-SENTINEL: Could not complete Temporal health checks "
                    f"({type(exc).__name__}); verify Temporal connectivity and sentinel logs"
                )
                raise

            if not report.healthy:
                message = report.to_slack_message()
                send_health_alert(message)
                raise CommandError(message)
            self.stdout.write(self.style.SUCCESS("Temporal health check passed"))
        finally:
            # The heartbeat records an attempt, not the health of Temporal.
            if options["heartbeat_url"]:
                ping_heartbeat(options["heartbeat_url"])
