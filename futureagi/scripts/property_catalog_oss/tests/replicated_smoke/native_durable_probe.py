"""Owned native INSERT/journal/proof diagnostic, not a lifecycle/Kafka release gate."""

import argparse
import json
import os
import re
import time
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from admission_probe import probe as admission_probe
from go_durable_probe import PROOF_TABLES, SYSTEM_TABLES, process_env, wait_for_keeper
from harness import ROOT, Docker, digest, load, plan, read_file, save_new
from run import NODES, Runner
from schema_probe import configure_libraries


def sources():
    root = ROOT / "futureagi/tracer/services/clickhouse/v2/property_catalog"
    names = (
        "native_write_proof.py",
        "native_write_transport.py",
        "native_write_journal.py",
        "durable_native_writer.py",
        "write_admission.py",
        "write_endpoint.py",
        "keeper_membership.py",
    )
    return {name: digest(read_file(root / name)) for name in names}


def cell(name, kind, *, run_id):
    """Schema-valid synthetic cells; these rows are NOT qualified catalog state."""
    if kind.startswith("Nullable("):
        return None
    if kind.startswith("Array("):
        return [cell(name, kind[6:-1], run_id=run_id)]
    if "Enum" in kind:
        options = re.findall(r"'([^']+)'\s*=\s*-?\d+", kind)
        if not options:
            raise ValueError("unknown fixture enum")
        return "disable" if "disable" in options else options[0]
    if "UUID" in kind:
        return uuid5(NAMESPACE_URL, "native-durable:" + run_id + ":" + name)
    if "DateTime" in kind:
        return datetime(2026, 9, 6, microsecond=123456, tzinfo=UTC)
    if "Int" in kind:
        return 0 if name.startswith("is_") else 1
    if "String" in kind:
        if (
            "sha256" in name
            or "fingerprint" in name
            or "digest" in name
            or "FixedString(64)" in kind
        ):
            return "0" * 64
        if name == "value_json":
            return '"fixture-native-value"'
        return "fixture-native-value"
    raise ValueError("unsupported native fixture type: " + kind)


def grants(manifest, family):
    database = "property_catalog_dev_" + family + "_" + manifest["run_id"]
    writer, proof = "smoke_native_writer_" + family, "smoke_native_proof_" + family
    password = digest(manifest["password"].encode())
    result = [
        f"CREATE USER {writer} IDENTIFIED WITH sha256_hash BY '{password}' SETTINGS "
        "readonly=0,async_insert=1,wait_for_async_insert=0,log_queries=1,log_query_settings=1,"
        "log_queries_probability=1,log_queries_min_query_duration_ms=0,max_threads=1,"
        "max_memory_usage=268435456,max_execution_time=10",
        f"CREATE USER {proof} IDENTIFIED WITH sha256_hash BY '{password}' SETTINGS readonly=2,max_threads=1,max_memory_usage=134217728",
    ]
    result += [
        f"GRANT INSERT ON {database}.{table} TO {writer}" for table in PROOF_TABLES
    ]
    result += [
        f"GRANT SELECT ON {database}.{table} TO {proof}" for table in PROOF_TABLES
    ]
    result += [f"GRANT SELECT ON system.{table} TO {proof}" for table in SYSTEM_TABLES]
    return result


