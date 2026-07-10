"""Copy vector tables (``feedbacks``, ``ground_truths``, ``syn``) from a
legacy non-replicated location into a replicated target.

Reads via ``clusterAllReplicas`` so every replica's slice is captured;
writes via ``ClickHouseVectorDB.create_table`` so the engine auto-selects
``ReplicatedReplacingMergeTree`` ON CLUSTER on a multi-replica cluster.
Idempotent: ``WHERE id NOT IN target`` makes re-runs a no-op.

Parity, column-alignment, and failure-propagation semantics come from the
shared ``model_hub.services.ch_migration`` module so both this command
and its sibling default-tables migrator behave identically.
"""

from __future__ import annotations

import os
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
from model_hub.services.ch_migration import (
    expected_replica_count,
    per_replica_counts,
    poll_replica_parity,
    require_identifier,
    shared_columns,
)
from model_hub.utils.kb_indexer import KB_TABLE_NAME

logger = structlog.get_logger(__name__)


KNOWN_TABLES = (FEEDBACK_TABLE_NAME, GROUND_TRUTH_TABLE_NAME, KB_TABLE_NAME)

# Vector-table dedup key. All three tables use `id UUID` as their
# ReplacingMergeTree order-by, so the copy anti-joins on it.
_DEDUP_COLUMN = "id"


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
        source_db = require_identifier(opts["source_database"], "--source-database")
        target_db = require_identifier(
            opts["target_database"] or source_db, "--target-database"
        )
        cluster = require_identifier(opts["cluster"], "--cluster")
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
        is_clustered = db_client._is_clustered()
        # Probe replicas even in dry-run so the preview matches the real path.
        expected_replicas = expected_replica_count(db_client.client, cluster)

        if dry_run:
            self._print_dry_run_header(
                source_db=source_db, target_db=target_db, cluster=cluster,
                is_clustered=is_clustered, expected_replicas=expected_replicas,
                tables=tables,
            )
        else:
            # Bootstrap the target DB on every replica before any CREATE TABLE.
            # ON CLUSTER requires Keeper; drop it on single-node CH.
            on_cluster = f" ON CLUSTER '{cluster}'" if is_clustered else ""
            db_client.client.execute(
                f"CREATE DATABASE IF NOT EXISTS {target_db}{on_cluster}"
            )

        logger.info(
            "migrate_ch_vector_tables_started",
            source_database=source_db,
            target_database=target_db,
            tables=tables,
            cluster=cluster,
            expected_replicas=expected_replicas,
            dry_run=dry_run,
        )

        total_copied = 0
        total_dry_new = 0
        failures: list[str] = []
        for table in tables:
            copied, ok = self._migrate_one_table(
                db_client=db_client,
                table=table,
                source_db=source_db,
                target_db=target_db,
                cluster=cluster,
                expected_replicas=expected_replicas,
                is_clustered=is_clustered,
                dry_run=dry_run,
            )
            if dry_run:
                total_dry_new += copied
            else:
                total_copied += copied
            if not ok:
                failures.append(table)

        logger.info(
            "migrate_ch_vector_tables_complete",
            tables=tables,
            total_rows_copied=total_copied,
            failures=failures,
            dry_run=dry_run,
        )

        if dry_run:
            self._print_dry_run_footer(
                source_db=source_db, target_db=target_db, cluster=cluster,
                tables=tables, total_estimated_new=total_dry_new,
                refused=failures,
            )
            return

        if failures:
            # Non-zero exit so an exit-code-gated cutover can't flip
            # CH_DATABASE before every replicated copy has converged.
            raise CommandError(
                f"Migration did not fully converge for {failures}. "
                f"Do NOT flip CH_DATABASE. Inspect per-table logs; re-run once "
                f"lagging replicas catch up (vector tables are idempotent)."
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Tables processed: {tables}. "
                f"Total rows copied: {total_copied}."
            )
        )

    _BAR = "=" * 68

    def _print_dry_run_header(
        self, *, source_db: str, target_db: str, cluster: str,
        is_clustered: bool, expected_replicas: int, tables: list[str],
    ) -> None:
        mode = (
            f"clustered  (Replicated* engines; CREATE ... ON CLUSTER '{cluster}')"
            if is_clustered
            else "single-node  (plain engines; no ON CLUSTER, Keeper absent)"
        )
        bootstrap = (
            f"CREATE DATABASE IF NOT EXISTS {target_db} ON CLUSTER '{cluster}'"
            if is_clustered
            else f"CREATE DATABASE IF NOT EXISTS {target_db}"
        )
        self.stdout.write("")
        self.stdout.write(self._BAR)
        self.stdout.write("CH Migration DRY RUN  (no writes will occur)")
        self.stdout.write(self._BAR)
        self.stdout.write(f"Command:            migrate_ch_vector_tables")
        self.stdout.write(f"Source database:    {source_db}")
        self.stdout.write(f"Target database:    {target_db}")
        self.stdout.write(f"Cluster:            {cluster}")
        self.stdout.write(f"Mode:               {mode}")
        self.stdout.write(f"Expected replicas:  {expected_replicas}")
        self.stdout.write(f"Tables:             {', '.join(tables)}")
        self.stdout.write(f"Bootstrap step:     {bootstrap}")
        self.stdout.write(self._BAR)

    def _print_dry_run_footer(
        self, *, source_db: str, target_db: str, cluster: str,
        tables: list[str], total_estimated_new: int, refused: list[str],
    ) -> None:
        refused_line = (
            "Would refuse:       none"
            if not refused
            else f"Would refuse:       {', '.join(refused)}  (see per-table warnings above)"
        )
        self.stdout.write(self._BAR)
        self.stdout.write("Summary")
        self.stdout.write(f"  Tables scanned:     {len(tables)}")
        self.stdout.write(f"  New rows to insert: {total_estimated_new}")
        self.stdout.write(f"  {refused_line}")
        self.stdout.write("")
        self.stdout.write("Real run:  same command without --dry-run:")
        self.stdout.write(f"  python manage.py migrate_ch_vector_tables \\")
        self.stdout.write(f"    --source-database {source_db} \\")
        self.stdout.write(f"    --target-database {target_db} \\")
        self.stdout.write(f"    --cluster {cluster}")
        self.stdout.write(self._BAR)

    def _migrate_one_table(
        self,
        *,
        db_client: ClickHouseVectorDB,
        table: str,
        source_db: str,
        target_db: str,
        cluster: str,
        expected_replicas: int,
        is_clustered: bool = True,
        dry_run: bool,
    ) -> tuple[int, bool]:
        """Return ``(rows_copied_or_estimated, converged_ok)``.

        In dry-run, the first element is the estimated new-row count
        (source distinct rows that are not yet in the target).
        """
        source_qualified = f"{source_db}.{table}"
        target_qualified = f"{target_db}.{table}"

        source_exists = db_client.client.execute(
            "SELECT count() FROM system.tables "
            "WHERE database = %(db)s AND name = %(t)s",
            {"db": source_db, "t": table},
        )[0][0]

        if dry_run:
            engine_hint = (
                "ReplicatedReplacingMergeTree ON CLUSTER"
                if is_clustered
                else "ReplacingMergeTree() [plain, single-node]"
            )
            if not source_exists:
                self.stdout.write(
                    f"  {table}:  source MISSING; target would be created empty  "
                    f"[engine: {engine_hint}]"
                )
                return 0, True
            source_distinct_count = db_client.client.execute(
                f"SELECT uniqExact({_DEDUP_COLUMN}) FROM clusterAllReplicas("
                f"'{cluster}', {source_qualified})"
            )[0][0]
            target_exists = db_client.client.execute(
                "SELECT count() FROM system.tables "
                "WHERE database = %(db)s AND name = %(t)s",
                {"db": target_db, "t": table},
            )[0][0]
            if target_exists:
                target_now = db_client.client.execute(
                    f"SELECT count() FROM {target_qualified}"
                )[0][0]
                new_rows = db_client.client.execute(
                    f"SELECT count() FROM clusterAllReplicas("
                    f"'{cluster}', {source_qualified}) "
                    f"WHERE {_DEDUP_COLUMN} NOT IN "
                    f"(SELECT {_DEDUP_COLUMN} FROM {target_qualified})"
                )[0][0]
            else:
                target_now = 0
                new_rows = source_distinct_count
            self.stdout.write(
                f"  {table}:  source={source_distinct_count}  "
                f"target_now={target_now}  would_insert={new_rows}  "
                f"[engine: {engine_hint}]"
            )
            return new_rows, True

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
            return 0, True

        source_distinct_count = db_client.client.execute(
            f"SELECT uniqExact({_DEDUP_COLUMN}) FROM clusterAllReplicas("
            f"'{cluster}', {source_qualified})"
        )[0][0]

        before_counts = per_replica_counts(
            db_client.client, target_db, table, cluster
        )
        existing_target_count = min(before_counts.values(), default=0)

        # Name-aligned copy: explicit shared column list, never SELECT *.
        # Guards a drifted legacy source (column order/set) against silent
        # positional misalignment; target-only columns fall back to DEFAULT.
        shared_cols, source_only = shared_columns(
            db_client.client, source_db, target_db, table
        )
        if not shared_cols:
            self.stderr.write(
                self.style.WARNING(
                    f"  {table}: no shared columns between {source_qualified} "
                    f"and {target_qualified}; refusing to copy."
                )
            )
            return 0, False
        if source_only:
            logger.warning(
                "migrate_source_only_columns_dropped",
                table=table,
                source_only_columns=source_only,
            )
        col_list = ", ".join(f"`{c}`" for c in shared_cols)

        insert_started = time.monotonic()
        db_client.client.execute(
            f"INSERT INTO {target_qualified} ({col_list}) "
            f"SELECT {col_list} FROM clusterAllReplicas("
            f"'{cluster}', {source_qualified}) "
            f"WHERE {_DEDUP_COLUMN} NOT IN "
            f"(SELECT {_DEDUP_COLUMN} FROM {target_qualified})"
        )
        insert_elapsed = time.monotonic() - insert_started

        # Keeper pull-down is async, so a fresh INSERT lags briefly on followers.
        replica_counts, converged = poll_replica_parity(
            db_client.client,
            database=target_db,
            table=table,
            cluster=cluster,
            expected=source_distinct_count,
            expected_replicas=expected_replicas,
        )
        after_target_count = min(replica_counts.values(), default=0)
        newly_copied = after_target_count - existing_target_count

        logger.info(
            "migrate_table_complete",
            source=source_qualified,
            target=target_qualified,
            source_distinct_count=source_distinct_count,
            target_count_before=existing_target_count,
            per_replica_counts=replica_counts,
            expected_replicas=expected_replicas,
            newly_copied=newly_copied,
            insert_elapsed_sec=round(insert_elapsed, 3),
            converged=converged,
        )
        if not converged:
            self.stderr.write(
                self.style.WARNING(
                    f"  {table}: NOT converged: per-replica {replica_counts}, "
                    f"expected {source_distinct_count} on each of "
                    f"{expected_replicas} replicas."
                )
            )
        else:
            self.stdout.write(
                f"  {table}: copied {newly_copied} rows; "
                f"per-replica counts {replica_counts}"
            )
        return newly_copied, converged
