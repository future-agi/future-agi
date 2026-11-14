"""SELECT-only pre/post contracts for the two packaged OSS CDC migrations.

No client creation, CLI, bootstrap wiring or DDL execution. The future caller
owns target/topology authorization and uses apply_schema's existing apply/version
helpers. Inspection never calls its main/--status path (which creates a table).
This checks additive columns, not full landing-table/CDC parity.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tracer.services.clickhouse.v2.apply_schema import SchemaFile, fetch_applied

MIGRATION_DIR = Path(__file__).with_name("oss_cdc_migrations")
MIGRATION_NAME = "cdc_001_catalog_target_fields.sql"

# Canonical schema.py CDC_MODEL_HUB_SCORE / CDC_SIMULATE_AGENT_DEFINITION.
# No default on either nullable field: absent scope/speaking order stays unknown.
_COLUMNS = {
    ("model_hub_score", "tracer_project_id"): ("Nullable(UUID)", "", ""),
    ("model_hub_score", "value_history"): ("String", "DEFAULT", "'[]'"),
    ("simulate_agent_definition", "target_speaks_first"): ("Nullable(UInt8)", "", ""),
}
# These are PG-owned fields, not derived catalog columns. Source-created PeerDB
# tables preserve the JSON NOT NULL / boolean NULL domains without CH defaults.
# Qualified through snapshot and live CDC on the Compose-pinned PeerDB version.
_SOURCE_FIELDS = {
    **_COLUMNS,
    ("model_hub_score", "value_history"): ("String", "", ""),
    ("simulate_agent_definition", "target_speaks_first"): ("Nullable(Bool)", "", ""),
}
MIGRATIONS = {
    MIGRATION_NAME: _COLUMNS,
    "cdc_002_usage_eval_fields.sql": {
        # Canonical system.columns spelling: ClickHouse removes the redundant
        # parentheses around IN. Keep this fixed with the packaged migration.
        ("usage_apicalllog", "eval_score"): (
            "Float64",
            "MATERIALIZED",
            "if(JSONType(JSONExtractString(config), 'output', 'output', 'score') IN ('Double', 'Int64', 'UInt64'), JSONExtractFloat(JSONExtractString(config), 'output', 'output', 'score'), JSONExtractFloat(JSONExtractString(config), 'output', 'output'))",
        ),
        ("usage_apicalllog", "eval_output_str"): (
            "String",
            "MATERIALIZED",
            "JSONExtractString(JSONExtractString(config), 'output', 'output')",
        ),
        ("usage_apicalllog", "eval_trace_id"): (
            "String",
            "MATERIALIZED",
            "JSONExtractString(JSONExtractString(config), 'trace_id')",
        ),
        ("usage_apicalllog", "eval_dataset_id"): (
            "String",
            "MATERIALIZED",
            "JSONExtractString(JSONExtractString(config), 'dataset_id')",
        ),
    },
}


class CDCUpgradeError(ValueError):
    """Unknown column shape or inconsistent migration history; never auto-repair."""


@dataclass(frozen=True)
class UpgradeInspection:
    migration: SchemaFile
    missing_columns: tuple[str, ...]
    recorded: bool


def migration_file(name: str = MIGRATION_NAME) -> SchemaFile:
    """Fixed packaged filenames/hashes, not operator versions or a schema glob."""
    if name not in MIGRATIONS:
        raise CDCUpgradeError("unknown packaged CDC migration")
    return SchemaFile.from_path(MIGRATION_DIR / name)


def inspect_upgrade(
    client, *, require_complete: bool = False, migration_name: str = MIGRATION_NAME
) -> UpgradeInspection:
    """Inspect the client's current database using SELECT only, even without a ledger.

    Missing target columns are eligible before apply; wrong existing types or
    defaults always fail. Use require_complete=True after applying, before readiness.
    A matching local schema_versions record never masks missing/incompatible columns.
    This uses the existing local topology identity, not an all-replica status claim.
    """
    return _inspect_contract(
        client, require_complete=require_complete, migration_name=migration_name
    )


def inspect_source_fields(client) -> None:
    """Require the three source-owned fields; never authorize a source-column ADD.

    An absent cdc001 record is normal for a source-created table. Validate a record
    if present, but return no migration to apply or record. This is a prerequisite
    check, not whole-table compatibility, historical recovery or CDC readiness.
    """
    _inspect_contract(
        client,
        require_complete=True,
        migration_name=MIGRATION_NAME,
        source_owned=True,
    )


def _inspect_contract(
    client, *, require_complete: bool, migration_name: str, source_owned=False
):
    migration = migration_file(migration_name)
    expected = _SOURCE_FIELDS if source_owned else MIGRATIONS[migration_name]
    prerequisites = (
        {("usage_apicalllog", "config")}
        if migration_name == "cdc_002_usage_eval_fields.sql"
        else set()
    )
    tables = {
        name
        for (name,) in client.query(
            """
            SELECT name FROM system.tables
            WHERE database = currentDatabase()
              AND name IN %(tables)s
            """,
            parameters={
                "tables": tuple(
                    sorted({table for table, _ in expected} | {"schema_versions"})
                )
            },
            settings={"readonly": 1},
        ).result_rows
    }
    missing_tables = {table for table, _ in expected} - tables
    if missing_tables:
        raise CDCUpgradeError(
            f"missing CDC destination tables: {sorted(missing_tables)}"
        )

    rows = client.query(
        """
        SELECT table, name, type, default_kind, default_expression FROM system.columns
        WHERE database = currentDatabase()
          AND (table, name) IN %(columns)s
        """,
        parameters={"columns": (*expected, *sorted(prerequisites))},
        settings={"readonly": 1},
    ).result_rows
    present = set()
    for table, name, data_type, default_kind, default_expression in rows:
        key = (table, name)
        if key not in expected.keys() | prerequisites or key in present:
            raise CDCUpgradeError(
                f"unexpected/duplicate column metadata: {table}.{name}"
            )
        actual = (data_type, default_kind, default_expression)
        if key in prerequisites:
            if actual not in {("String", "", ""), ("String", "DEFAULT", "'{}'")}:
                raise CDCUpgradeError("incompatible eval config prerequisite")
            present.add(key)
            continue
        if actual != expected[key]:
            raise CDCUpgradeError(
                f"incompatible {table}.{name}: expected {expected[key]!r}, got {actual!r}"
            )
        present.add(key)

    if prerequisites - present:
        raise CDCUpgradeError("missing eval config prerequisite")
    missing = tuple(
        f"{table}.{name}" for table, name in expected if (table, name) not in present
    )
    # fetch_applied is SELECT-only; do not ensure/create schema_versions to inspect it.
    prior = (
        fetch_applied(client).get(migration_name)
        if "schema_versions" in tables
        else None
    )
    if prior is not None and prior != migration.sha256:
        raise CDCUpgradeError(
            f"hash drift for {migration_name}; no force/overwrite contract"
        )
    if missing and (require_complete or prior is not None):
        raise CDCUpgradeError(f"incomplete CDC upgrade: {missing!r}")
    return UpgradeInspection(migration, missing, prior is not None)