def exercise(directory, manifest, runner):
    configure_libraries()
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.v2.property_catalog import (
        durable_native_writer as writer_module,
    )
    from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
        IDENTITY_FILENAME,
        load_identity,
    )
    from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
        NativeWriteJournal,
    )
    from tracer.services.clickhouse.v2.property_catalog.native_write_proof import (
        _COLUMNS,
        NativeWriteProof,
    )
    from tracer.services.clickhouse.v2.property_catalog.write_admission import (
        DirectCatalogConnection,
        WriteAdmission,
    )

    report = {}
    real_insert_once = writer_module.insert_once
    for family in ("standalone", "replicated"):
        database = "property_catalog_dev_" + family + "_" + manifest["run_id"]
        runtime = directory / ("admission-runtime-" + family)
        admission = WriteAdmission.decode(
            read_file(runtime / "write-admission-v1.json")
        )
        identity = load_identity(runtime / IDENTITY_FILENAME)
        nodes = NODES if family == "replicated" else NODES[:1]
        with ExitStack() as stack:
            connections = []
            for node in nodes:
                driver = ClickHouseClient(
                    host="127.0.0.1",
                    port=manifest["ports"][node + "_native"],
                    user="smoke_native_proof_" + family,
                    password=manifest["password"],
                    database=database,
                    pool_size=1,
                    connect_timeout=3,
                    server_enforced_readonly=True,
                    allow_query_settings_with_server_readonly=True,
                )
                stack.callback(driver.close)
                member = next(m for m in admission.members if m.name == node)
                connections.append(
                    DirectCatalogConnection(
                        name=node,
                        native_member_host="127.0.0.1",
                        expected_hostname=member.hostname,
                        driver=driver,
                        proof_username="smoke_native_proof_" + family,
                        proof_password=manifest["password"],
                    )
                )
            proof = NativeWriteProof(
                directory=runtime,
                identity=identity,
                admission=admission,
                connections=connections,
                route_resolver=lambda connection, discovered: (
                    "http://127.0.0.1:"
                    + str(manifest["ports"][connection.name + "_http"])
                ),
            )
            native = ClickHouseClient(
                host="127.0.0.1",
                port=manifest["ports"][nodes[0] + "_native"],
                user="smoke_native_writer_" + family,
                password=manifest["password"],
                database=database,
                pool_size=1,
                connect_timeout=3,
                server_enforced_readonly=False,
            )
            stack.callback(native.close)
            writer = writer_module.DurableNativeCatalogWriter(
                native, directory=runtime, proof=proof, member_name=nodes[0]
            )
            results, dispatches = [], []

            def dispatch(*args, dispatches=dispatches, family=family, **kwargs):
                result = real_insert_once(*args, **kwargs)
                dispatches.append(kwargs["query_id"])
                # One injected full-response loss AFTER the actual native client
                # completed. Sent is real/fsynced, ACK receipt was not written.
                if family == "replicated" and len(dispatches) == 7:
                    raise TimeoutError("fixture lost exactly one full native ACK")
                return result

            writer_module.insert_once = dispatch
            try:
                for table in _COLUMNS:
                    runner.verify_owned()
                    metadata = runner.sql(
                        nodes[0],
                        f"SELECT name,type FROM system.columns WHERE database='{database}' AND table='{table}' ORDER BY position",
                    )
                    row = {
                        item["name"]: cell(
                            item["name"], item["type"], run_id=manifest["run_id"]
                        )
                        for item in metadata
                    }
                    if set(row) != set(_COLUMNS[table]):
                        raise ValueError(
                            "fixture column inventory differs from pinned writer"
                        )
                    # No reader is bound; all rows are synthetic serialization
                    # fixtures. Explicit DISABLE where the action enum permits it.
                    token = "native-owned:" + family + ":" + table
                    options = {
                        "table": f"`{database}`.`{table}`",
                        "rows": [row],
                        "columns": _COLUMNS[table],
                        "timeout_ms": 30_000,
                        "deduplication_token": token,
                    }
                    started = time.monotonic()
                    lost = False
                    try:
                        writer.insert(**options)
                    except TimeoutError as exc:
                        if str(exc) != "fixture lost exactly one full native ACK":
                            raise
                        lost = True
                        with NativeWriteJournal(runtime) as journal:
                            with journal.session(table, token) as session:
                                saved = session.load()
                                if saved.state != "sent":
                                    raise ValueError(
                                        "lost ACK did not retain durable Sent"
                                    ) from exc
                        writer = writer_module.DurableNativeCatalogWriter(
                            native, directory=runtime, proof=proof, member_name=nodes[0]
                        )
                        deadline = time.monotonic() + 25
                        while True:
                            try:
                                writer.insert(
                                    **options
                                )  # only actual QueryFinish resolution; no resend
                                break
                            except writer_module.NativeWriteUnresolved:
                                if time.monotonic() >= deadline:
                                    raise
                                time.sleep(0.25)
                    writer.insert(**options)  # completed duplicate is proof-only
                    with NativeWriteJournal(runtime) as journal:
                        with journal.session(table, token) as session:
                            saved = session.load()
                            if saved.state != "complete":
                                raise ValueError("native receipt not complete")
                            results.append(
                                {
                                    "table": table,
                                    "state": saved.state,
                                    "query_id": saved.query_id,
                                    "lost_full_ack": lost,
                                    "acknowledgement": dict(saved.acknowledgement),
                                    "elapsed_seconds": round(
                                        time.monotonic() - started, 3
                                    ),
                                }
                            )
                if len(dispatches) != 7 or len(set(dispatches)) != 7:
                    raise ValueError("native INSERT was repeated or omitted")
                report[family] = {
                    "status": "passed",
                    "dispatch_count": len(dispatches),
                    "tables": results,
                }
            finally:
                writer_module.insert_once = real_insert_once
    return report


