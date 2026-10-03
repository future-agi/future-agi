"""
ch25_apply_schema — apply (or re-apply) the CH 25.3 schema files for the new
`spans` table and its sister tables.

Idempotent: reads sha256 of every file in `tracer/services/clickhouse/v2/schema/`,
skips files already applied with matching hash, errors out on drift unless
--force is passed.

Operator UX:
    python manage.py ch25_apply_schema
    python manage.py ch25_apply_schema --status
    python manage.py ch25_apply_schema --force          # bypass drift check (NEVER in prod without writing it down)
    python manage.py ch25_apply_schema --replicated --cluster prod_cluster \
        --zk-table-path-prefix /clickhouse/tables/ch25
"""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracer.services.clickhouse.v2 import get_v2_config
from tracer.services.clickhouse.v2.schema_topology import (
    SchemaTopology,
    SchemaTopologyError,
    is_hosted_production,
)


def _schema_topology_from_options(opts: dict) -> SchemaTopology:
    environment = os.environ.get(
        "ENV_TYPE",
        str(getattr(settings, "ENV_TYPE", "")),
    )
    cloud_deployment = os.environ.get(
        "CLOUD_DEPLOYMENT",
        str(getattr(settings, "CLOUD_DEPLOYMENT", "")),
    )
    try:
        return SchemaTopology.from_options(
            replicated=bool(opts.get("replicated")),
            cluster=opts.get("cluster"),
            zk_table_path_prefix=opts.get("zk_table_path_prefix"),
            require_replicated=is_hosted_production(
                environment,
                cloud_deployment,
            ),
        )
    except SchemaTopologyError as exc:
        raise CommandError(str(exc)) from exc


class Command(BaseCommand):
    help = "Apply the CH 25.3 spans schema (idempotent, hash-tracked)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--status", action="store_true", help="Print applied versions and exit"
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Bypass drift check (requires a DECISIONS log entry)",
        )
        parser.add_argument(
            "--files",
            nargs="+",
            default=None,
            help="Only apply these specific files (relative to schema/)",
        )
        parser.add_argument(
            "--replicated",
            action="store_true",
            help="Apply Replicated* engines across an explicit ClickHouse cluster",
        )
        parser.add_argument(
            "--cluster",
            default=None,
            help="Cluster name; required with --replicated",
        )
        parser.add_argument(
            "--zk-table-path-prefix",
            default=None,
            help="Keeper table-path prefix; required with --replicated",
        )

    def handle(self, *args, **opts):
        topology = _schema_topology_from_options(opts)

        # Defer the import so a missing CH client doesn't break `manage.py help`.
        from tracer.services.clickhouse.v2 import apply_schema as v2_apply

        cfg = get_v2_config()
        schema_dir = (
            Path(__file__).resolve().parent.parent.parent
            / "services"
            / "clickhouse"
            / "v2"
            / "schema"
        )
        if not schema_dir.is_dir():
            raise CommandError(f"schema dir not found: {schema_dir}")

        argv: list[str] = [
            "--schema-dir",
            str(schema_dir),
            "--ch-host",
            cfg["host"],
            "--ch-http-port",
            str(cfg["http_port"]),
            "--ch-user",
            cfg["user"],
            "--ch-database",
            cfg["database"],
        ]
        # Password via env var (apply_schema.py honors CH_PASSWORD)
        os.environ.setdefault("CH_PASSWORD", cfg["password"])

        if opts["status"]:
            argv.append("--status")
        if opts["force"]:
            argv.append("--force")
        if opts["files"]:
            argv.extend(["--files", *opts["files"]])
        if topology.replicated:
            argv.extend(
                [
                    "--replicated",
                    "--cluster",
                    str(topology.cluster),
                    "--zk-table-path-prefix",
                    str(topology.zk_table_path_prefix),
                ]
            )

        rc = v2_apply.main(argv)
        if rc != 0:
            raise CommandError(
                f"apply_schema exited {rc} — see structured log above for details"
            )
        self.stdout.write(self.style.SUCCESS("✓ CH 25.3 schema is up to date."))
