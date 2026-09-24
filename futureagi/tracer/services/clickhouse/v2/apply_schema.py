#!/usr/bin/env python3
"""
apply_schema.py — Idempotent ClickHouse schema runner.

Reads files from schema/*.sql in lexical order, applies each, records the
content sha256 + applied_at in the `schema_versions` table inside whichever
database --ch-database points at (default: `default`). Re-runs are no-ops
unless a file's content has drifted (then we error out unless --force).

Schema files use UNQUALIFIED table names (`spans`, not `default.spans`) so
that --ch-database is the single switch for choosing a dev / test / prod
database without editing SQL.

Design decisions (DECISIONS.md #004):
    • Append-only: schema files are never deleted or edited after apply.
    • Hash-tracked: drift = file changed after apply = manual decision required.
    • DDL is statement-by-statement (CH doesn't have multi-statement transactions).
    • Each file is split on `;\n` and each statement is sent individually so we
      get precise error locations.
    • Empty / comment-only statements are skipped.
    • XML config files (001_storage_policy.xml, _local_overrides.xml) are NOT
      handled here — they're loaded by the CH server at boot via volume mount.

Usage:
    apply_schema.py --schema-dir schema --ch-host 127.0.0.1 --ch-http-port 19001
    apply_schema.py --status                                  # show applied versions
    apply_schema.py --force --files 002_spans_v2.sql ...      # bypass hash check

Exit codes:
    0   success or no-op
    1   user error (bad flags, file missing)
    2   drift detected without --force
    3   CH error during apply
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import clickhouse_connect  # HTTP — easier to debug than native here
import structlog

from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    extract_table_name as _extract_table_name,
)
from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    rewrite_for_replicated,
    split_statements,
)
from tracer.services.clickhouse.v2.schema_topology import (
    CATALOG_TOPOLOGY_GUARD_TABLES,
    SchemaTopology,
    SchemaTopologyError,
    is_hosted_production,
)

# ──────────────────────────────────────────────────────────────────────────────
# Logging — structured, machine-parseable, never print()
# ──────────────────────────────────────────────────────────────────────────────
log = structlog.get_logger("apply_schema")


def _configure_logging() -> None:
    """Configure structlog for CLI runs. Called from main(), not at import, so
    importing this module never mutates the process's global structlog config."""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
    )


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────
def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--schema-dir",
        type=Path,
        # Default is THIS module's schema dir (v2). Earlier the default pointed
        # to ../schema which resolved to the legacy CH 24.10 schema directory
        # — codex review P1 finding. Tests sweep the v2 dir explicitly so they
        # didn't catch this, but a CLI user could have applied legacy SQL to
        # CH 25.3 by accident.
        default=Path(__file__).parent / "schema",
        help="Directory containing *.sql files (default: this module's v2 schema/)",
    )
    p.add_argument("--ch-host", default="127.0.0.1")
    p.add_argument("--ch-http-port", type=int, default=19001)
    p.add_argument("--ch-user", default="default")
    p.add_argument("--ch-password", default=os.environ.get("CH_PASSWORD", ""))
    p.add_argument("--ch-database", default="default")
    p.add_argument("--status", action="store_true", help="Show applied versions, exit")
    p.add_argument(
        "--force",
        action="store_true",
        help="Apply even if hash drift detected (requires DECISIONS.md justification)",
    )
    p.add_argument(
        "--files",
        nargs="+",
        default=None,
        help="Apply only these files (relative to --schema-dir). Default: all *.sql in order.",
    )
    p.add_argument(
        "--replicated",
        action="store_true",
        help=(
            "Rewrite engine declarations to their Replicated* variants and append "
            "ON CLUSTER to CREATE TABLE / CREATE MATERIALIZED VIEW. Use in production "
            "where tables are coordinated via ZooKeeper / ClickHouse Keeper. The local "
            "test rig (single-node, no Keeper) does NOT use this flag."
        ),
    )
    p.add_argument(
        "--cluster",
        default=None,
        help="Cluster name for --replicated. Must be supplied explicitly and match "
        "the remote_servers config on the target CH cluster.",
    )
    p.add_argument(
        "--zk-table-path-prefix",
        default=None,
        help=(
            "ZooKeeper/Keeper path prefix for Replicated tables. Must be supplied "
            "explicitly. Each table gets "
            "<prefix>/{shard}/<table>. {shard} and {replica} are resolved by "
            "CH macros server-side. The '/ch25' segment namespaces v2 tables "
            "away from the legacy '/clickhouse/tables/{shard}/<table>' paths "
            "used by the existing CH 24.10 tables (per schema.py:760). Sharing "
            "the path would cause replica-metadata collisions on the same "
            "Keeper instance — codex review P0 finding."
        ),
    )
    return p