def execute(directory, confirmation):
    manifest = load(directory)
    prepared = json.loads(read_file(directory / "native-plan.json"))
    if confirmation != manifest["run_id"] or prepared["sources"] != sources():
        raise ValueError("native fixture plan/confirmation changed")
    save_new(
        directory / "native-execute-intent.json",
        {"run_id": confirmation, "one_shot": True},
    )
    docker = Docker(directory, manifest)
    docker.connect()
    outcome = {
        "run_id": confirmation,
        "status": "failed",
        "production_admitted": False,
        "kafka_tested": False,
        "activation_tested": False,
    }
    try:
        docker.up(confirmation)
        runner = Runner(directory, manifest, docker)
        wait_for_keeper(runner)
        runner.bootstrap()
        admission_probe(directory)
        for family in ("standalone", "replicated"):
            for node in NODES if family == "replicated" else NODES[:1]:
                runner.verify_owned()
                for statement in grants(manifest, family):
                    runner.sql(node, statement, write=True)
        print(
            json.dumps(
                {
                    "status": "native_fixture_running",
                    "directory": str(directory),
                    "run_id": confirmation,
                }
            ),
            flush=True,
        )
        outcome["families"] = exercise(directory, manifest, runner)
        if prepared["sources"] != sources():
            raise ValueError("native sources changed during fixture")
        outcome["status"] = "passed"
    except BaseException as exc:
        outcome["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        try:
            docker.cleanup()
            outcome["cleanup"] = (
                "all owned containers/volumes/network absent; evidence retained"
            )
        finally:
            save_new(directory / "native-live-result.json", outcome)
            print(json.dumps(outcome), flush=True)


if __name__ == "__main__":
    environment = process_env()
    os.environ.clear()
    os.environ.update(environment)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("plan", "execute"))
    parser.add_argument("directory", nargs="?", type=Path)
    parser.add_argument("--confirm-run-id")
    args = parser.parse_args()
    if args.phase == "plan":
        if args.directory or args.confirm_run_id:
            parser.error("plan has no prior resource target")
        directory, manifest = plan()
        save_new(
            directory / "native-plan.json",
            {"sources": sources(), "run_id": manifest["run_id"]},
        )
        print(
            json.dumps(
                {
                    "directory": str(directory),
                    "run_id": manifest["run_id"],
                    "status": "planned_no_infrastructure",
                }
            )
        )
    else:
        if not args.directory or not args.confirm_run_id:
            parser.error("execute requires directory and exact --confirm-run-id")
        execute(args.directory.absolute(), args.confirm_run_id)
