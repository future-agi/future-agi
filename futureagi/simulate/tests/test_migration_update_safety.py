"""Simulate's data migrations added or edited for this release declare their tables.

The update probe (scripts/rehearse_update_compatibility.py) refuses a pending
RunPython or RunSQL whose migration has no UPDATE_SAFETY declaration. These
tests read each one with the probe's own reader, and compare its risk classes
with what the update gate (scripts/check_update_safety.py) detects for its
operations. The agent key move and the key column removal are wrapped in
SkipDatabaseIfLegacyApplied, so their data steps sit one level down.
"""

import ast
import functools
import importlib
import importlib.util
import inspect
import json
import re
from pathlib import Path

import pytest
from django.db import migrations
from django.db.migrations.loader import MigrationLoader

from tfc.migration_bridge import SkipDatabaseIfLegacyApplied

ROOT = Path(__file__).resolve().parents[3]
PROBE = ROOT / "scripts" / "rehearse_update_compatibility.py"
GATE = ROOT / "scripts" / "check_update_safety.py"
HIGH_VOLUME = ROOT / "deploy" / "update" / "high-volume-tables.json"
APP = "simulate"
DECLARED = {
    "0044_agentdefinition_webhook_secret": ["postgres:simulate_agent_version"],
    "0046_remove_agentdefinition_webhook_secret": ["postgres:simulate_agent_version"],
    "0047_agentdefinition_authentication_method_and_more": [
        "postgres:simulate_agent_definition",
        "postgres:simulate_agent_version",
    ],
    "0063_remove_callexecution_logurl_and_more": [
        "postgres:simulate_call_execution",
        "postgres:simulate_call_execution_snapshot",
    ],
    "0071_provider_credentials": [
        "postgres:simulate_agent_definition",
        "postgres:simulate_provider_credentials",
    ],
    "0074_canonicalize_simulate_eval_config_filters": ["postgres:simulate_eval_config"],
    "0076_backfill_provider_credentials": [
        "postgres:simulate_agent_definition",
        "postgres:simulate_agent_version",
        "postgres:simulate_provider_credentials",
    ],
    "0087_hostedharnessjob_content_updated_at": [
        "postgres:simulate_hosted_harness_job"
    ],
    "0088_hostedharnessjob_name": ["postgres:simulate_hosted_harness_job"],
    "0103_encrypt_agent_definition_api_key": ["postgres:simulate_agent_definition"],
    "0104_remove_agent_definition_api_key": ["postgres:simulate_agent_definition"],
    "0110_seed_system_personas": ["postgres:simulate_personas"],
}
# Dropping logUrl from the registered high-volume simulate_call_execution table.
RISK_CLASSES = {
    "0063_remove_callexecution_logurl_and_more": ["data_migration", "table_rewrite"],
}


def probe_reader():
    """The probe's own declared(), taken from the program it ships."""
    tree = ast.parse(PROBE.read_text())
    program = next(
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", None) == "PENDING_TABLE_SCOPE" for t in node.targets)
    )
    body = [
        node
        for node in ast.parse(program).body
        if (isinstance(node, ast.FunctionDef) and node.name in ("declared", "resolved"))
        or (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "IDENT" for t in node.targets)
        )
    ]
    namespace = {
        "ast": ast,
        "inspect": inspect,
        "re": re,
        "Path": Path,
        "schemas": {"postgres": "public", "clickhouse": "default"},
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), str(PROBE), "exec"), namespace)
    return namespace["declared"]


@functools.cache
def update_gate():
    """The update gate module, its high-volume registry and an offline loader."""
    spec = importlib.util.spec_from_file_location("check_update_safety", GATE)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    registry = json.loads(HIGH_VOLUME.read_text())
    loader = MigrationLoader(None, ignore_no_migrations=True, replace_migrations=False)
    return gate, registry, loader


def detected_risks(name):
    """Risk classes and tables the update gate detects, at the migration's parents.

    The gate refuses the SkipDatabaseIfLegacyApplied envelope itself, so the
    operations inside it are classified one by one.
    """
    gate, registry, loader = update_gate()
    key = (APP, name)
    parents = [node.key for node in loader.graph.node_map[key].parents]
    state = loader.project_state(parents)
    risks, fresh = [], set()
    for operation in loader.disk_migrations[key].operations:
        steps = (
            operation.operations
            if isinstance(operation, SkipDatabaseIfLegacyApplied)
            else [operation]
        )
        for step in steps:
            risks += gate.operation_risks(step, APP, state, registry, fresh)
            step.state_forwards(APP, state)
    return {r["risk_class"] for r in risks}, {r["table"] for r in risks if "table" in r}


def data_steps(operations):
    """RunPython and RunSQL operations, including those inside wrapper operations."""
    for operation in operations:
        if isinstance(operation, (migrations.RunPython, migrations.RunSQL)):
            yield operation
        elif isinstance(operation, migrations.SeparateDatabaseAndState):
            yield from data_steps(operation.database_operations)
        else:
            yield from data_steps(getattr(operation, "operations", []))


def runs_code(operation):
    if isinstance(operation, migrations.RunPython):
        return operation.code is not migrations.RunPython.noop
    return operation.sql != migrations.RunSQL.noop


@pytest.mark.parametrize("name", sorted(DECLARED))
def test_the_update_probe_reads_the_declared_tables(name):
    module = importlib.import_module(f"{APP}.migrations.{name}")
    migration = module.Migration(name, APP)
    assert any(runs_code(operation) for operation in data_steps(migration.operations))
    expected = {("postgres", "public." + t.split(":", 1)[1]) for t in DECLARED[name]}
    assert probe_reader()(migration) == expected
    declaration = module.UPDATE_SAFETY
    assert declaration["schema_version"] == 1
    assert declaration["risk_classes"] == RISK_CLASSES.get(name, ["data_migration"])
    assert declaration["tables"] == DECLARED[name]
    assert 30 <= len(declaration["mitigation"].strip()) <= 2000
    assert declaration["large_volume_rehearsal"] is True


@pytest.mark.parametrize("name", sorted(DECLARED))
def test_the_declaration_covers_what_the_update_gate_detects(name):
    declaration = importlib.import_module(f"{APP}.migrations.{name}").UPDATE_SAFETY
    classes, tables = detected_risks(name)
    assert classes == set(declaration["risk_classes"])
    assert tables <= set(declaration["tables"])
