"""Reference-aware lifecycle of ``trajectory_items``: GC, audit and erasure.

Items are content-addressed and shared by every span that points at them, so an
item's own age says nothing about whether a span still needs it. Deletion is
derived from the spans that are physically present instead.

GC rule. For project P, with M = GC_MARGIN (72 h):

    C_P = least(min(created_at) over P's physical span rows, now) - M

and copies with ``persisted_at < C_P`` are deleted. A span stored at
``created_at = c`` may only reuse a copy persisted at or after
``c - MAX_REUSE_AGE_CAP - IN_FLIGHT_MARGIN`` (the collector's reuse bound), which
is at or after ``min(created_at) - M = C_P``. GC therefore removes only copies
older than every span that could reference them. It never reads retention
policy, so hosted and OSS installs behave the same.

GC deletes only when all of these hold, checked in this order:

1. ``FI_TRAJECTORY_GC_DELETES_ENABLED`` is on (operator switch, default off;
   with it off GC issues no ClickHouse or Redis statement).
2. 040 is applied (``trajectory_items`` and the six span pointer columns).
3. The topology is one shard (local, or replicated with a known cluster whose
   host count matches the replica count) and no Distributed table fronts
   ``spans`` or ``trajectory_items``.
4. Replicated only: every replica of both tables is present, writable, in a
   live Keeper session, and no more than ``MAX_REPLICA_DELAY_SECONDS`` or
   ``MAX_REPLICA_INSERTS_IN_QUEUE`` behind. Every read a cut or the audit
   depends on also reads every replica (``clusterAllReplicas``): the
   oldest-span probe, and the audit's span sample and item lookup. A replica
   that is still fetching ``spans`` parts can then neither make a cut too new
   nor hide a violating span; the DELETE replicates. The reads that stay on
   the connected replica (candidate items, the ``LIMIT 1`` early-span check)
   can only hold a project back.
5. ``traj:contract:v2`` exists: some collector enforces the reuse bound.
6. ``traj:gc:latch`` is absent: no earlier run found a reference violation
   that an operator has not cleared since.
7. The reference audit of recent ``items_v1`` spans finds no missing item and
   no item older than the bound allows. A violation also sets
   ``traj:gc:latch``, so GC stays stopped after the violating span leaves the
   audit window. The audit runs even while 8 holds GC back, so reuse by a
   legacy collector past the bound latches GC as well.
8. No legacy ``traj:seen:<pid>:<scope>`` key exists, and none has been seen,
   with the contract key present, for ``LEGACY_QUIET_PERIOD`` (8 days,
   ``traj:gc:quiet_since``). A legacy pod reuses from its local LRU for up to
   7 days after its key is gone, so the absence of keys alone proves nothing.
   The period is measured by GC itself, so it starts when the switch is
   turned on.

Otherwise GC deletes nothing that run (latched, refused or inactive).

Erasure deletes all of one project's items once no replica holds a span row
for it (``tfc.temporal.trajectory_erasure``). It is not gated on the switch,
the latch or the read flag.

Every ClickHouse statement here is bounded: GC runs once a day, at most one
DELETE per partition bucket (more only past ``PROJECTS_PER_STATEMENT``
projects), and the deleted mask is applied to at most ``mask_limit``
partitions per run.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any

import structlog
from django.conf import settings

from tracer.services.clickhouse.v2.trajectory_hydration import (
    PROJECT_HASH_PREFIX,
    PROJECT_SCOPE_ID,
    TRAJECTORY_SPAN_COLUMNS,
)
from tracer.services.trajectory_seen_contract import (
    GC_MARGIN,
    LEGACY_QUIET_PERIOD,
    canonical_project_id,
    clear_gc_latch,
    contract_key_present,
    end_quiet_period,
    gc_latch_present,
    legacy_seen_keys_present,
    open_seen_redis,
    set_gc_latch,
    start_quiet_period,
)

logger = structlog.get_logger(__name__)

Execute = Callable[..., list]

BUCKETS = 32
ITEMS_TABLE = "trajectory_items"
SPANS_TABLE = "spans"
# Spans sampled per bucket by the reference audit.
AUDIT_SPANS_PER_BUCKET = 2000
# The audit looks at spans created in this window ...
AUDIT_WINDOW = timedelta(days=2)
# ... except the newest ones, whose items may still be replicating.
AUDIT_SETTLE = timedelta(minutes=15)
# Spans probe reads span rows created before min(item persisted_at) + M + this.
PROBE_LOOKAHEAD = timedelta(days=30)
PROJECTS_PER_STATEMENT = 500
# At most this many GC DELETE statements per run; the rest wait for the next
# daily run (the cuts only move forward, so nothing is lost by waiting).
MAX_DELETE_STATEMENTS_PER_RUN = 64
SCOPES_PER_LOOKUP = 500
DEFAULT_MASK_PARTITIONS_PER_RUN = 4
# Replicated GC refuses while any replica is further behind than this ...
MAX_REPLICA_DELAY_SECONDS = 300
# ... or has more part fetches queued (a replica being rebuilt has many).
MAX_REPLICA_INSERTS_IN_QUEUE = 100
# Erasure waits at least the collector's auth cache_ttl (5m) plus warm_ttl
# (1h, a stale entry served while a refresh fails) plus one minute.
ERASURE_MIN_INGEST_WAIT_SECONDS = 3960

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _us(delta: timedelta) -> int:
    return int(delta.total_seconds() * 1_000_000)


def gc_deletes_enabled() -> bool:
    return bool(getattr(settings, "FI_TRAJECTORY_GC_DELETES_ENABLED", False))


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


def open_executor() -> tuple[Execute, Callable[[], None]]:
    """``(execute, close)`` on the v2 ClickHouse for scheduled and operator runs."""
    from clickhouse_driver import Client

    from tracer.services.clickhouse.v2 import get_v2_config

    config = get_v2_config()
    client = Client(
        host=config["host"],
        port=config["tcp_port"],
        user=config["user"],
        password=config["password"],
        database=config["database"],
        connect_timeout=10,
        send_receive_timeout=3600,
    )
    query_settings = {"max_execution_time": 3600, "use_query_cache": 0}

    def execute(sql: str, params: dict | None = None) -> list:
        return list(client.execute(sql, params or {}, settings=query_settings))

    return execute, client.disconnect


@contextmanager
def _executor(execute: Execute | None) -> Iterator[Execute]:
    if execute is not None:
        yield execute
        return
    run, close = open_executor()
    try:
        yield run
    finally:
        close()


# ---------------------------------------------------------------------------
# Schema and topology
# ---------------------------------------------------------------------------


def items_table_exists(execute: Execute) -> bool:
    rows = execute(
        "SELECT count() FROM system.tables "
        "WHERE database = currentDatabase() AND name = %(table)s",
        {"table": ITEMS_TABLE},
    )
    return bool(rows and rows[0][0])


def store_present(execute: Execute) -> bool:
    """040 is fully applied: the items table and all six span pointer columns."""
    if not items_table_exists(execute):
        return False
    rows = execute(
        "SELECT name FROM system.columns WHERE database = currentDatabase() "
        "AND table = %(table)s AND name IN %(names)s",
        {"table": SPANS_TABLE, "names": tuple(TRAJECTORY_SPAN_COLUMNS)},
    )
    return {row[0] for row in rows} == set(TRAJECTORY_SPAN_COLUMNS)


@dataclass(frozen=True)
class Topology:
    replicated: bool
    cluster: str | None = None
    refusal: str | None = None
    # Replicated only: hosts in ``cluster`` and the database the tables are in.
    hosts: int = 0
    database: str = ""


def current_database(execute: Execute) -> str:
    name = str(execute("SELECT currentDatabase()")[0][0])
    if not _IDENTIFIER.match(name):
        raise ValueError("unexpected ClickHouse database name")
    return name


def resolve_topology(execute: Execute) -> Topology:
    """How rows of ``spans`` and ``trajectory_items`` are laid out.

    Lifecycle jobs support one shard only: local tables, or replicated tables
    on one single-shard cluster. Anything else, or a cluster that cannot be
    identified, is a refusal (the caller deletes nothing).
    """
    distributed = execute(
        "SELECT name, engine_full FROM system.tables "
        "WHERE database = currentDatabase() AND engine = 'Distributed'"
    )
    for name, engine_full in distributed:
        text = str(engine_full or "")
        if name in (SPANS_TABLE, ITEMS_TABLE) or any(
            f"'{table}'" in text for table in (SPANS_TABLE, ITEMS_TABLE)
        ):
            return Topology(replicated=False, refusal="distributed_table")

    engines = dict(
        execute(
            "SELECT name, engine FROM system.tables "
            "WHERE database = currentDatabase() AND name IN %(tables)s",
            {"tables": (SPANS_TABLE, ITEMS_TABLE)},
        )
    )
    kinds = {str(engine).startswith("Replicated") for engine in engines.values()}
    if len(kinds) > 1:
        return Topology(replicated=False, refusal="mixed_replication")
    if not kinds or kinds == {False}:
        return Topology(replicated=False)

    total = execute(
        "SELECT max(total_replicas) FROM system.replicas "
        "WHERE database = currentDatabase() AND table IN %(tables)s",
        {"tables": (SPANS_TABLE, ITEMS_TABLE)},
    )
    replicas = int(total[0][0] or 0) if total else 0
    clusters: dict[str, tuple[int, int, tuple[str, ...]]] = {
        str(name): (int(shards), int(hosts), tuple(str(m) for m in members))
        for name, shards, hosts, members in execute(
            "SELECT cluster, uniqExact(shard_num), count(), "
            "arraySort(groupArray(concat(host_name, ':', toString(port)))) "
            "FROM system.clusters "
            "GROUP BY cluster HAVING countIf(is_local) > 0"
        )
    }
    database = current_database(execute)
    configured = str(getattr(settings, "FI_TRAJECTORY_CH_CLUSTER", "") or "")
    if configured:
        if configured not in clusters:
            return Topology(replicated=True, refusal="cluster_not_found")
        shards, hosts, _members = clusters[configured]
        if shards != 1:
            return Topology(replicated=True, refusal="multi_shard")
        if hosts != replicas:
            # clusterAllReplicas over it would miss a replica, or count a host
            # that holds none of these tables.
            return Topology(replicated=True, refusal="cluster_replica_mismatch")
        return Topology(
            replicated=True, cluster=configured, hosts=hosts, database=database
        )
    candidates = sorted(
        name
        for name, (shards, hosts, _members) in clusters.items()
        if shards == 1 and hosts == replicas
    )
    if not candidates:
        return Topology(replicated=True, refusal="cluster_unknown")
    if len({clusters[name][2] for name in candidates}) > 1:
        # Clusters over different hosts fit equally well; set
        # FI_TRAJECTORY_CH_CLUSTER instead of guessing.
        return Topology(replicated=True, refusal="cluster_ambiguous")
    return Topology(
        replicated=True,
        cluster=candidates[0],
        hosts=clusters[candidates[0]][1],
        database=database,
    )


_REPLICA_HEALTH_SQL = (
    "SELECT count(), countIf(is_readonly OR is_session_expired "
    "OR active_replicas < total_replicas "
    "OR absolute_delay > %(max_delay)s OR inserts_in_queue > %(max_inserts)s) "
    "FROM clusterAllReplicas(%(cluster)s, system.replicas) "
    "WHERE database = %(database)s AND table IN %(tables)s "
    "SETTINGS skip_unavailable_shards = 0"
)


def replica_health_refusal(execute: Execute, topology: Topology) -> str | None:
    """Why the replicas of ``spans`` and ``trajectory_items`` are not fit for GC.

    None for local tables, or when every host of the cluster holds both tables
    and none is read-only, out of its Keeper session, missing a replica, or
    behind. GC reads part of its input from one replica while its DELETE goes
    to all of them, so it must not run while any replica could be missing
    rows. An unreachable replica fails the query, which also stops GC.
    """
    if not topology.replicated:
        return None
    rows = execute(
        _REPLICA_HEALTH_SQL,
        {
            "cluster": topology.cluster or "",
            "database": topology.database,
            "tables": (SPANS_TABLE, ITEMS_TABLE),
            "max_delay": MAX_REPLICA_DELAY_SECONDS,
            "max_inserts": MAX_REPLICA_INSERTS_IN_QUEUE,
        },
    )
    present, unhealthy = (int(rows[0][0]), int(rows[0][1])) if rows else (0, 0)
    if present != 2 * topology.hosts:
        return "replica_missing"
    if unhealthy:
        return "replica_unhealthy"
    return None


_REPLICA_COUNT_SQL = (
    "SELECT count() FROM clusterAllReplicas(%(cluster)s, %(database)s, %(table)s) "
    "WHERE project_id = toUUID(%(project)s)"
)
_LOCAL_COUNT_SQL = {
    SPANS_TABLE: "SELECT count() FROM spans WHERE project_id = toUUID(%(project)s)",
    ITEMS_TABLE: (
        "SELECT count() FROM trajectory_items WHERE project_id = toUUID(%(project)s)"
    ),
}


def _count_project_rows(
    execute: Execute, topology: Topology, table: str, project_id: str
) -> int:
    """Physical rows of ``table`` for one project, on every replica."""
    params: dict[str, Any] = {"project": project_id}
    if topology.replicated:
        params.update(
            cluster=topology.cluster or "",
            database=topology.database or current_database(execute),
            table=table,
        )
        sql = _REPLICA_COUNT_SQL
    else:
        sql = _LOCAL_COUNT_SQL[table]
    rows = execute(sql, params)
    return int(rows[0][0]) if rows else 0


def server_now_us(execute: Execute) -> int:
    return int(execute("SELECT toUnixTimestamp64Micro(now64(6, 'UTC'))")[0][0])


# ---------------------------------------------------------------------------
# Reference audit (the GC latch; also trajectory_audit)
# ---------------------------------------------------------------------------


@dataclass
class AuditReport:
    spans_sampled: int = 0
    references: int = 0
    missing: int = 0
    stale: int = 0
    buckets: int = 0

    @property
    def violations(self) -> int:
        return self.missing + self.stale

    def as_dict(self) -> dict[str, int]:
        return {**asdict(self), "violations": self.violations}


def _span_refs(
    project_id: str,
    scope_id: str,
    hashes: Sequence[str],
) -> Iterator[tuple[str, str, str]]:
    for value in hashes:
        text = str(value or "")
        if not text:
            continue
        if text.startswith(PROJECT_HASH_PREFIX):
            yield project_id, PROJECT_SCOPE_ID, text[len(PROJECT_HASH_PREFIX) :]
        elif scope_id:
            yield project_id, scope_id, text


def _replica_params(topology: Topology | None, table: str) -> dict[str, Any]:
    if topology is None or not topology.replicated:
        return {}
    return {
        "cluster": topology.cluster or "",
        "database": topology.database,
        "table": table,
    }


def _audit_bucket(
    execute: Execute,
    bucket: int,
    *,
    since_us: int,
    until_us: int,
    limit: int,
    topology: Topology | None = None,
) -> AuditReport:
    replicated = bool(topology and topology.replicated)
    params: dict[str, Any] = {
        "bucket": bucket,
        "since": since_us,
        "until": until_us,
        "limit": limit,
        **_replica_params(topology, SPANS_TABLE),
    }
    if replicated:
        # Every replica: one that is behind must not hide a violating span.
        rows = execute(
            "SELECT toString(project_id), item_scope_id, "
            "toUnixTimestamp64Micro(created_at), "
            "arrayConcat(input_item_hashes, output_item_hashes, tool_def_hashes, "
            "[JSONExtractString(payload_envelope, 'in', 'sh'), "
            "JSONExtractString(payload_envelope, 'out', 'sh')]) "
            "FROM clusterAllReplicas(%(cluster)s, %(database)s, %(table)s) "
            "WHERE payload_encoding = 'items_v1' "
            "AND modulo(cityHash64(project_id), 32) = %(bucket)s "
            "AND created_at >= fromUnixTimestamp64Micro(%(since)s, 'UTC') "
            "AND created_at < fromUnixTimestamp64Micro(%(until)s, 'UTC') "
            "LIMIT %(limit)s SETTINGS skip_unavailable_shards = 0",
            params,
        )
    else:
        rows = execute(
            "SELECT toString(project_id), item_scope_id, "
            "toUnixTimestamp64Micro(created_at), "
            "arrayConcat(input_item_hashes, output_item_hashes, tool_def_hashes, "
            "[JSONExtractString(payload_envelope, 'in', 'sh'), "
            "JSONExtractString(payload_envelope, 'out', 'sh')]) "
            "FROM spans "
            "WHERE payload_encoding = 'items_v1' "
            "AND modulo(cityHash64(project_id), 32) = %(bucket)s "
            "AND created_at >= fromUnixTimestamp64Micro(%(since)s, 'UTC') "
            "AND created_at < fromUnixTimestamp64Micro(%(until)s, 'UTC') "
            "LIMIT %(limit)s",
            params,
        )
    report = AuditReport(spans_sampled=len(rows), buckets=1)
    # Newest span created_at per referenced key: the strictest age bound.
    newest_ref: dict[tuple[str, str, str], int] = {}
    for project_id, scope_id, created_us, hashes in rows:
        for key in _span_refs(str(project_id), str(scope_id or ""), hashes):
            newest_ref[key] = max(newest_ref.get(key, 0), int(created_us))
    if not newest_ref:
        return report

    persisted: dict[tuple[str, str, str], int] = {}
    projects = sorted({key[0] for key in newest_ref})
    scopes = sorted({key[1] for key in newest_ref})
    for start in range(0, len(scopes), SCOPES_PER_LOOKUP):
        lookup: dict[str, Any] = {
            "bucket": bucket,
            "partition": str(bucket),
            "projects": tuple(projects),
            "scopes": tuple(scopes[start : start + SCOPES_PER_LOOKUP]),
            **_replica_params(topology, ITEMS_TABLE),
        }
        if replicated:
            # The newest copy on any replica: a replica that has not fetched
            # it yet must not report the item missing.
            found = execute(
                "SELECT toString(project_id), scope_id, toString(item_hash), "
                "toUnixTimestamp64Micro(max(persisted_at)) "
                "FROM clusterAllReplicas(%(cluster)s, %(database)s, %(table)s) "
                "WHERE modulo(cityHash64(project_id), 32) = %(bucket)s "
                "AND project_id IN %(projects)s AND scope_id IN %(scopes)s "
                "GROUP BY project_id, scope_id, item_hash "
                "SETTINGS skip_unavailable_shards = 0",
                lookup,
            )
        else:
            found = execute(
                "SELECT toString(project_id), scope_id, toString(item_hash), "
                "toUnixTimestamp64Micro(max(persisted_at)) "
                "FROM trajectory_items "
                "WHERE _partition_id = %(partition)s "
                "AND project_id IN %(projects)s AND scope_id IN %(scopes)s "
                "GROUP BY project_id, scope_id, item_hash",
                lookup,
            )
        for project_id, scope_id, item_hash, newest_us in found:
            key = (str(project_id), str(scope_id), str(item_hash).rstrip("\x00"))
            persisted[key] = max(persisted.get(key, 0), int(newest_us))

    bound = _us(GC_MARGIN)
    report.references = len(newest_ref)
    for key, created_us in newest_ref.items():
        stored = persisted.get(key)
        if stored is None:
            report.missing += 1
        elif stored < created_us - bound:
            report.stale += 1
    return report


def audit_references(
    execute: Execute,
    *,
    since_us: int,
    until_us: int,
    limit: int = AUDIT_SPANS_PER_BUCKET,
    topology: Topology | None = None,
) -> AuditReport:
    """Check that recent ``items_v1`` spans point only at present, fresh items.

    Per bucket, up to ``limit`` spans created in ``[since, until)`` are read.
    A violation is a referenced key with no item row (missing), or whose newest
    copy was persisted more than GC_MARGIN before the newest span that
    references it (stale: some collector reused past the bound). Counts only.
    On a replicated ``topology`` both reads cover every replica.
    """
    total = AuditReport()
    for bucket in range(BUCKETS):
        part = _audit_bucket(
            execute,
            bucket,
            since_us=since_us,
            until_us=until_us,
            limit=limit,
            topology=topology,
        )
        total.spans_sampled += part.spans_sampled
        total.references += part.references
        total.missing += part.missing
        total.stale += part.stale
        total.buckets += 1
    return total


def _recent_items_v1_spans(execute: Execute, *, since_us: int) -> bool:
    rows = execute(
        "SELECT count() FROM (SELECT 1 FROM spans "
        "WHERE payload_encoding = 'items_v1' "
        "AND created_at >= fromUnixTimestamp64Micro(%(since)s, 'UTC') LIMIT 1)",
        {"since": since_us},
    )
    return bool(rows and rows[0][0])


def run_audit(
    *, execute: Execute | None = None, since_us: int | None = None
) -> dict[str, Any]:
    """Report-only audit for ``manage.py trajectory_audit``.

    On replicated tables it reads every replica, so a clean report cannot come
    from a replica that lacks the violating spans. ``refused`` when the
    topology is not supported.
    """
    with _executor(execute) as run:
        if not store_present(run):
            return {"status": "absent"}
        topology = resolve_topology(run)
        if topology.refusal:
            return {"status": "refused", "reason": topology.refusal}
        now_us = server_now_us(run)
        since = now_us - _us(AUDIT_WINDOW) if since_us is None else since_us
        report = audit_references(
            run,
            since_us=since,
            until_us=now_us - _us(AUDIT_SETTLE),
            topology=topology,
        )
        return {
            "status": "ok" if not report.violations else "violations",
            **report.as_dict(),
        }


# ---------------------------------------------------------------------------
# GC
# ---------------------------------------------------------------------------


@dataclass
class GcReport:
    status: str
    buckets: int = 0
    projects_considered: int = 0
    # Projects with a span within GC_MARGIN of their oldest item: nothing of
    # theirs can be deleted, so the oldest-span probe skips them.
    projects_held: int = 0
    projects_cut: int = 0
    delete_statements: int = 0
    masked_partitions: int = 0
    audit: dict[str, int] = field(default_factory=dict)
    reason: str | None = None
    capped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _chunks(values: Sequence, size: int) -> Iterator[Sequence]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


# Whether a project has any span row created at or before a time (local
# replica). A hit only holds the project back, so one replica is enough.
_EARLY_SPAN_SQL = (
    "SELECT count() FROM (SELECT 1 FROM spans "
    "WHERE project_id = toUUID(%(project)s) "
    "AND created_at <= fromUnixTimestamp64Micro(%(until)s, 'UTC') LIMIT 1)"
)
# Oldest span created_at per project below the horizon. Replicated: the
# minimum over every replica, so a replica missing rows cannot raise it.
_OLDEST_SPAN_SQL = {
    False: (
        "SELECT toString(project_id), toUnixTimestamp64Micro(min(created_at)) "
        "FROM spans WHERE project_id IN %(projects)s "
        "AND created_at < fromUnixTimestamp64Micro(%(horizon)s, 'UTC') "
        "GROUP BY project_id"
    ),
    True: (
        "SELECT toString(project_id), toUnixTimestamp64Micro(min(created_at)) "
        "FROM clusterAllReplicas(%(cluster)s, %(database)s, %(table)s) "
        "WHERE project_id IN %(projects)s "
        "AND created_at < fromUnixTimestamp64Micro(%(horizon)s, 'UTC') "
        "GROUP BY project_id SETTINGS skip_unavailable_shards = 0"
    ),
}


def _has_span_at_or_before(execute: Execute, project_id: str, until_us: int) -> bool:
    rows = execute(_EARLY_SPAN_SQL, {"project": project_id, "until": until_us})
    return bool(rows and rows[0][0])


def _gc_bucket(
    execute: Execute,
    bucket: int,
    *,
    now_us: int,
    report: GcReport,
    topology: Topology | None = None,
) -> None:
    margin = _us(GC_MARGIN)
    replicated = bool(topology and topology.replicated)
    candidates = execute(
        "SELECT toString(project_id), toUnixTimestamp64Micro(min(persisted_at)) "
        "FROM trajectory_items WHERE _partition_id = %(partition)s "
        "GROUP BY project_id "
        "HAVING min(persisted_at) < fromUnixTimestamp64Micro(%(older)s, 'UTC') "
        "ORDER BY project_id",
        {"partition": str(bucket), "older": now_us - margin},
    )
    report.projects_considered += len(candidates)
    for chunk in _chunks(candidates, PROJECTS_PER_STATEMENT):
        if report.delete_statements >= MAX_DELETE_STATEMENTS_PER_RUN:
            report.capped = True
            return
        # A span at or before (oldest item + M) puts the project's cut at or
        # below its oldest item, so nothing of it can go. Checking that with
        # LIMIT 1 per project keeps the probe below from reading the whole
        # span history of long-lived projects every day.
        oldest_item: dict[str, int] = {}
        for pid, min_us in chunk:
            if _has_span_at_or_before(execute, str(pid), int(min_us) + margin):
                report.projects_held += 1
            else:
                oldest_item[str(pid)] = int(min_us)
        if not oldest_item:
            continue
        horizon = {
            pid: min(min_us + margin + _us(PROBE_LOOKAHEAD), now_us)
            for pid, min_us in oldest_item.items()
        }
        params: dict[str, Any] = {
            "projects": tuple(oldest_item),
            "horizon": max(horizon.values()),
            **_replica_params(topology, SPANS_TABLE),
        }
        observed = {
            str(pid): int(min_us)
            for pid, min_us in execute(_OLDEST_SPAN_SQL[replicated], params)
        }
        cuts: dict[str, int] = {}
        for pid, min_item_us in oldest_item.items():
            # No span below the horizon: the horizon is a lower bound on the
            # oldest span, so using it keeps the cut conservative.
            oldest_span = min(observed.get(pid, horizon[pid]), horizon[pid])
            cut = min(oldest_span, now_us) - margin
            if min_item_us < cut:
                cuts[pid] = cut
        if not cuts:
            continue
        pids = sorted(cuts)
        execute(
            "DELETE FROM trajectory_items "
            "WHERE modulo(cityHash64(project_id), 32) = %(bucket)s "
            "AND project_id IN %(projects)s "
            "AND toUnixTimestamp64Micro(persisted_at) < transform("
            "toString(project_id), %(project_keys)s, "
            "CAST(%(cuts)s, 'Array(Int64)'), toInt64(0)) "
            "SETTINGS lightweight_deletes_sync = 2",
            {
                "bucket": bucket,
                "projects": tuple(pids),
                "project_keys": pids,
                "cuts": [cuts[pid] for pid in pids],
            },
        )
        report.projects_cut += len(pids)
        report.delete_statements += 1


def masked_item_partitions(execute: Execute, *, limit: int) -> list[str]:
    """Partitions with lightweight-deleted rows, the longest-waiting first.

    Oldest masked part first, so the same large partitions cannot take every
    slot day after day.
    """
    if limit <= 0:
        return []
    rows = execute(
        "SELECT partition_id FROM system.parts "
        "WHERE database = currentDatabase() AND table = %(table)s "
        "AND active AND has_lightweight_delete "
        "GROUP BY partition_id ORDER BY min(modification_time), partition_id "
        "LIMIT %(limit)s",
        {"table": ITEMS_TABLE, "limit": int(limit)},
    )
    return [str(row[0]) for row in rows]


def apply_item_masks(execute: Execute, partitions: Sequence[str]) -> int:
    """Physically remove lightweight-deleted item rows, one partition at a time."""
    done = 0
    for partition in partitions:
        if not str(partition).isdigit() or not 0 <= int(partition) < BUCKETS:
            continue
        execute(
            "ALTER TABLE trajectory_items APPLY DELETED MASK "
            "IN PARTITION ID %(partition)s SETTINGS mutations_sync = 2",
            {"partition": str(partition)},
        )
        done += 1
    return done


def trajectory_items_gc(
    *,
    execute: Execute | None = None,
    redis_client: Any = None,
    progress: Callable[[int], None] | None = None,
    mask_limit: int = DEFAULT_MASK_PARTITIONS_PER_RUN,
) -> dict[str, Any]:
    """The daily reference-aware GC. Returns a counts-only report."""
    if not gc_deletes_enabled():
        logger.info("trajectory_gc_deletes_disabled")
        return GcReport(status="deletes_disabled").as_dict()

    with _executor(execute) as run:
        if not store_present(run):
            logger.info("trajectory_gc_skipped", reason="store_absent")
            return GcReport(status="absent").as_dict()
        topology = resolve_topology(run)
        refusal = topology.refusal or replica_health_refusal(run, topology)
        if refusal:
            logger.error("trajectory_gc_refused", reason=refusal)
            return GcReport(status="refused", reason=refusal).as_dict()

        now_us = server_now_us(run)
        with _seen_client(redis_client) as seen:
            blocked, audit = _gc_gate(run, seen, now_us=now_us, topology=topology)
        if blocked is not None:
            return blocked.as_dict()

        report = GcReport(status="ok", audit=audit.as_dict())
        for bucket in range(BUCKETS):
            _gc_bucket(run, bucket, now_us=now_us, report=report, topology=topology)
            report.buckets += 1
            if progress is not None:
                progress(bucket)
        report.masked_partitions = apply_item_masks(
            run, masked_item_partitions(run, limit=mask_limit)
        )
        logger.info("trajectory_gc_completed", **report.as_dict())
        return report.as_dict()


def _gc_gate(
    run: Execute, seen: Any, *, now_us: int, topology: Topology | None = None
) -> tuple[GcReport | None, AuditReport]:
    """Steps 5-8 of the GC rule. Returns (report that stops GC, audit)."""
    since_us = now_us - _us(AUDIT_WINDOW)
    if not contract_key_present(seen):
        # No collector vouches for the bound: the quiet period starts over.
        end_quiet_period(seen)
        if _recent_items_v1_spans(run, since_us=since_us):
            logger.error("trajectory_gc_latched", reason="contract_key_absent")
            return GcReport(status="latched", reason="contract_key_absent"), (
                AuditReport()
            )
        logger.info("trajectory_gc_inactive", reason="contract_key_absent")
        return GcReport(status="inactive", reason="contract_key_absent"), (
            AuditReport()
        )
    if gc_latch_present(seen):
        logger.error("trajectory_gc_latched", reason="persisted_latch")
        return GcReport(status="latched", reason="persisted_latch"), AuditReport()
    legacy = legacy_seen_keys_present(seen)
    quiet_since: int | None = None
    if legacy:
        end_quiet_period(seen)
    else:
        quiet_since = start_quiet_period(seen, now_us=now_us)

    # Audit before the legacy checks below can stop GC, so a legacy collector
    # reusing past the bound sets the persistent latch too.
    audit = audit_references(
        run,
        since_us=since_us,
        until_us=now_us - _us(AUDIT_SETTLE),
        topology=topology,
    )
    if audit.violations:
        persisted = set_gc_latch(
            seen, reason="reference_violation", at_us=now_us, counts=audit.as_dict()
        )
        logger.error(
            "trajectory_gc_latched",
            reason="reference_violation",
            latch_persisted=persisted,
            **audit.as_dict(),
        )
        return (
            GcReport(
                status="latched", reason="reference_violation", audit=audit.as_dict()
            ),
            audit,
        )
    if legacy:
        logger.error("trajectory_gc_latched", reason="legacy_seen_keys")
        return (
            GcReport(
                status="latched", reason="legacy_seen_keys", audit=audit.as_dict()
            ),
            audit,
        )
    if quiet_since is None or now_us - quiet_since < _us(LEGACY_QUIET_PERIOD):
        # Absent or unreadable mark, or a legacy pod-local LRU may still reuse.
        logger.info(
            "trajectory_gc_waiting",
            reason="legacy_quiet_period",
            quiet_since_us=quiet_since,
        )
        return (
            GcReport(
                status="waiting",
                reason="legacy_quiet_period",
                audit=audit.as_dict(),
            ),
            audit,
        )
    return None, audit


@contextmanager
def _seen_client(redis_client: Any) -> Iterator[Any]:
    if redis_client is not None:
        yield redis_client
        return
    from redis.exceptions import RedisError

    client = open_seen_redis()
    try:
        yield client
    finally:
        with contextlib.suppress(RedisError, OSError):
            client.close()


def clear_trajectory_gc_latch(*, redis_client: Any = None) -> bool:
    """Remove ``traj:gc:latch`` (operator action). Returns whether it was set."""
    with _seen_client(redis_client) as seen:
        removed = clear_gc_latch(seen)
    logger.warning("trajectory_gc_latch_cleared", was_set=removed)
    return removed


# ---------------------------------------------------------------------------
# Erasure steps (activities in tracer.tasks.trajectory_lifecycle)
# ---------------------------------------------------------------------------


class TrajectoryErasurePending(Exception):
    """A step's precondition does not hold yet; Temporal retries the step."""


