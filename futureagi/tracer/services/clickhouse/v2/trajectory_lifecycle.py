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
3. The topology is one shard (local, or replicated with a known cluster) and
   no Distributed table fronts ``spans`` or ``trajectory_items``.
4. ``traj:contract:v2`` exists: every collector enforces the reuse bound.
5. The reference audit of recent ``items_v1`` spans finds no missing item and
   no item older than the bound allows.

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
    canonical_project_id,
    contract_key_present,
    open_seen_redis,
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
SCOPES_PER_LOOKUP = 500
DEFAULT_MASK_PARTITIONS_PER_RUN = 4

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
        return client.execute(sql, params or {}, settings=query_settings)

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
    clusters = {
        str(name): (int(shards), int(hosts))
        for name, shards, hosts in execute(
            "SELECT cluster, uniqExact(shard_num), count() FROM system.clusters "
            "GROUP BY cluster HAVING countIf(is_local) > 0"
        )
    }
    configured = str(getattr(settings, "FI_TRAJECTORY_CH_CLUSTER", "") or "")
    if configured:
        if configured not in clusters:
            return Topology(replicated=True, refusal="cluster_not_found")
        if clusters[configured][0] != 1:
            return Topology(replicated=True, refusal="multi_shard")
        return Topology(replicated=True, cluster=configured)
    candidates = sorted(
        name
        for name, (shards, hosts) in clusters.items()
        if shards == 1 and hosts == replicas
    )
    if not candidates:
        return Topology(replicated=True, refusal="cluster_unknown")
    return Topology(replicated=True, cluster=candidates[0])


def _count_project_rows(
    execute: Execute, topology: Topology, table: str, project_id: str
) -> int:
    """Physical rows of ``table`` for one project, on every replica."""
    params = {"project": project_id}
    if topology.replicated:
        database = current_database(execute)
        sql = (
            f"SELECT count() FROM clusterAllReplicas(%(cluster)s, {database}.{table}) "
            "WHERE project_id = toUUID(%(project)s)"
        )
        params["cluster"] = topology.cluster
    else:
        sql = f"SELECT count() FROM {table} WHERE project_id = toUUID(%(project)s)"
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


