"""Separate actual-native-adapter ACK diagnostic; never a quorum qualification."""

from datetime import UTC, datetime
from functools import partial
from uuid import NAMESPACE_URL, uuid5

from harness import ROOT, digest, read_file, save_new, validate_manifest
from schema_probe import capture, configure_libraries, reservation_row

DATA_TABLE = "property_catalog_source_streams"
CONTROL_TABLE = "property_catalog_activation_control_events"


def profile_statements(m, database):
    validate_manifest(m)
    if database != "property_catalog_dev_standalone_" + m["run_id"]:
        raise ValueError("async diagnostic requires this run's standalone database")
    user = "smoke_async_" + m["run_id"]
    password_hash = digest(m["password"].encode())
    return user, [
        f"CREATE USER {user} IDENTIFIED WITH sha256_hash BY '{password_hash}' "
        "SETTINGS readonly=0, async_insert=1, wait_for_async_insert=0, "
        "async_insert_use_adaptive_busy_timeout=0, async_insert_busy_timeout_ms=60000, "
        "max_threads=1, max_memory_usage=268435456, max_execution_time=10, "
        "log_queries=1, log_query_settings=1",
        f"GRANT SELECT, INSERT ON {database}.* TO {user}",
        *[
            f"GRANT SELECT ON system.{table} TO {user}"
            for table in ("databases", "settings", "tables")
        ],
    ]


def synchronous_evidence(profile, cases, logs):
    """Require actual server settings, complete rows, and exact one-row log ACKs."""
    if (
        profile.get("async_insert") != "1"
        or profile.get("wait_for_async_insert") != "0"
    ):
        return False
    if len(cases) != 2 or len(logs) != 2:
        return False
    for case in cases:
        matched = [row for row in logs if row["token"] == case["token"]]
        if (
            case["transport"]["outcome"] != "returned"
            or not case["exact_row_immediately_visible"]
            or case["elapsed_seconds"] >= 60
            or len(matched) != 1
            or matched[0]["type"] != "QueryFinish"
            or matched[0]["async_insert"] != "0"
            or matched[0]["wait_for_async_insert"] != "0"
            or matched[0]["written_rows"] != 1
            or matched[0]["exception_code"] != 0
        ):
            return False
    return True