# ──────────────────────────────────────────────────────────────────────────────
# DDL helpers
# ──────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SchemaFile:
    path: Path
    sha256: str

    @classmethod
    def from_path(cls, p: Path) -> SchemaFile:
        return cls(path=p, sha256=hashlib.sha256(p.read_bytes()).hexdigest())


@dataclass(frozen=True)
class LocalMergeTreeConflict:
    """A table that would make replicated CREATE IF NOT EXISTS unsafe."""

    host: str
    table: str
    engine: str

    def as_log_dict(self) -> dict[str, str]:
        return {
            "host": self.host,
            "table": self.table,
            "engine": self.engine,
        }


def find_local_merge_tree_conflicts(
    client,
    *,
    database: str,
    cluster: str,
) -> list[LocalMergeTreeConflict]:
    """Find local catalog/version tables across every target replica.

    A replicated ``CREATE TABLE IF NOT EXISTS`` silently preserves an existing
    local MergeTree. Refuse before the first DDL instead of leaving a mixed
    topology whose rows and schema history diverge by replica.
    """

    guarded_names = ", ".join(
        f"'{name}'" for name in sorted(CATALOG_TOPOLOGY_GUARD_TABLES)
    )
    rows = client.query(
        f"""
        SELECT hostName(), name, engine
        FROM clusterAllReplicas(%(cluster)s, system.tables)
        WHERE database = %(database)s
          AND name IN ({guarded_names})
        ORDER BY hostName(), name
        """,
        parameters={"cluster": cluster, "database": database},
    ).result_rows
    conflicts = []
    for host, table, engine in rows:
        normalized_table = str(table)
        if normalized_table not in CATALOG_TOPOLOGY_GUARD_TABLES:
            continue
        normalized_engine = str(engine or "")
        lowered_engine = normalized_engine.lower()
        if lowered_engine.endswith("mergetree") and not lowered_engine.startswith(
            "replicated"
        ):
            conflicts.append(
                LocalMergeTreeConflict(
                    host=str(host),
                    table=normalized_table,
                    engine=normalized_engine,
                )
            )
    return sorted(conflicts, key=lambda item: (item.host, item.table, item.engine))


def ensure_versions_table(
    client,
    *,
    replicated: bool = False,
    cluster: str = "default",
    zk_prefix: str = "/clickhouse/tables",
) -> None:
    """Create schema_versions if it doesn't exist. Bootstrap; not tracked itself.

    Uses unqualified table name so it lands in the connection's current database
    (set via --ch-database). Schema files use the same convention so dev / test /
    prod can use whichever DB they need without editing SQL.

    In `replicated` mode we MUST use ReplicatedMergeTree for this table too —
    otherwise each replica would track its own apply history and they'd
    diverge. ON CLUSTER ensures the DDL fans out atomically.
    """
    on_cluster = f" ON CLUSTER '{cluster}'" if replicated else ""
    if replicated:
        engine = (
            f"ReplicatedMergeTree('{zk_prefix}/{{shard}}/schema_versions', "
            f"'{{replica}}')"
        )
    else:
        engine = "MergeTree"
    client.command(f"""
        CREATE TABLE IF NOT EXISTS schema_versions{on_cluster} (
            filename   String,
            sha256     FixedString(64),
            applied_at DateTime64(3, 'UTC') DEFAULT now64(3, 'UTC'),
            applied_by String DEFAULT '',
            notes      String DEFAULT ''
        ) ENGINE = {engine} ORDER BY (filename, applied_at)
    """)