def _project_is_live(project_id: str) -> bool:
    from tracer.models.project import Project

    return Project._base_manager.filter(pk=project_id, deleted=False).exists()


def erasure_gate(project_id: str, *, execute: Execute | None = None) -> dict[str, Any]:
    """Pass only when the project is gone from Postgres and every replica.

    Raises TrajectoryErasurePending otherwise (spans first, items after).
    """
    pid = canonical_project_id(project_id)
    if _project_is_live(pid):
        raise TrajectoryErasurePending("project_not_deleted")
    with _executor(execute) as run:
        topology = resolve_topology(run)
        if topology.refusal:
            raise TrajectoryErasurePending(f"topology_{topology.refusal}")
        spans = _count_project_rows(run, topology, SPANS_TABLE, pid)
        if spans:
            raise TrajectoryErasurePending("spans_present")
        return {"project_id": pid, "spans": 0}


def erase_project_items(
    project_id: str, *, execute: Execute | None = None
) -> dict[str, Any]:
    """Delete every copy of one project's items and apply the deleted mask.

    Idempotent: a rerun with nothing left issues no DELETE, and applies the
    mask only if the bucket still has masked parts.
    """
    pid = canonical_project_id(project_id)
    with _executor(execute) as run:
        if not items_table_exists(run):
            return {"project_id": pid, "rows": 0, "status": "absent"}
        topology = resolve_topology(run)
        if topology.refusal:
            raise TrajectoryErasurePending(f"topology_{topology.refusal}")
        bucket = int(
            run(
                "SELECT modulo(cityHash64(toUUID(%(project)s)), 32)",
                {"project": pid},
            )[0][0]
        )
        params = {"bucket": bucket, "project": pid}
        # Every replica: a lagging one may not have the rows yet, and a local
        # zero would skip the DELETE while verify still finds them elsewhere.
        rows = _count_project_rows(run, topology, ITEMS_TABLE, pid)
        statements = 0
        if rows:
            run(
                "DELETE FROM trajectory_items "
                "WHERE modulo(cityHash64(project_id), 32) = %(bucket)s "
                "AND project_id = toUUID(%(project)s) "
                "SETTINGS lightweight_deletes_sync = 2",
                params,
            )
            statements = 1
        masked = [
            partition
            for partition in masked_item_partitions(run, limit=BUCKETS)
            if partition == str(bucket)
        ]
        masks = apply_item_masks(run, masked)
        return {
            "project_id": pid,
            "rows": rows,
            "delete_statements": statements,
            "masked_partitions": masks,
            "status": "ok",
        }


