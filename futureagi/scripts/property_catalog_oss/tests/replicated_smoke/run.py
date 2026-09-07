#!/usr/bin/env python3
"""Explicit phases: plan -> coordinated up -> bootstrap -> probe -> faults -> cleanup."""

from __future__ import annotations

import argparse
import base64
import http.client
import http.server
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from harness import Docker, canonical, digest, load, plan, read_file, save_new
from schema_probe import (
    adapter_probes,
    capture,
    exact_schema,
    reservation_row,
    schema_bundle,
    source_fingerprints,
    topology_errors,
)

TABLE = "property_catalog_source_streams"
NODES = ("replica1", "replica2")


class UnknownWrite(RuntimeError):
    pass


def request_once(port, path, body, authorization, *, write=False):
    """No redirect, retry, environment proxy, or ambiguous-success fallback."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        connection.request(
            "POST",
            path,
            body=body,
            headers={
                "Authorization": authorization,
                "Content-Type": "text/plain; charset=utf-8",
            },
        )
        response = connection.getresponse()
        raw = response.read((1 << 20) + 1)
        if len(raw) > 1 << 20:
            raise ValueError("response exceeds bounded evidence limit")
        length = response.getheader("Content-Length")
        if length is not None and int(length) != len(raw):
            raise ValueError("incomplete Content-Length response body")
        exception = response.getheader("X-ClickHouse-Exception-Code", "0")
        if response.status != 200 or exception != "0" or (write and raw):
            raise ValueError(
                f"HTTP {response.status}, exception={exception}, body={raw[:4096]!r}"
            )
        return raw
    except Exception as exc:
        if write:
            raise UnknownWrite(f"{type(exc).__name__}: {exc}; NOT RETRIED") from exc
        raise
    finally:
        connection.close()


def lose_ack(port, path, body, authorization):
    """One isolated loopback hop: forward once, capture upstream result, drop ACK."""
    evidence = {"forward_attempts": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if (
                self.path != path
                or self.headers.get("Authorization") != authorization
                or self.headers.get("Content-Length") != str(len(body))
            ):
                self.send_error(403)
                return
            received = self.rfile.read(len(body))
            if received != body:
                self.send_error(400)
                return
            evidence["forward_attempts"] += 1
            evidence["upstream"] = capture(
                lambda: request_once(
                    port, path, received, authorization, write=True
                ).decode()
            )
            # Deliberate response loss AFTER forwarding. Do not send a status
            # line or retry upstream, including when the upstream result itself
            # is uncertain. Physical reads are the only subsequent resolution.
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 20
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        evidence["client"] = capture(
            lambda: request_once(
                server.server_port, path, body, authorization, write=True
            ).decode()
        )
        thread.join(timeout=20)
        if thread.is_alive():
            raise RuntimeError("ACK-loss proxy did not finish; do not rerun the write")
    finally:
        server.server_close()
    return evidence


def row_witness(expected, observed):
    identities = {
        node: {"rows": rows, "row_sha256": digest(canonical(rows))}
        for node, rows in observed.items()
    }
    exact = {node: rows == [expected] for node, rows in observed.items()}
    return {
        "nodes": identities,
        "exact_on_each_replica": exact,
        "all_replica_visibility_proven": set(exact) == set(NODES)
        and all(exact.values()),
        "claim": "exact bounded row visibility; not fsync/power-loss or atomic multi-table durability",
    }


class Runner:
    def __init__(self, directory, m, docker):
        self.directory, self.m, self.docker = directory, m, docker
        if m["source_files"] != source_fingerprints():
            raise RuntimeError(
                "captured product/schema inputs changed; use a fresh plan"
            )
        self.bundle = json.loads(read_file(directory / "schema.json"))
        if self.bundle != schema_bundle(m):
            raise RuntimeError("captured schema differs from exact pinned renderer")

    def auth(self, write=False):
        user = "smoke_admin" if write else "smoke_probe"
        return (
            "Basic "
            + base64.b64encode((user + ":" + self.m["password"]).encode()).decode()
        )

    def sql(self, node, sql, *, write=False):
        if node not in NODES:
            raise ValueError("unknown replica")
        if not write and not sql.lstrip().upper().startswith(("SELECT ", "SHOW ")):
            raise ValueError("read-only proof attempted a mutation")
        settings = {
            "max_execution_time": 10,
            "max_threads": 1,
            "max_memory_usage": 268435456,
            "max_result_rows": 512,
            "max_result_bytes": 1 << 20,
            "result_overflow_mode": "throw",
            "wait_end_of_query": 1,
        }
        if not write:
            settings.update(readonly=2, output_format_json_quote_64bit_integers=0)
            sql += " FORMAT JSONEachRow"
        raw = request_once(
            self.m["ports"][node + "_http"],
            "/?" + urlencode(settings),
            sql.encode(),
            self.auth(write),
            write=write,
        )
        return [] if write else [json.loads(line) for line in raw.splitlines()]

    def verify_owned(self):
        resources = self.docker.owned(running=True)
        image = json.loads(read_file(self.directory / "image.json"))
        if any(
            row["Image"] != image["id"]
            for kind, _name, row in resources
            if kind == "container"
        ):
            raise RuntimeError("actual image differs from captured image identity")
        for node in NODES:
            identity = self.sql(
                node,
                "SELECT hostName() AS host, version() AS version, currentUser() AS user",
            )
            if (
                len(identity) != 1
                or identity[0]["host"] != self.m["project"] + "-" + node
                or identity[0]["user"] != "smoke_probe"
                or not identity[0]["version"].startswith("25.3.")
            ):
                raise RuntimeError(
                    f"endpoint/version is not the exact owned CH25.3 replica: {identity}"
                )
        return [
            {
                "kind": kind,
                "name": name,
                "id": row.get("Id"),
                "state": row.get("State"),
                "image": row.get("Image"),
            }
            for kind, name, row in resources
        ]

    def bootstrap(self):
        deadline = time.monotonic() + 45
        while True:
            try:
                self.verify_owned()
                break
            except (OSError, http.client.HTTPException):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        save_new(
            self.directory / "bootstrap-intent.json",
            {"outcome": "starting", "atomic_multi_table": False},
        )
        # Only the fourteen planned CREATEs per server; no source tables,
        # ALTERs, arbitrary SQL input, or production installer invocation.
        for node in NODES:
            for expected in self.bundle["databases"].values():
                self.sql(
                    node,
                    f"CREATE DATABASE {expected['name']} ENGINE = Atomic",
                    write=True,
                )
                for table in expected["tables"]:
                    self.sql(node, table["sql"], write=True)
        return {
            "outcome": "created",
            "database_engine": "Atomic",
            "replication": "table-level, not replicated database DDL",
        }

    def probe(self, *, product_adapters=True):
        evidence = {
            "resources": self.verify_owned(),
            "fixture_replicas": 2,
            "production_required_replicas": self.bundle["production_required_replicas"],
            "production_admitted": False,
            "keeper_ha_proven": False,
            "nodes": {},
        }
        for node in NODES:
            result = {
                "databases": {},
                "cluster": self.sql(
                    node,
                    "SELECT shard_num, replica_num, host_name FROM system.clusters WHERE cluster='smoke_cluster' ORDER BY replica_num",
                ),
                "settings": self.sql(
                    node,
                    "SELECT name, value, readonly FROM system.settings WHERE name IN ('readonly','async_insert','wait_for_async_insert','insert_quorum','insert_quorum_parallel','select_sequential_consistency') ORDER BY name",
                ),
                "grants": self.sql(node, "SHOW GRANTS FOR CURRENT_USER"),
            }
            for family, expected in self.bundle["databases"].items():
                database = expected["name"]
                tables = self.sql(
                    node,
                    f"SELECT database, name, engine, engine_full, sorting_key, primary_key, partition_key, create_table_query FROM system.tables WHERE database='{database}' ORDER BY name",
                )
                schema_result = capture(
                    lambda tables=tables, expected=expected: exact_schema(
                        tables, expected
                    )
                )
                replica_rows = self.sql(
                    node,
                    f"SELECT table, zookeeper_path, replica_name, is_readonly, is_session_expired, queue_size, active_replicas, total_replicas FROM system.replicas WHERE database='{database}' ORDER BY table",
                )
                errors, keeper_children = [], {}
                if family == "replicated":
                    errors = topology_errors(replica_rows, expected, node)
                    for table in expected["tables"]:
                        path = table["keeper_path"].replace("{shard}", "1")
                        children = self.sql(
                            node,
                            f"SELECT name FROM system.zookeeper WHERE path='{path}/replicas' ORDER BY name",
                        )
                        keeper_children[table["name"]] = children
                        if children != [{"name": name} for name in NODES]:
                            errors.append(
                                "wrong Keeper replica children: " + table["name"]
                            )
                elif replica_rows:
                    errors.append(
                        "standalone tables unexpectedly registered with Keeper"
                    )
                result["databases"][family] = {
                    "database": self.sql(
                        node,
                        f"SELECT name, engine FROM system.databases WHERE name='{database}'",
                    ),
                    "tables": tables,
                    "exact_schema": schema_result,
                    "replicas": replica_rows,
                    "topology_errors": errors,
                    "keeper_children": keeper_children,
                    "all_local_queues_empty": all(
                        not row["queue_size"]
                        and not row["is_readonly"]
                        and not row["is_session_expired"]
                        for row in replica_rows
                    )
                    if replica_rows
                    else None,
                    "product_adapters": adapter_probes(self.m, node, database)
                    if product_adapters
                    else "not_requested",
                }
            evidence["nodes"][node] = result
        evidence["fixture_exact_topology_and_schema"] = all(
            db["exact_schema"]["outcome"] == "returned"
            and not db["topology_errors"]
            and db["database"]
            == [{"name": self.bundle["databases"][family]["name"], "engine": "Atomic"}]
            for result in evidence["nodes"].values()
            for family, db in result["databases"].items()
        )
        return evidence

    def witness(self, row):
        database = self.bundle["databases"]["replicated"]["name"]
        predicate = (
            f"organization_id='{row['organization_id']}' AND workspace_id='{row['workspace_id']}' "
            f"AND catalog_epoch=1 AND catalog_revision={row['catalog_revision']} AND build_token='{row['build_token']}' "
            f"AND source_adapter='system_manifest' AND producer_stream_id='{row['producer_stream_id']}'"
        )
        return row_witness(
            row,
            {
                node: self.sql(
                    node, f"SELECT * FROM {database}.{TABLE} WHERE {predicate} LIMIT 3"
                )
                for node in NODES
            },
        )

    def scenarios(self, confirmed):
        if confirmed != self.m["run_id"]:
            raise RuntimeError("explicit run-id fault-injection confirmation required")
        before = self.probe()
        if not before["fixture_exact_topology_and_schema"]:
            raise RuntimeError(
                "fault injection requires exact baseline schema and shared two-replica membership"
            )
        save_new(
            self.directory / "scenario-intent.json",
            {
                "outcome": "starting",
                "before": before,
                "retry_policy": "NO INSERT REPLAY, even after process death",
            },
        )
        database = self.bundle["databases"]["replicated"]["name"]
        results = []
        for revision, name in ((1, "lag_without_quorum"), (2, "lost_http_ack")):
            row = reservation_row(self.m, revision)
            body = (
                f"INSERT INTO {database}.{TABLE} FORMAT JSONEachRow\n".encode()
                + canonical(row)
            )
            query_id = self.m["project"] + "-" + name
            # These mirror the synchronous batched Go INSERT settings. The
            # intentionally absent quorum is the gap under test, not a fix.
            settings = {
                "query_id": query_id,
                "async_insert": 0,
                "wait_end_of_query": 1,
                "max_execution_time": 10,
                "max_threads": 1,
                "max_memory_usage": 268435456,
            }
            path = "/?" + urlencode(settings)
            save_new(
                self.directory / (name + "-write-intent.json"),
                {
                    "query_id": query_id,
                    "body_sha256": digest(body),
                    "expected_row": row,
                    "settings": settings,
                    "initial_outcome": "UNKNOWN",
                    "attempt_limit": 1,
                },
            )
            result = {
                "scenario": name,
                "query_id": query_id,
                "body_sha256": digest(body),
                "write_attempts": 1,
            }
            self.verify_owned()
            self.sql("replica2", f"SYSTEM STOP FETCHES {database}.{TABLE}", write=True)
            try:
                if name == "lost_http_ack":
                    result["transport"] = lose_ack(
                        self.m["ports"]["replica1_http"], path, body, self.auth(True)
                    )
                else:
                    result["transport"] = capture(
                        lambda path=path, body=body: request_once(
                            self.m["ports"]["replica1_http"],
                            path,
                            body,
                            self.auth(True),
                            write=True,
                        ).decode()
                    )
                save_new(
                    self.directory / (name + "-transport.json"), result["transport"]
                )
                result["while_fetches_stopped"] = self.witness(row)
                result["lagging_topology"] = self.probe()
                local_only = result["while_fetches_stopped"][
                    "exact_on_each_replica"
                ] == {"replica1": True, "replica2": False}
                if name == "lag_without_quorum":
                    observed = (
                        local_only and result["transport"]["outcome"] == "returned"
                    )
                else:
                    observed = (
                        local_only
                        and result["transport"]["forward_attempts"] == 1
                        and result["transport"]["client"].get("type") == "UnknownWrite"
                    )
                result["failure_result"] = (
                    "OBSERVED_MISSING_ALL_REPLICA_VISIBILITY"
                    if observed
                    else "NOT_REPRODUCED_OR_DIFFERENT_FAILURE"
                )
            finally:
                # Restart only the exact table's fetches, never its container,
                # and never resend an uncertain data write.
                self.sql(
                    "replica2", f"SYSTEM START FETCHES {database}.{TABLE}", write=True
                )
            result["sync_result"] = capture(
                lambda: self.sql(
                    "replica2", f"SYSTEM SYNC REPLICA {database}.{TABLE}", write=True
                )
            )
            result["after_fetch_resume"] = self.witness(row)
            save_new(self.directory / (name + "-result.json"), result)
            results.append(result)
        return {
            "scenarios": results,
            "product_fixed": False,
            "production_admitted": False,
            "minimum_gap_demonstrated": all(
                r["failure_result"] == "OBSERVED_MISSING_ALL_REPLICA_VISIBILITY"
                for r in results
            ),
            "all_replica_catchup_observed": all(
                r["after_fetch_resume"]["all_replica_visibility_proven"]
                for r in results
            ),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "phase",
        choices=(
            "plan",
            "up",
            "bootstrap",
            "probe",
            "scenarios",
            "adapter-ack",
            "cleanup",
        ),
        nargs="?",
        default="plan",
    )
    parser.add_argument("directory", type=Path, nargs="?")
    parser.add_argument(
        "--confirm-headroom",
        help="run id; only after coordinating the full 6.5 GiB workload",
    )
    parser.add_argument(
        "--confirm-faults",
        help="run id; authorizes bounded owned-table fetch stop and two single INSERTs",
    )
    parser.add_argument(
        "--confirm-adapter-ack",
        help="run id; isolated async-profile user and two actual adapter INSERTs",
    )
    args = parser.parse_args()
    # This process may import product library modules, never their ambient
    # deployment credentials/settings. Docker context discovery still needs HOME.
    for key in list(os.environ):
        if key not in {"PATH", "HOME", "LANG", "LC_ALL"}:
            del os.environ[key]
    if args.phase == "plan":
        if (
            args.directory
            or args.confirm_headroom
            or args.confirm_faults
            or args.confirm_adapter_ack
        ):
            parser.error("plan allocates a new owned directory; no live flags")
        directory, m = plan()
        print(
            json.dumps(
                {
                    "directory": str(directory),
                    "run_id": m["run_id"],
                    "infra_started": False,
                    "memory_cap_gib": 6.5,
                    "production_admitted": False,
                },
                indent=2,
            )
        )
        return 0
    if args.directory is None:
        parser.error("an exact owned run directory is required")
    directory = args.directory.absolute()
    m = load(directory)
    docker = Docker(directory, m)
    docker.connect()
    result = {"phase": args.phase, "run_id": m["run_id"]}
    try:
        if args.phase == "cleanup":
            docker.cleanup()
            result["owned_test_resources_removed"] = True
        elif args.phase == "up":
            docker.up(args.confirm_headroom)
            result["started"] = True
        else:
            runner = Runner(directory, m, docker)
            from adapter_ack import run_adapter_ack

            result.update(
                runner.bootstrap()
                if args.phase == "bootstrap"
                else runner.probe()
                if args.phase == "probe"
                else run_adapter_ack(runner, args.confirm_adapter_ack)
                if args.phase == "adapter-ack"
                else runner.scenarios(args.confirm_faults)
            )
        exit_code = 0
        if args.phase == "probe" and not result["fixture_exact_topology_and_schema"]:
            exit_code = 2
        if (
            args.phase == "adapter-ack"
            and not result["actual_adapter_synchronous_ack_proven"]
        ):
            exit_code = 2
        if args.phase == "scenarios" and not (
            result["minimum_gap_demonstrated"]
            and result["all_replica_catchup_observed"]
        ):
            exit_code = 2
    except Exception as exc:
        result.update(
            outcome="ERROR",
            type=type(exc).__name__,
            error=str(exc),
            automatic_write_retry=False,
        )
        exit_code = 1
    evidence = directory / (args.phase + "-" + secrets.token_hex(6) + ".json")
    save_new(evidence, result)
    print(
        json.dumps(
            {
                "evidence": str(evidence),
                "exit_code": exit_code,
                "production_admitted": False,
            }
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
