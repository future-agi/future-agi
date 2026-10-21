"""Additive sharded schema plans. Historical single-node migrations stay untouched.

Only packaged CREATE declarations enter this compiler, never operator SQL. Local
replicated tables own storage and materialized views; public names route reads
and inserts through Distributed. A topology change is a new-database migration,
not an engine conversion or an invitation to rehash an installed baseline.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .apply_schema_rewriter import rewrite_for_replicated, split_statements
from .schema_topology import SchemaTopology

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z_0-9]{0,127}$")
_CREATE = re.compile(
    r"^CREATE\s+(TABLE|MATERIALIZED VIEW|DICTIONARY|VIEW)\s+IF NOT EXISTS\s+"
    r"(?:(?P<db>[A-Za-z_][A-Za-z_0-9]*)\.)?(?P<name>[A-Za-z_][A-Za-z_0-9]*)\b",
    re.IGNORECASE,
)
_TOKEN = re.compile(
    r"'(?:\\.|''|[^'\\])*'|`[^`]*`|--[^\n]*|/\*[\s\S]*?\*/|[A-Za-z_][A-Za-z_0-9]*|\S"
)

# Never include mutable session/user labels in the routing identity: a late
# association/update must return to the shard holding the original RMT row.
SHARD_KEYS = {
    "spans": "cityHash64(project_id, trace_id)",
    "traces": "cityHash64(project_id, toString(id))",
    "end_users": "cityHash64(id)",
    "trace_sessions": "cityHash64(id)",
    "end_user_id_remap": "cityHash64(old_id)",
    "trace_session_id_remap": "cityHash64(old_id)",
    "tracer_eval_logger_v2": "cityHash64(id)",
    "eval_per_config": "cityHash64(custom_eval_config_id)",
    "spans_v2_dead_letter": "cityHash64(pg_id)",
    "backfill_checkpoints": "cityHash64(project_id)",
    "spans_per_session": "cityHash64(project_id)",
    "spans_hourly_rollup": "cityHash64(project_id)",
    "trace_count_rollup": "cityHash64(project_id)",
    "dashboard_attr_rollup": "cityHash64(project_id)",
    "span_user_rollup": "cityHash64(project_id)",
    "property_definition_catalog": "cityHash64(workspace_id)",
    "span_attribute_value_catalog": "cityHash64(project_id)",
    "property_catalog_checkpoints": "cityHash64(workspace_id)",
    "property_catalog_activations": "cityHash64(workspace_id)",
    "property_catalog_deliveries": "cityHash64(workspace_id)",
    "property_catalog_source_streams": "cityHash64(workspace_id)",
    "observed_attribute_keys": "cityHash64(project_id)",
    "observed_attribute_values": "cityHash64(project_id)",
}
QUERY_SETTINGS = {
    "distributed_product_mode": "global",
    "prefer_global_in_and_join": 1,
    "distributed_group_by_no_merge": 0,
    "optimize_skip_unused_shards": 0,
    "optimize_distributed_group_by_sharding_key": 0,
    "max_parallel_replicas": 1,
    "skip_unavailable_shards": 0,
}


class ClusterSchemaError(ValueError):
    """Unsafe topology, unsupported DDL, or schema drift; no automatic repair."""


@dataclass(frozen=True)
class ClusterTopology:
    database: str
    cluster: str
    keeper_prefix: str
    shards: int = 3
    replicas: int = 2
    insert_quorum: int = 2

    def __post_init__(self):
        if not _IDENTIFIER.fullmatch(self.database) or self.database.lower() in {
            "default",
            "system",
            "information_schema",
        }:
            raise ClusterSchemaError(
                "cluster mode requires an explicit new non-default database"
            )
        SchemaTopology.from_options(
            replicated=True,
            cluster=self.cluster,
            zk_table_path_prefix=self.keeper_prefix,
        )
        for value in (self.shards, self.replicas, self.insert_quorum):
            if type(value) is not int or value < 1:
                raise ClusterSchemaError(
                    "shards, replicas and quorum must be positive integers"
                )
        if self.replicas < 2 or self.insert_quorum > self.replicas:
            raise ClusterSchemaError(
                "at least two replicas and quorum <= replicas required"
            )

    @property
    def identity(self):
        return (
            f"cluster/v1;db={self.database};cluster={self.cluster};"
            f"keeper={self.keeper_prefix};shards={self.shards};replicas={self.replicas}"
        )

    @property
    def on_cluster(self):
        return f"ON CLUSTER '{self.cluster}'"

    @classmethod
    def from_env(cls, database=None, env=None):
        env = os.environ if env is None else env
        return cls(
            database=database or env.get("CH_DATABASE", "default"),
            cluster=env.get("CH_CLUSTER_NAME", ""),
            keeper_prefix=env.get("CH_CLUSTER_KEEPER_PREFIX", ""),
            shards=int(env.get("CH_CLUSTER_SHARDS", "3")),
            replicas=int(env.get("CH_CLUSTER_REPLICAS", "2")),
            insert_quorum=int(env.get("CH_CLUSTER_INSERT_QUORUM", "2")),
        )


def cluster_enabled(env=None):
    env = os.environ if env is None else env
    return env.get("CH_CLUSTER_ENABLED", "false").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class SchemaStep:
    key: str
    name: str
    engine: str
    sql: str

    @property
    def checksum(self):
        return hashlib.sha256(self.sql.encode()).hexdigest()


def _clean(sql):
    return (
        _TOKEN.sub(lambda m: " " if m[0].startswith(("--", "/*")) else m[0], sql)
        .strip()
        .rstrip(";")
    )


def _local_references(sql, tables, database):
    # Lexical replacement avoids corrupting literals, dictionary names, or
    # columns whose name happens to contain a table name.
    sql = _TOKEN.sub(
        lambda m: m[0] + "_local" if m[0] in tables else m[0], sql
    ).replace("__database__", database)
    for table in tables:
        sql = re.sub(
            rf"\b(FROM|TO)\s+({re.escape(table)}_local)\b", rf"\1 {database}.\2", sql
        )
    return sql


def compile_definitions(definitions, topology, *, version, shard_keys=None):
    keys = {**SHARD_KEYS, **(shard_keys or {})}
    parsed = []
    for sql in definitions:
        sql = _clean(sql).replace("__database__", topology.database)
        match = _CREATE.match(sql)
        if not match or (match["db"] and match["db"] != topology.database):
            raise ClusterSchemaError(
                "only additive CREATEs in the selected database are allowed"
            )
        parsed.append((match[1].upper(), match["name"], sql, match))
    names = [name for _, name, _, _ in parsed]
    if len(set(names)) != len(names):
        raise ClusterSchemaError("duplicate schema object")
    tables = {name for kind, name, _, _ in parsed if kind == "TABLE"}
    local, aliases, dictionaries, views = [], [], [], []
    for kind, name, sql, match in parsed:
        qualified = f"{topology.database}.{name}"
        header = f"CREATE {kind} IF NOT EXISTS {qualified}"
        sql = header + sql[match.end() :]
        if kind == "TABLE":
            physical = name + "_local"
            rewritten = sql.replace(
                header, f"CREATE TABLE IF NOT EXISTS {topology.database}.{physical}", 1
            )
            rewritten = rewrite_for_replicated(
                rewritten,
                table_name=physical,
                cluster=topology.cluster,
                zk_prefix=f"{topology.keeper_prefix}/{topology.database}",
            )
            engine = re.search(r"ENGINE\s*=\s*(\w+)", rewritten)[1]
            local.append(
                SchemaStep(f"{version}/{physical}", physical, engine, rewritten)
            )
            key = keys.get(name)
            if key is None:
                raise ClusterSchemaError(
                    f"explicit immutable shard key required for {name}"
                )
            alias = (
                f"{header} {topology.on_cluster} AS {topology.database}.{physical} "
                f"ENGINE = Distributed('{topology.cluster}', '{topology.database}', '{physical}', {key})"
            )
            aliases.append(SchemaStep(f"{version}/{name}", name, "Distributed", alias))
        elif kind == "DICTIONARY":
            sql = sql.replace(header, f"{header} {topology.on_cluster}", 1)
            # Load all shards, not just this replica's physical dimension table.
            if not re.search(r"\bDB\s+'", sql):
                sql = sql.replace(
                    "SOURCE(CLICKHOUSE(",
                    f"SOURCE(CLICKHOUSE(DB '{topology.database}' ",
                    1,
                )
            dictionaries.append(
                SchemaStep(f"{version}/{name}", name, "Dictionary", sql)
            )
        else:
            if kind == "MATERIALIZED VIEW":
                sql = _local_references(sql, tables, topology.database)
            sql = sql.replace(header, f"{header} {topology.on_cluster}", 1)
            views.append(
                SchemaStep(
                    f"{version}/{name}",
                    name,
                    "MaterializedView" if kind == "MATERIALIZED VIEW" else "View",
                    sql,
                )
            )
    # Dictionaries resolve lazily. All local tables + aliases exist before any
    # materialized view attaches, so each inserted block fans out only once.
    span_local = [step for step in local if step.name == "spans_local"]
    span_alias = [step for step in aliases if step.name == "spans"]
    return (
        [step for step in local if step not in span_local]
        + [step for step in aliases if step not in span_alias]
        + dictionaries
        + span_local
        + span_alias
        + views
    )


def native_plan(topology):
    steps = []
    known_tables = set()
    for path in sorted(Path(__file__).with_name("cluster_schema").glob("*.sql")):
        statements = split_statements(path.read_text())
        creates = [sql for sql in statements if not _clean(sql).startswith("ALTER ")]
        additions = [sql for sql in statements if _clean(sql).startswith("ALTER ")]
        created = compile_definitions(creates, topology, version=path.stem)
        steps.extend(created)
        known_tables.update(
            step.name for step in created if step.engine == "Distributed"
        )
        for sql in additions:
            steps.extend(
                compile_addition(
                    sql, topology, version=path.stem, known_tables=known_tables
                )
            )
    return steps


def compile_addition(sql, topology, *, version, known_tables):
    """The upgrade grammar is deliberately additive; no MODIFY/DROP/mutation."""
    sql = _clean(sql).replace("__database__", topology.database)
    match = re.fullmatch(
        r"ALTER TABLE (?:(?P<db>[A-Za-z_][A-Za-z_0-9]*)\.)?(?P<table>[A-Za-z_][A-Za-z_0-9]*)\s+"
        r"ADD (?P<kind>COLUMN|INDEX|PROJECTION) IF NOT EXISTS (?P<field>[A-Za-z_][A-Za-z_0-9]*)\s+(?P<body>[\s\S]+)",
        sql,
    )
    if (
        not match
        or match["table"] not in known_tables
        or (match["db"] and match["db"] != topology.database)
    ):
        raise ClusterSchemaError(
            "only named additive upgrades to known cluster tables are allowed"
        )
    # A migration is one action; reject a second ALTER action hidden after it.
    tokens = [
        m[0].upper() for m in _TOKEN.finditer(match["body"]) if not m[0].startswith("'")
    ]
    if any(
        token in {";", "DROP", "DELETE", "UPDATE", "MODIFY", "RENAME", "ADD"}
        for token in tokens
    ):
        raise ClusterSchemaError("destructive/multiple migration actions are forbidden")
    names = [match["table"] + "_local"]
    if match["kind"] == "COLUMN":
        names.append(match["table"])
    return [
        SchemaStep(
            f"{version}/add/{name}/{match['kind']}/{match['field']}",
            name,
            "",
            f"ALTER TABLE {topology.database}.{name} {topology.on_cluster} ADD {match['kind']} IF NOT EXISTS {match['field']} {match['body']}",
        )
        for name in names
    ]
