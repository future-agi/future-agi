import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracer.sql_workbench.provision import service_access_ddl


class Command(BaseCommand):
    help = "Print least-privilege SQL worker DDL. Application-only; never give this account to BI clients."

    def add_arguments(self, parser):
        parser.add_argument("--principal", default="futureagi_sql_service")
        parser.add_argument("--client-cidr", required=True)
        parser.add_argument("--include-derived-metrics", action="store_true")

    def handle(self, *args, **options):
        try:
            statements = service_access_ddl(
                database=settings.CLICKHOUSE_V2["CH25_DATABASE"],
                principal=options["principal"],
                password=os.environ.get("SQL_WORKBENCH_CH_PASSWORD", ""),
                cidr=options["client_cidr"],
                eval_table=settings.CH25_EVAL_LOGGER_TABLE,
                include_derived_metrics=options["include_derived_metrics"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            "-- Application service principal only. Never distribute to notebooks or BI clients."
        )
        for statement in statements:
            self.stdout.write(statement + ";")
