#!/usr/bin/env python3
"""Owned, bounded ClickHouse snapshot feasibility experiment; not product wiring.

Only a newly labelled disposable ClickHouse service is started. The actual span
schema is installed there. A restricted user can SELECT the source but can only
create/attach/drop objects inside the run's disposable snapshot database. Source
updates below are synthetic fixture writes by the fixture administrator.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from run import LABEL, PREFIX, ROOT, Run, compose_document, reserve_ports, save

PROJECT = "64e7f55c-d8d3-4b5f-a910-889ef1cd88aa"


def execute() -> dict:
    token = secrets.token_hex(8)
    parent = Path(tempfile.mkdtemp(prefix="property-catalog-snapshot-experiment-"))
    directory = parent / ("property-catalog-managed-" + token)
    directory.mkdir(mode=0o700)
    manifest = {
        "run_id": token,
        "project": PREFIX + token,
        "ports": reserve_ports(),
        "password": secrets.token_hex(16),
        "cluster_id": "unused",
        "candidate_topic": "unused",
        "ordered_topic": "unused",
    }
    document = compose_document(manifest)
    document["services"] = {"clickhouse": document["services"]["clickhouse"]}
    document["volumes"] = {"clickhouse-data": document["volumes"]["clickhouse-data"]}
    # One owned loopback native port for the actual Python source-reader SQL.
    document["services"]["clickhouse"]["ports"] = [
        f"127.0.0.1:{manifest['ports']['native']}:9000"
    ]
    save(directory / "compose.json", document)
    manifest["compose_sha256"] = hashlib.sha256(
        (directory / "compose.json").read_bytes()
    ).hexdigest()
    save(directory / "manifest.json", manifest)
    (directory / "empty.env").touch(mode=0o600)
    run = Run(directory, manifest, timeout=240)
    report = {
        "project": manifest["project"],
        "scope": "disposable snapshot feasibility only",
    }
    print(f"Starting one 3 GiB / 2 CPU owned ClickHouse: {directory}", flush=True)

    def query(sql: str, *, user: str = "default", check: bool = True):
        return run.execute(
            "clickhouse",
            ["clickhouse-client", "--user", user, "--multiquery"],
            stdin=sql,
            timeout=35,
            check=check,
        )

    def logical(database: str, *, user: str = "default", table: str = "spans"):
        return json.loads(
            query(
                "SELECT groupArray(tuple(id, value, deleted)) AS rows FROM ("
                "SELECT id, argMax(attrs_string['snapshot_test'], _version) AS value, "
                "argMax(is_deleted, _version) AS deleted "
                f"FROM `{database}`.`{table}` WHERE project_id=toUUID('{PROJECT}') "
                "GROUP BY id ORDER BY id) SETTINGS max_execution_time=5, max_threads=1 FORMAT JSONEachRow",
                user=user,
            ).stdout
        )["rows"]

    def insert(identifier: str, value: str, version: int, deleted: int = 0):
        # Every argument is an internal constant or a loop-produced integer.
        query(
            "INSERT INTO default.spans "
            "(project_id, observation_type, start_time, trace_id, id, name, attrs_string, _version, is_deleted) "
            f"VALUES ('{PROJECT}', 'span', toDateTime64('2026-09-06 00:00:00',6,'UTC'), "
            f"'9299d173-9012-480a-92dd-ec0f78e5d817', '{identifier}', 'snapshot_fixture', map('snapshot_test','{value}'), {version}, {deleted})"
        )

    def catalog_proof(database: str, after_values, *, table: str = "spans"):
        # Library-only configuration: no .env, ORM, application startup or test
        # hook. The transport is limited to this run's proven native endpoint.
        sys.path.insert(0, str(ROOT / "futureagi"))
        from django.conf import settings

        from tfc.settings.runtime_setting_specs import RUNTIME_NUMERIC_SETTING_SPECS

        if not settings.configured:
            settings.configure(
                **{k: v.default for k, v in RUNTIME_NUMERIC_SETTING_SPECS.items()}
            )
        from clickhouse_driver import Client

        from tracer.services.clickhouse.v2.property_catalog.publisher import (
            SharedCatalogDeadline,
        )
        from tracer.services.clickhouse.v2.property_catalog.span_source import (
            CanonicalSpanSourceReader,
            SpanAuditAccumulator,
        )

        driver = Client(
            "127.0.0.1",
            port=manifest["ports"]["native"],
            database=database,
            user="snapshot_builder",
            connect_timeout=3,
            send_receive_timeout=15,
        )

        class Source:
            source_database = database
            source_table = table

            def query(self, sql, params, *, timeout_ms, settings):
                rows, columns = driver.execute(
                    sql, params, settings=settings, with_column_types=True
                )
                return [
                    dict(zip((c[0] for c in columns), row, strict=True)) for row in rows
                ]

        try:
            reader = CanonicalSpanSourceReader(
                Source(),
                source_database=database,
                source_table=table,
                catalog_database="unused_catalog_fixture",
                deadline=SharedCatalogDeadline(wall_ms=20_000),
                page_rows=1,
            )
            frozen = reader.freeze(
                project_ids=[PROJECT],
                since=datetime(2026, 9, 6, tzinfo=UTC),
                until=datetime(2026, 9, 7, tzinfo=UTC),
            )
            accumulator, cursor, pages = SpanAuditAccumulator(), None, 0
            while True:
                page = reader.read_page(frozen, cursor=cursor)
                pages += 1
                for observation in page.observation_sha256s:
                    accumulator.add(observation)
                if page.terminal:
                    break
                cursor = page.next_cursor
            after_values()
            audit = reader.audit(frozen)
            return {
                "pages": pages,
                "source_count": accumulator.proof.count,
                "source_digest": accumulator.proof.digest,
                "audit_count": audit.count,
                "audit_digest": audit.digest,
                "state_conflict_count": audit.state_conflict_count,
                "matches": (accumulator.proof.count, accumulator.proof.digest)
                == (audit.count, audit.digest),
            }
        finally:
            driver.disconnect()

    def durable_capture_test():
        # These are the product manager, schema transform and native transport,
        # running against the one owned source-local fixture endpoint. Runtime
        # factory/GC and production grants are still separate integration gates.
        sys.path.insert(0, str(ROOT / "futureagi"))
        from django.conf import settings

        from tfc.settings.runtime_setting_specs import RUNTIME_NUMERIC_SETTING_SPECS

        if not settings.configured:
            settings.configure(
                **{k: v.default for k, v in RUNTIME_NUMERIC_SETTING_SPECS.items()}
            )
        from clickhouse_driver import Client

        from tracer.services.clickhouse.v2.property_catalog.source_capture import (
            DurableSourceCapture,
            SourceCaptureSpec,
        )
        from tracer.services.clickhouse.v2.property_catalog.source_capture_native import (
            CaptureResourceBudget,
            NativeSourceCaptureBackend,
        )
        from tracer.services.clickhouse.v2.property_catalog.source_capture_schema import (
            qualified_capture_schema,
        )
        from tracer.services.clickhouse.v2.property_catalog.write_admission import (
            WriteMember,
        )

        identity = json.loads(
            query(
                "SELECT hostName() AS hostname, toString(serverUUID()) AS server_uuid, "
                "toString(uuid) AS table_uuid, create_table_query FROM system.tables "
                "WHERE database='default' AND name='spans' FORMAT JSONEachRow"
            ).stdout
        )
        report["source_part_checksum_metadata"] = [
            json.loads(line)
            for line in query(
                "SELECT name, toTypeName(hash_of_all_files) AS type, toString(hash_of_all_files) AS checksum "
                "FROM system.parts WHERE database='default' AND table='spans' AND active ORDER BY name LIMIT 10 FORMAT JSONEachRow"
            ).stdout.splitlines()
        ]
        spec = SourceCaptureSpec(
            installation_id=str(uuid4()),
            organization_id=str(uuid4()),
            workspace_id=str(uuid4()),
            build_token=str(uuid4()),
            source_server_uuid=identity["server_uuid"],
            source_database="default",
            catalog_database="snapshot_fixture",
            source_table_uuid=identity["table_uuid"],
            source_schema_sha256="0" * 64,
            capture_schema_sha256="0" * 64,
        )
        schema = qualified_capture_schema(
            identity["create_table_query"],
            source_database="default",
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
        query(
            f"CREATE DATABASE `{spec.capture_database}` ENGINE=Atomic; "
            f"GRANT SELECT, INSERT, CREATE TABLE, ALTER DELETE, DROP TABLE ON `{spec.capture_database}`.* TO snapshot_builder; "
            "CREATE USER snapshot_reader SETTINGS max_threads=1, max_execution_time=30, "
            "max_result_rows=1100, max_result_bytes=8388608, max_memory_usage=268435456, "
            "result_overflow_mode='throw', timeout_overflow_mode='throw', use_query_cache=0, readonly=1; "
            f"GRANT SELECT ON `{spec.capture_database}`.* TO snapshot_reader; "
            "GRANT SELECT ON default.spans TO snapshot_reader; "
            "GRANT SELECT ON system.tables TO snapshot_reader; "
            "GRANT SELECT ON system.parts TO snapshot_reader; "
            "GRANT SELECT ON system.parts_columns TO snapshot_reader"
        )

        class Driver:
            def __init__(self, user, readonly):
                self.database, self.user, self.server_enforced_readonly = (
                    "default",
                    user,
                    readonly,
                )
                self.native = Client(
                    "127.0.0.1",
                    port=manifest["ports"]["native"],
                    database=self.database,
                    user=user,
                    connect_timeout=3,
                    send_receive_timeout=15,
                )

            @contextmanager
            def connection(self):
                yield self.native

            def execute_read(self, sql, params, *, timeout_ms, settings):
                result, columns = self.native.execute(
                    sql,
                    params,
                    settings=None if self.server_enforced_readonly else settings,
                    with_column_types=True,
                )
                return result, columns, 0

        writer, reader = (
            Driver("snapshot_builder", False),
            Driver("snapshot_reader", True),
        )
        member = WriteMember(
            "fixture-database",
            identity["hostname"],
            "fixture",
            identity["server_uuid"],
            (),
            "http://unused:1",
        )
        control = directory / "source-capture-control"
        control.mkdir(mode=0o700)

        def reserve(_spec, retained_bytes):
            if retained_bytes > 128 << 20:
                raise RuntimeError(
                    "owned fixture capture exceeds its 128 MiB disk budget"
                )

        backend = NativeSourceCaptureBackend(
            writer,
            source_reader=reader,
            member=member,
            spec=spec,
            schema=schema,
            budget=CaptureResourceBudget(2, 1000, 128 << 20),
            capacity_reservation=reserve,
        )
        manager = DurableSourceCapture(
            str(control), backend, can_retire=lambda value: value == spec
        )
        try:
            before = time.monotonic()
            observed = manager.acquire(spec)
            report["durable_capture"] = {
                "rows": observed.rows,
                "parts": observed.parts,
                "seconds": time.monotonic() - before,
            }
            report["durable_capture"]["scanner_audit"] = catalog_proof(
                spec.capture_database,
                lambda: insert("during_durable_capture", "live", 249),
                table=spec.capture_table,
            )
            if not report["durable_capture"]["scanner_audit"]["matches"]:
                raise RuntimeError("durable capture scanner/audit mismatch")
            writer.native.disconnect()
            reader.native.disconnect()
            restarted = DurableSourceCapture(
                str(control), backend, can_retire=lambda value: value == spec
            )
            if restarted.acquire(spec) != observed:
                raise RuntimeError(
                    "durable journal restart changed capture identity or contents"
                )
            report["durable_capture"]["journal_restart_reused"] = True
            restarted.retire(spec)
            if backend.inspect(spec) is not None:
                raise RuntimeError("exact owned capture was not retired")
            report["durable_capture"]["retired"] = True

            # Execute real DDL, then lose the response at the lifecycle boundary.
            # Each case gets a new name/UUID; no source table is mutated here.
            for failure in ("create", "attach"):
                attempt = replace(spec, build_token=str(uuid4()))
                attempt_schema = qualified_capture_schema(
                    identity["create_table_query"],
                    source_database="default",
                    source_table="spans",
                    target_database=attempt.capture_database,
                    target_table=attempt.capture_table,
                    target_uuid=attempt.capture_table_uuid,
                )
                attempt = replace(
                    attempt, capture_schema_sha256=attempt_schema.capture_sha256
                )
                attempt_backend = NativeSourceCaptureBackend(
                    writer,
                    source_reader=reader,
                    member=member,
                    spec=attempt,
                    schema=attempt_schema,
                    budget=CaptureResourceBudget(2, 1000, 128 << 20),
                    capacity_reservation=reserve,
                )
                attempt_manager = DurableSourceCapture(
                    str(control), attempt_backend, can_retire=lambda value: False
                )
                original_command = attempt_backend._command
                commands = []

                def lose_response(
                    value,
                    sql,
                    query_id,
                    *,
                    before_send=None,
                    commands=commands,
                    original_command=original_command,
                    failure=failure,
                ):
                    commands.append(sql)
                    response = original_command(
                        value, sql, query_id, before_send=before_send
                    )
                    if (failure == "create" and sql.startswith("CREATE")) or (
                        failure == "attach" and sql.startswith("ALTER")
                    ):
                        raise TimeoutError(
                            "fixture deliberately lost acknowledged DDL response"
                        )
                    return response

                attempt_backend._command = lose_response
                try:
                    attempt_manager.acquire(attempt)
                    raise AssertionError("injected DDL response loss was not observed")
                except TimeoutError:
                    pass
                before_count = len(commands)
                from tracer.services.clickhouse.v2.property_catalog.source_capture import (
                    SourceCaptureUncertain,
                )

                try:
                    attempt_manager.acquire(attempt)
                    raise AssertionError("uncertain capture was silently retried")
                except SourceCaptureUncertain:
                    pass
                if len(commands) != before_count:
                    raise RuntimeError("uncertain DDL was sent again")
                attempt_backend._command = original_command
                attempt_manager.retire(attempt)
                if attempt_backend.inspect(attempt) is not None:
                    raise RuntimeError("unexposed failed capture was not reclaimed")
                if failure == "create":
                    # Model an original CREATE completing after an absent GC
                    # check. It can only create an EMPTY permanently vetoed name.
                    original_command(
                        attempt,
                        attempt_schema.create_sql,
                        attempt.query_id + "-late-fixture",
                    )
                    attempt_manager.retire(attempt)
                    if attempt_backend.inspect(attempt) is not None:
                        raise RuntimeError("late empty CREATE was not reclaimed")
                report["durable_capture"][f"lost_{failure}_reply_no_retry"] = True
            report["durable_capture"]["late_empty_create_reclaimed"] = True
        finally:
            writer.native.disconnect()
            reader.native.disconnect()

    try:
        if run.owned():
            raise RuntimeError("fresh experiment unexpectedly has existing containers")
        for kind in ("volume", "network"):
            names = run.command(
                [
                    "docker",
                    kind,
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={manifest['project']}",
                ]
            ).stdout
            if names.strip():
                raise RuntimeError(
                    "fresh experiment unexpectedly has existing resources"
                )
        run.command(
            run.compose + ["up", "-d", "--wait", "--wait-timeout", "90"], timeout=110
        )
        for schema in (
            "002_spans_v2.sql",
            "013_attributes_extra_as_string.sql",
            "014_peerdb_is_deleted_back_compat.sql",
        ):
            query(
                (
                    ROOT / "futureagi/tracer/services/clickhouse/v2/schema" / schema
                ).read_text()
            )
        query("ALTER TABLE default.spans ADD COLUMN trace_name String DEFAULT ''")
        query(
            (
                ROOT
                / "futureagi/tracer/services/clickhouse/v2/schema/015_traces_and_trace_dict.sql"
            ).read_text()
        )
        report["version"] = query("SELECT version()").stdout.strip()
        container = json.loads(run.command(["docker", "inspect", *run.owned()]).stdout)[
            0
        ]
        if container["Config"]["Labels"].get(LABEL) != token or container[
            "NetworkSettings"
        ]["Ports"]["9000/tcp"] != [
            {"HostIp": "127.0.0.1", "HostPort": str(manifest["ports"]["native"])}
        ]:
            raise RuntimeError("source-reader endpoint ownership was not proven")
        report["image_id"] = container["Image"]
        insert("one", "initial", 100)
        insert("two", "retained", 100)
        query(
            "CREATE DATABASE snapshot_fixture; CREATE USER snapshot_builder; "
            "GRANT SELECT ON default.spans TO snapshot_builder; "
            "GRANT SELECT, CREATE TABLE, INSERT, ALTER, DROP TABLE ON snapshot_fixture.* TO snapshot_builder"
        )
        started = time.monotonic()
        original_create = json.loads(
            query(
                "SELECT create_table_query FROM system.tables WHERE database='default' "
                "AND name='spans' FORMAT JSONEachRow"
            ).stdout
        )["create_table_query"]
        snapshot_create, renamed = re.subn(
            r"^CREATE TABLE default\.spans\b",
            "CREATE TABLE snapshot_fixture.spans",
            original_create,
        )
        snapshot_create, engine_changed = re.subn(
            r"\bENGINE = ReplacingMergeTree\(_version, is_deleted\)(?= PARTITION BY )",
            "ENGINE = MergeTree",
            snapshot_create,
        )
        if renamed != 1 or engine_changed != 1:
            raise RuntimeError("the actual canonical fixture schema changed")
        query(
            snapshot_create,
            user="snapshot_builder",
        )
        query("ALTER TABLE snapshot_fixture.spans REMOVE TTL", user="snapshot_builder")
        report["snapshot_create"] = query(
            "SHOW CREATE TABLE snapshot_fixture.spans"
        ).stdout
        if "TTL " in report["snapshot_create"]:
            raise RuntimeError("snapshot inherited a mutable TTL policy")
        query(
            "ALTER TABLE snapshot_fixture.spans ATTACH PARTITION ALL FROM default.spans",
            user="snapshot_builder",
        )
        report["capture_seconds"] = time.monotonic() - started
        report["source_grants"] = query(
            "SHOW GRANTS FOR snapshot_builder"
        ).stdout.splitlines()
        report["initial"] = logical("snapshot_fixture", user="snapshot_builder")
        if report["initial"] != [["one", "initial", 0], ["two", "retained", 0]]:
            raise RuntimeError("initial source membership was not copied exactly")
        durable_capture_test()
        report["actual_snapshot_source_audit"] = catalog_proof(
            "snapshot_fixture", lambda: insert("arrived_during_build", "later", 250)
        )
        if not report["actual_snapshot_source_audit"]["matches"]:
            raise RuntimeError("actual snapshot source scanner and audit disagree")
        report["live_source_negative_control"] = catalog_proof(
            "default", lambda: insert("arrived_during_live_build", "later", 251)
        )
        if report["live_source_negative_control"]["matches"]:
            raise RuntimeError(
                "live-source negative control did not reproduce mismatch"
            )
        for index in range(4):
            insert(f"new{index}", "new", 200 + index)
            insert("one", f"updated{index}", 300 + index)
            if (
                logical("snapshot_fixture", user="snapshot_builder")
                != report["initial"]
            ):
                raise RuntimeError("new source arrivals changed the snapshot")
        insert("two", "retained", 400, deleted=1)
        report["source_after"] = logical("default")
        report["snapshot_after"] = logical("snapshot_fixture", user="snapshot_builder")
        if (
            report["source_after"] == report["snapshot_after"]
            or report["snapshot_after"] != report["initial"]
        ):
            raise RuntimeError("source update/delete isolation was not proven")
        forbidden = query(
            "ALTER TABLE default.spans REMOVE TTL", user="snapshot_builder", check=False
        )
        if forbidden.returncode == 0 or "ACCESS_DENIED" not in forbidden.stderr:
            raise RuntimeError("source mutation was not denied to snapshot role")
        report["source_mutation_denied"] = True
        run.command(run.compose + ["restart", "clickhouse"], timeout=35)
        run.command(
            run.compose + ["up", "-d", "--wait", "--wait-timeout", "60"], timeout=75
        )
        if logical("snapshot_fixture", user="snapshot_builder") != report["initial"]:
            raise RuntimeError("snapshot changed across ClickHouse restart")
        report["restart_preserved"] = True
        report["status"] = "passed"
        return report
    except BaseException as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        save(directory / "snapshot-feasibility.json", report)
        run.cleanup()


if __name__ == "__main__":
    result = execute()
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "snapshot_create"},
            indent=2,
        )
    )
