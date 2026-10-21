from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

SCHEMA_DIR = Path(__file__).with_name("schema")
SCHEMA_FILES = (
    "025_property_catalog_data.sql",
    "026_property_catalog_state.sql",
    "027_property_catalog_delivery.sql",
)
EXPECTED_TABLES = (
    "property_definition_catalog",
    "span_attribute_value_catalog",
    "property_catalog_checkpoints",
    "property_catalog_activations",
    "property_catalog_deliveries",
    "property_catalog_source_streams",
)
RETIRED_SCHEMA_FILES = (
    "025_span_attribute_catalog.sql",
    "026_span_attribute_catalog_delivery.sql",
    "027_span_attribute_catalog_source_kind.sql",
)
RETIRED_TABLES = (
    "span_attribute_key_catalog",
    "span_attribute_catalog_checkpoints",
    "span_attribute_catalog_activations",
    "span_attribute_catalog_deliveries",
    "span_attribute_catalog_source_streams",
)


def _load_topology_subject():
    module_path = Path(__file__).with_name("schema_topology.py")
    spec = importlib.util.spec_from_file_location(
        "property_catalog_schema_topology_subject", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load schema_topology.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _executable_sql(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    )


def _statements(path: Path) -> list[str]:
    return [
        statement.strip()
        for statement in _executable_sql(path).split(";\n")
        if statement.strip()
    ]


def _normalized(value: str) -> str:
    return " ".join(value.lower().split())


def test_clean_pre_release_schema_is_exactly_six_create_only_tables() -> None:
    assert all(not (SCHEMA_DIR / name).exists() for name in RETIRED_SCHEMA_FILES)

    statements = [
        statement
        for filename in SCHEMA_FILES
        for statement in _statements(SCHEMA_DIR / filename)
    ]
    assert len(statements) == 6
    assert (
        tuple(
            re.match(
                r"CREATE TABLE IF NOT EXISTS ([A-Za-z_][A-Za-z0-9_]*)\b",
                statement,
            ).group(1)
            for statement in statements
        )
        == EXPECTED_TABLES
    )

    executable = "\n".join(statements)
    assert re.search(r"(?i)\b(?:ALTER|DROP|INSERT|TRUNCATE)\b", executable) is None
    assert re.search(r"(?i)\bMATERIALIZED\s+VIEW\b", executable) is None
    assert re.search(r"(?i)\bFROM\s+spans\b", executable) is None
    assert "schema_versions" not in executable
    assert all(table not in executable for table in RETIRED_TABLES)


def test_topology_allowlist_is_exact_and_retired_names_remain_guarded() -> None:
    topology = _load_topology_subject()
    assert topology.PROPERTY_CATALOG_TABLES == frozenset(EXPECTED_TABLES)
    assert topology.RETIRED_PROPERTY_CATALOG_TABLES == frozenset(RETIRED_TABLES)
    assert topology.CATALOG_TOPOLOGY_GUARD_TABLES == frozenset(
        {"schema_versions", *EXPECTED_TABLES, *RETIRED_TABLES}
    )


def test_definition_catalog_pins_identity_scope_search_and_tombstones() -> None:
    definition = _normalized(_statements(SCHEMA_DIR / SCHEMA_FILES[0])[0])
    for fragment in (
        "organization_id uuid",
        "workspace_id uuid",
        "catalog_epoch uint16",
        "catalog_revision uint64",
        "build_token uuid",
        "projection_version uint16",
        "binding_id fixedstring(64)",
        "source_version uint64",
        "property_id string",
        "sort_name_folded string",
        "search_text_folded string",
        "definition_sha256 fixedstring(64)",
        "is_deleted uint8",
        "deleted_at nullable(datetime64(6, 'utc'))",
        "state_sha256 fixedstring(64)",
        "engine = mergetree",
        "partition by cityhash64(workspace_id) % 64",
    ):
        assert fragment in definition

    for enum_member in (
        "'always' = 1",
        "'workspace_default' = 2",
        "'project' = 3",
        "'agent_definition' = 4",
        "'dataset' = 5",
        "'system_manifest' = 1",
        "'span_attribute' = 2",
        "'eval_template' = 3",
        "'eval_config' = 4",
        "'simulation_eval_config' = 5",
        "'annotation_label' = 6",
        "'dataset_column' = 7",
    ):
        assert enum_member in definition

    assert "replacingmergetree" not in definition
    assert "aggregatingmergetree" not in definition
    assert re.search(
        r"order by\s*\( organization_id, workspace_id, catalog_epoch, "
        r"catalog_revision, build_token, binding_id, source_version, state_sha256 \)",
        definition,
    )


def test_value_catalog_is_native_tenant_project_and_epoch_scoped() -> None:
    value = _normalized(_statements(SCHEMA_DIR / SCHEMA_FILES[0])[1])
    for fragment in (
        "organization_id uuid",
        "workspace_id uuid",
        "project_id uuid",
        "catalog_epoch uint16",
        "catalog_revision uint64",
        "build_token uuid",
        "source_kind enum8",
        "'custom_attribute' = 1",
        "'system_attribute' = 2",
        "value_fingerprint fixedstring(64)",
        "value_search_text_folded simpleaggregatefunction(anylast, string)",
        "engine = aggregatingmergetree",
    ):
        assert fragment in value
    assert re.search(
        r"order by\s*\( organization_id, workspace_id, project_id, "
        r"catalog_epoch, catalog_revision, build_token, source_kind, attribute_key, "
        r"attribute_type, value_fingerprint \)",
        value,
    )


def test_control_tables_share_tenant_revision_and_projection_identity() -> None:
    control_statements = [
        statement
        for filename in SCHEMA_FILES[1:]
        for statement in _statements(SCHEMA_DIR / filename)
    ]
    assert len(control_statements) == 4
    normalized_controls = tuple(map(_normalized, control_statements))
    for statement in normalized_controls:
        assert "organization_id" in statement
        assert "workspace_id" in statement
        assert "catalog_epoch" in statement
        assert "catalog_revision" in statement
        assert "build_token uuid" in statement
        assert "projection_version" in statement
    assert "engine = replacingmergetree(_version)" in normalized_controls[0]
    assert "engine = replacingmergetree(_version)" in normalized_controls[1]
    assert "engine = mergetree" in normalized_controls[2]
    assert "replacingmergetree" not in normalized_controls[2]
    assert "engine = replacingmergetree(_version)" in normalized_controls[3]

    activation = _normalized(control_statements[1])
    assert "lifecycle_mode enum8" in activation
    assert "'initial_backfill' = 1" in activation
    assert "'incremental' = 2" in activation
    assert "'full_repair' = 3" in activation
    assert "lineage_anchor_revision uint64" in activation
    assert activation.index("projection_version uint16") < activation.index(
        "lifecycle_mode enum8"
    )
    assert activation.index("lifecycle_mode enum8") < activation.index(
        "lineage_anchor_revision uint64"
    )
    assert activation.index("lineage_anchor_revision uint64") < activation.index(
        "activation_sequence uint64"
    )
    assert "activation_sequence uint64" in activation
    assert "source_manifest_sha256 fixedstring(64)" in activation
    assert "revision_fence_sha256 fixedstring(64)" in activation
    assert "activation_sha256 fixedstring(64)" in activation
    assert "'building' = 1" in activation
    assert "'active' = 2" in activation
    assert "'disabled' = 3" in activation

    delivery = _normalized(control_statements[2])
    stream = _normalized(control_statements[3])
    assert "previous_payload_sha256 fixedstring(64)" in delivery
    assert "terminal uint8" in delivery
    assert "kafka_offset int64 default -1" in delivery
    assert "max_contiguous_sequence uint64" in stream
    assert "last_issued_sequence uint64" in stream
    assert "'draining' = 2" in stream
    assert "drain_deadline nullable(datetime64(6, 'utc'))" in stream
    assert "kafka_high_water_offset int64 default -1" in stream
