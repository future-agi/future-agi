"""Cluster adaptation for the existing PostgreSQL outbox, not a second writer."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from tracer.services.clickhouse import oss_cdc_bootstrap as core

from .cluster_apply import DDL_SETTINGS, apply_plan, preflight, verify
from .cluster_schema import ClusterTopology, compile_definitions


@dataclass(frozen=True)
class ClusterCDCResult:
    created: tuple[str, ...]


def cdc_plan(source, topology, *, include_usage_schema):
    definitions = core._source_definitions(
        source, include_usage_schema=include_usage_schema
    )
    declarations, _ = core._definitions(
        topology.database, include_usage_schema=include_usage_schema
    )
    definitions.update({name: declarations[name] for name in core.DEPENDENT})
    # Source additions have their own source-profile identity. Existing objects
    # are strictly inspected and only missing PG columns can be added; neither
    # a new profile hash nor IF NOT EXISTS authorizes engine/key/type changes.
    ordered = [definitions[name] for name in sorted(definitions)]
    digest = hashlib.sha256("\n".join(ordered).encode()).hexdigest()[:16]
    if any(table.primary_key != ("id",) for table in source.tables.values()):
        raise core.BootstrapError(
            "cluster CDC requires immutable single-column id keys"
        )
    return compile_definitions(
        ordered,
        topology,
        version=f"cdc-v1-{digest}",
        shard_keys=dict.fromkeys(source.tables, "cityHash64(id)"),
    )


def verify_columns(client, topology, steps, *, allow_missing_additions=False):
    physical = {
        step.name: step for step in steps if step.engine.startswith("Replicated")
    }
    from tracer.services.clickhouse.oss_native_bootstrap import _table_parts

    planned_additions = set()
    for step in steps:
        if step.engine or step.name not in physical:
            continue
        addition = re.search(
            r" ADD COLUMN IF NOT EXISTS ([A-Za-z_][A-Za-z_0-9]* [\s\S]+)$", step.sql
        )
        if addition:
            original = physical[step.name]
            head, entries, tail = _table_parts(original.sql)
            planned_additions.add((step.name, addition[1].split()[0]))
            entries.append(addition[1])
            physical[step.name] = replace(
                original, sql=head + "(" + ",".join(entries) + ")" + tail
            )
    columns = {}
    for host, table, name, data_type, kind, expression in client.query(
        f"SELECT hostName(), table, name, type, default_kind, default_expression FROM clusterAllReplicas('{topology.cluster}', system.columns) WHERE database = %(database)s AND table IN %(tables)s",
        parameters={
            "database": topology.database,
            "tables": tuple(physical)
            + tuple(name.removesuffix("_local") for name in physical),
        },
        settings={"skip_unavailable_shards": 0},
    ).result_rows:
        columns.setdefault((host, table), {})[name] = (data_type, kind, expression)
    for (host, table), shape in columns.items():
        if table not in physical:
            continue
        logical = columns.get((host, table.removesuffix("_local")))
        if logical is None:
            continue  # partial bootstrap; apply_plan checks complete object coverage
        missing = set(shape) - set(logical)
        permitted = (
            {field for name, field in planned_additions if name == table}
            if allow_missing_additions
            else set()
        )
        if (
            missing - permitted
            or set(logical) - set(shape)
            or any(
                shape[field] != logical[field] for field in set(shape) & set(logical)
            )
        ):
            raise core.BootstrapError(
                f"{table}: Distributed columns differ from physical storage on {host}"
            )
    for host, table, *row in client.query(
        f"SELECT hostName(), name, engine, engine_full, partition_key, sorting_key, primary_key, create_table_query FROM clusterAllReplicas('{topology.cluster}', system.tables) WHERE database = %(database)s AND name IN %(tables)s",
        parameters={"database": topology.database, "tables": tuple(physical)},
        settings={"skip_unavailable_shards": 0},
    ).result_rows:
        # Constraints are not columns. The shared native compatibility reader
        # predates the catalog CHECK constraints; keep its column/key contract.
        head, entries, tail = _table_parts(physical[table].sql)
        ddl = (
            head
            + "("
            + ",".join(
                entry for entry in entries if not entry.startswith("CONSTRAINT ")
            )
            + ")"
            + tail
        )
        core._check_table(
            table,
            row,
            columns.get((host, table), {}),
            {},
            ddl,
            database=topology.database,
            native=True,
            source_owned=True,
            allow_additions=allow_missing_additions,
            additions=planned_additions,
        )


def apply_cdc(client, source, *, config, pg):
    from tracer.services.clickhouse.oss_outbox_cdc import add_source_columns

    topology = ClusterTopology.from_env(config.destination.database)
    steps = cdc_plan(source, topology, include_usage_schema=config.include_usage_schema)
    preflight(client, topology)
    verify(client, topology, steps, complete=False)
    existing = {
        row[0]
        for row in client.query(
            "SELECT name FROM system.tables WHERE database = currentDatabase() AND engine = 'Distributed'"
        ).result_rows
    }
    added = []
    for name, table in source.tables.items():
        if name in existing:
            present = [
                row[0]
                for row in client.query(
                    f"SELECT name FROM clusterAllReplicas('{topology.cluster}', system.columns) WHERE database = %(database)s AND table IN %(tables)s GROUP BY name HAVING uniqExact(tuple(hostName(), table)) = %(copies)s",
                    parameters={
                        "database": topology.database,
                        "tables": (name, name + "_local"),
                        "copies": 2 * topology.shards * topology.replicas,
                    },
                    settings={"skip_unavailable_shards": 0},
                ).result_rows
            ]
            added += add_source_columns(pg, client, name, table, present)
    verify_columns(client, topology, steps)
    apply_plan(
        client, topology, steps, user=config.ch_user, password=config.ch_password
    )
    verify_columns(client, topology, steps)
    created = tuple(name for name in source.tables if name not in existing)
    return created, added, ClusterCDCResult(tuple(core.DEPENDENT))


def check_cdc(client, source, *, config):
    topology = ClusterTopology.from_env(config.destination.database)
    steps = cdc_plan(source, topology, include_usage_schema=config.include_usage_schema)
    preflight(client, topology)
    verify(client, topology, steps)
    verify_columns(client, topology, steps)


def add_column(client, table, column, data_type):
    """Both ALTERs are additive/retryable; only the outbox owns source columns."""
    topology = ClusterTopology.from_env()
    for name in (table + "_local", table):
        client.command(
            f"ALTER TABLE {topology.database}.{name} {topology.on_cluster} ADD COLUMN IF NOT EXISTS `{column}` {data_type}",
            settings=DDL_SETTINGS,
        )


def apply_observed_catalog(client, *, database, writer_password, reader_password):
    from pathlib import Path

    from .apply_schema_rewriter import split_statements

    topology = ClusterTopology.from_env(database)
    definitions = split_statements(
        (Path(__file__).with_name("observed_catalog") / "schema.sql").read_text()
    )
    steps = compile_definitions(definitions, topology, version="observed-v1")
    verify_columns(client, topology, steps)
    apply_plan(client, topology, steps)
    verify_columns(client, topology, steps)
    for user, password, readonly in (
        ("observed_catalog_writer", writer_password, ""),
        ("observed_catalog_reader", reader_password, " SETTINGS readonly=2"),
    ):
        auth = "IDENTIFIED WITH sha256_password BY {password:String} HOST ANY"
        client.command(
            f"CREATE USER IF NOT EXISTS {user} {topology.on_cluster} {auth}",
            parameters={"password": password},
            settings=DDL_SETTINGS,
        )
        client.command(
            f"ALTER USER {user} {topology.on_cluster} {auth}{readonly}",
            parameters={"password": password},
            settings=DDL_SETTINGS,
        )
        privileges = "SELECT" if readonly else "SELECT, INSERT"
        for table in ("observed_attribute_keys", "observed_attribute_values"):
            client.command(
                f"GRANT {topology.on_cluster} {privileges} ON {database}.{table} TO {user}",
                settings=DDL_SETTINGS,
            )
            if not readonly:
                client.command(
                    f"GRANT {topology.on_cluster} SELECT ON {database}.{table}_local TO {user}",
                    settings=DDL_SETTINGS,
                )
        if not readonly:
            for metadata in ("system.tables", "system.replicas"):
                client.command(
                    f"GRANT {topology.on_cluster} SELECT ON {metadata} TO {user}",
                    settings=DDL_SETTINGS,
                )
