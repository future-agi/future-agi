"""Native PostgreSQL/Django proof for the narrow choices-only scope exception."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.db import connection, migrations, models
from django.db.backends.utils import strip_quotes, truncate_name
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ModelState, ProjectState

try:
    from django.db.migrations.utils import resolve_relation
except ImportError:
    from django.db.migrations.operations.utils import resolve_relation

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.django_db(transaction=True)


def scope_functions():
    source = ast.parse((ROOT / "scripts/rehearse_update_compatibility.py").read_text())
    literal = next(
        ast.literal_eval(node.value)
        for node in source.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PENDING_TABLE_SCOPE"
            for target in node.targets
        )
    )
    nodes = [
        node
        for node in ast.parse(literal).body
        if isinstance(node, ast.FunctionDef)
        or isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id in {"IDENT", "NO_TABLE", "MODEL_OPS"}
            for target in node.targets
        )
    ]
    import re

    namespace = {
        "models": models,
        "migrations": migrations,
        "ast": ast,
        "re": re,
        "schemas": {"postgres": "public", "clickhouse": "default"},
        "resolve_relation": resolve_relation,
        "strip_quotes": strip_quotes,
        "truncate_name": truncate_name,
    }
    exec(
        compile(
            ast.Module(body=nodes, type_ignores=[]), "<production-table-scope>", "exec"
        ),
        namespace,
    )
    return namespace


def fixture_state(field):
    state = ProjectState()
    state.add_model(
        ModelState(
            "scope_fixture",
            "Record",
            [
                ("id", models.AutoField(primary_key=True)),
                ("value", field),
            ],
            options={"db_table": "scope_fixture_record"},
        )
    )
    return state


def collect_sql(operation, app, before):
    assert connection.vendor == "postgresql"
    assert connection.pg_version // 10000 == 16
    after = before.clone()
    operation.state_forwards(app, after)
    with connection.schema_editor(collect_sql=True, atomic=False) as editor:
        operation.database_forwards(app, editor, before, after)
    return editor.collected_sql


def test_exact_tracer_0130_changes_state_but_emits_no_postgres_sql():
    loader = MigrationLoader(connection=None)
    migration = loader.get_migration("tracer", "0130_observationspan_internal_type")
    before = loader.project_state(nodes=[("tracer", migration.name)], at_end=False)
    assert len(migration.operations) == 1
    operation = migration.operations[0]
    field_before = before.models[("tracer", "observationspan")].fields[
        "observation_type"
    ]
    assert ("internal", "Internal") not in field_before.choices
    assert ("internal", "Internal") in operation.field.choices
    assert collect_sql(operation, "tracer", before) == []
    assert scope_functions()["touched"](operation, migration, before) == set()


@pytest.mark.requires_ee
def test_exact_usage_0030_is_state_only_despite_the_unique_key():
    loader = MigrationLoader(connection=None)
    migration = loader.get_migration("usage", "0030_add_jev_evaluator_call_type")
    before = loader.project_state(nodes=[("usage", migration.name)], at_end=False)
    assert len(migration.operations) == 1
    operation = migration.operations[0]
    old = before.models[("usage", "apicalltype")].fields["name"]
    assert old.unique and operation.field.unique
    assert old.max_length == operation.field.max_length == 50
    assert old.choices != operation.field.choices
    assert collect_sql(operation, "usage", before) == []
    assert scope_functions()["touched"](operation, migration, before) == set()


@pytest.mark.parametrize(
    "change",
    [
        {"max_length": 40},
        {"null": True},
        {"db_index": True},
        {"db_default": "a"},
        {"db_column": "renamed_value"},
    ],
)
def test_schema_changes_keep_physical_scope_and_emit_sql(change):
    before = fixture_state(models.CharField(max_length=20, choices=[("a", "A")]))
    options = {"max_length": 20, "choices": [("a", "A"), ("b", "B")], **change}
    operation = migrations.AlterField("record", "value", models.CharField(**options))
    migration = SimpleNamespace(app_label="scope_fixture")
    assert scope_functions()["touched"](operation, migration, before) == {
        ("postgres", "public.scope_fixture_record")
    }
    assert collect_sql(operation, "scope_fixture", before)


def test_custom_fields_and_operations_are_not_treated_as_state_only():
    class CustomCharField(models.CharField):
        pass

    class CustomAlterField(migrations.AlterField):
        pass

    functions = scope_functions()
    before = fixture_state(models.CharField(max_length=20, choices=[("a", "A")]))
    after = models.CharField(max_length=20, choices=[("a", "A"), ("b", "B")])
    assert not functions["choices_only_alter"](
        CustomAlterField("record", "value", after), "scope_fixture", before
    )
    assert not functions["choices_only_alter"](
        migrations.AlterField(
            "record", "value", CustomCharField(max_length=20, choices=after.choices)
        ),
        "scope_fixture",
        before,
    )
    assert not functions["choices_only_alter"](
        migrations.AlterField("record", "value", after, preserve_default=False),
        "scope_fixture",
        before,
    )