def fetch_applied(
    client,
    *,
    topology: SchemaTopology | None = None,
) -> dict[str, str]:
    """Return the most recent hashes for one exact schema topology.

    NOTE: CH `FixedString(64)` round-trips as Python `bytes` via clickhouse-connect.
    Decode to str so comparisons against `hashlib.sha256(...).hexdigest()` work.
    The cast also handles the case where CH ever returns str directly (no-op).

    Historical local applies stored blank ``notes``. They remain part of the
    local identity only; a replicated apply can never inherit their skip/drift
    decisions.
    """
    topology = topology or SchemaTopology.from_options(replicated=False)
    rows = client.query(
        """
        SELECT filename, argMax(sha256, applied_at) AS sha256
        FROM   schema_versions
        WHERE  notes = %(topology_identity)s
           OR (%(include_legacy_local)s = 1 AND notes = '')
        GROUP  BY filename
        """,
        parameters={
            "topology_identity": topology.version_identity,
            "include_legacy_local": int(topology.includes_legacy_local_versions),
        },
    ).result_rows
    out: dict[str, str] = {}
    for fn, sha in rows:
        if isinstance(sha, bytes):
            sha = sha.decode("ascii")
        out[fn] = sha
    return out


def discover_files(schema_dir: Path, only: Iterable[str] | None) -> list[SchemaFile]:
    if only:
        files = [schema_dir / name for name in only]
        missing = [p for p in files if not p.exists()]
        if missing:
            log.error("missing_files", missing=[str(m) for m in missing])
            sys.exit(1)
    else:
        files = sorted(
            p for p in schema_dir.glob("*.sql") if not p.name.startswith("_")
        )
    return [SchemaFile.from_path(p) for p in files]


