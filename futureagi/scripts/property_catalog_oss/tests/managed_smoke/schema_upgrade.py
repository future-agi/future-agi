"""Exercise the real OSS enum upgrade on populated, disposable old metadata."""

import hashlib
import json

from run import ROOT, Run, save


def execute(run: Run) -> dict:
    # Called after application writers have joined. No live deployment or
    # application catalog is changed; this is another new DB in our container.
    database = "property_catalog_dev_upgrade_" + run.manifest["run_id"]

    def sql(query, *, data=None):
        return run.execute(
            "clickhouse", ["clickhouse-client", "--query", query], stdin=data
        ).stdout

    if (
        sql(f"SELECT count() FROM system.databases WHERE name='{database}'").strip()
        != "0"
    ):
        raise RuntimeError("schema upgrade fixture requires an absent destination")
    sql(f"CREATE DATABASE `{database}`")
    schema = (
        ROOT / "futureagi/scripts/property_catalog_oss/reader_activation_control.sql"
    ).read_text()
    old_schema = schema.replace("'rollback' = 3, 'follow' = 4", "'rollback' = 3")
    if old_schema == schema:
        raise RuntimeError(
            "review old action contract before extending upgrade fixture"
        )
    run.execute(
        "clickhouse",
        ["clickhouse-client", "--database", database, "--multiquery"],
        stdin=old_schema,
    )
    # Opaque metadata rows prove physical preservation, not logical qualification.
    rows = []
    for sequence, action in enumerate(("activate", "disable", "rollback"), 1):
        rows.append(
            {
                "organization_id": "00000000-0000-0000-0000-000000000001",
                "workspace_id": "00000000-0000-0000-0000-000000000002",
                "catalog_epoch": 7,
                "projection_version": 3,
                "control_sequence": sequence,
                "request_id": f"00000000-0000-0000-0000-{sequence:012d}",
                "action": action,
                "target_catalog_revision": 8,
                "target_build_token": "00000000-0000-0000-0000-000000000003",
                "target_activation_sha256": "a" * 64,
                "previous_control_sha256": "b" * 64,
                "control_sha256": str(sequence) * 64,
                "controlled_at": "2026-09-01 00:00:00.000000",
            }
        )
    table = f"`{database}`.property_catalog_activation_control_events"
    sql(
        f"INSERT INTO {table} FORMAT JSONEachRow",
        data="\n".join(json.dumps(row) for row in rows),
    )
    query = f"SELECT *, toInt8(action) AS action_code FROM {table} ORDER BY control_sequence FORMAT JSONEachRow"
    before = sql(query)
    for _ in range(2):
        run.execute(
            "clickhouse",
            [
                "env",
                f"PROPERTY_CATALOG_TARGET_DATABASE={database}",
                "PROPERTY_CATALOG_SOURCE_DATABASE=default",
                "PROPERTY_CATALOG_SCHEMA_DIRECTORY=/schema",
                "CLICKHOUSE_HOST=127.0.0.1",
                "sh",
                "/bootstrap/bootstrap_clickhouse.sh",
            ],
        )
        if sql(query) != before:
            raise RuntimeError(
                "legacy control rows changed during automatic bootstrap upgrade"
            )
    action_type = sql(
        f"SELECT type FROM system.columns WHERE database='{database}' AND table='property_catalog_activation_control_events' AND name='action' FORMAT TabSeparatedRaw"
    ).strip()
    if (
        action_type
        != "Enum8('activate' = 1, 'disable' = 2, 'rollback' = 3, 'follow' = 4)"
    ):
        raise RuntimeError("managed action was not installed")
    result = {
        "status": "passed",
        "old_rows": len(rows),
        "bootstrap_runs": 2,
        "content_sha256": hashlib.sha256(before.encode()).hexdigest(),
        "unchanged_rows_and_enum_codes": True,
        "action_type": action_type,
    }
    save(run.directory / "schema-upgrade-evidence.json", result)
    return result
