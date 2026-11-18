"""Copy the non-vector ClickHouse tables that live in a legacy non-replicated
location (``cluster_centroids``, ``trace_input_embeddings``, ``error_embeddings``,
``llm_logs``, ``events``) into a replicated target database.

Sibling of ``migrate_ch_vector_tables``: same shape (read every
replica via ``clusterAllReplicas``, create the target with the replicated
engine, poll per-replica parity), but each table here has its own schema and
dedup key, so the copy is driven by a per-table spec instead of a fixed
``WHERE id NOT IN`` clause.

Dedup keys
----------
* ``trace_input_embeddings`` -> ``(project_id, trace_id)``
* ``error_embeddings``       -> ``id``
* ``cluster_centroids``      -> ``cluster_id``
* ``llm_logs`` / ``events``  -> append-only logs with no unique key. The copy is
  one-shot: it runs only when the target is confirmed empty on EVERY replica and
  refuses otherwise, because a second pass would duplicate every row (no key to
  dedup on).

Safety invariants
-----------------
* Replica awareness: the target row count and parity checks require every
  expected replica (``system.clusters``) to be present in the
  ``clusterAllReplicas ... GROUP BY hostName()`` result. A replica that is
  missing (still registering) or behind is treated as not-yet-converged, never
  as "empty" or "done".
* Name-aligned copy: rows are copied with an explicit shared column list, not
  ``SELECT *``. A legacy source whose column order/set has drifted from the
  canonical target schema is copied by column name (target-only columns fall
  back to their DEFAULT); it never silently misaligns by position.
* Failure propagation: a table that cannot reach per-replica parity fails the
  whole command with a non-zero exit, so an exit-code-gated cutover cannot flip
  before the replicated copies have converged.

Idempotent for the keyed tables; one-shot (guarded) for the log tables. Run from
a backend pod that connects to the production CH cluster.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import structlog
from django.core.management.base import BaseCommand, CommandError

from agentic_eval.core.database.ch_vector import (
    ClickHouseVectorDB,
    get_clickhouse_cluster_name,
)
from model_hub.services.ch_migration import (
    OneShotDecision,
    expected_replica_count,
    one_shot_decision,
    per_replica_counts,
    poll_replica_parity,
    require_identifier,
    shared_columns,
    simulated_post_ensure_counts,
)
from model_hub.services.legacy_ch_tables import (
    EVENTS_TABLE,
    LLM_LOGS_TABLE,
    ensure_events_table,
    ensure_llm_logs_table,
)
from tracer.services.clickhouse.clustering_tables import (
    CENTROIDS_TABLE,
    ERROR_EMBEDDINGS_TABLE,
    TRACE_INPUTS_TABLE,
    ensure_centroid_table,
    ensure_error_embeddings_table,
    ensure_trace_inputs_table,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _LegacyTable:
    """How to recreate and back-fill one legacy ``default.*`` table.

    ``dedup_columns`` is the comma-separated key used for the idempotent
    ``NOT IN`` anti-join; ``None`` marks an append-only log with no unique key
    (copied one-shot into a confirmed-empty target only). ``pass_vectordb`` says
    whether the ensure helper takes a ``ClickHouseVectorDB`` (the clustering
    tables) or a raw client (the model-hub log tables).
    """

    name: str
    dedup_columns: str | None
    _ensure: Callable
    pass_vectordb: bool

    def ensure_target(
        self, db: ClickHouseVectorDB, target_db: str, cluster: str
    ) -> None:
        client_arg = db if self.pass_vectordb else db.client
        self._ensure(client_arg, database=target_db, cluster=cluster)

    @property
    def is_keyed(self) -> bool:
        return self.dedup_columns is not None


_TABLES: dict[str, _LegacyTable] = {
    t.name: t
    for t in (
        _LegacyTable(
            TRACE_INPUTS_TABLE, "project_id, trace_id", ensure_trace_inputs_table, True
        ),
        _LegacyTable(ERROR_EMBEDDINGS_TABLE, "id", ensure_error_embeddings_table, True),
        _LegacyTable(CENTROIDS_TABLE, "cluster_id", ensure_centroid_table, True),
        _LegacyTable(LLM_LOGS_TABLE, None, ensure_llm_logs_table, False),
        _LegacyTable(EVENTS_TABLE, None, ensure_events_table, False),
    )
}


class Command(BaseCommand):
    help = (
        "Migrate the legacy non-replicated default-database CH tables "
        "(cluster_centroids, trace_input_embeddings, error_embeddings, "
        "llm_logs, events) into a replicated target database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-database",
            default=os.getenv("CH_DATABASE") or "default",
        )
        parser.add_argument("--target-database", default=None)
        parser.add_argument(
            "--tables",
            default=",".join(_TABLES),
            help=f"Comma-separated subset of {', '.join(_TABLES)}.",
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
        table_names = [t.strip() for t in opts["tables"].split(",") if t.strip()]

        unknown = [t for t in table_names if t not in _TABLES]
        if unknown:
            raise CommandError(
                f"--tables contains unknown entries {unknown}. "
                f"Allowed: {', '.join(_TABLES)}."
            )

        if source_db == target_db:
            raise CommandError(
                f"--source-database and --target-database must differ ({source_db!r})."
            )

        db_client = ClickHouseVectorDB()
        is_clustered = db_client._is_clustered()
        expected_replicas = expected_replica_count(db_client.client, cluster)

        if dry_run:
            self._print_dry_run_header(
                source_db=source_db, target_db=target_db, cluster=cluster,
                is_clustered=is_clustered, expected_replicas=expected_replicas,
                tables=table_names,
            )
        else:
            on_cluster = f" ON CLUSTER '{cluster}'" if is_clustered else ""
            db_client.client.execute(
                f"CREATE DATABASE IF NOT EXISTS {target_db}{on_cluster}"
            )

        logger.info(
            "migrate_legacy_default_tables_started",
            source_database=source_db,
            target_database=target_db,
            tables=table_names,
            cluster=cluster,
            expected_replicas=expected_replicas,
            dry_run=dry_run,
        )

        total_copied = 0
        total_dry_new = 0
        failures: list[str] = []
        for name in table_names:
            copied, ok = self._migrate_one_table(
                db_client=db_client,
                spec=_TABLES[name],
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
                failures.append(name)

        logger.info(
            "migrate_legacy_default_tables_complete",
            tables=table_names,
            total_rows_copied=total_copied,
            failures=failures,
            dry_run=dry_run,
        )

        if dry_run:
            self._print_dry_run_footer(
                source_db=source_db, target_db=target_db, cluster=cluster,
                tables=table_names, total_estimated_new=total_dry_new,
                refused=failures,
            )
            return

        if failures:
            # Non-zero exit so an exit-code-gated cutover can't flip CH_DATABASE
            # before every replicated copy has converged.
            raise CommandError(
                f"Migration did not fully converge for {failures}. "
                f"Do NOT flip CH_DATABASE. Inspect per-table logs; re-run once "
                f"lagging replicas catch up (keyed tables are idempotent)."
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Tables processed: {table_names}. "
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
        self.stdout.write("Command:            migrate_legacy_default_tables_to_replicated")
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
        self.stdout.write("  python manage.py migrate_legacy_default_tables_to_replicated \\")
        self.stdout.write(f"    --source-database {source_db} \\")
        self.stdout.write(f"    --target-database {target_db} \\")
        self.stdout.write(f"    --tables {','.join(tables)} \\")
        self.stdout.write(f"    --cluster {cluster}")
        self.stdout.write(self._BAR)

    def _migrate_one_table(
        self,
        *,
        db_client: ClickHouseVectorDB,
        spec: _LegacyTable,
        source_db: str,
        target_db: str,
        cluster: str,
        expected_replicas: int,
        is_clustered: bool = True,
        dry_run: bool,
    ) -> tuple[int, bool]:
        """Return ``(rows_copied_or_estimated, converged_ok)``.

        In dry-run, the first element is the estimated new-row count.
        """
        source_qualified = f"{source_db}.{spec.name}"
        target_qualified = f"{target_db}.{spec.name}"

        source_exists = db_client.client.execute(
            "SELECT count() FROM system.tables "
            "WHERE database = %(db)s AND name = %(t)s",
            {"db": source_db, "t": spec.name},
        )[0][0]

        # uniqExact over the dedup key for keyed tables; row count for the
        # append-only log tables.
        count_expr = f"uniqExact({spec.dedup_columns})" if spec.is_keyed else "count()"
        mode_label = (
            f"keyed by ({spec.dedup_columns}), idempotent"
            if spec.is_keyed
            else "append-only, one-shot (target must be empty on all replicas)"
        )

        if dry_run:
            if not source_exists:
                self.stdout.write(
                    f"  {spec.name}:  source MISSING; target would be created empty  "
                    f"[{mode_label}]"
                )
                return 0, True
            source_count = db_client.client.execute(
                f"SELECT {count_expr} FROM clusterAllReplicas("
                f"'{cluster}', {source_qualified})"
            )[0][0]
            # Target visibility via clusterAllReplicas; empty dict means
            # ``ensure_target`` will create it cluster-wide on the real run.
            before_counts = per_replica_counts(
                db_client.client, target_db, spec.name, cluster
            )
            if not before_counts:
                self.stdout.write(
                    f"  {spec.name}:  source={source_count}  target does not exist "
                    f"(would be created)  would_insert={source_count}  [{mode_label}]"
                )
                return source_count, True
            if spec.is_keyed:
                new_rows = db_client.client.execute(
                    f"SELECT count() FROM clusterAllReplicas("
                    f"'{cluster}', {source_qualified}) "
                    f"WHERE ({spec.dedup_columns}) NOT IN "
                    f"(SELECT {spec.dedup_columns} FROM {target_qualified})"
                )[0][0]
                self.stdout.write(
                    f"  {spec.name}:  source={source_count}  "
                    f"target_now={min(before_counts.values(), default=0)}  "
                    f"would_insert={new_rows}  [{mode_label}]"
                )
                return new_rows, True
            # One-shot append-only: pad + decide against post-ensure state.
            sim_counts = simulated_post_ensure_counts(
                before_counts, expected_replicas
            )
            decision = one_shot_decision(sim_counts, expected_replicas, source_count)
            if decision == OneShotDecision.NOOP:
                self.stdout.write(
                    f"  {spec.name}:  source={source_count}  target already populated "
                    f"({before_counts})  would_insert=0 (no-op)  [{mode_label}]"
                )
                return 0, True
            if decision == OneShotDecision.COPY:
                self.stdout.write(
                    f"  {spec.name}:  source={source_count}  target empty everywhere  "
                    f"would_insert={source_count}  [{mode_label}]"
                )
                return source_count, True
            truncate_on_cluster = (
                f" ON CLUSTER '{cluster}'" if is_clustered else ""
            )
            self.stderr.write(
                self.style.WARNING(
                    f"  {spec.name}:  source={source_count}  target NOT empty "
                    f"({before_counts})  would REFUSE at real run  [{mode_label}]"
                )
            )
            self.stderr.write(
                self.style.WARNING(
                    f"    -> to migrate this table: "
                    f"TRUNCATE TABLE {target_qualified}{truncate_on_cluster}, "
                    f"then re-run without --dry-run."
                )
            )
            self.stderr.write(
                self.style.WARNING(
                    "    -> to skip this table: omit it from --tables."
                )
            )
            return 0, False

        spec.ensure_target(db_client, target_db, cluster)

        if not source_exists:
            logger.info(
                "migrate_target_created_source_missing",
                source=source_qualified,
                target=target_qualified,
            )
            self.stdout.write(
                f"  {spec.name}: target ready; source {source_qualified} "
                f"missing, nothing to copy"
            )
            return 0, True

        source_count = db_client.client.execute(
            f"SELECT {count_expr} FROM clusterAllReplicas("
            f"'{cluster}', {source_qualified})"
        )[0][0]

        before_counts = per_replica_counts(
            db_client.client, target_db, spec.name, cluster
        )

        if not spec.is_keyed:
            # Append-only log: keyless copy is not idempotent, so refuse
            # unless the target is confirmed empty on every replica.
            decision = one_shot_decision(
                before_counts, expected_replicas, source_count
            )
            if decision == OneShotDecision.NOOP:
                self.stdout.write(
                    f"  {spec.name}: already populated and converged on all "
                    f"{expected_replicas} replicas ({before_counts}); one-shot no-op."
                )
                return 0, True
            if decision == OneShotDecision.REFUSE:
                self.stderr.write(
                    self.style.WARNING(
                        f"  {spec.name}: append-only table is not confirmed empty on "
                        f"all {expected_replicas} replicas (per-replica {before_counts}); "
                        f"refusing to stay non-duplicating. TRUNCATE TABLE "
                        f"{target_qualified} ON CLUSTER '{cluster}' and re-run to force "
                        f"a fresh copy once every replica is up."
                    )
                )
                logger.warning(
                    "migrate_oneshot_target_not_confirmed_empty",
                    target=target_qualified,
                    expected_replicas=expected_replicas,
                    per_replica_counts=before_counts,
                )
                return 0, False

        existing_target_count = min(before_counts.values(), default=0)

        # Name-aligned copy: explicit shared column list, never SELECT *. Guards
        # against a drifted legacy source (column order/set) silently misaligning
        # by position; target-only columns fall back to their DEFAULT.
        shared_cols, source_only = shared_columns(
            db_client.client, source_db, target_db, spec.name
        )
        if not shared_cols:
            self.stderr.write(
                self.style.WARNING(
                    f"  {spec.name}: no shared columns between {source_qualified} and "
                    f"{target_qualified}; refusing to copy."
                )
            )
            return 0, False
        if source_only:
            logger.warning(
                "migrate_source_only_columns_dropped",
                table=spec.name,
                source_only_columns=source_only,
            )
        col_list = ", ".join(f"`{c}`" for c in shared_cols)

        where = ""
        if spec.is_keyed:
            where = (
                f" WHERE ({spec.dedup_columns}) NOT IN "
                f"(SELECT {spec.dedup_columns} FROM {target_qualified})"
            )

        insert_started = time.monotonic()
        db_client.client.execute(
            f"INSERT INTO {target_qualified} ({col_list}) "
            f"SELECT {col_list} FROM clusterAllReplicas("
            f"'{cluster}', {source_qualified}){where}"
        )
        insert_elapsed = time.monotonic() - insert_started

        replica_counts, converged = poll_replica_parity(
            db_client.client,
            database=target_db,
            table=spec.name,
            cluster=cluster,
            expected=source_count,
            expected_replicas=expected_replicas,
        )
        after_target_count = min(replica_counts.values(), default=0)
        newly_copied = after_target_count - existing_target_count

        logger.info(
            "migrate_table_complete",
            source=source_qualified,
            target=target_qualified,
            source_count=source_count,
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
                    f"  {spec.name}: NOT converged: per-replica {replica_counts}, "
                    f"expected {source_count} on each of {expected_replicas} replicas."
                )
            )
        else:
            self.stdout.write(
                f"  {spec.name}: copied {newly_copied} rows; "
                f"per-replica counts {replica_counts}"
            )
        return newly_copied, converged