def apply_file(
    client,
    sf: SchemaFile,
    applied_by: str,
    *,
    replicated: bool = False,
    cluster: str = "default",
    zk_prefix: str = "/clickhouse/tables",
    topology: SchemaTopology | None = None,
) -> int:
    """Apply one file. Returns the number of statements executed.

    When `replicated=True`, each statement is passed through
    `rewrite_for_replicated` to swap engines and append ON CLUSTER. The
    rewrite is deterministic — the schema_versions row records the
    ORIGINAL file sha256 (not the rewritten content), so re-applying with
    the same validated topology continues to be a no-op via the existing
    drift-detection path. Local and replicated histories never share an
    identity.
    """
    expected_topology = SchemaTopology.from_options(
        replicated=replicated,
        cluster=cluster if replicated else None,
        zk_table_path_prefix=zk_prefix if replicated else None,
    )
    if topology is not None and topology != expected_topology:
        raise SchemaTopologyError(
            "schema apply topology identity does not match rewrite topology"
        )
    topology = topology or expected_topology
    sql = sf.path.read_text()
    statements = split_statements(sql)
    log.info(
        "apply_file_begin",
        file=sf.path.name,
        sha256=sf.sha256[:12],
        statements=len(statements),
        replicated=replicated,
    )
    for i, stmt in enumerate(statements, 1):
        if replicated:
            table_name = _extract_table_name(stmt)
            if table_name:
                stmt = rewrite_for_replicated(
                    stmt,
                    table_name=table_name,
                    cluster=cluster,
                    zk_prefix=zk_prefix,
                )
        first_line = stmt.splitlines()[0][:80]
        log.info(
            "apply_statement",
            file=sf.path.name,
            n=i,
            of=len(statements),
            preview=first_line,
        )
        try:
            client.command(stmt)
        except Exception as e:
            log.error(
                "statement_failed",
                file=sf.path.name,
                n=i,
                statement_preview=stmt[:500],
                err=str(e),
            )
            raise
    # Record successful apply
    client.insert(
        "schema_versions",
        [[sf.path.name, sf.sha256, applied_by, topology.version_identity]],
        column_names=["filename", "sha256", "applied_by", "notes"],
    )
    log.info(
        "apply_file_complete",
        file=sf.path.name,
        statements=len(statements),
        topology=topology.version_identity,
    )
    return len(statements)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    args = build_argparser().parse_args(argv)

    try:
        topology = SchemaTopology.from_options(
            replicated=args.replicated,
            cluster=args.cluster,
            zk_table_path_prefix=args.zk_table_path_prefix,
            require_replicated=is_hosted_production(
                os.environ.get("ENV_TYPE", ""),
                os.environ.get("CLOUD_DEPLOYMENT", ""),
            ),
        )
    except SchemaTopologyError as exc:
        log.error("invalid_schema_topology", error=str(exc))
        return 1

    log.info(
        "connect",
        host=args.ch_host,
        port=args.ch_http_port,
        database=args.ch_database,
        topology=topology.version_identity,
    )
    client = clickhouse_connect.get_client(
        host=args.ch_host,
        port=args.ch_http_port,
        username=args.ch_user,
        password=args.ch_password,
        database=args.ch_database,
        # Sane request settings for DDL
        send_receive_timeout=120,
    )

    if topology.replicated:
        try:
            conflicts = find_local_merge_tree_conflicts(
                client,
                database=args.ch_database,
                cluster=str(topology.cluster),
            )
        except Exception as exc:
            log.error(
                "replicated_topology_preflight_failed",
                database=args.ch_database,
                cluster=topology.cluster,
                error_type=type(exc).__name__,
            )
            return 3
        if conflicts:
            log.error(
                "replicated_local_mergetree_conflict",
                database=args.ch_database,
                topology=topology.version_identity,
                conflicts=[conflict.as_log_dict() for conflict in conflicts],
                hint=(
                    "refusing CREATE IF NOT EXISTS over local catalog/version "
                    "tables; migrate or remove only those explicit targets first"
                ),
            )
            return 3

    # Always make sure the versions table exists. In replicated mode this
    # also fans out via ON CLUSTER so all replicas share an apply history.
    ensure_versions_table(
        client,
        replicated=topology.replicated,
        cluster=str(topology.cluster or "default"),
        zk_prefix=str(topology.zk_table_path_prefix or "/clickhouse/tables"),
    )

    if args.status:
        applied = fetch_applied(client, topology=topology)
        log.info("status_topology", topology=topology.version_identity)
        if not applied:
            log.info("status_empty", topology=topology.version_identity)
        for fn, sha in sorted(applied.items()):
            log.info(
                "status_applied",
                file=fn,
                sha256=sha[:12],
                topology=topology.version_identity,
            )
        return 0

    files = discover_files(args.schema_dir, args.files)
    if not files:
        log.warning("no_schema_files", dir=str(args.schema_dir))
        return 0

    applied = fetch_applied(client, topology=topology)

    user = os.environ.get("FI_MIGRATION_USER", os.environ.get("USER", "unknown"))

    # Walk files in their (already lexically sorted) order from discover_files.
    # Decide per-file: skip / apply-as-new / drifted. With --force, drifted files
    # are queued IN PLACE so the final to_apply list is still in lexical order
    # (codex P2: appending drifted at the end caused 002 to run after 005).
    drift = []
    to_apply: list[SchemaFile] = []
    for sf in files:
        prior_sha = applied.get(sf.path.name)
        if prior_sha is None:
            to_apply.append(sf)
        elif prior_sha != sf.sha256:
            drift.append((sf, prior_sha))
            if args.force:
                to_apply.append(sf)
        else:
            log.info("skip_already_applied", file=sf.path.name, sha256=sf.sha256[:12])

    if drift and not args.force:
        for sf, prior in drift:
            log.error(
                "drift_detected",
                file=sf.path.name,
                prior_sha=prior[:12],
                current_sha=sf.sha256[:12],
                hint="rerun with --force after writing a DECISIONS.md entry justifying the schema edit",
            )
        return 2

    if not to_apply:
        log.info("nothing_to_apply")
        return 0

    if topology.replicated:
        log.info(
            "replicated_mode",
            cluster=topology.cluster,
            zk_prefix=topology.zk_table_path_prefix,
            topology=topology.version_identity,
            note="engines will be rewritten to Replicated* and ON CLUSTER appended",
        )

    total_stmts = 0
    t0 = time.time()
    for sf in to_apply:
        try:
            total_stmts += apply_file(
                client,
                sf,
                user,
                replicated=topology.replicated,
                cluster=str(topology.cluster or "default"),
                zk_prefix=str(topology.zk_table_path_prefix or "/clickhouse/tables"),
                topology=topology,
            )
        except Exception as e:
            log.error("apply_aborted", file=sf.path.name, err=str(e))
            return 3
    log.info(
        "apply_complete",
        files=len(to_apply),
        statements=total_stmts,
        elapsed_sec=round(time.time() - t0, 2),
        topology=topology.version_identity,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
