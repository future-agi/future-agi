"""rebuild_dashboard_attr_rollup — reconcile the dashboard_attr_rollup aggregate.

Idempotent TRUNCATE + re-aggregate from ``spans FINAL`` (``is_deleted = 0``) using
the MV's SELECT shape, so soft-deletes the AggregatingMergeTree can't retract are
reconciled out. Run before enabling the rollup on a fresh deploy; ``--dry-run``
prints the SQL without writing.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from tracer.services.clickhouse.query_builders.dashboard import (
    _ROLLUP_COVERED_ATTRS,
    _sanitize_attr_key,
)
from tracer.services.clickhouse.v2 import get_v2_config

_ROLLUP_TABLE = "dashboard_attr_rollup"


class Command(BaseCommand):
    help = (
        "Rebuild dashboard_attr_rollup from current spans (FINAL, is_deleted=0) "
        "so soft-deletes are reconciled out of the aggregate (idempotent)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the rebuild SQL and exit; no TRUNCATE / INSERT.",
        )

    def _client(self):
        import clickhouse_connect

        cfg = get_v2_config()
        self.stdout.write(
            self.style.MIGRATE_LABEL(
                f"CH target: {cfg['host']}:{cfg['http_port']} db={cfg['database']}"
            )
        )
        return clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
            send_receive_timeout=600,
        )

    def _rebuild_sql(self) -> str:
        # Same covered set as the router gate — single source of truth.
        attr_keys = ", ".join(
            f"'{_sanitize_attr_key(k)}'" for k in sorted(_ROLLUP_COVERED_ATTRS)
        )
        return (
            f"INSERT INTO {_ROLLUP_TABLE}\n"
            "SELECT\n"
            "    project_id,\n"
            "    toStartOfHour(start_time)         AS hour,\n"
            "    attr_key,\n"
            "    attrs_string[attr_key]            AS attr_value,\n"
            "    countState()                      AS n,\n"
            "    sumState(toInt64(latency_ms))     AS latency_sum\n"
            "FROM spans FINAL\n"
            f"ARRAY JOIN [{attr_keys}] AS attr_key\n"
            "WHERE is_deleted = 0\n"
            "  AND parent_span_id = ''\n"
            "GROUP BY project_id, toStartOfHour(start_time), "
            "attr_key, attrs_string[attr_key]"
        )

    def handle(self, *args, **opts):
        sql = self._rebuild_sql()
        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("DRY RUN — SQL below, no writes:"))
            self.stdout.write(f"TRUNCATE TABLE {_ROLLUP_TABLE};")
            self.stdout.write(sql)
            return

        client = self._client()
        try:
            client.command(f"TRUNCATE TABLE IF EXISTS {_ROLLUP_TABLE}")
            client.command(sql)
        finally:
            client.close()
        self.stdout.write(
            self.style.SUCCESS(f"✓ {_ROLLUP_TABLE} rebuilt from current spans.")
        )
