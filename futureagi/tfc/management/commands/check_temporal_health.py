"""
Read-only Temporal health sentinel.

Usage:
    python manage.py check_temporal_health
    python manage.py check_temporal_health --heartbeat-url https://hc-ping.com/...

Checks every expected task queue for live pollers and every schedule for
paused/stale state, sends one consolidated Slack alert on problems, and
exits non-zero so the CronJob run is marked failed.
"""

from django.core.management.base import BaseCommand, CommandError

from tfc.temporal.health import ping_heartbeat, run_health_checks
from tfc.utils.slack import send_critical_slack_notification


class Command(BaseCommand):
    help = "Check Temporal queue pollers and schedules; alert to Slack on problems"

    def add_arguments(self, parser):
        parser.add_argument(
            "--heartbeat-url",
            type=str,
            default=None,
            help="URL to GET after the check runs (dead-man's-switch ping)",
        )

    def handle(self, *args, **options):
        report = run_health_checks()

        if not report.healthy:
            send_critical_slack_notification(report.to_slack_message())

        # The heartbeat means "the check ran", so it fires on unhealthy
        # results too; only a crash in the checks above skips it.
        if options["heartbeat_url"]:
            ping_heartbeat(options["heartbeat_url"])

        if not report.healthy:
            raise CommandError(report.to_slack_message())

        self.stdout.write(self.style.SUCCESS("Temporal health check passed"))
