"""Bounded OSS PeerDB setup; default invocation only inspects.

Run after PG migrations/seeding and native ClickHouse initialization, in an
exclusive deployment window. --apply creates only absent peers and one initial
snapshot mirror containing all absent landing tables. Existing objects are never
repaired, resynced, restarted or removed. An accepted CREATE is not replication readiness:
the separate CDC installer must wait and qualify source-owned landing schemas.
--wait-for-mirrors is read-only: wait for retained snapshots, then check readiness.

Creation uses the existing flow API with explicit per-mirror nullability, never
PeerDB SQL or ambient worker defaults. Every write is attempted once. On failure retain partial state and explicitly
invoke again to inspect it; never retry an uncertain statement in this process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from contextlib import ExitStack
from urllib.parse import quote

from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse import oss_cdc_install as install
from tracer.services.clickhouse import oss_cdc_inventory as inventory
from tracer.services.clickhouse import oss_native_bootstrap as native
from tracer.services.clickhouse.oss_cdc_source import inspect_source

Config = install.Config
_NAME = re.compile(r"[a-z_][a-z_0-9]{0,62}\Z")
_PENDING = frozenset({"STATUS_SETUP", "STATUS_SNAPSHOT"})


def _mirror_name(config, tables):
    identity = [
        [config.source.name, *config.source.endpoint],
        [config.destination.name, *config.destination.endpoint],
        sorted(tables),
    ]
    digest = hashlib.sha256(
        json.dumps(identity, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return "futureagi_cdc_" + digest


class SetupError(ValueError):
    """Safe error without driver payloads, SQL, or credentials."""


def _inspect_peers(request, config):
    """Inspect identities globally; reuse effective mapping checks per writer.

    A failed info lookup never proves absence. Only the complete peers list does.
    Pending mirrors close the writer. Their configuration is checked separately
    from readiness using the shared running-mirror configuration validator.
    """
    cache = {}

    def read(method, path, body=None):
        key = method, path, json.dumps(body, sort_keys=True)
        if key not in cache:
            response = request(method, path, body)
            if not isinstance(response, dict):
                raise SetupError("invalid PeerDB response envelope")
            cache[key] = response
        return cache[key]

    # v0.36.9 exposes the complete list in items; sourceItems/destinationItems
    # are filtered convenience lists and must not be used to prove absence.
    peers = read("GET", "/v1/peers/list").get("items")
    mirrors = read("GET", "/v1/mirrors/list").get("mirrors")
    if not isinstance(peers, list) or not isinstance(mirrors, list):
        raise SetupError("complete PeerDB peer and mirror lists required")
    names = {}
    for entry in peers:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not entry["name"]
            or entry["name"] in names
            or not isinstance(entry.get("type"), str)
        ):
            raise SetupError("invalid or duplicate peer list identity")
        names[entry["name"]] = entry["type"]

    def peer(name, kind):
        if not isinstance(name, str) or name not in names or names[name] != kind:
            raise SetupError("missing or contradictory mirror peer identity")
        record = read("GET", "/v1/peers/info/" + quote(name, safe="")).get("peer")
        if (
            kind not in ("POSTGRES", "CLICKHOUSE")
            or not isinstance(record, dict)
            or record.get("name") != name
            or record.get("type") != kind
        ):
            raise SetupError("invalid peer name or type")
        settings = record.get(
            "postgresConfig" if kind == "POSTGRES" else "clickhouseConfig"
        )
        if not isinstance(settings, dict) or settings.get("sshConfig"):
            raise SetupError("missing peer configuration or unqualified SSH routing")
        target = inventory.PeerTarget(
            name, settings.get("host"), settings.get("port"), settings.get("database")
        )
        user = settings.get("user")
        if not isinstance(user, str) or not user:
            raise SetupError("explicit peer user required")
        return target, user

    missing_peers = []
    for target, kind, user in (
        (config.source, "POSTGRES", config.pg_user),
        (config.destination, "CLICKHOUSE", config.ch_user),
    ):
        if target.name not in names:
            missing_peers.append(target.name)
        else:
            actual, actual_user = peer(target.name, kind)
            if actual.endpoint != target.endpoint or actual_user != user:
                raise SetupError("configured peer endpoint or user differs")

    seen, mappings, pending = set(), {}, []
    for mirror in mirrors:
        if (
            not isinstance(mirror, dict)
            or not isinstance(mirror.get("name"), str)
            or not mirror["name"]
            or mirror["name"] in seen
        ):
            raise SetupError("invalid or duplicate mirror name")
        name = mirror["name"]
        seen.add(name)
        dst, user = peer(mirror.get("destinationName"), mirror.get("destinationType"))
        if (
            mirror["destinationType"] != "CLICKHOUSE"
            or dst.endpoint != config.destination.endpoint
        ):
            continue
        if user != config.ch_user:
            raise SetupError("destination alias user differs")
        if mirror.get("isCdc") is not True or mirror.get("sourceType") != "POSTGRES":
            raise SetupError("unqualified writer into the ClickHouse database")
        src, user = peer(mirror.get("sourceName"), "POSTGRES")
        if src.endpoint != config.source.endpoint or user != config.pg_user:
            raise SetupError("foreign source endpoint or user writes into ClickHouse")
        status = read(
            "POST",
            "/v1/mirrors/status",
            {
                "flowJobName": name,
                "includeFlowInfo": True,
                "excludeBatches": True,
            },
        )
        states = mirror.get("status"), status.get("currentFlowState")
        if status.get("flowJobName") != name or any(
            s not in _PENDING | {"STATUS_RUNNING"} for s in states
        ):
            raise SetupError("invalid or unhealthy existing mirror status")
        if any(s in _PENDING for s in states):
            pending.append(name)

        def one_writer(method, path, body=None, *, mirror=mirror):
            # The shared validator couples configuration checks to RUNNING.
            # Project only those two status fields for configuration validation;
            # original status is retained in pending and ALWAYS closes writes.
            # Copies do not modify the API inventory or claim runtime readiness.
            if (method, path) == ("GET", "/v1/mirrors/list"):
                return {"mirrors": [{**mirror, "status": "STATUS_RUNNING"}]}
            if (method, path) == ("POST", "/v1/mirrors/status"):
                return {
                    **read(method, path, body),
                    "currentFlowState": "STATUS_RUNNING",
                }
            return read(method, path, body)

        for mirror_name, table in inventory.inspect_mappings(
            one_writer, source=src, destination=dst
        ):
            if table not in core.LANDING or table in mappings:
                raise SetupError("unknown or duplicate destination mapping")
            mappings[table] = mirror_name
    return missing_peers, mappings, pending, seen


def _inspect_landing(client, config, definitions):
    params = {"database": config.destination.database, "names": tuple(core.LANDING)}
    tables = {}
    for name, *row in native._rows(
        client,
        "SELECT name, engine, engine_full, partition_key, sorting_key, primary_key, create_table_query "
        "FROM system.tables WHERE database = %(database)s AND name IN %(names)s",
        params,
        7,
    ):
        if (
            name not in definitions
            or name in tables
            or any(not isinstance(v, str) for v in row)
        ):
            raise SetupError("invalid or duplicate landing table metadata")
        tables[name] = tuple(row)
    columns, indexes = {n: {} for n in tables}, {n: {} for n in tables}
    for name, column, *shape in native._rows(
        client,
        "SELECT table, name, type, default_kind, default_expression FROM system.columns "
        "WHERE database = %(database)s AND table IN %(names)s ORDER BY table, position",
        params,
        5,
    ):
        if (
            name not in tables
            or column in columns[name]
            or not column
            or any(not isinstance(v, str) for v in (column, *shape))
        ):
            raise SetupError("invalid or duplicate landing column metadata")
        columns[name][column] = tuple(shape)
    for name, index, expr, kind, granularity in native._rows(
        client,
        "SELECT table, name, expr, type_full, granularity FROM system.data_skipping_indices "
        "WHERE database = %(database)s AND table IN %(names)s",
        params,
        5,
    ):
        if name not in tables or index in indexes[name]:
            raise SetupError("invalid landing index metadata")
        indexes[name][index] = core._tokens(
            f"{expr} TYPE {kind} GRANULARITY {granularity}"
        )
    for name, row in tables.items():
        core._check_table(
            name,
            row,
            columns[name],
            indexes[name],
            definitions[name],
            database=config.destination.database,
            source_owned=True,
            allow_additions=True,
        )
    return set(tables)


def _creates(config, missing_peers, missing_tables):
    """Build only closed CREATE payloads; validate even standalone helper calls."""
    if (
        not isinstance(config, Config)
        or any(not _NAME.fullmatch(t.name) for t in (config.source, config.destination))
        or config.source.name == config.destination.name
    ):
        raise SetupError("distinct lowercase peer identifiers required")
    if not isinstance(missing_peers, (list, tuple)) or not isinstance(
        missing_tables, (list, tuple)
    ):
        raise SetupError("explicit missing peer and table sequences required")
    missing_peers, missing_tables = tuple(missing_peers), tuple(missing_tables)
    for names, allowed in (
        (missing_peers, {config.source.name, config.destination.name}),
        (missing_tables, core.LANDING),
    ):
        if any(
            not isinstance(name, str)
            or not _NAME.fullmatch(name)
            or name not in allowed
            for name in names
        ) or len(set(names)) != len(names):
            raise SetupError("unknown, unsafe or duplicate CREATE plan identifier")
    mirror_name = _mirror_name(config, missing_tables) if missing_tables else None
    if missing_tables and (
        not isinstance(mirror_name, str) or not _NAME.fullmatch(mirror_name)
    ):
        raise SetupError("invalid generated mirror identifier")

    result = []
    for target, kind, user, password in (
        (config.source, "POSTGRES", config.pg_user, config.pg_password),
        (config.destination, "CLICKHOUSE", config.ch_user, config.ch_password),
    ):
        if target.name not in missing_peers:
            continue
        settings = {
            "host": target.host,
            "port": target.port,
            "database": target.database,
            "user": user,
            "password": password,
        }
        if kind == "CLICKHOUSE":
            settings["disableTls"] = True
        result.append(
            (
                "peer",
                target.name,
                {
                    "peer": {
                        "name": target.name,
                        "type": kind,
                        "postgresConfig"
                        if kind == "POSTGRES"
                        else "clickhouseConfig": settings,
                    },
                    "allowUpdate": False,
                    "disableValidation": False,
                },
            )
        )
    if missing_tables:
        result.append(
            (
                "mirror",
                mirror_name,
                {
                    "connectionConfigs": {
                        "flowJobName": mirror_name,
                        "sourceName": config.source.name,
                        "destinationName": config.destination.name,
                        "tableMappings": [
                            {
                                "sourceTableIdentifier": "public." + table,
                                "destinationTableIdentifier": table,
                            }
                            for table in missing_tables
                        ],
                        "doInitialSnapshot": True,
                        "resync": False,
                        "initialSnapshotOnly": False,
                        "snapshotMaxParallelWorkers": 1,
                        "snapshotNumTablesInParallel": 1,
                        "idleTimeoutSeconds": "10",
                        "softDeleteColName": "_peerdb_is_deleted",
                        "syncedAtColName": "_peerdb_synced_at",
                        "system": "Q",
                        # Pin fresh-mirror semantics independently of worker/global defaults.
                        "env": {
                            "PEERDB_NULLABLE": "true",
                            "PEERDB_CLICKHOUSE_INITIAL_LOAD_ALLOW_NON_EMPTY_TABLES": "false",
                        },
                    },
                    "attachToExisting": False,
                },
            )
        )
    return result


def _create_once(request, kind, name, payload):
    path = "/v1/peers/create" if kind == "peer" else "/v1/flows/cdc/create"
    try:
        response = request("POST", path, payload)
        if kind == "peer":
            accepted = (
                isinstance(response, dict) and response.get("status") == "CREATED"
            )
        else:
            workflow = (
                response.get("workflowId") if isinstance(response, dict) else None
            )
            accepted = isinstance(workflow, str) and bool(workflow.strip())
        if not accepted:
            raise SetupError("invalid creation acknowledgement")
    except Exception as error:
        if isinstance(error, SetupError):
            category = "invalid creation acknowledgement"
        elif isinstance(error, TimeoutError):
            category = "timeout"
        elif isinstance(error, (install.InstallError, OSError)):
            category = "transport error"
        else:
            category = "operation error"
        raise SetupError(
            f"PeerDB CREATE {kind.upper()} {name} failed ({category}); "
            "partial state retained. Explicitly invoke again to inspect; "
            "no automatic retry or cleanup."
        ) from None


def run(
    config: Config,
    *,
    apply=False,
    wait_for_mirrors=False,
    timeout=300,
    pg_connect=None,
    ch_connect=None,
    request=None,
):
    """Inspect or wait read-only; apply submits a preflighted CREATE-only plan once."""
    try:
        return _run(
            config,
            apply=apply,
            wait_for_mirrors=wait_for_mirrors,
            timeout=timeout,
            pg_connect=pg_connect,
            ch_connect=ch_connect,
            request=request,
        )
    except SetupError:
        raise
    except Exception:
        raise SetupError(
            "PeerDB setup failed; partial state retained. Explicitly invoke again to inspect; no automatic retry or cleanup."
        ) from None


def _run(config, *, apply, wait_for_mirrors, timeout, pg_connect, ch_connect, request):
    if type(apply) is not bool:
        raise SetupError("apply must be explicitly true or false")
    if type(wait_for_mirrors) is not bool or (wait_for_mirrors and apply):
        raise SetupError("mirror readiness waiting is read-only and cannot use apply")
    if apply and config.hosted:
        raise SetupError("hosted/replicated PeerDB apply is not supported")
    if config.source.name == config.destination.name or any(
        not _NAME.fullmatch(t.name) for t in (config.source, config.destination)
    ):
        raise SetupError("distinct literal source and destination peer names required")
    if any(os.environ.get(k) for k in ("PGHOSTADDR", "PGSERVICE")):
        raise SetupError("PGHOSTADDR/PGSERVICE routing is not supported")
    deadline = install.Deadline(timeout)
    request = request or install.peerdb_transport(
        config.peerdb_url, deadline, allow_create=apply
    )
    import clickhouse_connect
    import psycopg

    with ExitStack() as stack:
        pg = (pg_connect or psycopg.connect)(
            host=config.source.host,
            port=config.source.port,
            dbname=config.source.database,
            user=config.pg_user,
            password=config.pg_password,
            autocommit=False,
            connect_timeout=max(1, math.ceil(deadline.remaining(3))),
            options="-c statement_timeout=10000 -c lock_timeout=2000",
        )
        stack.callback(pg.close)
        pg.read_only = True
        pg.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        if apply:
            identity = json.dumps(config.destination.endpoint, separators=(",", ":"))
            key = int.from_bytes(
                hashlib.sha256(("oss-cdc-bootstrap:" + identity).encode()).digest()[:8],
                "big",
                signed=True,
            )
            if pg.execute(
                "SELECT pg_try_advisory_xact_lock(%s)", (key,)
            ).fetchall() != [(True,)]:
                raise SetupError("another catalog installer holds the target lock")

        def pg_query(statement, parameters):
            if not statement.lstrip().startswith("SELECT "):
                raise SetupError("source inspection must use SELECT")
            deadline.remaining()
            return pg.execute(statement, parameters).fetchall()

        source = inspect_source(
            pg_query, source=config.source, tables=tuple(core.LANDING)
        )
        definitions = core._source_definitions(source)
        pool = install._SingleAttemptPool()
        stack.callback(pool.clear)
        raw = (ch_connect or clickhouse_connect.get_client)(
            host=config.destination.host,
            port=config.http_port,
            database=config.destination.database,
            username=config.ch_user,
            password=config.ch_password,
            connect_timeout=deadline.remaining(3),
            send_receive_timeout=deadline.remaining(),
            query_retries=0,
            pool_mgr=pool,
            show_clickhouse_errors=False,
            autogenerate_session_id=False,
        )
        stack.callback(raw.close)

        class Reader:
            def query(self, statement, parameters=None, settings=None):
                if not statement.lstrip().startswith("SELECT "):
                    raise SetupError("ClickHouse inspection must use SELECT")
                return raw.query(
                    statement,
                    parameters=parameters,
                    settings={
                        **(settings or {}),
                        "readonly": 1,
                        "max_threads": 1,
                        "max_execution_time": max(1, math.ceil(deadline.remaining(5))),
                    },
                )

        client = Reader()
        missing_native = native.inspect_native(
            client, database=config.destination.database
        )
        while True:
            deadline.remaining()
            missing_peers, mappings, pending, mirror_names = _inspect_peers(
                request, config
            )
            if not pending:
                break
            # Reinspect identities/configuration on every read-only poll; never
            # put a CREATE or an uncertain failure inside this loop.
            complete = (
                not missing_peers
                and set(mappings) == set(core.LANDING)
                and not missing_native
            )
            if wait_for_mirrors and complete:
                time.sleep(deadline.remaining(2))
                continue
            # Pending physical metadata cannot authorize a CREATE, even for a
            # different missing mirror. Default checks still report not ready.
            return {
                "ready": False,
                "accepted": apply and complete,
                "applied": False,
                "pending_mirrors": pending,
                "missing_native": list(missing_native),
                "created_peers": [],
                "created_mirrors": [],
            }
        present = _inspect_landing(client, config, definitions)
        if present - mappings.keys():
            raise SetupError(
                "existing landing table has no mirror; refusing initial copy into retained data"
            )
        if mappings.keys() - present:
            raise SetupError(
                "running mirror has no landing table; no repair is permitted"
            )
        missing_tables = [table for table in core.LANDING if table not in mappings]
        if missing_tables and _mirror_name(config, missing_tables) in mirror_names:
            raise SetupError("required new mirror name is already occupied")
        plan = _creates(config, missing_peers, missing_tables)
        result = {
            "ready": not (plan or missing_native),
            "accepted": False,
            "applied": False,
            "missing_peers": missing_peers,
            "missing_mirrors": [_mirror_name(config, missing_tables)]
            if missing_tables
            else [],
            "missing_tables": missing_tables,
            "missing_native": list(missing_native),
            "pending_mirrors": [],
            "created_peers": [],
            "created_mirrors": [],
        }
        if not apply or missing_native:
            return result
        for kind, name, payload in plan:
            deadline.remaining()
            _create_once(request, kind, name, payload)
            result["created_peers" if kind == "peer" else "created_mirrors"].append(
                name
            )
        result.update(accepted=True, applied=True)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Create only absent OSS peers/mirrors after preflight",
    )
    mode.add_argument(
        "--wait-for-mirrors",
        action="store_true",
        help="Poll compatible retained setup/snapshot states read-only before checking landing schemas",
    )
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--source-peer", default="pg_source")
    parser.add_argument("--destination-peer", default="ch_dest")
    parser.add_argument("--peerdb-url", default=None)
    args = parser.parse_args(argv)
    try:
        config = Config.from_env(
            os.environ,
            source_peer=args.source_peer,
            destination_peer=args.destination_peer,
            peerdb_url=args.peerdb_url,
        )
        install.Deadline(args.timeout)
        with install._wall_clock_limit(args.timeout):
            result = run(
                config,
                apply=args.apply,
                wait_for_mirrors=args.wait_for_mirrors,
                timeout=args.timeout,
            )
        print(json.dumps(result))
        return 0 if result["ready"] or (args.apply and result["accepted"]) else 1
    except SetupError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        print(
            "PeerDB setup failed; partial state retained. No automatic retry or cleanup.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
