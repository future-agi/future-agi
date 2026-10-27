"""Explicit single-node feature-store provisioning; never run on API requests."""

from django.core.management.base import BaseCommand

from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.grouping.feature_store import grouping_feature_ddl


class Command(BaseCommand):
    help = (
        "Print F6 ClickHouse table DDL. --apply explicitly creates missing tables "
        "in the configured database. Review cluster topology before production use."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Execute the reviewed DDL; default is print-only.",
        )

    def handle(self, *args, **options):
        statements = grouping_feature_ddl()
        if not options["apply"]:
            for statement in statements:
                self.stdout.write(statement + ";")
            return
        client = ClickHouseClient()
        for statement in statements:
            client.execute(statement)
        self.stdout.write(
            self.style.SUCCESS(
                "Grouping tables provisioned; rollout remains unchanged."
            )
        )
