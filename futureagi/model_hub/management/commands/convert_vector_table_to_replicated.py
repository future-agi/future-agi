"""Convert a plain CH vector table (``syn`` / ``feedbacks``) to
``ReplicatedReplacingMergeTree`` in place, without losing data.

On a multi-replica cluster a plain engine leaves each replica holding its own
slice (e.g. 2 / 3 / 0 rows), so reads are non-deterministic. This reads the
union of rows across every replica (deduped by ``id``) into a replicated temp
table, verifies per-replica parity, then swaps it in via ``EXCHANGE TABLES ...
ON CLUSTER`` and keeps the old plain data as ``<table>__plain_backup`` for
rollback. The EXCHANGE runs per node, not globally atomically, so nodes briefly
disagree; the required write freeze covers that window.

Runs by default; ``--dry-run`` previews the plan. Executing requires
``--write-freeze-confirmed``. No-op if the table is absent, already
Replicated, or on a single node.
"""

from __future__ import annotations

import os

import structlog
from django.core.management.base import BaseCommand, CommandError

from agentic_eval.core.database.ch_vector import (
    ClickHouseVectorDB,
    get_clickhouse_cluster_name,
)
from agentic_eval.core.embeddings.embedding_manager import FEEDBACK_TABLE_NAME
from model_hub.services.ch_migration import (
    expected_replica_count,
    per_replica_counts,
    poll_replica_parity,
    require_identifier,
)
from model_hub.utils.kb_indexer import KB_TABLE_NAME

logger = structlog.get_logger(__name__)

KNOWN_TABLES = (FEEDBACK_TABLE_NAME, KB_TABLE_NAME)


def _distinct_engines(client, database: str, table: str, cluster: str) -> set[str]:
    rows = client.execute(
        f"SELECT DISTINCT engine FROM clusterAllReplicas('{cluster}', system.tables) "
        "WHERE database = %(d)s AND name = %(t)s",
        {"d": database, "t": table},
    )
    return {r[0] for r in rows}


def _shared_columns_same_db(client, database: str, live: str, target: str) -> list[str]:
    """Ordered ``live`` columns; aborts if any is absent from ``target`` (never SELECT *)."""

    def cols(table: str) -> list[str]:
        rows = client.execute(
            "SELECT name FROM system.columns "
            "WHERE database = %(d)s AND table = %(t)s ORDER BY position",
            {"d": database, "t": table},
        )
        return [r[0] for r in rows]

    live_cols = cols(live)
    target_set = set(cols(target))
    missing = [c for c in live_cols if c not in target_set]
    if missing:
        raise CommandError(
            f"{database}.{live} has column(s) {missing} absent from the replicated "
            f"schema {database}.{target}; converting would drop them. Reconcile the "
            "schema before retrying."
        )
    return live_cols


def _table_hosts(client, database: str, table: str, cluster: str) -> set[str]:
    rows = client.execute(
        f"SELECT hostName() FROM clusterAllReplicas('{cluster}', system.tables) "
        "WHERE database = %(d)s AND name = %(t)s",
        {"d": database, "t": table},
    )
    return {row[0] for row in rows}


def _conflicting_ids(client, database: str, table: str, cluster: str) -> int:
    rows = client.execute(
        f"SELECT count() FROM ("
        f"SELECT id FROM clusterAllReplicas('{cluster}', {database}.{table}) "
        "GROUP BY id HAVING uniqExact(sipHash64(toString(tuple(eval_id, vector, metadata.key, metadata.value, deleted)))) > 1"
        ")"
    )
    return rows[0][0]


