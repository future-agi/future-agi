#!/usr/bin/env python3
"""Opt-in RRMT source -> product MergeTree capture gate; NOT a serving gate.

Plan is offline. Execute requires the parent's released 6.5 GiB fixture budget
and exact run-id confirmation. No private image, application, production
admission, or automatic retry of a write is involved. Source DDL/DML occurs
only during fixture-admin bootstrap, before the unchanged-source baseline.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from contextlib import ExitStack
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import sqlparse
from go_durable_probe import process_env, wait_for_keeper
from harness import (
    ROOT,
    Docker,
    digest,
    load,
    plan,
    read_file,
    save_new,
    validate_manifest,
)
from run import NODES, Runner
from schema_probe import configure_libraries

SOURCE_FILE = ROOT / "futureagi/tracer/services/clickhouse/v2/schema/002_spans_v2.sql"
MIGRATIONS = (
    SOURCE_FILE.with_name("013_attributes_extra_as_string.sql"),
    SOURCE_FILE.with_name("014_peerdb_is_deleted_back_compat.sql"),
)
WRITER, READER = "smoke_capture_writer", "smoke_capture_source"
SOURCE_ENGINE = "ReplicatedReplacingMergeTree"
EXPECTED_VALUES = {"live": "new", "stable": "kept"}
PHYSICAL_ROWS = 5
SCOPE = "two-replica canonical source capture/scanner/audit only; no serving, factory route, or three-replica production admission"


def names(manifest):
    validate_manifest(manifest)
    token = manifest["run_id"]
    return "pc_capture_source_" + token, "pc_capture_catalog_" + token


def canonical_source_create(manifest):
    """Only bind the object and RRMT transport args; keep the actual full DDL."""
    source, _ = names(manifest)
    ddl = sqlparse.split(read_file(SOURCE_FILE).decode())[0]
    header = "CREATE TABLE IF NOT EXISTS spans"
    engine = "ENGINE = ReplacingMergeTree(_version, is_deleted)"
    if ddl.count(header) != 1 or ddl.count(engine) != 1:
        raise ValueError("canonical source DDL changed; refuse an approximate fixture")
    return ddl.replace(header, f"CREATE TABLE `{source}`.`spans`", 1).replace(
        engine,
        f"ENGINE = {SOURCE_ENGINE}('/clickhouse/capture-probe/{manifest['run_id']}/spans', "
        "'{replica}', _version, is_deleted)",
        1,
    )


def sources():
    root = ROOT / "futureagi/tracer/services/clickhouse/v2"
    files = sorted((root / "property_catalog").glob("*.py"))
    files += [
        SOURCE_FILE,
        *MIGRATIONS,
        root.parent / "client.py",
        root / "attribute_catalog_backfill.py",
        root / "attribute_catalog_builder.py",
        Path(__file__),
        Path(__file__).with_name("harness.py"),
    ]
    return {str(path.relative_to(ROOT)): digest(read_file(path)) for path in files}


def source_migrations(manifest):
    """Normal source bootstrap only, before fixture rows/baseline; once on RRMT."""
    source, _ = names(manifest)
    result = []
    for path in MIGRATIONS:
        statements = sqlparse.split(
            sqlparse.format(read_file(path).decode(), strip_comments=True)
        )
        if len(statements) != 1 or not statements[0].startswith("ALTER TABLE spans\n"):
            raise ValueError(
                "canonical source migration changed; refuse approximate setup"
            )
        result.append(
            statements[0].replace(
                "ALTER TABLE spans\n", f"ALTER TABLE `{source}`.`spans`\n", 1
            )
        )
    return tuple(result)


def prepare():
    directory, manifest = plan()
    ddl = canonical_source_create(manifest)
    save_new(directory / "capture-source.sql", ddl.encode())
    save_new(
        directory / "capture-plan.json",
        {
            "run_id": manifest["run_id"],
            "sources": sources(),
            "ddl_sha256": digest(ddl.encode()),
            "scope": SCOPE,
        },
    )
    return directory, manifest


def grants(manifest):
    source, catalog = names(manifest)
    capture = catalog + "_source_capture"
    password_hash = digest(manifest["password"].encode())
    statements = [
        f"CREATE USER {WRITER} IDENTIFIED WITH sha256_hash BY '{password_hash}' SETTINGS "
        "readonly=0, max_threads=1, max_execution_time=30, max_memory_usage=268435456",
        f"CREATE USER {READER} IDENTIFIED WITH sha256_hash BY '{password_hash}' SETTINGS "
        "readonly=1, max_threads=1, max_execution_time=30, max_memory_usage=268435456, "
        "max_result_rows=1100, max_result_bytes=8388608, result_overflow_mode='throw', "
        "timeout_overflow_mode='throw', use_query_cache=0",
        f"GRANT SELECT ON `{source}`.spans TO {WRITER}",
        f"GRANT SELECT, INSERT, CREATE TABLE, ALTER DELETE, ALTER TTL, DROP TABLE ON `{capture}`.* TO {WRITER}",
        f"GRANT SELECT ON `{source}`.spans TO {READER}",
        f"GRANT SELECT ON `{capture}`.* TO {READER}",
    ]
    fields = {
        "tables": "database, name, uuid, engine, create_table_query, storage_policy",
        "parts": "database, table, active, name, hash_of_all_files, rows, bytes_on_disk, disk_name",
        "parts_columns": "database, table, active, name, rows, bytes_on_disk, column, type",
        "storage_policies": "policy_name, disks",
        "disks": "name, path, total_space, unreserved_space, keep_free_space, type, is_read_only",
    }
    statements.extend(
        f"GRANT SELECT({columns}) ON system.{table} TO {READER}"
        for table, columns in fields.items()
    )
    return statements


def fixture_insert(manifest, since):
    source, _ = names(manifest)
    project = str(uuid5(NAMESPACE_URL, "capture-project:" + manifest["run_id"]))
    if since.tzinfo != UTC or since != since.replace(
        hour=0, minute=0, second=0, microsecond=0
    ):
        raise ValueError("fixture window must be a UTC day")
    # Disable insert-time replacing for this fixture block only. CH25.3 defaults
    # optimize_on_insert=1, so one block alone does NOT preserve five versions.
    # Do not weaken the physical-preservation assertion or change source DDL.
    rows = [
        ("live", "old", 1, 0),
        ("live", "new", 2, 0),
        ("dead", "removed", 1, 0),
        ("dead", "removed", 2, 1),
        ("stable", "kept", 1, 0),
    ]
    values = ",".join(
        f"('{project}', 'span', 'capture-probe', toDateTime64('{since:%Y-%m-%d} 12:00:00',6,'UTC'), "
        f"'capture-trace', '{identifier}', 'capture-probe', map('capture_gate','{value}'), {version}, {deleted})"
        for identifier, value, version, deleted in rows
    )
    return project, (
        f"INSERT INTO `{source}`.spans "
        "(project_id, observation_type, service_name, start_time, trace_id, id, name, attrs_string, _version, is_deleted) "
        "SETTINGS optimize_on_insert=0 VALUES " + values
    )


def source_witness(runner, source):
    result = {}
    for node in NODES:
        result[node] = {
            "table": runner.sql(
                node,
                "SELECT toString(serverUUID()) AS server_uuid, hostName() AS hostname, "
                "toString(uuid) AS table_uuid, engine, create_table_query FROM system.tables "
                f"WHERE database='{source}' AND name='spans' LIMIT 2",
            ),
            "parts": runner.sql(
                node,
                "SELECT name, toString(hash_of_all_files) AS checksum, rows, bytes_on_disk "
                f"FROM system.parts WHERE database='{source}' AND table='spans' AND active ORDER BY name LIMIT 11",
            ),
            "rows": runner.sql(
                node,
                "SELECT id, _version, is_deleted, attrs_string['capture_gate'] AS value "
                f"FROM `{source}`.spans ORDER BY id, _version, is_deleted, value LIMIT 11",
            ),
            "columns": runner.sql(
                node,
                "SELECT name, type, default_kind, default_expression FROM system.columns "
                f"WHERE database='{source}' AND table='spans' "
                "AND name IN ('attributes_extra','_peerdb_is_deleted') ORDER BY name LIMIT 3",
            ),
        }
    return result


def assert_source_unchanged(before, after):
    if before != after:
        raise RuntimeError(
            "capture operation changed source UUID/schema/physical parts/rows"
        )


def readiness_checks(witness, membership, manifest):
    expected_path = f"/clickhouse/capture-probe/{manifest['run_id']}/spans"
    expected_columns = [
        {
            "name": "_peerdb_is_deleted",
            "type": "UInt8",
            "default_kind": "ALIAS",
            "default_expression": "is_deleted",
        },
        {
            "name": "attributes_extra",
            "type": "String",
            "default_kind": "DEFAULT",
            "default_expression": "'{}'",
        },
    ]
    return {
        node: {
            "membership": membership[node]
            == [
                {
                    "zookeeper_path": expected_path,
                    "replica_name": node,
                    "total_replicas": 2,
                    "active_replicas": 2,
                }
            ],
            "row_count": len(witness[node]["rows"]) == PHYSICAL_ROWS,
            "migrated_columns": witness[node]["columns"] == expected_columns,
            "engine": len(witness[node]["table"]) == 1
            and witness[node]["table"][0]["engine"] == SOURCE_ENGINE,
            "part_row_count": sum(p["rows"] for p in witness[node]["parts"])
            == PHYSICAL_ROWS,
            "replica_rows_agree": witness[node]["rows"] == witness[NODES[0]]["rows"],
        }
        for node in NODES
    }


def wait_replicated_source(runner, manifest, *, directory, timeout=30):
    source, _ = names(manifest)
    started = time.monotonic()
    evidence = {
        "status": "waiting",
        "attempts": 0,
        "witness": {},
        "membership": {},
        "checks": {},
    }
    try:
        while True:
            evidence["attempts"] += 1
            witness = source_witness(runner, source)
            evidence["witness"] = witness
            membership = {
                node: runner.sql(
                    node,
                    "SELECT zookeeper_path, replica_name, total_replicas, active_replicas "
                    f"FROM system.replicas WHERE database='{source}' AND table='spans' LIMIT 2",
                )
                for node in NODES
            }
            evidence["membership"] = membership
            checks = readiness_checks(witness, membership, manifest)
            evidence["checks"] = checks
            if evidence["attempts"] == 1:
                save_new(directory / "capture-readiness-first.json", evidence)
            if all(all(values.values()) for values in checks.values()):
                evidence["status"] = "ready"
                return witness, membership
            if time.monotonic() >= started + timeout:
                failed = [
                    node + ":" + name
                    for node, values in checks.items()
                    for name, passed in values.items()
                    if not passed
                ]
                raise RuntimeError(
                    "replicated source readiness failed: " + ", ".join(failed)
                )
            time.sleep(0.2)  # SELECT-only readiness; never retry an INSERT/CREATE.
    except BaseException as exc:
        evidence.update(
            status="failed", error_type=type(exc).__name__, error=str(exc)[:4096]
        )
        raise
    finally:
        evidence["seconds"] = time.monotonic() - started
        # Preserve exact bounded SELECT results before execute() cleans resources.
        # Two exclusive files maximum; never rewrite an earlier failed result.
        save_new(directory / "capture-readiness.json", evidence)


def scanner_proof(reader, *, project, since):
    from tracer.services.clickhouse.v2.property_catalog.span_source import (
        SpanAuditAccumulator,
    )

    frozen = reader.freeze(
        project_ids=[project], since=since, until=since + timedelta(days=1)
    )
    accumulator, cursor, values = SpanAuditAccumulator(), None, {}
    pages = 0
    for _ in range(16):
        pages += 1
        page = reader.read_page(frozen, cursor=cursor)
        for span in page.spans:
            identifier = span.cursor.span_id
            if identifier in values or span.gap_reasons:
                raise RuntimeError("duplicate or incomplete scanner span")
            values[identifier] = span.attrs_string.get("capture_gate")
        for observation in page.observation_sha256s:
            accumulator.add(observation)
        if page.terminal:
            break
        if not page.next_cursor or page.next_cursor == cursor:
            raise RuntimeError("scanner cursor failed to advance")
        cursor = page.next_cursor
    else:
        raise RuntimeError("bounded scanner did not terminate")
    audit = reader.audit(frozen)  # Real independent aggregate SQL, not page reuse.
    if (
        values != EXPECTED_VALUES
        or accumulator.proof.count != len(EXPECTED_VALUES)
        or audit.state_conflict_count != 0
        or (accumulator.proof.count, accumulator.proof.digest)
        != (audit.count, audit.digest)
    ):
        raise RuntimeError(
            "version/tombstone scanner result or independent audit disagrees"
        )
    return {
        "values": values,
        "pages": pages,
        "count": audit.count,
        "digest": audit.digest,
        "state_conflict_count": audit.state_conflict_count,
    }


def exercise(directory, manifest, runner):
    configure_libraries()
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        NativeSourceClient,
    )
    from tracer.services.clickhouse.v2.property_catalog.publisher import (
        SharedCatalogDeadline,
    )
    from tracer.services.clickhouse.v2.property_catalog.source_capture import (
        DurableSourceCapture,
        SourceCaptureSpec,
    )
    from tracer.services.clickhouse.v2.property_catalog.source_capture_capacity import (
        SourceCaptureCapacity,
    )
    from tracer.services.clickhouse.v2.property_catalog.source_capture_native import (
        CaptureResourceBudget,
        NativeSourceCaptureBackend,
    )
    from tracer.services.clickhouse.v2.property_catalog.source_capture_schema import (
        qualified_capture_schema,
    )
    from tracer.services.clickhouse.v2.property_catalog.span_source import (
        CanonicalSpanSourceReader,
    )
    from tracer.services.clickhouse.v2.property_catalog.write_admission import (
        WriteMember,
    )

    source, catalog = names(manifest)
    since = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    project, insert = fixture_insert(manifest, since)
    # Exclusive intent prevents this fixture's setup writes being replayed.
    save_new(
        directory / "capture-setup-intent.json",
        {"source": source, "since": since.isoformat()},
    )
    for node in NODES:
        runner.verify_owned()
        for sql in (
            f"CREATE DATABASE `{source}` ENGINE=Atomic",
            f"CREATE DATABASE `{catalog}_source_capture` ENGINE=Atomic",
            canonical_source_create(manifest),
            *grants(manifest),
        ):
            runner.sql(node, sql, write=True)
    # RRMT propagates these existing bootstrap migrations; never send them to
    # both replicas or retry an uncertain ALTER. No source DDL occurs afterward.
    for sql in source_migrations(manifest):
        runner.sql(NODES[0], sql, write=True)
    runner.sql(NODES[0], insert, write=True)
    before, membership = wait_replicated_source(runner, manifest, directory=directory)
    save_new(directory / "capture-source-before.json", before)
    identity = before[NODES[0]]["table"][0]
    spec = SourceCaptureSpec(
        **{
            key: str(uuid5(NAMESPACE_URL, f"{manifest['run_id']}:{key}"))
            for key in (
                "installation_id",
                "organization_id",
                "workspace_id",
                "build_token",
            )
        },
        source_server_uuid=identity["server_uuid"],
        source_database=source,
        catalog_database=catalog,
        source_table_uuid=identity["table_uuid"],
        source_schema_sha256="0" * 64,
        capture_schema_sha256="0" * 64,
    )
    schema = qualified_capture_schema(
        identity["create_table_query"],
        source_database=source,
        source_table="spans",
        target_database=spec.capture_database,
        target_table=spec.capture_table,
        target_uuid=spec.capture_table_uuid,
    )
    spec = replace(
        spec,
        source_schema_sha256=schema.source_sha256,
        capture_schema_sha256=schema.capture_sha256,
    )
    save_new(directory / "capture-spec.json", asdict(spec))
    save_new(directory / "capture-product.sql", schema.create_sql.encode())
    control = directory / "capture-control"
    control.mkdir(mode=0o700)
    with ExitStack() as stack:

        def driver(user, database, readonly):
            result = ClickHouseClient(
                host="127.0.0.1",
                port=manifest["ports"][NODES[0] + "_native"],
                database=database,
                user=user,
                password=manifest["password"],
                pool_size=1,
                connect_timeout=3,
                server_enforced_readonly=readonly,
            )
            stack.callback(result.close)
            return result

        writer, metadata = driver(WRITER, source, False), driver(READER, source, True)
        member = WriteMember(
            NODES[0],
            identity["hostname"],
            "fixture",
            identity["server_uuid"],
            (),
            "http://unused:1",
        )
        backend = NativeSourceCaptureBackend(
            writer,
            source_reader=metadata,
            member=member,
            spec=spec,
            schema=schema,
            budget=CaptureResourceBudget(2, 10, 128 << 20),
            capacity_reservation=SourceCaptureCapacity(spec, metadata),
        )
        finished = set()

        def can_retire(value):
            return value == spec and value.binding_sha256 in finished

        manager = DurableSourceCapture(str(control), backend, can_retire=can_retire)
        started = time.monotonic()
        captured = manager.acquire(spec)
        if captured.rows != PHYSICAL_ROWS:
            raise RuntimeError("capture lost physical versions/tombstones")
        assert_source_unchanged(before, source_witness(runner, source))
        capture_client = NativeSourceClient(
            driver(READER, spec.capture_database, True),
            source_database=spec.capture_database,
            source_table=spec.capture_table,
            catalog_database=catalog,
        )
        reader = CanonicalSpanSourceReader(
            capture_client,
            source_database=spec.capture_database,
            source_table=spec.capture_table,
            catalog_database=catalog,
            deadline=SharedCatalogDeadline(wall_ms=60_000),
            page_rows=1,
        )
        proof = scanner_proof(reader, project=project, since=since)
        restarted = DurableSourceCapture(str(control), backend, can_retire=can_retire)
        if restarted.acquire(spec) != captured:
            raise RuntimeError("journal restart failed to reuse the exact capture")
        if runner.sql(
            NODES[1],
            "SELECT name FROM system.tables "
            f"WHERE database='{spec.capture_database}' AND name='{spec.capture_table}' LIMIT 2",
        ):
            raise RuntimeError(
                "capture unexpectedly replicated to the other source member"
            )
        finished.add(
            spec.binding_sha256
        )  # Exact fixture build has finished every source read.
        restarted.retire(spec)
        if backend.inspect(spec) is not None or restarted.reservation_snapshot(spec):
            raise RuntimeError("owned capture table/reservation was not retired")
        after = source_witness(runner, source)
        save_new(directory / "capture-source-after.json", after)
        assert_source_unchanged(before, after)
        return {
            "source_engine": SOURCE_ENGINE,
            "capture_engine": "MergeTree",
            "membership": membership,
            "physical_rows": captured.rows,
            "capture_parts": captured.parts,
            "scanner_audit": proof,
            "source_unchanged": True,
            "owned_capture_retired": True,
            "journal_restart_reused": True,
            "capture_local_to_replica1": True,
            "seconds": time.monotonic() - started,
        }


def execute(directory, confirmation):
    manifest = load(directory)
    prepared = json.loads(read_file(directory / "capture-plan.json"))
    ddl = canonical_source_create(manifest).encode()
    if (
        confirmation != manifest["run_id"]
        or prepared
        != {
            "run_id": confirmation,
            "sources": sources(),
            "ddl_sha256": digest(ddl),
            "scope": SCOPE,
        }
        or read_file(directory / "capture-source.sql") != ddl
    ):
        raise ValueError("exact run confirmation and unchanged capture plan required")
    save_new(directory / "capture-execute-intent.json", {"run_id": confirmation})
    docker = Docker(directory, manifest)
    docker.connect()
    # Never clean a preexisting fixture, even when its ownership labels match.
    if any(docker.inspect(kind, name) is not None for kind, name in docker.resources()):
        raise RuntimeError("capture execution refuses preexisting resources")
    outcome = {
        "status": "failed",
        "scope": SCOPE,
        "production_admitted": False,
        "serving_tested": False,
        "run_id": confirmation,
    }
    try:
        docker.up(confirmation)
        runner = Runner(directory, manifest, docker)
        wait_for_keeper(runner)
        outcome["proof"] = exercise(directory, manifest, runner)
        if prepared["sources"] != sources():
            raise RuntimeError("capture product sources changed during execution")
        outcome["status"] = "passed"
    except BaseException as exc:
        # Native errors can include query text; full SQL/grants are never printed.
        outcome["error_type"] = type(exc).__name__
        raise
    finally:
        try:
            docker.cleanup()
            outcome["owned_resources_absent"] = True
        except BaseException as exc:
            outcome["status"] = "failed"
            outcome["cleanup_error_type"] = type(exc).__name__
            raise
        finally:
            save_new(directory / "capture-result.json", outcome)
            print(json.dumps(outcome), flush=True)


def main():
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
            parser.error("plan has no existing target")
        directory, manifest = prepare()
        print(
            json.dumps(
                {
                    "directory": str(directory),
                    "run_id": manifest["run_id"],
                    "status": "planned_no_infrastructure",
                    "scope": SCOPE,
                }
            )
        )
    else:
        if not args.directory or not args.confirm_run_id:
            parser.error("execute requires directory and exact --confirm-run-id")
        execute(args.directory.absolute(), args.confirm_run_id)


if __name__ == "__main__":
    main()
