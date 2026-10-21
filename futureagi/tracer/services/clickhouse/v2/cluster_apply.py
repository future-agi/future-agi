"""Checkpointed ON CLUSTER execution; never replay destructive historical SQL.

ON CLUSTER is not a transaction. A failed/timed-out step has no receipt and may
already exist on some hosts. Re-running that exact additive step finishes it.
The operator owns an exclusive schema window (stop ingestion during bootstrap).
"""

from __future__ import annotations

from .apply_schema_rewriter import with_dictionary_credentials
from .cluster_schema import ClusterSchemaError, ClusterTopology, SchemaStep

DDL_SETTINGS = {
    "distributed_ddl_task_timeout": 120,
    "distributed_ddl_output_mode": "throw",
    "skip_unavailable_shards": 0,
}
_LEDGER = "cluster_schema_versions"


def _rows(client, sql, parameters=None):
    return client.query(
        sql, parameters=parameters or {}, settings={"skip_unavailable_shards": 0}
    ).result_rows


def preflight(client, topology: ClusterTopology):
    """Require every declared node, correct shard/replica macros and replication."""
    rows = _rows(
        client,
        "SELECT shard_num, replica_num, internal_replication FROM system.clusters WHERE cluster = %(cluster)s",
        {"cluster": topology.cluster},
    )
    expected = {
        (s, r)
        for s in range(1, topology.shards + 1)
        for r in range(1, topology.replicas + 1)
    }
    if {(int(s), int(r)) for s, r, _ in rows} != expected or not all(
        row[2] for row in rows
    ):
        raise ClusterSchemaError(
            "remote_servers must match the declared shards/replicas with internal_replication=true"
        )
    macros = _rows(
        client,
        f"SELECT hostName(), macro, substitution FROM clusterAllReplicas('{topology.cluster}', system.macros) WHERE macro IN ('shard', 'replica')",
    )
    hosts = {}
    for host, key, value in macros:
        hosts.setdefault(host, {})[key] = value
    identities = {(m.get("shard"), m.get("replica")) for m in hosts.values()}
    if (
        len(hosts) != topology.shards * topology.replicas
        or len(identities) != len(hosts)
        or any(None in pair or "" in pair for pair in identities)
    ):
        raise ClusterSchemaError(
            "all nodes must be reachable with unique (shard, replica) macros"
        )
    by_shard = {}
    for shard, replica in identities:
        by_shard.setdefault(shard, set()).add(replica)
    if len(by_shard) != topology.shards or any(
        len(rs) != topology.replicas for rs in by_shard.values()
    ):
        raise ClusterSchemaError(
            "Keeper shard macros do not match the declared topology"
        )
    mapping = _rows(
        client,
        f"SELECT hostName(), shard_num FROM clusterAllReplicas('{topology.cluster}', system.clusters) WHERE cluster = %(cluster)s AND is_local",
        {"cluster": topology.cluster},
    )
    if len(mapping) != len(hosts) or any(
        str(shard) != hosts.get(host, {}).get("shard") for host, shard in mapping
    ):
        raise ClusterSchemaError(
            "remote_servers shard ordinal must match each node's shard macro"
        )
    required = {
        "distributed_foreground_insert": "1",
        "async_insert": "0",
        "insert_quorum": str(topology.insert_quorum),
        "distributed_product_mode": "global",
        "prefer_global_in_and_join": "1",
        "distributed_group_by_no_merge": "0",
        "skip_unavailable_shards": "0",
        "max_parallel_replicas": "1",
        "optimize_skip_unused_shards": "0",
        "optimize_distributed_group_by_sharding_key": "0",
    }
    settings = _rows(
        client,
        f"SELECT hostName(), name, value FROM clusterAllReplicas('{topology.cluster}', system.settings) WHERE name IN %(names)s",
        {"names": tuple(required)},
    )
    if len(settings) != len(hosts) * len(required) or any(
        str(value) != required[name] for _, name, value in settings
    ):
        raise ClusterSchemaError(
            "all cluster users require the documented synchronous write and global-read profile"
        )
    return len(hosts)


def live_objects(client, topology):
    return _rows(
        client,
        f"SELECT hostName(), name, engine, engine_full FROM clusterAllReplicas('{topology.cluster}', system.tables) WHERE database = %(database)s",
        {"database": topology.database},
    )


