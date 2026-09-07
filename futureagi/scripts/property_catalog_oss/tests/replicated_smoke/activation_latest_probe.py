"""Tiny real SQL qualification; run explicitly with --run in the safe test env.

Uses only a disposable, uniquely labelled clickhouse-local container, the cached
image below, fixed synthetic data, and Memory tables. No replication, admission,
network, host bind mounts, or production qualification is claimed. Evidence goes
to stdout. Without --run, imports/compiles the exact helpers and prints SQL only.
--diagnose version/help/help-threads/select-one/select-one-tuned exposes bounded
diagnostics. The untuned select-one deliberately reproduces the startup fault.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[5]
IMAGE = "sha256:a4e8cf46e526a06f4f52d22f7fa8d07472b55d3c896d7ca3f159e249be38ae62"
ENDPOINT = "unix:///Users/nikhilpareek/.colima/default/docker.sock"
PROTECTED = "c2854450c0d804243ab7ba370d49cb5f98e8d60f12dc547262eccb20163204d4"
LABEL = "futureagi.activation-latest-probe.owner"
DATABASE = "property_catalog_dev_latest_probe"
TABLE = f"`{DATABASE}`.`property_catalog_activations`"
ORG, WORKSPACE, BUILD = (str(UUID(int=value)) for value in (1, 2, 3))
# The cached binary's help marks background_schedule_pool_size as server-only.
# v25.3.14.14-lts/src/Core/ServerSettings.cpp confirms its default is 512.
# Configure via the documented --config-file option, without a host file/mount.
# https://github.com/ClickHouse/ClickHouse/blob/v25.3.14.14-lts/src/Core/ServerSettings.cpp
SCHEDULER_CONFIG = (
    "<clickhouse><background_schedule_pool_size>1</background_schedule_pool_size>"
    "<max_thread_pool_size>32</max_thread_pool_size></clickhouse>\n"
)
SQL_LOCAL_OPTIONS = (
    "--path",
    "/tmp/catalog-latest",
    "--max_threads=1",
    "--max_insert_threads=1",
    "--max_memory_usage=134217728",
    "--max_execution_time=10",
    "--logger.console=1",
    "--log-level=information",
    "--stacktrace",
)
SAFE_ENV = {
    "FI_SKIP_CH25_SCHEMA_APPLY": "1",
    "TESTING": "false",
    "CH_ENABLED": "false",
    "CH_HOST": "127.0.0.1",
    "CH_PORT": "2",
    "CH_HTTP_PORT": "3",
    "CH_DATABASE": "test_catalog_offline",
    "CH25_HOST": "127.0.0.1",
    "CH25_TCP_PORT": "2",
    "CH25_HTTP_PORT": "3",
    "CH25_DATABASE": "test_catalog_offline",
    "PG_HOST": "127.0.0.1",
    "PG_PORT": "1",
    "REDIS_URL": "redis://127.0.0.1:4/0",
    "DJANGO_SETTINGS_MODULE": "tfc.settings.test",
    "PYTHONPATH": "futureagi",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def emit(event, **value):
    print(json.dumps({"event": event, **value}, sort_keys=True), flush=True)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def libraries():
    require(
        all(os.environ.get(k) == v for k, v in SAFE_ENV.items()),
        "full safe test environment required",
    )
    # Library-only settings, as in the adjacent schema probe: never import the
    # project's .env/bootstrap, initialize apps, or contact external services.
    from django.conf import settings

    from tfc.settings.runtime_setting_specs import RUNTIME_NUMERIC_SETTING_SPECS

    require(not settings.configured, "run in a fresh standalone Python process")
    settings.configure(
        **{name: spec.default for name, spec in RUNTIME_NUMERIC_SETTING_SPECS.items()}
    )
    from tracer.services.clickhouse.v2.property_catalog import state_store

    return state_store


def synthetic_row(store, *, revision=1, anchor=1, sequence=1, version=1):
    from tracer.services.clickhouse.v2.property_catalog.activation import (
        ActivationRecord,
        ActivationStatus,
        CatalogLifecycleMode,
    )
    from tracer.services.clickhouse.v2.property_catalog.codec import (
        canonical_json,
        canonical_json_sha256,
    )

    mode = (
        CatalogLifecycleMode.INITIAL_BACKFILL
        if revision == 1
        else (
            CatalogLifecycleMode.FULL_REPAIR
            if revision == anchor
            else CatalogLifecycleMode.INCREMENTAL
        )
    )
    manifest = canonical_json(
        {"lifecycle_mode": str(mode), "lineage_anchor_revision": anchor}
    )
    timestamp = datetime(2026, 9, 6, 12, tzinfo=UTC)
    record = ActivationRecord(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        catalog_epoch=1,
        catalog_revision=revision,
        build_token=BUILD,
        projection_version=1,
        lifecycle_mode=mode,
        lineage_anchor_revision=anchor,
        activation_sequence=sequence,
        source_manifest_json=manifest,
        source_manifest_sha256=canonical_json_sha256(manifest),
        revision_fence_sha256="a" * 64,
        activation_sha256="b" * 64,
        status=ActivationStatus.ACTIVE,
        live_definition_rows=0,
        tombstone_rows=0,
        value_rows=0,
        qualified_at=timestamp,
        updated_at=timestamp,
        version=version,
    )
    return store._activation_row(record)


def cases(store):
    active = synthetic_row(store)
    disabled = {**active, "status": "disabled", "_version": 2}
    latest = {**active, "_version": 3}
    second = synthetic_row(store, revision=2, sequence=2, version=2)
    repair = synthetic_row(store, revision=3, anchor=3, sequence=3, version=3)
    terminal_version = (1 << 64) - 1
    terminal = {**active, "status": "disabled", "_version": terminal_version}
    # Expected output explicitly describes physical variants, independent of
    # the helper's SQL or the Python latest-version implementation.
    return (
        (
            "terminal_dominates_late_active",
            (terminal, active, latest),
            ((1, "disabled", terminal_version),),
            (),
            1,
            False,
        ),
        (
            "terminal_after_old_active",
            (active, latest, terminal),
            ((1, "disabled", terminal_version),),
            (),
            1,
            False,
        ),
        (
            "duplicate_exact_terminal",
            (active, terminal, terminal),
            ((1, "disabled", terminal_version), (1, "disabled", terminal_version)),
            (),
            1,
            False,
        ),
        (
            "terminal_same_version_conflict",
            (terminal, {**active, "_version": terminal_version}),
            ((1, "active", terminal_version), (1, "disabled", terminal_version)),
            (),
            1,
            True,
        ),
        (
            "terminal_preserves_successor_lineage",
            (terminal, repair, active),
            ((3, "active", 3), (1, "disabled", terminal_version)),
            (3,),
            3,
            False,
        ),
        (
            "old_active_then_new_disabled",
            (active, disabled),
            ((1, "disabled", 2),),
            (),
            1,
            False,
        ),
        (
            "disabled_then_late_old_active",
            (disabled, active),
            ((1, "disabled", 2),),
            (),
            1,
            False,
        ),
        (
            "disabled_then_newer_active",
            (disabled, latest),
            ((1, "active", 3),),
            (1,),
            1,
            False,
        ),
        (
            "duplicate_exact_latest",
            (active, latest, latest),
            ((1, "active", 3), (1, "active", 3)),
            (1,),
            1,
            False,
        ),
        (
            "conflicting_same_version",
            ({**active, "_version": 2}, disabled),
            ((1, "active", 2), (1, "disabled", 2)),
            (),
            1,
            True,
        ),
        (
            "cross_scope_latest_isolation",
            (
                active,
                {**disabled, "workspace_id": str(UUID(int=90)), "_version": 99},
                {**active, "workspace_id": str(UUID(int=90)), "status": "disabled"},
                {**disabled, "organization_id": str(UUID(int=91)), "_version": 100},
                {**disabled, "catalog_epoch": 2, "_version": 101},
            ),
            ((1, "active", 1),),
            (1,),
            1,
            False,
        ),
        (
            "through_revision_cutoff",
            (active, second, repair),
            ((3, "active", 3), (2, "active", 2), (1, "active", 1)),
            (3,),
            2,
            False,
        ),
        (
            "invalidated_latest_lineage",
            (
                active,
                second,
                {**second, "status": "disabled", "_version": 20},
                repair,
            ),
            ((3, "active", 3), (2, "disabled", 20), (1, "active", 1)),
            (3,),
            2,
            False,
        ),
    )


def compile_probe(store):
    from clickhouse_driver.util.escape import escape_param, escape_params

    source = (
        ROOT
        / "futureagi/tracer/services/clickhouse/v2/schema/026_property_catalog_state.sql"
    )
    schema = source.read_text()
    marker = "CREATE TABLE IF NOT EXISTS property_catalog_activations\n"
    require(schema.count(marker) == 1, "ambiguous canonical activation schema")
    columns = schema.split(marker)[1].split("\nENGINE =", 1)[0]
    require(
        columns.startswith("(") and columns.rstrip().endswith(")"),
        "unexpected canonical columns",
    )
    templates = {
        variant: store.activation_latest_rows_sql(DATABASE, through_revision=variant)
        for variant in (False, True)
    }
    emit(
        "source",
        state_store_sha256=digest(Path(store.__file__).read_bytes()),
        schema_sha256=digest(source.read_bytes()),
    )
    for variant, sql in templates.items():
        emit(
            "helper_sql", through_revision=variant, sql=sql, sha256=digest(sql.encode())
        )
    statements = [
        "SELECT version() AS probe_version FORMAT JSONEachRow",
        f"CREATE DATABASE `{DATABASE}` ENGINE=Memory",
        f"CREATE TABLE {TABLE} {columns} ENGINE=Memory",
        "SET output_format_json_quote_64bit_integers=0",
    ]
    expected = {}
    for name, rows, statuses, lineage, cutoff, conflict in cases(store):
        require(len(rows) <= 5, "synthetic row bound exceeded")
        statements.append(f"TRUNCATE TABLE {TABLE}")
        for row in rows:
            require(set(row) == set(store._ACTIVATION_COLUMNS), "fixture column drift")
            values = []
            for column in store._ACTIVATION_COLUMNS:
                value = row[column]
                if isinstance(value, datetime):
                    value = value.strftime("%Y-%m-%d %H:%M:%S.%f")
                values.append(str(escape_param(value, None)))
            statements.append(
                f"INSERT INTO {TABLE} ({', '.join(store._ACTIVATION_COLUMNS)}) VALUES ({', '.join(values)})"
            )
        for variant in (False, True):
            key = f"{name}:{'through_revision' if variant else 'all_revisions'}"
            params = {
                "organization_id": UUID(ORG),
                "workspace_id": UUID(WORKSPACE),
                "catalog_epoch": 1,
            }
            if variant:
                params["catalog_revision"] = cutoff
            # Exact production query, with client-side binding only. No query
            # rewriting, hand-translated SELECT, or alternate latest algorithm.
            sql = templates[variant] % escape_params(params, None)
            statements.extend(
                (
                    f"SELECT {escape_param(key, None)} AS probe_case FORMAT JSONEachRow",
                    sql + " FORMAT JSONEachRow",
                )
            )
            emit("bound_sql", case=key, sql=sql)
            selected_statuses = tuple(
                item for item in statuses if not variant or item[0] <= cutoff
            )
            selected_lineage = lineage
            if variant and name == "through_revision_cutoff":
                selected_lineage = (1, 2)
            if variant and name == "invalidated_latest_lineage":
                selected_lineage = (1,)
            expected[key] = (selected_statuses, selected_lineage, conflict)
    script = ";\n".join(statements) + ";\n"
    require(len(script.encode()) < 128 * 1024, "synthetic SQL exceeds bound")
    emit(
        "fixture",
        cases=len(expected),
        inserted_rows=sum(len(case[1]) for case in cases(store)),
        max_rows_per_case=5,
        sql_bytes=len(script.encode()),
        sql_sha256=digest(script.encode()),
        engine="Memory",
        replication_qualified=False,
    )
    return script, templates, expected


def docker(*args, timeout=5):
    return subprocess.run(
        ["docker", "--context", "colima", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def inspect_container(target):
    result = docker("container", "inspect", target)
    if result.returncode:
        require(
            "No such container" in result.stderr or "No such object" in result.stderr,
            f"cannot establish exact container absence: {result.stderr}",
        )
        return None
    values = json.loads(result.stdout)
    require(len(values) == 1, "ambiguous container identity")
    return values[0]


def owned(info, name, owner, expected_id=None):
    require(
        info["Id"] != PROTECTED and re.fullmatch(r"[a-f0-9]{64}", info["Id"]),
        "protected or malformed container identity",
    )
    require(
        info["Name"] == "/" + name
        and info["Image"] == IMAGE
        and info["Config"]["Labels"].get(LABEL) == owner,
        "container name/image/ownership mismatch; refuse mutation",
    )
    require(
        expected_id is None or info["Id"] == expected_id,
        "container identity replaced; refuse mutation",
    )
    return info["Id"]


def fixture_constraints(info):
    host = info["HostConfig"]
    require(
        host["AutoRemove"]
        and host["NetworkMode"] == "none"
        and host["Memory"] == 512 << 20
        and host["NanoCpus"] == 1_000_000_000
        and host["PidsLimit"] == 128
        and host["ReadonlyRootfs"],
        "fixture resource constraints differ",
    )
    require(
        host["CapDrop"] == ["ALL"]
        and host["SecurityOpt"] == ["no-new-privileges"]
        and info["Config"]["User"] == "65532:65532",
        "fixture privilege constraints differ",
    )
    require(
        not host.get("Binds")
        and not host.get("PortBindings")
        and host["Tmpfs"] == {"/tmp": "rw,nosuid,size=128m,mode=1777"},
        "unexpected bind mount, port, or tmpfs",
    )
    # The pinned image declares this anonymous volume. No host bind is passed;
    # --rm owns its lifecycle. Record and verify its removal as well.
    mounts = [mount for mount in info["Mounts"] if mount["Type"] != "tmpfs"]
    require(
        all(
            mount["Destination"] == "/tmp"
            for mount in info["Mounts"]
            if mount["Type"] == "tmpfs"
        ),
        "unexpected tmpfs destination",
    )
    require(
        len(mounts) == 1
        and mounts[0]["Type"] == "volume"
        and mounts[0]["Destination"] == "/var/lib/clickhouse"
        and re.fullmatch(r"[a-f0-9]{64}", mounts[0]["Name"]),
        "unexpected image volume",
    )
    return mounts[0]["Name"]


def run_fixture(script, *, diagnostic=None, local_options=None):
    context = docker("context", "inspect", "colima")
    require(
        context.returncode == 0
        and json.loads(context.stdout)[0]["Endpoints"]["docker"]["Host"] == ENDPOINT,
        "unexpected Docker endpoint",
    )
    image = docker("image", "inspect", IMAGE)
    require(image.returncode == 0, "pinned image is not cached; no pull authorized")
    metadata = json.loads(image.stdout)[0]
    require(
        (metadata["Id"], metadata["Os"], metadata["Architecture"])
        == (IMAGE, "linux", "arm64"),
        "image identity/platform mismatch",
    )
    emit(
        "cached_image",
        image=IMAGE,
        platform="linux/arm64",
        image_build_label=metadata["Config"]["Labels"].get(
            "com.clickhouse.build.version"
        ),
    )
    name, owner = "pclatest-" + str(uuid4()), str(uuid4())
    require(inspect_container(name) is None, "unique fixture name already exists")
    command = [
        "docker",
        "--context",
        "colima",
        "create",
        "--pull=never",
        "--rm",
        "-i",
        "--name",
        name,
        "--label",
        f"{LABEL}={owner}",
        "--network",
        "none",
        "--memory",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "128",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "65532:65532",
        "--tmpfs",
        "/tmp:rw,nosuid,size=128m,mode=1777",
        "--entrypoint",
        "/usr/bin/clickhouse",
        IMAGE,
        "local",
    ]
    if local_options is None:
        require(len(script.encode()) < 128 * 1024, "fixed SQL argument exceeds bound")
        command += [
            *SQL_LOCAL_OPTIONS,
            "--config-file",
            "/dev/stdin",
            "--query",
            script,
        ]
        input_text = SCHEDULER_CONFIG
    else:
        command += local_options
        input_text = script
    command_evidence = list(command)
    if "--query" in command_evidence:
        index = command_evidence.index("--query") + 1
        sql = command_evidence[index]
        command_evidence[index] = {
            "sql_bytes": len(sql.encode()),
            "sql_sha256": digest(sql.encode()),
        }
    emit(
        "launch",
        name=name,
        ownership_label={LABEL: owner},
        command=command_evidence,
        timeout_seconds=45,
        diagnostic=diagnostic,
        stdin_bytes=len(input_text.encode()),
        stdin_sha256=digest(input_text.encode()),
    )
    started = time.monotonic()
    # Inspect the exact created ID before starting even fast --version/--help
    # variants. Same AutoRemove, image, user, mounts and resource constraints.
    process = None
    container_id = volume = None
    output = errors = ""
    try:
        created = subprocess.run(
            command, capture_output=True, text=True, timeout=5, check=False
        )
        require(created.returncode == 0, f"fixture create failed: {created.stderr}")
        container_id = created.stdout.strip()
        require(
            re.fullmatch(r"[a-f0-9]{64}", container_id) is not None,
            "invalid created container ID",
        )
        info = inspect_container(container_id)
        require(info is not None, "created fixture disappeared")
        owned(info, name, owner, container_id)
        volume = fixture_constraints(info)
        emit(
            "owned_container",
            id=container_id,
            name=name,
            image=info["Image"],
            label=owner,
            anonymous_volume=volume,
        )
        process = subprocess.Popen(
            [
                "docker",
                "--context",
                "colima",
                "start",
                "--attach",
                "--interactive",
                container_id,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="backslashreplace",
        )
        output, errors = process.communicate(
            input_text, timeout=max(1, 45 - (time.monotonic() - started))
        )
        emit(
            "child_exit",
            returncode=process.returncode,
            elapsed_seconds=round(time.monotonic() - started, 3),
            stderr=errors,
            signal_if_128_plus_signal=(
                signal.Signals(process.returncode - 128).name
                if 128 < process.returncode < 160
                else None
            ),
            fault_signals_reported=list(
                dict.fromkeys(
                    int(value) for value in re.findall(r"Received signal (\d+)", errors)
                )
            ),
        )
        if process.returncode or diagnostic:
            if diagnostic == "help-threads":
                lines = output.splitlines()
                indices = {
                    j
                    for i, line in enumerate(lines)
                    if re.search(
                        r"background_schedule_pool_size|max_thread_pool_size|background_pool_size|logger",
                        line,
                    )
                    for j in range(max(0, i - 1), min(len(lines), i + 10))
                }
                emit(
                    "diagnostic_stdout",
                    diagnostic=diagnostic,
                    stdout="\n".join(lines[i] for i in sorted(indices)),
                    full_stdout_bytes=len(output.encode()),
                    full_stdout_sha256=digest(output.encode()),
                )
            else:
                emit("diagnostic_stdout", diagnostic=diagnostic, stdout=output)
        require(
            process.returncode == 0,
            "clickhouse-local failed within fixed constraints; no expanded retry",
        )
        require(len(output.encode()) <= 1 << 20, "unexpected result size")
        return output
    finally:
        # A CLI timeout does not prove the container exited. Resolve only this
        # exact random name, validate label/image/ID again, then remove by ID.
        if process is not None and process.poll() is None:
            process.kill()  # Stop only this invocation's CLI before reconciliation.
            process.communicate(timeout=5)
        try:
            info = inspect_container(name)
            if info is not None:
                exact_id = owned(info, name, owner, container_id)
                container_id = exact_id
                if volume is None:
                    for mount in info["Mounts"]:
                        if (
                            mount["Type"] == "volume"
                            and mount["Destination"] == "/var/lib/clickhouse"
                        ):
                            volume = mount["Name"]
                result = docker("container", "rm", "--force", exact_id)
                if result.returncode:
                    require(
                        inspect_container(exact_id) is None,
                        f"owned cleanup failed: {result.stderr}",
                    )
            require(inspect_container(name) is None, "owned container name remains")
            if container_id:
                require(
                    inspect_container(container_id) is None,
                    "owned container ID remains",
                )
            if volume:
                result = docker("volume", "inspect", volume)
                require(
                    result.returncode != 0
                    and "no such volume" in result.stderr.lower(),
                    "owned anonymous volume not auto-removed",
                )
            emit(
                "cleanup",
                name=name,
                id=container_id,
                container_absent=True,
                anonymous_volume=volume,
                volume_absent=bool(volume),
                protected_container_untouched=PROTECTED,
            )
        finally:
            if process is not None and process.poll() is None:
                process.kill()  # Only the Popen child owned by this invocation.
            if process is not None:
                process.communicate(timeout=5)


def verify_output(store, output, templates, expected):
    groups, current, version = {}, None, None
    for line in output.splitlines():
        row = json.loads(line)
        if "probe_version" in row:
            require(version is None, "duplicate version output")
            version = row["probe_version"]
        elif "probe_case" in row:
            current = row["probe_case"]
            require(
                current in expected and current not in groups,
                "unknown/repeated case marker",
            )
            groups[current] = []
        else:
            require(
                current is not None and set(row) == set(store._ACTIVATION_COLUMNS),
                "unexpected output shape",
            )
            groups[current].append(row)
    require(
        version is not None and set(groups) == set(expected),
        "incomplete SQL execution evidence",
    )
    emit("clickhouse_version", version=version)
    for key, rows in groups.items():
        statuses, lineage, conflict = expected[key]
        observed = Counter(
            (row["catalog_revision"], row["status"], row["_version"]) for row in rows
        )
        require(
            observed == Counter(statuses),
            f"wrong real latest rows for {key}: {observed}",
        )
        require(
            all(
                (row["organization_id"], row["workspace_id"], row["catalog_epoch"])
                == (ORG, WORKSPACE, 1)
                for row in rows
            ),
            "cross-scope result leaked",
        )

        class ReturnedRows:
            catalog_database = DATABASE

            def __init__(self, values):
                self.values = values

            def query(self, sql, params, *, timeout_ms):
                require(sql == templates[False], "state parser used unexpected SQL")
                require(
                    params
                    == {
                        "organization_id": ORG,
                        "workspace_id": WORKSPACE,
                        "catalog_epoch": 1,
                    },
                    "state parser changed exact scope",
                )
                return self.values

        def no_mutation(*args, **kwargs):
            raise RuntimeError("parser attempted mutation")

        state = store.ClickHouseCatalogStateStore(
            ReturnedRows(rows),
            database=DATABASE,
            serializer=SimpleNamespace(serialize=no_mutation),
        )

        def parse_active(state=state):
            return state.list_activations(
                organization_id=ORG, workspace_id=WORKSPACE, catalog_epoch=1
            )

        if conflict:
            for parse in (parse_active, lambda rows=rows: store._active_lineage(rows)):
                try:
                    parse()
                except store.PropertyCatalogStateConflict:
                    pass
                else:
                    raise RuntimeError(f"production parser accepted conflict: {key}")
            emit(
                "case_pass",
                case=key,
                physical_rows=len(rows),
                conflict_rejected_by=["list_activations", "_active_lineage"],
            )
        else:
            records = parse_active()
            require(
                tuple(record.catalog_revision for record in records)
                == tuple(
                    sorted({rev for rev, status, _ in statuses if status == "active"})
                ),
                f"wrong active records: {key}",
            )
            actual_lineage = store._active_lineage(rows)
            require(
                actual_lineage == tuple((rev, BUILD, 1) for rev in lineage),
                f"wrong lineage: {key}: {actual_lineage}",
            )
            emit(
                "case_pass",
                case=key,
                physical_rows=len(rows),
                statuses=sorted(observed.elements()),
                active_revisions=[record.catalog_revision for record in records],
                lineage=actual_lineage,
            )
    emit(
        "qualification_pass",
        version=version,
        cases=len(groups),
        sql_variants=2,
        replication_qualified=False,
    )


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--diagnose":
        require(
            all(os.environ.get(k) == v for k, v in SAFE_ENV.items()),
            "full safe test environment required",
        )
        variants = {
            "version": (["--version"], ""),
            "help": (["--help"], ""),
            "help-threads": (["--help", "--verbose"], ""),
            "select-one-tuned": (
                [
                    *SQL_LOCAL_OPTIONS,
                    "--config-file",
                    "/dev/stdin",
                    "--query",
                    "SELECT version() AS probe_version, 1 AS result FORMAT JSONEachRow; "
                    "SELECT name, value FROM system.server_settings "
                    "WHERE name IN ('background_schedule_pool_size', 'max_thread_pool_size') "
                    "ORDER BY name FORMAT JSONEachRow",
                ],
                SCHEDULER_CONFIG,
            ),
            "select-one": (
                [
                    "--path",
                    "/tmp/catalog-latest",
                    "--max_threads=1",
                    "--max_insert_threads=1",
                    "--max_memory_usage=134217728",
                    "--max_execution_time=10",
                    "--logger.console=1",
                    "--logger.level=trace",
                    "--stacktrace",
                    "--query",
                    "SELECT version() AS probe_version, 1 AS result FORMAT JSONEachRow",
                ],
                "",
            ),
        }
        require(sys.argv[2] in variants, "unknown bounded diagnostic variant")
        local_options, script = variants[sys.argv[2]]
        run_fixture(script, diagnostic=sys.argv[2], local_options=local_options)
        return
    require(
        sys.argv[1:] in ([], ["--run"]), "usage: activation_latest_probe.py [--run]"
    )
    store = libraries()
    script, templates, expected = compile_probe(store)
    if sys.argv[1:] == ["--run"]:
        verify_output(store, run_fixture(script), templates, expected)
    else:
        emit(
            "prepared_only", cases=len(expected), next_step="run explicitly with --run"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit(
            "qualification_failed",
            error_type=type(exc).__name__,
            error=str(exc),
            replication_qualified=False,
        )
        raise SystemExit(1) from exc
