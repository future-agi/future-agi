"""Export Marketplace usage reports in the shape Google's verification asks for.

Google's usage-reporting test compares what we sent against the Customer
Incremental Insights report, and wants each report as a row with the time,
operation id, window, consumer id, metric name and value.

Usage:
    python manage.py export_gcp_marketplace_usage --period 2026-09
    python manage.py export_gcp_marketplace_usage --period 2026-09 --out reports.csv
    python manage.py export_gcp_marketplace_usage --entitlement <id> --all
"""

import csv
import sys

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.gcp_marketplace_usage import _period_bounds
from accounts.models.gcp_marketplace import (
    GCPMarketplaceUsageCheckpoint,
    GCPUsageReportStatus,
)
from accounts.services.gcp_procurement import metric_id_for
from accounts.services.gcp_service_control import gcp_service_control

COLUMNS = [
    "time_utc",
    "operation_id",
    "start_time",
    "end_time",
    "consumer_id",
    "metric_name",
    "metric_value",
    "status",
    "entitlement_id",
    "organization_id",
]


def _utc(moment) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else ""


class Command(BaseCommand):
    help = "Export GCP Marketplace usage reports as CSV for Google's verification"

    def add_arguments(self, parser):
        parser.add_argument(
            "--period",
            help="Billing period YYYY-MM (default: current month)",
        )
        parser.add_argument("--entitlement", help="Restrict to one entitlement id")
        parser.add_argument("--out", help="File path (default: stdout)")
        parser.add_argument(
            "--all",
            action="store_true",
            help="Include PENDING and FAILED rows, not only REPORTED",
        )

    def handle(self, *args, **options):
        period = options["period"] or timezone.now().strftime("%Y-%m")
        if len(period) != 7 or period[4] != "-":
            raise CommandError(f"--period must be YYYY-MM, got {period!r}")

        period_start, period_end = _period_bounds(period)
        rows = GCPMarketplaceUsageCheckpoint.objects.filter(
            window_start__gte=period_start, window_start__lt=period_end
        ).select_related("entitlement")

        if options["entitlement"]:
            rows = rows.filter(entitlement__entitlement_id=options["entitlement"])
        if not options["all"]:
            rows = rows.filter(report_status=GCPUsageReportStatus.REPORTED)

        rows = rows.order_by("reported_at", "window_start", "metric")

        out = open(options["out"], "w", newline="") if options["out"] else sys.stdout
        try:
            writer = csv.writer(out)
            writer.writerow(COLUMNS)
            count = 0
            for row in rows.iterator(chunk_size=500):
                metric_id = metric_id_for(row.entitlement.plan_id, row.metric)
                metric_name = (
                    gcp_service_control.metric_name(metric_id)
                    if metric_id
                    else f"<unmapped:{row.metric}>"
                )
                writer.writerow(
                    [
                        _utc(row.reported_at or row.updated_at),
                        row.operation_id,
                        _utc(row.window_start),
                        _utc(row.window_end),
                        row.entitlement.usage_reporting_id,
                        metric_name,
                        f"{row.quantity_reported.normalize():f}",
                        row.report_status,
                        row.entitlement.entitlement_id,
                        str(row.organization_id),
                    ]
                )
                count += 1
        finally:
            if out is not sys.stdout:
                out.close()

        if options["out"]:
            self.stdout.write(f"Wrote {count} rows to {options['out']}")