def verify(client, topology, steps, *, complete=True):
    expected = {step.name: step for step in steps if step.engine}
    found = {}
    for host, name, engine, engine_full in live_objects(client, topology):
        if name not in expected:
            continue
        step = expected[name]
        if engine != step.engine:
            raise ClusterSchemaError(
                f"{name}: engine mismatch on {host}; use a new database"
            )
        if engine.startswith("Replicated"):
            # The server substitutes macros in system.replicas, but preserves
            # them in engine_full. A foreign table path must never be adopted.
            path = f"'{topology.keeper_prefix}/{topology.database}/{{shard}}/{name}'"
            if path not in engine_full or "'{replica}'" not in engine_full:
                raise ClusterSchemaError(f"{name}: foreign Keeper path on {host}")
        elif engine == "Distributed":
            for part in (topology.cluster, topology.database, name + "_local"):
                if f"'{part}'" not in engine_full:
                    raise ClusterSchemaError(
                        f"{name}: foreign Distributed target on {host}"
                    )
        found.setdefault(name, set()).add(host)
    if complete:
        missing = [
            name
            for name in expected
            if len(found.get(name, ())) != topology.shards * topology.replicas
        ]
        if missing:
            raise ClusterSchemaError(
                f"schema incomplete across the cluster: {', '.join(missing)}"
            )


def apply_plan(
    client, topology, steps: list[SchemaStep], *, user="default", password=""
):
    preflight(client, topology)
    live = live_objects(client, topology)
    has_ledger = any(name == _LEDGER for _, name, _, _ in live)
    expected = {step.name for step in steps}
    if not has_ledger and any(name in expected for _, name, _, _ in live):
        raise ClusterSchemaError(
            "refusing an unowned existing schema; stage cluster migration in a new database"
        )
    verify(client, topology, steps, complete=False)
    ledger_path = f"'{topology.keeper_prefix}/{topology.database}/schema-ledger'"
    if any(
        name == _LEDGER
        and (
            engine != "ReplicatedMergeTree"
            or ledger_path not in ddl
            or "'{shard}-{replica}'" not in ddl
        )
        for _, name, engine, ddl in live
    ):
        raise ClusterSchemaError(
            "foreign schema ledger; refusing to adopt migration history"
        )
    client.command(
        f"CREATE DATABASE IF NOT EXISTS {topology.database} {topology.on_cluster}",
        settings=DDL_SETTINGS,
    )
    # One global replication group for receipts, unlike data (one per shard).
    # Both macros form the ledger replica identity, even when replica labels are
    # reused across shards. No per-shard ledger can incorrectly skip another.
    client.command(
        f"CREATE TABLE IF NOT EXISTS {topology.database}.{_LEDGER} {topology.on_cluster} "
        "(identity String, step String, sha256 FixedString(64), applied_at DateTime64(6) DEFAULT now64(6)) "
        f"ENGINE = ReplicatedMergeTree('{topology.keeper_prefix}/{topology.database}/schema-ledger', '{{shard}}-{{replica}}') "
        "ORDER BY (identity, step, applied_at)",
        settings=DDL_SETTINGS,
    )
    history = _rows(
        client, f"SELECT identity, step, sha256 FROM {topology.database}.{_LEDGER}"
    )
    applied = {}
    for identity, step, digest in history:
        if identity != topology.identity:
            raise ClusterSchemaError(
                "installed topology differs; shard-count/path changes require a new database"
            )
        digest = digest.decode() if isinstance(digest, bytes) else digest
        if step in applied and applied[step] != digest:
            raise ClusterSchemaError("conflicting migration receipts")
        applied[step] = digest
    # Validate ALL hashes before issuing ANY step (not half a migration first).
    for step in steps:
        if step.key in applied and applied[step.key] != step.checksum:
            raise ClusterSchemaError(f"immutable cluster migration changed: {step.key}")
    count = 0
    for step in steps:
        if step.key in applied:
            continue
        sql = with_dictionary_credentials(step.sql, user, password)
        client.command(sql, settings=DDL_SETTINGS)
        verify(client, topology, [step])
        client.insert(
            f"{topology.database}.{_LEDGER}",
            [[topology.identity, step.key, step.checksum]],
            column_names=["identity", "step", "sha256"],
            settings={
                "async_insert": 0,
                "insert_quorum": "auto",
                "insert_quorum_timeout": 120000,
            },
        )
        count += 1
    verify(client, topology, steps)
    return count
