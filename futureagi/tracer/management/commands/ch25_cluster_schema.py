"""Provision additive cluster objects; existing installs use a new database."""

from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.clickhouse.v2.cluster_schema import (
    ClusterTopology,
    native_plan,
)


class Command(BaseCommand):
    help = "Plan/apply versioned sharded ClickHouse schema (never converts existing tables)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Execute the additive plan; default prints SQL",
        )

    def handle(self, *args, **options):
        import clickhouse_connect

        from tracer.services.clickhouse.v2.cluster_apply import apply_plan
        from tracer.services.clickhouse.v2.cluster_cdc import verify_columns

        cfg = get_v2_config()
        try:
            topology = ClusterTopology.from_env(cfg["database"])
            steps = native_plan(topology)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if not options["apply"]:
            for step in steps:
                self.stdout.write(f"-- {step.key} {step.checksum}\n{step.sql};")
            return
        from model_hub.apps import explicit_management_mutation_authorized

        if not explicit_management_mutation_authorized(
            ["manage.py", "ch25_cluster_schema"]
        ):
            raise CommandError(
                "cluster schema apply requires an authorized bootstrap/operator process"
            )
        client = clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"],
            database="default",
            send_receive_timeout=180,
        )
        try:
            verify_columns(client, topology, steps, allow_missing_additions=True)
            count = apply_plan(
                client, topology, steps, user=cfg["user"], password=cfg["password"]
            )
            verify_columns(client, topology, steps)
        except Exception as exc:
            # Driver failures may contain dictionary credentials. Do not echo.
            raise CommandError(
                f"cluster apply failed ({type(exc).__name__}); inspect DDL queue and retry the same plan"
            ) from None
        finally:
            client.close()
        self.stdout.write(f"Cluster schema verified; {count} new steps applied.")