def _audit_bucket(
    execute: Execute, bucket: int, *, since_us: int, until_us: int, limit: int
) -> AuditReport:
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
        {"bucket": bucket, "since": since_us, "until": until_us, "limit": limit},
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
        found = execute(
            "SELECT toString(project_id), scope_id, toString(item_hash), "
            "toUnixTimestamp64Micro(max(persisted_at)) "
            "FROM trajectory_items "
            "WHERE _partition_id = %(partition)s "
            "AND project_id IN %(projects)s AND scope_id IN %(scopes)s "
            "GROUP BY project_id, scope_id, item_hash",
            {
                "partition": str(bucket),
                "projects": tuple(projects),
                "scopes": tuple(scopes[start : start + SCOPES_PER_LOOKUP]),
            },
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
) -> AuditReport:
    """Check that recent ``items_v1`` spans point only at present, fresh items.

    Per bucket, up to ``limit`` spans created in ``[since, until)`` are read.
    A violation is a referenced key with no item row (missing), or whose newest
    copy was persisted more than GC_MARGIN before the newest span that
    references it (stale: some collector reused past the bound). Counts only.
    """
    total = AuditReport()
    for bucket in range(BUCKETS):
        part = _audit_bucket(
            execute, bucket, since_us=since_us, until_us=until_us, limit=limit
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
    """Report-only audit for ``manage.py trajectory_audit``."""
    with _executor(execute) as run:
        if not store_present(run):
            return {"status": "absent"}
        now_us = server_now_us(run)
        since = now_us - _us(AUDIT_WINDOW) if since_us is None else since_us
        report = audit_references(
            run, since_us=since, until_us=now_us - _us(AUDIT_SETTLE)
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
    projects_cut: int = 0
    delete_statements: int = 0
    masked_partitions: int = 0
    audit: dict[str, int] = field(default_factory=dict)
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _chunks(values: Sequence, size: int) -> Iterator[Sequence]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _gc_bucket(execute: Execute, bucket: int, *, now_us: int, report: GcReport) -> None:
    margin = _us(GC_MARGIN)
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
        oldest_item = {str(pid): int(min_us) for pid, min_us in chunk}
        horizon = {
            pid: min(min_us + margin + _us(PROBE_LOOKAHEAD), now_us)
            for pid, min_us in oldest_item.items()
        }
        observed = {
            str(pid): int(min_us)
            for pid, min_us in execute(
                "SELECT toString(project_id), toUnixTimestamp64Micro(min(created_at)) "
                "FROM spans WHERE project_id IN %(projects)s "
                "AND created_at < fromUnixTimestamp64Micro(%(horizon)s, 'UTC') "
                "GROUP BY project_id",
                {"projects": tuple(oldest_item), "horizon": max(horizon.values())},
            )
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
    if limit <= 0:
        return []
    rows = execute(
        "SELECT partition_id FROM system.parts "
        "WHERE database = currentDatabase() AND table = %(table)s "
        "AND active AND has_lightweight_delete "
        "GROUP BY partition_id ORDER BY sum(rows) DESC, partition_id "
        f"LIMIT {int(limit)}",
        {"table": ITEMS_TABLE},
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
        if topology.refusal:
            logger.error("trajectory_gc_refused", reason=topology.refusal)
            return GcReport(status="refused", reason=topology.refusal).as_dict()

        now_us = server_now_us(run)
        since_us = now_us - _us(AUDIT_WINDOW)
        if not _contract_present(redis_client):
            if _recent_items_v1_spans(run, since_us=since_us):
                logger.error("trajectory_gc_latched", reason="contract_key_absent")
                return GcReport(
                    status="latched", reason="contract_key_absent"
                ).as_dict()
            logger.info("trajectory_gc_inactive", reason="contract_key_absent")
            return GcReport(status="inactive", reason="contract_key_absent").as_dict()

        audit = audit_references(
            run, since_us=since_us, until_us=now_us - _us(AUDIT_SETTLE)
        )
        if audit.violations:
            logger.error(
                "trajectory_gc_latched", reason="reference_violation", **audit.as_dict()
            )
            return GcReport(
                status="latched", reason="reference_violation", audit=audit.as_dict()
            ).as_dict()

        report = GcReport(status="ok", audit=audit.as_dict())
        for bucket in range(BUCKETS):
            _gc_bucket(run, bucket, now_us=now_us, report=report)
            report.buckets += 1
            if progress is not None:
                progress(bucket)
        report.masked_partitions = apply_item_masks(
            run, masked_item_partitions(run, limit=mask_limit)
        )
        logger.info("trajectory_gc_completed", **report.as_dict())
        return report.as_dict()


def _contract_present(redis_client: Any) -> bool:
    if redis_client is not None:
        return contract_key_present(redis_client)
    try:
        client = open_seen_redis()
    except Exception:
        return False
    try:
        return contract_key_present(client)
    finally:
        try:
            client.close()
        except Exception:
            pass


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
        rows = int(
            run(
                "SELECT count() FROM trajectory_items "
                "WHERE modulo(cityHash64(project_id), 32) = %(bucket)s "
                "AND project_id = toUUID(%(project)s)",
                params,
            )[0][0]
        )
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

    wait = int(getattr(settings, "FI_TRAJECTORY_ERASURE_INGEST_WAIT_SECONDS", 3960))
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
    "erase_project_items",
    "erasure_gate",
    "gc_deletes_enabled",
    "open_executor",
    "request_trajectory_erasure",
    "resolve_topology",
    "run_audit",
    "store_present",
    "trajectory_items_gc",
    "verify_project_erased",
]