class Command(BaseCommand):
    help = "Convert a plain CH vector table to ReplicatedReplacingMergeTree in place."

    def add_arguments(self, parser):
        parser.add_argument(
            "--table", required=True,
            help=f"One of: {', '.join(KNOWN_TABLES)}",
        )
        parser.add_argument("--database", default=os.getenv("CH_DATABASE") or "default")
        parser.add_argument("--cluster", default=get_clickhouse_cluster_name())
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print the plan without changing anything.",
        )
        parser.add_argument(
            "--write-freeze-confirmed", action="store_true",
            help="Required to convert, after relevant vector writers are stopped.",
        )

    def handle(self, *args, **opts):
        table = opts["table"].strip()
        if table not in KNOWN_TABLES:
            raise CommandError(f"--table must be one of {KNOWN_TABLES}, got {table!r}")
        database = require_identifier(opts["database"], "--database")
        cluster = require_identifier(opts["cluster"], "--cluster")
        dry_run = opts["dry_run"]

        db = ClickHouseVectorDB()
        client = db.client

        if not db._is_clustered():
            self.stdout.write(
                f"{database}.{table}: single-node CH, plain engine is correct. "
                "Nothing to convert."
            )
            return

        engines = _distinct_engines(client, database, table, cluster)
        if not engines:
            self.stdout.write(f"{database}.{table}: absent on the cluster. Nothing to do.")
            return
        if all("Replicated" in e for e in engines):
            self.stdout.write(
                f"{database}.{table}: already Replicated ({sorted(engines)}). Nothing to do."
            )
            return
        if any("Replicated" in e for e in engines):
            raise CommandError(
                f"{database}.{table}: engines differ across replicas ({sorted(engines)}). "
                "Mixed Replicated/plain is unsafe to auto-convert; inspect manually."
            )

        # Plain engine on a cluster: gather the union and show the divergence.
        union_count = client.execute(
            f"SELECT uniqExact(id) FROM clusterAllReplicas('{cluster}', {database}.{table})"
        )[0][0]
        before = per_replica_counts(client, database, table, cluster)
        expected_replicas = expected_replica_count(client, cluster)
        tmp = f"{table}__repl_tmp"
        backup = f"{table}__plain_backup"
        live_hosts = _table_hosts(client, database, table, cluster)
        if len(live_hosts) != expected_replicas:
            raise CommandError(
                f"{database}.{table} is present on {len(live_hosts)} of {expected_replicas} replicas; inspect manually."
            )
        if _table_hosts(client, database, tmp, cluster) or _table_hosts(client, database, backup, cluster):
            raise CommandError(
                f"{database}.{table}: temporary or backup table already exists; inspect and recover it before retrying."
            )
        conflicts = _conflicting_ids(client, database, table, cluster)
        if conflicts:
            raise CommandError(
                f"{database}.{table} has {conflicts} duplicate id(s) with conflicting payloads; refusing arbitrary selection."
            )

        self.stdout.write(f"{database}.{table}: plain engine(s) {sorted(engines)}")
        self.stdout.write(f"  per-replica rows (diverged): {before}")
        self.stdout.write(f"  union distinct ids:          {union_count}")
        self.stdout.write(f"  expected replicas:           {expected_replicas}")

        if dry_run:
            self.stdout.write(
                "\nDRY-RUN. Would, in order:\n"
                f"  1. CREATE {database}.{tmp} as ReplicatedReplacingMergeTree ON CLUSTER\n"
                f"  2. INSERT the deduped union ({union_count} rows) from all replicas\n"
                f"  3. verify parity ({union_count} on each of {expected_replicas} replicas)\n"
                f"  4. EXCHANGE TABLES {database}.{table} <-> {database}.{tmp} ON CLUSTER\n"
                f"  5. RENAME the old plain table to {database}.{backup} (kept for rollback)\n"
                "Re-run without --dry-run to perform it."
            )
            return

        if not opts["write_freeze_confirmed"]:
            raise CommandError(
                "refusing to convert without --write-freeze-confirmed; "
                "stop vector writers first, then re-run with the flag."
            )

        # 1. Replicated temp table (own Keeper path via create_table).
        db.create_table(
            tmp,
            cluster=cluster,
            database=database,
            keeper_table_name=table,
        )

        # 2. Insert the deduped union of every replica's slice. LIMIT 1 BY id
        #    collapses any id that appears on more than one replica.
        cols = _shared_columns_same_db(client, database, table, tmp)
        if not cols:
            raise CommandError(
                f"no shared columns between {database}.{table} and {database}.{tmp}; aborting."
            )
        col_list = ", ".join(f"`{c}`" for c in cols)
        client.execute(
            f"INSERT INTO {database}.{tmp} ({col_list}) "
            f"SELECT {col_list} FROM clusterAllReplicas('{cluster}', {database}.{table}) "
            f"LIMIT 1 BY id"
        )

        # 3. Verify parity BEFORE swapping. Do not swap a lagging copy.
        counts, converged = poll_replica_parity(
            client, database=database, table=tmp, cluster=cluster,
            expected=union_count, expected_replicas=expected_replicas,
        )
        if not converged:
            raise CommandError(
                f"{database}.{tmp} did not converge (per-replica {counts}, expected "
                f"{union_count} on {expected_replicas} replicas). Live table untouched; "
                f"temp left for inspection. Re-run once replicas catch up, or drop "
                f"{database}.{tmp} and retry."
            )

        # 4. Atomic cutover, then 5. keep the old plain data as a backup.
        client.execute(
            f"EXCHANGE TABLES {database}.{table} AND {database}.{tmp} ON CLUSTER '{cluster}'"
        )
        client.execute(
            f"RENAME TABLE {database}.{tmp} TO {database}.{backup} ON CLUSTER '{cluster}'"
        )

        # Verify the swap reached every node before printing success.
        after, after_converged = poll_replica_parity(
            client, database=database, table=table, cluster=cluster,
            expected=union_count, expected_replicas=expected_replicas,
        )
        if not after_converged:
            raise CommandError(
                f"{database}.{table}: post-swap parity not reached (per-replica {after}, "
                f"expected {union_count} on {expected_replicas}); the EXCHANGE may be "
                f"partially applied. Old plain data is preserved at {database}.{backup}."
            )
        logger.info(
            "convert_vector_table_done",
            table=f"{database}.{table}", union_count=union_count,
            per_replica_after=after, backup=f"{database}.{backup}",
        )
        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {database}.{table} is now ReplicatedReplacingMergeTree with "
            f"{union_count} rows; per-replica {after}. Old plain data preserved at "
            f"{database}.{backup} (drop it once verified)."
        ))
