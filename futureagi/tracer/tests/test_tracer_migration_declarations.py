"""Update-safety declarations of the edited tracer data migrations.

The pending-migration table-scope probe in scripts/rehearse_update_compatibility.py
takes the tables of a RunPython step only from one module-level UPDATE_SAFETY
literal and refuses a migration that has none. Its declared() runs here unchanged.
The update-safety gate's declaration() and validate_declaration() from
scripts/check_update_safety.py then check each literal against the seed migration
whose reviewer and review fields are still placeholders.
"""

import ast
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "futureagi/tracer/migrations"
REFERENCE = MIGRATIONS / "0132_seed_alert_issue_occurrences_high_water.py"
SCHEMAS = {"postgres": "public", "clickhouse": "default"}
# What the gate's classifier reports for a RunPython step that is not a no-op.
DATA_STEP = [{"risk_class": "data_migration", "operation": "RunPython"}]
# Synthetic values in the shape the gate accepts; they name no actual review.
SYNTHETIC_REVIEW = {
    "reviewer": "fixture-reviewer",
    "review": "https://github.com/future-agi/future-agi/pull/123#discussion_r456",
}

TABLES = {
    "0032_auto_20251030_1000.py": [
        "postgres:tracer_observation_span",
        "postgres:tracer_project",
    ],
    "0076_canonicalise_filter_ops.py": [
        "postgres:tracer_custom_eval_config",
        "postgres:tracer_eval_task",
        "postgres:tracer_saved_view",
        "postgres:tracer_useralertmonitor",
    ],
    "0078_canonicalize_persisted_filter_contracts.py": [
        "postgres:tracer_custom_eval_config",
        "postgres:tracer_dashboardwidget",
        "postgres:tracer_eval_task",
        "postgres:tracer_saved_view",
        "postgres:tracer_useralertmonitor",
    ],
    "0079_backfill_skipped_reason.py": [
        "postgres:fi_cdc_outbox",
        "postgres:tracer_eval_logger",
    ],
    "0081_rename_eval_task_filters_key.py": [
        "postgres:tracer_eval_task",
    ],
    "0084_rewrite_latency_ms_refs.py": [
        "postgres:tracer_dashboardwidget",
        "postgres:tracer_eval_task",
        "postgres:tracer_saved_view",
    ],
    "0087_eval_entry_baseline_backfill.py": [
        "postgres:fi_cdc_outbox",
        "postgres:model_hub_composite_eval_child",
        "postgres:model_hub_evaltemplate",
        "postgres:tracer_custom_eval_config",
        "postgres:tracer_eval_logger",
    ],
    "0094_canonicalize_dashboard_widget_configs.py": [
        "postgres:tracer_dashboardwidget",
    ],
    "0094_normalize_eval_task_filter_casing.py": [
        "postgres:tracer_eval_task",
    ],
    "0096_repair_scanner_cluster_error_count.py": [
        "postgres:tracer_trace_error_group",
        "postgres:tracer_trace_scan_issue",
    ],
}


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "tracer_declarations_update_safety_gate",
        ROOT / "scripts/check_update_safety.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_probe_declared():
    """declared() from the probe program text, with only the names it uses."""
    script = ast.parse((ROOT / "scripts/rehearse_update_compatibility.py").read_text())
    probe = next(
        ast.literal_eval(node.value)
        for node in script.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PENDING_TABLE_SCOPE"
            for target in node.targets
        )
    )
    wanted = {"IDENT", "resolved", "declared"}
    body = [
        node
        for node in ast.parse(probe).body
        if (isinstance(node, ast.FunctionDef) and node.name in wanted)
        or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id in wanted
                for target in node.targets
            )
        )
    ]
    env = {
        "ast": ast,
        "re": re,
        "Path": Path,
        # declared() reads the source file of the migration's class.
        "inspect": SimpleNamespace(getsourcefile=lambda klass: klass.path),
        "schemas": dict(SCHEMAS),
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), "<scope-probe>", "exec"), env)
    return env["declared"]


gate = _load_gate()
declared = _load_probe_declared()
REGISTRY = gate.document(ROOT / "deploy/update/high-volume-tables.json")


def _fails_only_on_the_review_fields(value, path):
    with pytest.raises(gate.GateError, match="large-volume rehearsal are mandatory"):
        gate.validate_declaration(value, DATA_STEP, REGISTRY, str(path))
    # The same literal passes once only reviewer and review hold accepted values.
    gate.validate_declaration(
        {**value, **SYNTHETIC_REVIEW}, DATA_STEP, REGISTRY, str(path)
    )


@pytest.mark.parametrize("name", sorted(TABLES))
def test_probe_reads_the_declared_tables_of_each_data_step(name):
    migration = type("Migration", (), {"path": str(MIGRATIONS / name)})()
    try:
        scope = declared(migration)
    except AssertionError as exc:
        # A data step without one module-level declaration is refused here.
        scope = str(exc)
    expected = set()
    for table in TABLES[name]:
        engine, table_name = table.split(":", 1)
        expected.add((engine, SCHEMAS[engine] + "." + table_name))
    assert scope == expected


@pytest.mark.parametrize("name", sorted(TABLES))
def test_declaration_fails_the_gate_only_on_the_review_fields(name):
    reference = gate.declaration(REFERENCE.read_text(), REFERENCE)
    _fails_only_on_the_review_fields(reference, REFERENCE)

    path = MIGRATIONS / name
    value = gate.declaration(path.read_text(), path)
    assert value is not None, f"{name}: no UPDATE_SAFETY declaration"
    assert set(value) == set(reference)
    assert value["schema_version"] == 1
    assert value["risk_classes"] == ["data_migration"]
    assert value["tables"] == TABLES[name]
    assert 30 <= len(value["mitigation"].strip()) <= 2000
    assert value["large_volume_rehearsal"] is True
    # Placeholders exactly as the seed migration's, never an invented review.
    assert value["reviewer"] == reference["reviewer"]
    assert value["review"] == reference["review"]
    _fails_only_on_the_review_fields(value, path)