def run_adapter_ack(runner, confirmed):
    import time

    if confirmed != runner.m["run_id"]:
        raise RuntimeError("explicit run-id async-profile confirmation required")
    runner.verify_owned()
    database = runner.bundle["databases"]["standalone"]["name"]
    user, statements = profile_statements(runner.m, database)
    paths = (
        "tracer/services/clickhouse/client.py",
        "tracer/services/clickhouse/v2/property_catalog/dev_runtime.py",
        "tracer/services/clickhouse/v2/property_catalog/reader_activation_client.py",
        "tracer/services/clickhouse/v2/property_catalog/activation_control.py",
    )
    sources = {path: digest(read_file(ROOT / "futureagi" / path)) for path in paths}
    # This phase has its own capture because it may run after the read-only
    # parity phase. It cannot silently overwrite/replay a previous adapter test.
    save_new(
        runner.directory / "async-profile-intent.json",
        {
            "source_files": sources,
            "user": user,
            "database": database,
            "attempt_limit_per_insert": 1,
            "outcome": "UNKNOWN",
        },
    )
    configure_libraries()
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.v2.property_catalog.activation_control import (
        ACTIVATION_CONTROL_COLUMNS,
        ActivationControlAction,
        ActivationControlEvent,
        ActivationControlTarget,
    )
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        NativeCatalogClient,
    )
    from tracer.services.clickhouse.v2.property_catalog.reader_activation_client import (
        _ActivationControlClient,
    )

    # No IF NOT EXISTS: every user and grant is new, fixture-local and exclusive.
    for sql in statements:
        runner.sql("replica1", sql, write=True)
    driver = ClickHouseClient(
        host="127.0.0.1",
        port=runner.m["ports"]["replica1_native"],
        database=database,
        user=user,
        password=runner.m["password"],
        pool_size=1,
        connect_timeout=3,
        send_timeout=10,
        receive_timeout=10,
        server_enforced_readonly=False,
    )
    try:
        profile = dict(
            driver.execute(
                "SELECT name, value FROM system.settings WHERE name IN "
                "('async_insert','wait_for_async_insert','async_insert_busy_timeout_ms')"
            )
        )
        native = NativeCatalogClient(driver, database=database)
        control = _ActivationControlClient(
            driver,
            database=database,
            user=user,
            deployment="dev",
            expected_hostnames=(runner.m["project"] + "-replica1",),
        )
        expected_data = reservation_row(runner.m, 3)
        native_data = dict(expected_data)
        for field in ("started_at", "updated_at", "drain_deadline"):
            native_data[field] = datetime.fromisoformat(native_data[field]).replace(
                tzinfo=UTC
            )
        # A synthetic DISABLE event exercises serialization/INSERT only. No
        # lifecycle qualification, reader binding, or catalog activation runs.
        event = ActivationControlEvent.create(
            control_sequence=1,
            request_id=str(
                uuid5(NAMESPACE_URL, runner.m["project"] + "/async-control")
            ),
            action=ActivationControlAction.DISABLE,
            target=ActivationControlTarget(
                organization_id=expected_data["organization_id"],
                workspace_id=expected_data["workspace_id"],
                catalog_epoch=1,
                projection_version=1,
                catalog_revision=3,
                build_token=expected_data["build_token"],
                activation_sha256="0" * 64,
            ),
            previous_control_sha256="0" * 64,
            controlled_at=datetime.now(UTC),
        )
        expected_control = event.as_row()
        expected_control["controlled_at"] = event.controlled_at.strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )
        cases = []
        for name, adapter, table, row, expected, columns in (
            (
                "NativeCatalogClient",
                native,
                DATA_TABLE,
                native_data,
                expected_data,
                tuple(expected_data),
            ),
            (
                "ActivationControlClient",
                control,
                CONTROL_TABLE,
                event.as_row(),
                expected_control,
                ACTIVATION_CONTROL_COLUMNS,
            ),
        ):
            token = runner.m["project"] + "-async-" + name
            save_new(
                runner.directory / (name + "-write-intent.json"),
                {
                    "token": token,
                    "expected_row": expected,
                    "attempt_limit": 1,
                    "initial_outcome": "UNKNOWN",
                    "adapter": name,
                },
            )
            started = time.monotonic()
            transport = capture(
                partial(
                    adapter.insert,
                    f"`{database}`.`{table}`",
                    [row],
                    columns=columns,
                    timeout_ms=8000,
                    deduplication_token=token,
                )
            )
            save_new(runner.directory / (name + "-transport.json"), transport)
            observed = runner.sql(
                "replica1", f"SELECT * FROM {database}.{table} LIMIT 3"
            )
            case = {
                "adapter": name,
                "token": token,
                "transport": transport,
                "expected_row": expected,
                "observed_rows": observed,
                "exact_row_immediately_visible": observed == [expected],
                "elapsed_seconds": time.monotonic() - started,
            }
            cases.append(case)
            save_new(runner.directory / (name + "-result.json"), case)
        # Only this owned server; no user query-log privilege is added to relax
        # the product adapter's exact grant attestation.
        runner.sql("replica1", "SYSTEM FLUSH LOGS", write=True)
        logs = runner.sql(
            "replica1",
            "SELECT toString(type) AS type, query_id, query, written_rows, exception_code, "
            "Settings['async_insert'] AS async_insert, "
            "Settings['wait_for_async_insert'] AS wait_for_async_insert, "
            "Settings['insert_deduplication_token'] AS token "
            f"FROM system.query_log WHERE user='{user}' AND query_kind='Insert' "
            "AND type != 'QueryStart' ORDER BY event_time_microseconds LIMIT 4",
        )
        unchanged = sources == {
            path: digest(read_file(ROOT / "futureagi" / path)) for path in paths
        }
        return {
            "profile": profile,
            "cases": cases,
            "server_query_log": logs,
            "source_files": sources,
            "source_files_unchanged": unchanged,
            "actual_adapter_synchronous_ack_proven": unchanged
            and synchronous_evidence(profile, cases, logs),
            "production_admitted": False,
            "quorum_proven": False,
            "scope": "actual Python native adapters on one owned standalone database; no Go/Kafka/replicated durability claim",
        }
    finally:
        driver.close()
