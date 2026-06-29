"""Copy vector tables (``feedbacks``, ``ground_truths``, ``syn``) from a
legacy non-replicated location into a replicated target.

Reads via ``clusterAllReplicas`` so every replica's slice is captured;
writes via ``ClickHouseVectorDB.create_table`` so the engine auto-selects
``ReplicatedReplacingMergeTree`` ON CLUSTER on a multi-replica cluster.
Idempotent: ``WHERE id NOT IN target`` makes re-runs a no-op.
"""

from __future__ import annotations

import os
import re
import time

import structlog
from django.core.management.base import BaseCommand, CommandError

from agentic_eval.core.database.ch_vector import (
    ClickHouseVectorDB,
    get_clickhouse_cluster_name,
)
from agentic_eval.core.embeddings.embedding_manager import (
    FEEDBACK_TABLE_NAME,
    GROUND_TRUTH_TABLE_NAME,
)
from model_hub.utils.kb_indexer import KB_TABLE_NAME

logger = structlog.get_logger(__name__)


KNOWN_TABLES = (FEEDBACK_TABLE_NAME, GROUND_TRUTH_TABLE_NAME, KB_TABLE_NAME)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _require_identifier(value: str, flag: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise CommandError(
            f"{flag} {value!r} is not a valid ClickHouse identifier. "
            "Allowed: letters, digits, underscores; must not start with a digit."
        )
    return value


class Command(BaseCommand):
    help = "Migrate vector tables from a non-replicated source to a replicated target."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-database",
            default=os.getenv("CH_DATABASE") or "default",
        )
        parser.add_argument("--target-database", default=None)
        parser.add_argument(
            "--tables",
            default=",".join(KNOWN_TABLES),
            help=f"Comma-separated subset of {', '.join(KNOWN_TABLES)}.",
        )
        parser.add_argument("--cluster", default=get_clickhouse_cluster_name())
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        source_db = _require_identifier(opts["source_database"], "--source-database")
        target_db = _require_identifier(
            opts["target_database"] or source_db, "--target-database"
        )
        cluster = _require_identifier(opts["cluster"], "--cluster")
        dry_run = opts["dry_run"]
        tables = [t.strip() for t in opts["tables"].split(",") if t.strip()]

        unknown = [t for t in tables if t not in KNOWN_TABLES]
        if unknown:
            raise CommandError(
                f"--tables contains unknown entries {unknown}. "
                f"Allowed: {', '.join(KNOWN_TABLES)}."
            )

        if source_db == target_db:
            raise CommandError(
                f"--source-database and --target-database must differ ({source_db!r})."
            )

        db_client = ClickHouseVectorDB()
        if not dry_run and not db_client._is_clustered():
            raise CommandError(
                "CH server is not a multi-replica cluster member "
                "(no row in `system.clusters` with `replica_num > 1`). "
                "Run from a backend pod that connects to the production CH cluster."
            )

        if not dry_run:
            # Bootstrap the target DB on every replica before any CREATE TABLE.
            db_client.client.execute(
                f"CREATE DATABASE IF NOT EXISTS {target_db} ON CLUSTER '{cluster}'"
            )

        logger.info(
            "migrate_legacy_vectors_to_replicated_started",
            source_database=source_db,
            target_database=target_db,
            tables=tables,
            dry_run=dry_run,
        )

        total_copied = 0
        for table in tables:
            copied = self._migrate_one_table(
                db_client=db_client,
                table=table,
                source_db=source_db,
                target_db=target_db,
                cluster=cluster,
                dry_run=dry_run,
            )
            total_copied += copied

        logger.info(
            "migrate_legacy_vectors_to_replicated_complete",
            tables=tables,
            total_rows_copied=total_copied,
            dry_run=dry_run,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Tables processed: {tables}. "
                f"Total rows copied: {total_copied}."
            )
        )

    def _migrate_one_table(
        self,
        *,
        db_client: ClickHouseVectorDB,
        table: str,
        source_db: str,
        target_db: str,
        cluster: str,
        dry_run: bool,
    ) -> int:
        source_qualified = f"{source_db}.{table}"
        target_qualified = f"{target_db}.{table}"

        source_exists = db_client.client.execute(
            "SELECT count() FROM system.tables "
            "WHERE database = %(db)s AND name = %(t)s",
            {"db": source_db, "t": table},
        )[0][0]

        if dry_run:
            if not source_exists:
                self.stdout.write(
                    f"  dry-run: source {source_qualified} missing; "
                    f"would create empty target {target_qualified}"
                )
                return 0
            source_count_row = db_client.client.execute(
                f"SELECT uniqExact(id) FROM clusterAllReplicas("
                f"'{cluster}', {source_qualified})"
            )
            source_distinct_count = source_count_row[0][0] if source_count_row else 0
            self.stdout.write(
                f"  dry-run: would copy {source_distinct_count} rows from "
                f"{source_qualified} -> {target_qualified}"
            )
            return 0

        # Create target unconditionally; ground_truths may have no source yet.
        # Qualify the table explicitly with database= rather than mutating
        # connection.database (which is HELLO-time only and would otherwise
        # silently land the table in the connection's original database).
        db_client.create_table(table, cluster=cluster, database=target_db)

        if not source_exists:
            logger.info(
                "migrate_target_created_source_missing",
                source=source_qualified,
                target=target_qualified,
            )
            self.stdout.write(
                f"  {table}: target ready; source {source_qualified} "
                f"missing, nothing to copy"
            )
            return 0

        source_count_row = db_client.client.execute(
            f"SELECT uniqExact(id) FROM clusterAllReplicas("
            f"'{cluster}', {source_qualified})"
        )
        source_distinct_count = source_count_row[0][0] if source_count_row else 0

        # Use clusterAllReplicas + min so a leader-only read can't mask a
        # follower that's still behind from a previous half-completed run.
        existing_target_rows = db_client.client.execute(
            f"SELECT hostName(), count() FROM clusterAllReplicas("
            f"'{cluster}', {target_qualified}) GROUP BY hostName()"
        )
        existing_target_count = (
            min(c for _, c in existing_target_rows) if existing_target_rows else 0
        )

        insert_started = time.monotonic()
        db_client.client.execute(
            f"INSERT INTO {target_qualified} "
            f"SELECT * FROM clusterAllReplicas("
            f"'{cluster}', {source_qualified}) "
            f"WHERE id NOT IN (SELECT id FROM {target_qualified})"
        )
        insert_elapsed = time.monotonic() - insert_started

        # Keeper pull-down is async, so a fresh INSERT lags briefly on followers.
        per_replica_counts = self._poll_replica_parity(
            db_client=db_client,
            target_qualified=target_qualified,
            cluster=cluster,
            expected=source_distinct_count,
        )
        after_target_count = min(per_replica_counts.values()) if per_replica_counts else 0
        newly_copied = after_target_count - existing_target_count
        parity_ok = bool(per_replica_counts) and all(
            c >= source_distinct_count for c in per_replica_counts.values()
        )

        logger.info(
            "migrate_table_complete",
            source=source_qualified,
            target=target_qualified,
            source_distinct_count=source_distinct_count,
            target_count_before=existing_target_count,
            per_replica_counts=per_replica_counts,
            newly_copied=newly_copied,
            insert_elapsed_sec=round(insert_elapsed, 3),
            parity_ok=parity_ok,
        )
        if not parity_ok:
            self.stderr.write(
                self.style.WARNING(
                    f"  parity mismatch on {table}: per-replica counts "
                    f"{per_replica_counts}, source distinct = {source_distinct_count}"
                )
            )
        else:
            self.stdout.write(
                f"  {table}: copied {newly_copied} rows; "
                f"per-replica counts {per_replica_counts}"
            )
        return newly_copied

    def _poll_replica_parity(
        self,
        *,
        db_client: ClickHouseVectorDB,
        target_qualified: str,
        cluster: str,
        expected: int,
        max_wait_sec: float = 30.0,
        poll_interval: float = 2.0,
    ) -> dict[str, int]:
        """Return per-replica row counts, polling until every replica reaches expected."""
        deadline = time.monotonic() + max_wait_sec
        counts: dict[str, int] = {}
        while True:
            rows = db_client.client.execute(
                f"SELECT hostName(), count() FROM clusterAllReplicas("
                f"'{cluster}', {target_qualified}) GROUP BY hostName()"
            )
            counts = {host: cnt for host, cnt in rows}
            if counts and all(c >= expected for c in counts.values()):
                return counts
            if time.monotonic() >= deadline:
                logger.warning(
                    "migrate_parity_wait_timed_out",
                    target=target_qualified,
                    expected=expected,
                    per_replica_counts=counts,
                    max_wait_sec=max_wait_sec,
                )
                return counts
            time.sleep(poll_interval)