def verify_project_erased(
    project_id: str, *, execute: Execute | None = None
) -> dict[str, Any]:
    """Raise unless no replica holds a span or item row; log the receipt."""
    pid = canonical_project_id(project_id)
    with _executor(execute) as run:
        topology = resolve_topology(run)
        if topology.refusal:
            raise TrajectoryErasurePending(f"topology_{topology.refusal}")
        spans = _count_project_rows(run, topology, SPANS_TABLE, pid)
        items = (
            _count_project_rows(run, topology, ITEMS_TABLE, pid)
            if items_table_exists(run)
            else 0
        )
        if spans or items:
            raise TrajectoryErasurePending("rows_present")
        now_us = server_now_us(run)
    receipt = {
        "project_id": pid,
        "spans": 0,
        "items": 0,
        "all_replicas": topology.replicated,
        "verified_at_us": now_us,
    }
    logger.info("trajectory_erasure_receipt", **receipt)
    return receipt


def request_trajectory_erasure(project_ids: Sequence[str]) -> list[str]:
    """Start (or join) one TrajectoryErasureWorkflow per project.

    Call it after the project's spans have been deleted, for example on commit
    of a hard delete. A request for a project whose erasure is running joins
    that run instead of restarting it. Returns the workflow ids.
    """
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

    from tfc.temporal.common.client import start_workflow_sync
    from tfc.temporal.trajectory_erasure import (
        ERASURE_QUEUE,
        TrajectoryErasureInput,
        TrajectoryErasureWorkflow,
        erasure_workflow_id,
    )

    wait = max(
        int(
            getattr(
                settings,
                "FI_TRAJECTORY_ERASURE_INGEST_WAIT_SECONDS",
                ERASURE_MIN_INGEST_WAIT_SECONDS,
            )
        ),
        ERASURE_MIN_INGEST_WAIT_SECONDS,
    )
    started = []
    for pid in sorted({canonical_project_id(p) for p in project_ids}):
        workflow_id = erasure_workflow_id(pid)
        start_workflow_sync(
            TrajectoryErasureWorkflow,
            TrajectoryErasureInput(project_id=pid, ingest_wait_seconds=wait),
            workflow_id,
            ERASURE_QUEUE,
            cancel_existing=False,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        logger.info("trajectory_erasure_requested", project_id=pid)
        started.append(workflow_id)
    return started


__all__ = [
    "AuditReport",
    "GcReport",
    "Topology",
    "TrajectoryErasurePending",
    "apply_item_masks",
    "audit_references",
    "clear_trajectory_gc_latch",
    "erase_project_items",
    "erasure_gate",
    "gc_deletes_enabled",
    "open_executor",
    "replica_health_refusal",
    "request_trajectory_erasure",
    "resolve_topology",
    "run_audit",
    "store_present",
    "trajectory_items_gc",
    "verify_project_erased",
]
