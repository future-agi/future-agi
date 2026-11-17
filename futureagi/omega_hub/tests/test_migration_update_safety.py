"""Every Hub data migration declares the tables its data step touches.

The Hub is new, so an update applies all of its migrations. The update probe
(scripts/rehearse_update_compatibility.py) refuses a pending RunPython or RunSQL
whose migration has no UPDATE_SAFETY declaration. These tests read each Hub
migration with the probe's own reader.
"""

import ast
import importlib
import inspect
import pkgutil
import re
from pathlib import Path

import pytest
from django.db import migrations

ROOT = Path(__file__).resolve().parents[3]
PROBE = ROOT / "scripts" / "rehearse_update_compatibility.py"
APP = "omega_hub"
DECLARED = {
    "0002_version_strict_semver": ["postgres:omega_hub_version"],
    "0003_erasure_and_email_verification": [
        "postgres:omega_hub_audit_event",
        "postgres:omega_hub_report",
    ],
    "0004_queue_age_and_bundle_locks": ["postgres:omega_hub_check_job"],
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


def test_every_hub_migration_with_a_data_step_is_declared():
    package = importlib.import_module(f"{APP}.migrations")
    with_code = set()
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{APP}.migrations.{info.name}")
        migration = module.Migration(info.name, APP)
        if any(runs_code(operation) for operation in data_steps(migration.operations)):
            with_code.add(info.name)
    assert with_code == set(DECLARED)


@pytest.mark.parametrize("name", sorted(DECLARED))
def test_the_update_probe_reads_the_declared_tables(name):
    module = importlib.import_module(f"{APP}.migrations.{name}")
    migration = module.Migration(name, APP)
    assert any(runs_code(operation) for operation in data_steps(migration.operations))
    expected = {("postgres", "public." + t.split(":", 1)[1]) for t in DECLARED[name]}
    assert probe_reader()(migration) == expected
    declaration = module.UPDATE_SAFETY
    assert declaration["schema_version"] == 1
    assert declaration["risk_classes"] == ["data_migration"]
    assert declaration["tables"] == DECLARED[name]
    assert 30 <= len(declaration["mitigation"].strip()) <= 2000
    assert declaration["large_volume_rehearsal"] is True
