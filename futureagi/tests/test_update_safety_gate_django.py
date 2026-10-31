"""Real Django MigrationLoader/ProjectState checks, collected by backend pytest.

Fixture tests use an isolated app registry and no database. The OSS production
registry cross-check runs in a fresh process (test.py deliberately enables EE).
Run this module with unittest in a private Django-only venv for local fixtures;
the production registry case requires the complete locked backend environment.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from django.db import migrations, models
from django.db.migrations.loader import MigrationLoader
from django.test import override_settings

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("safety_gate_django", ROOT / "scripts/check_update_safety.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
REGISTRY = {"schema_version": 1, "tables": [{"database": "postgres", "table": "hot_events",
            "reason": "One row per observation", "source": "fixture"}]}
INITIAL = '''from django.db import migrations, models
class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [migrations.CreateModel(name="Event", fields=[
        ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("value", models.CharField(max_length=20, null=True)),
    ], options={"db_table": "hot_events"}), migrations.CreateModel(name="ColdEvent", fields=[
        ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
    ], options={"db_table": "cold_events"})]
'''
SECOND = '''from django.db import migrations

def forward(apps, schema_editor):
    Event = apps.get_model("fixed_label", "Event")
    Event.objects.all().update(value="filled")

def cold_seed(apps, schema_editor):
    from product.defaults import DEFAULT_VALUE
    Cold = apps.get_model("fixed_label", "ColdEvent")
    Cold.objects.update(value=DEFAULT_VALUE)

def hidden_backfill(apps, schema_editor):
    from product.backfill import run
    run(apps, schema_editor)

class Migration(migrations.Migration):
    dependencies = [("fixed_label", "0001_initial")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
'''


def reviewed(risks):
    return {"schema_version": 1, "risk_classes": sorted({r["risk_class"] for r in risks}),
            "tables": sorted({r.get("table", "postgres:hot_events") for r in risks}),
            "mitigation": "Nullable expansion followed by 500-row batched cursor backfill; a separate index migration.",
            "review": "https://github.com/future-agi/future-agi/pull/123#discussion_r456",
            "reviewer": "fixture-reviewer", "large_volume_rehearsal": True}


class TestLoaderFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from django.apps import apps
        from django.conf import settings
        if not settings.configured:
            settings.configure(INSTALLED_APPS=[], SECRET_KEY='fixture', DEFAULT_AUTO_FIELD='django.db.models.AutoField',
                               DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}})
        if not apps.ready:
            import django
            django.setup()
        cls.directory = tempfile.TemporaryDirectory(prefix="update-safety-gate-")
        cls.root = Path(cls.directory.name)
        cls.package = cls.root / "update_gate_fixture"
        (cls.package / "migrations").mkdir(parents=True)
        (cls.package / "__init__.py").write_text("")
        (cls.package / "apps.py").write_text('from django.apps import AppConfig\nclass FixtureConfig(AppConfig):\n    name="update_gate_fixture"\n    label="fixed_label"\n')
        (cls.package / "models.py").write_text('from django.db import models\nclass Event(models.Model):\n    value=models.CharField(max_length=20,null=True)\n    class Meta:\n        db_table="hot_events"\nclass ColdEvent(models.Model):\n    class Meta:\n        db_table="cold_events"\n')
        (cls.package / "migrations/__init__.py").write_text("")
        (cls.package / "migrations/0001_initial.py").write_text(INITIAL)
        (cls.package / "migrations/0002_backfill.py").write_text(SECOND)
        sys.path.insert(0, str(cls.root))

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(cls.root))
        for key in list(sys.modules):
            if key == "update_gate_fixture" or key.startswith("update_gate_fixture."):
                del sys.modules[key]
        cls.directory.cleanup()

    def setUp(self):
        self.override = override_settings(INSTALLED_APPS=["update_gate_fixture.apps.FixtureConfig"], MIGRATION_MODULES={}, BASE_DIR=self.root)
        self.override.enable()
        self.loader = MigrationLoader(None, replace_migrations=False)
        self.state = self.loader.project_state([("fixed_label", "0001_initial")])
        self.mapping = {"schema_version": 1, "apps": [{"label": "fixed_label", "module": "update_gate_fixture",
                        "source_root": "update_gate_fixture", "condition": "always", "migrations": True}]}
        self.addCleanup(self.override.disable)

    def risks(self, operation):
        return gate.operation_risks(operation, "fixed_label", self.state, REGISTRY)

    def test_real_layout_with_explicit_label_passes(self):
        from django.apps import apps
        from django.conf import settings
        gate.check_migration_layout(apps, self.loader, settings, self.mapping)

    def test_registry_model_table_cross_check_passes_and_rejects_typo(self):
        from django.apps import apps
        registry = copy.deepcopy(REGISTRY)
        registry["tables"][0].update(app_label="fixed_label", model="Event")
        gate.check_registry_models(apps, registry, self.mapping)
        registry["tables"][0]["table"] = "wrong_table"
        with self.assertRaisesRegex(gate.GateError, "table differs from registry"):
            gate.check_registry_models(apps, registry, self.mapping)

    def test_conditional_source_presence_uses_app_root_not_settings_base_dir(self):
        from django.apps import apps
        from django.conf import settings
        self.mapping["apps"][0]["condition"] = "if_present"
        with override_settings(BASE_DIR=self.root / "misleading_tfc"):
            gate.check_migration_layout(apps, self.loader, settings, self.mapping)

    def test_wrong_basename_label_fails(self):
        from django.apps import apps
        from django.conf import settings
        self.mapping["apps"][0]["label"] = "update_gate_fixture"
        with self.assertRaisesRegex(gate.GateError, "unknown label/module fixed_label.*migration-apps.json"):
            gate.check_migration_layout(apps, self.loader, settings, self.mapping)

    def test_custom_migration_modules_fails(self):
        from django.apps import apps
        from django.conf import settings
        with override_settings(MIGRATION_MODULES={"fixed_label": "different.migrations"}):
            with self.assertRaisesRegex(gate.GateError, "custom MIGRATION_MODULES"):
                gate.check_migration_layout(apps, self.loader, settings, self.mapping)

    def test_squashed_migration_fails(self):
        from django.apps import apps
        from django.conf import settings
        self.loader.disk_migrations[("fixed_label", "0002_backfill")].replaces = [("fixed_label", "old")]
        with self.assertRaisesRegex(gate.GateError, "squashed/replaces"):
            gate.check_migration_layout(apps, self.loader, settings, self.mapping)

    def test_type_change_fails_without_review(self):
        risks = self.risks(migrations.AlterField("event", "value", models.IntegerField(null=True)))
        self.assertEqual({r["risk_class"] for r in risks}, {"type_change"})
        with self.assertRaisesRegex(gate.GateError, "safe patterns"):
            gate.validate_declaration(None, risks, REGISTRY, "0003.py")

    def load_candidate(self, name, source):
        import importlib
        path = self.package / "migrations" / (name + ".py")
        path.write_text(source)
        importlib.invalidate_caches()
        self.addCleanup(path.unlink)
        self.addCleanup(sys.modules.pop, "update_gate_fixture.migrations." + name, None)
        return MigrationLoader(None, replace_migrations=False), path.relative_to(self.root).as_posix()

    def foreign_key_state(self):
        state = self.state.clone()
        for operation in (migrations.CreateModel("Parent", [
                ("id", models.IntegerField(primary_key=True)),
                ("code", models.CharField(max_length=32, unique=True))]),
                migrations.AddField("event", "parent", models.ForeignKey("fixed_label.Parent", null=True,
                    db_index=False, db_constraint=False, on_delete=models.CASCADE))):
            operation.state_forwards("fixed_label", state)
        return state

    def test_unmanaged_existing_table_alias_does_not_exempt_backfill(self):
        source = '''from django.db import migrations, models

def forward(apps, schema_editor):
    apps.get_model("fixed_label", "ExistingEvent").objects.all().update(value="rewritten")

class Migration(migrations.Migration):
    dependencies = [("fixed_label", "0002_backfill")]
    operations = [migrations.CreateModel("ExistingEvent", [
        ("id", models.AutoField(primary_key=True)),
        ("value", models.CharField(max_length=20, null=True)),
    ], options={"db_table": "hot_events", "managed": False}),
    migrations.RunPython(forward, migrations.RunPython.noop)]
'''
        loader, relative = self.load_candidate('0003_existing_alias', source)
        with self.assertRaisesRegex(gate.GateError, 'data_migration'):
            gate.lint_migrations(loader, [relative], self.root, REGISTRY)

    def test_same_type_foreign_key_retarget_still_requires_validation_scan(self):
        state = self.state.clone()
        for operation in (migrations.CreateModel('Parent', [('id', models.IntegerField(primary_key=True)),
                ('code', models.IntegerField(unique=True))]),
                migrations.AddField('event', 'parent', models.ForeignKey('fixed_label.Parent', null=True,
                    db_index=False, db_constraint=True, on_delete=models.CASCADE))):
            operation.state_forwards('fixed_label', state)
        operation = migrations.AlterField('event', 'parent', models.ForeignKey('fixed_label.Parent', to_field='code',
            null=True, db_index=False, db_constraint=True, on_delete=models.CASCADE))
        self.assertIn('constraint', {r['risk_class'] for r in gate.operation_risks(operation, 'fixed_label', state, REGISTRY)})

    def test_foreign_key_target_storage_type_change_requires_declaration(self):
        from django.db import connection
        from unittest.mock import patch
        state = self.foreign_key_state()
        operation = migrations.AlterField("event", "parent", models.ForeignKey("fixed_label.Parent", to_field="code",
            null=True, db_index=False, db_constraint=False, on_delete=models.CASCADE))
        after = state.clone()
        operation.state_forwards("fixed_label", after)
        old = state.apps.get_model("fixed_label", "Event")._meta.get_field("parent")
        new = after.apps.get_model("fixed_label", "Event")._meta.get_field("parent")
        self.assertEqual(old.get_internal_type(), new.get_internal_type())
        self.assertNotEqual(old.db_type(connection), new.db_type(connection))
        with patch.object(connection, "cursor", side_effect=AssertionError("type inspection must not query the database")):
            risks = gate.operation_risks(operation, "fixed_label", state, REGISTRY)
        self.assertEqual({r["risk_class"] for r in risks}, {"type_change", "constraint"})
        with self.assertRaisesRegex(gate.GateError, "type_change.*hot_events"):
            gate.validate_declaration(None, risks, REGISTRY, "0003_fk_type.py")

    def test_loader_requires_review_when_existing_foreign_key_becomes_nullable(self):
        from django.db.backends.postgresql.base import DatabaseWrapper
        from unittest.mock import patch
        historical = '''from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [("fixed_label", "0002_backfill")]
    operations = [migrations.AddField("event", "parent", models.ForeignKey(
        "fixed_label.ColdEvent", db_index=False, on_delete=models.CASCADE))]
'''
        # This is an already-applied parent migration, not part of the new plan:
        # its AddField risk must not mask the new AlterField regression.
        self.load_candidate('0003_existing_fk', historical)
        source = '''from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [("fixed_label", "0003_existing_fk")]
    operations = [migrations.AlterField("event", "parent", models.ForeignKey(
        "fixed_label.ColdEvent", null=True, db_index=False, on_delete=models.CASCADE))]
'''
        loader, relative = self.load_candidate('0004_nullable_fk', source)
        # Exercise the production classifier with an unconnected PostgreSQL
        # backend: no cached server version or SQL-composition stub is needed.
        backend = DatabaseWrapper({'ENGINE': 'django.db.backends.postgresql', 'NAME': 'fixture'})
        with patch('django.db.connection', backend), patch.object(backend, 'cursor',
                side_effect=AssertionError('risk classification must not query PostgreSQL')), patch.object(
                backend, 'ensure_connection', side_effect=AssertionError('risk classification must not connect')):
            with self.assertRaisesRegex(gate.GateError, 'constraint.*hot_events'):
                gate.lint_migrations(loader, [relative], self.root, REGISTRY)
            value = reviewed([{'table': 'postgres:hot_events', 'risk_class': 'constraint'}])
            (self.root / relative).write_text(source + '\nUPDATE_SAFETY = ' + repr(value) + '\n')
            self.assertEqual(gate.lint_migrations(loader, [relative], self.root, REGISTRY), [
                {'path': relative, 'tables': ['postgres:hot_events'], 'risk_classes': ['constraint'],
                 'large_volume_rehearsal': True}])

    def test_postgresql_nullable_fk_alter_recreates_validating_constraint(self):
        from django.db.backends.postgresql.base import DatabaseWrapper
        from unittest.mock import patch
        backend = DatabaseWrapper({'ENGINE': 'django.db.backends.postgresql', 'NAME': 'fixture'})
        backend.pg_version = 160000
        state = self.state.clone()
        migrations.AddField('event', 'parent', models.ForeignKey('fixed_label.ColdEvent',
            db_index=False, on_delete=models.CASCADE)).state_forwards('fixed_label', state)
        operation = migrations.AlterField('event', 'parent', models.ForeignKey('fixed_label.ColdEvent',
            null=True, db_index=False, on_delete=models.CASCADE))
        after = state.clone()
        operation.state_forwards('fixed_label', after)
        old = state.apps.get_model('fixed_label', 'Event')._meta.get_field('parent')
        model = after.apps.get_model('fixed_label', 'Event')
        new = model._meta.get_field('parent')

        def compose_parameter_free_sql(sql, params):
            # psycopg 3's mogrify opens a cursor even for empty params. This
            # DDL has no interpolation: retain Django's SQL and stub only that
            # driver boundary, failing if the fixture ever needs adaptation.
            self.assertIn(params, ((), []))
            self.assertNotIn('%', sql)
            return sql

        with patch.object(backend, 'cursor', side_effect=AssertionError('SQL collection must not contact PostgreSQL')):
            with patch.object(backend.ops, 'compose_sql', side_effect=compose_parameter_free_sql) as compose:
                with backend.schema_editor(collect_sql=True, atomic=False) as editor:
                    # Catalog names are a fixture; Django still emits all DDL.
                    with patch.object(editor, '_constraint_names', return_value=['hot_events_parent_fk']) as names:
                        editor.alter_field(model, old, new)
                    names.assert_called_once_with(model, ['parent_id'], foreign_key=True)
                compose.assert_called()
            sql = '\n'.join(editor.collected_sql)
            self.assertIn('DROP CONSTRAINT', sql)
            self.assertIn('DROP NOT NULL', sql)
            self.assertIn('FOREIGN KEY', sql)
            self.assertIn('REFERENCES "cold_events"', sql)
            self.assertNotIn('NOT VALID', sql)
            # The production classifier must work without the composition stub.
            with patch('django.db.connection', backend):
                risks = gate.operation_risks(operation, 'fixed_label', state, REGISTRY)
                self.assertEqual({r['risk_class'] for r in risks}, {'constraint'})
                with self.assertRaisesRegex(gate.GateError, 'constraint.*hot_events'):
                    gate.validate_declaration(None, risks, REGISTRY, '0004_nullable_fk.py')

    def test_existing_constrained_fk_schema_changes_require_review_but_metadata_does_not(self):
        from django.db import connection
        from unittest.mock import patch
        state = self.state.clone()
        original = {'null': False, 'db_index': False, 'on_delete': models.CASCADE}
        migrations.AddField('event', 'parent', models.ForeignKey('fixed_label.ColdEvent',
            **original)).state_forwards('fixed_label', state)
        for change in ({'null': True}, {'default': 1}, {'db_default': 1}, {'db_index': True},
                       {'unique': True}, {'db_column': 'owner_id'}):
            operation = migrations.AlterField('event', 'parent', models.ForeignKey(
                'fixed_label.ColdEvent', **{**original, **change}))
            with self.subTest(change=change), patch.object(connection, 'cursor',
                    side_effect=AssertionError('risk classification must not query the database')):
                risks = gate.operation_risks(operation, 'fixed_label', state, REGISTRY)
                self.assertIn('constraint', {r['risk_class'] for r in risks})
                with self.assertRaisesRegex(gate.GateError, 'hot_events'):
                    gate.validate_declaration(None, risks, REGISTRY, '0004_fk_change.py')
        for change in ({'help_text': 'Documentation only'}, {'blank': True}, {'related_name': 'events'},
                       {'on_delete': models.PROTECT}, {'db_comment': 'Column documentation'}):
            operation = migrations.AlterField('event', 'parent', models.ForeignKey(
                'fixed_label.ColdEvent', **{**original, **change}))
            with self.subTest(metadata=change):
                self.assertEqual(gate.operation_risks(operation, 'fixed_label', state, REGISTRY), [])

    def test_small_target_pk_type_change_flags_high_volume_inbound_fk(self):
        state = self.foreign_key_state()
        operation = migrations.AlterField("parent", "id", models.BigIntegerField(primary_key=True))
        risks = gate.operation_risks(operation, "fixed_label", state, REGISTRY)
        self.assertEqual({(r["table"], r["risk_class"]) for r in risks}, {("postgres:hot_events", "type_change")})

    def test_backend_type_resolution_errors_fail_closed(self):
        from unittest.mock import patch
        operation = migrations.AlterField("event", "value", models.CharField(max_length=20, null=True))
        with patch.object(models.CharField, "db_parameters", side_effect=ValueError("unresolvable custom type")):
            with self.assertRaisesRegex(gate.GateError, "cannot resolve historical backend column type"):
                self.risks(operation)

    def test_foreign_key_same_storage_metadata_change_is_safe(self):
        state = self.foreign_key_state()
        operation = migrations.AlterField("event", "parent", models.ForeignKey("fixed_label.Parent", null=True,
            db_index=False, db_constraint=False, on_delete=models.CASCADE, help_text="Documentation only"))
        self.assertEqual(gate.operation_risks(operation, "fixed_label", state, REGISTRY), [])

    def test_loader_rejects_undeclared_fk_type_then_requires_reviewed_rehearsal(self):
        source = '''from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [("fixed_label", "0002_backfill")]
    operations = [
        migrations.CreateModel("Parent", [("id", models.IntegerField(primary_key=True)),
            ("code", models.CharField(max_length=32, unique=True))]),
        migrations.AddField("event", "parent", models.ForeignKey("fixed_label.Parent", null=True,
            db_index=False, db_constraint=False, on_delete=models.CASCADE)),
        migrations.AlterField("event", "parent", models.ForeignKey("fixed_label.Parent", to_field="code",
            null=True, db_index=False, db_constraint=False, on_delete=models.CASCADE))]
'''
        loader, relative = self.load_candidate("0003_fk_type", source)
        with self.assertRaisesRegex(gate.GateError, "type_change.*hot_events"):
            gate.lint_migrations(loader, [relative], self.root, REGISTRY)
        value = reviewed([{"table": "postgres:hot_events", "risk_class": risk} for risk in ("type_change", "constraint")])
        (self.root / relative).write_text(source + "\nUPDATE_SAFETY = " + repr(value) + "\n")
        rows = gate.lint_migrations(loader, [relative], self.root, REGISTRY)
        self.assertEqual(rows, [{"path": relative, "tables": ["postgres:hot_events"],
                                "risk_classes": ["constraint", "type_change"], "large_volume_rehearsal": True}])

    def test_not_null_and_index_scan_detected(self):
        operations = [migrations.AlterField("event", "value", models.CharField(max_length=20)),
                      migrations.AddIndex("event", models.Index(fields=["value"], name="hot_value")),
                      migrations.AddConstraint("event", models.UniqueConstraint(fields=["value"], name="hot_unique"))]
        self.assertEqual([{r["risk_class"] for r in self.risks(op)} for op in operations],
                         [{"not_null"}, {"index"}, {"constraint"}])

    def test_callable_and_volatile_db_default_detected(self):
        from django.db.models.functions import Now
        for field in (models.IntegerField(default=lambda: 1), models.DateTimeField(db_default=Now())):
            with self.subTest(field=field):
                self.assertIn("volatile_default", {r["risk_class"] for r in self.risks(migrations.AddField("event", "extra", field))})

    def test_nullable_and_constant_db_default_are_safe(self):
        for field in (models.CharField(max_length=20, null=True), models.IntegerField(db_default=0)):
            with self.subTest(field=field):
                self.assertEqual(self.risks(migrations.AddField("event", "extra", field)), [])

    def test_positive_integer_addfield_requires_backend_check_constraint_review(self):
        from django.db import connection
        from unittest.mock import patch
        for field_type in (models.PositiveSmallIntegerField, models.PositiveIntegerField, models.PositiveBigIntegerField):
            for options in ({'null': True}, {'db_default': 0}):
                operation = migrations.AddField('event', 'positive_count', field_type(**options))
                after = self.state.clone()
                operation.state_forwards('fixed_label', after)
                rendered = after.apps.get_model('fixed_label', 'Event')._meta.get_field('positive_count')
                self.assertTrue(rendered.db_parameters(connection)['check'])
                with self.subTest(field=field_type.__name__, options=options), patch.object(connection, 'cursor',
                        side_effect=AssertionError('constraint inspection must not query the database')):
                    risks = self.risks(operation)
                    self.assertIn('constraint', {r['risk_class'] for r in risks})
                    with self.assertRaisesRegex(gate.GateError, 'constraint.*hot_events'):
                        gate.validate_declaration(None, risks, REGISTRY, '0003_positive_count.py')

    def test_postgresql_backend_emits_the_nullable_positive_field_check_without_database_io(self):
        from django.db.backends.postgresql.base import DatabaseWrapper
        from unittest.mock import patch
        backend = DatabaseWrapper({'ENGINE': 'django.db.backends.postgresql', 'NAME': 'fixture'})
        backend.pg_version = 160000
        operation = migrations.AddField('event', 'positive_count', models.PositiveIntegerField(null=True))
        after = self.state.clone()
        operation.state_forwards('fixed_label', after)
        model = after.apps.get_model('fixed_label', 'Event')
        field = model._meta.get_field('positive_count')
        with patch.object(backend, 'cursor', side_effect=AssertionError('SQL collection must not contact PostgreSQL')):
            with backend.schema_editor(collect_sql=True, atomic=False) as editor:
                editor.add_field(model, field)
            self.assertTrue(any('CHECK' in sql and 'positive_count' in sql and '>= 0' in sql
                                for sql in editor.collected_sql), editor.collected_sql)
            with patch('django.db.connection', backend):
                self.assertIn('constraint', {r['risk_class'] for r in self.risks(operation)})

    def test_loader_requires_declaration_for_nullable_positive_integer_check(self):
        source = '''from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies = [("fixed_label", "0002_backfill")]
    operations = [migrations.AddField("event", "positive_count", models.PositiveIntegerField(null=True))]
'''
        loader, relative = self.load_candidate('0003_positive_check', source)
        with self.assertRaisesRegex(gate.GateError, 'constraint.*hot_events'):
            gate.lint_migrations(loader, [relative], self.root, REGISTRY)
        (self.root / relative).write_text(source + '\nUPDATE_SAFETY = ' + repr(reviewed([
            {'table': 'postgres:hot_events', 'risk_class': 'constraint'}])) + '\n')
        self.assertEqual(gate.lint_migrations(loader, [relative], self.root, REGISTRY)[0]['risk_classes'], ['constraint'])

    def test_alterfield_backend_check_addition_requires_constraint_review(self):
        state = self.state.clone()
        migrations.AddField('event', 'count', models.IntegerField(null=True)).state_forwards('fixed_label', state)
        operation = migrations.AlterField('event', 'count', models.PositiveIntegerField(null=True))
        risks = gate.operation_risks(operation, 'fixed_label', state, REGISTRY)
        self.assertIn('constraint', {r['risk_class'] for r in risks})

    def test_existing_positive_field_help_text_does_not_add_a_check(self):
        state = self.state.clone()
        migrations.AddField('event', 'count', models.PositiveIntegerField(null=True)).state_forwards('fixed_label', state)
        self.assertEqual(gate.operation_risks(migrations.AlterField('event', 'count', models.PositiveIntegerField(
            null=True, help_text='Documentation only')), 'fixed_label', state, REGISTRY), [])

    def test_postgres_constraint_addition_and_validation_are_classified(self):
        from django.contrib.postgres.operations import AddConstraintNotValid, ValidateConstraint
        constraint = models.CheckConstraint(condition=models.Q(value__isnull=False), name='hot_value_check')
        for operation in (AddConstraintNotValid('event', constraint), ValidateConstraint('event', 'hot_value_check')):
            with self.subTest(operation=operation):
                self.assertEqual({r['risk_class'] for r in self.risks(operation)}, {'constraint'})

    def test_metadata_only_change_is_safe(self):
        self.assertEqual(self.risks(migrations.AlterField("event", "value", models.CharField(max_length=20, null=True, help_text="new docs"))), [])

    def test_runpython_requires_review_without_claiming_inferred_tables(self):
        operation = self.loader.disk_migrations[("fixed_label", "0002_backfill")].operations[0]
        self.assertEqual(self.risks(operation), [{"risk_class": "data_migration", "operation": "RunPython"}])
        gate.validate_declaration(reviewed(self.risks(operation)), self.risks(operation), REGISTRY, "0002.py")

    def test_known_small_table_and_pure_utilities_still_require_review(self):
        import importlib
        module = importlib.import_module("update_gate_fixture.migrations.0002_backfill")
        self.assertEqual(self.risks(migrations.RunPython(module.cold_seed)), [{"risk_class": "data_migration", "operation": "RunPython"}])
        self.assertEqual(self.risks(migrations.RunSQL("UPDATE cold_events SET value=1")), [{"risk_class": "data_migration", "operation": "RunSQL"}])
        source = '''from django.db import migrations
from django.utils import timezone
from django.utils.timezone import now as current
import uuid as identities

def forward(apps, schema_editor):
    Cold = apps.get_model("fixed_label", "ColdEvent")
    Cold.objects.count()
    timezone.now()
    current()
    identities.uuid4()
class Migration(migrations.Migration):
    dependencies = [("fixed_label", "0002_backfill")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
'''
        loader, relative = self.load_candidate("0003_pure_utilities", source)
        with self.assertRaisesRegex(gate.GateError, "data_migration"):
            gate.lint_migrations(loader, [relative], self.root, REGISTRY)

    def test_imported_hidden_database_helper_is_not_trusted(self):
        import importlib
        module = importlib.import_module("update_gate_fixture.migrations.0002_backfill")
        self.assertTrue(self.risks(migrations.RunPython(module.hidden_backfill)))

    def test_module_scope_imported_backfills_fail_with_real_loader(self):
        # The entrypoint's explicit model is small; only the imported module
        # writes hot_events. Cover both import syntaxes, aliases, and a chain.
        helper = self.package / "backfill.py"
        helper.write_text('def run(apps, schema_editor):\n    Event = apps.get_model("fixed_label", "Event")\n    Event.objects.all().update(value=None)\n')
        self.addCleanup(helper.unlink)
        self.addCleanup(sys.modules.pop, "update_gate_fixture.backfill", None)
        variants = [("from update_gate_fixture import backfill", "backfill.run", ""),
                    ("from update_gate_fixture import backfill as work", "work.run", ""),
                    ("import update_gate_fixture.backfill as work", "work.run", ""),
                    ("import update_gate_fixture.backfill", "update_gate_fixture.backfill.run", ""),
                    ("from update_gate_fixture import backfill", "bridge", "def bridge(apps, schema_editor):\n    backfill.run(apps, schema_editor)\n"),
                    ("import os as process", "bridge", "def bridge(apps, schema_editor):\n    process.system('python backfill.py')\n"),
                    ("from os import system as invoke", "bridge", "def bridge(apps, schema_editor):\n    invoke('python backfill.py')\n"),
                    ("from django.utils.module_loading import import_string", "bridge", "def bridge(apps, schema_editor):\n    import_string('update_gate_fixture.backfill.run')(apps, schema_editor)\n")]
        for index, (imports, call, bridge) in enumerate(variants):
            with self.subTest(imports=imports, call=call):
                source = '''from django.db import migrations
{imports}
{bridge}
def forward(apps, schema_editor):
    Cold = apps.get_model("fixed_label", "ColdEvent")
    Cold.objects.count()
    {call}(apps, schema_editor)
class Migration(migrations.Migration):
    dependencies = [("fixed_label", "0002_backfill")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
'''.format(imports=imports, bridge=bridge, call=call)
                loader, relative = self.load_candidate("0003_hidden_" + str(index), source)
                operation = loader.disk_migrations[("fixed_label", Path(relative).stem)].operations[0]
                risks = self.risks(operation)
                self.assertEqual(risks, [{"risk_class": "data_migration", "operation": "RunPython"}])
                with self.assertRaisesRegex(gate.GateError, "data_migration"):
                    gate.lint_migrations(loader, [relative], self.root, REGISTRY)
                (self.root / relative).write_text(source + "\nUPDATE_SAFETY = " + repr(reviewed(risks)) + "\n")
                rows = gate.lint_migrations(loader, [relative], self.root, REGISTRY)
                self.assertEqual(rows[0]["tables"], ["postgres:hot_events"])
                self.assertTrue(rows[0]["large_volume_rehearsal"])

    def test_callable_aliases_and_dynamic_factories_cannot_hide_backfills(self):
        helper = self.package / 'backfill.py'
        helper.write_text('def run(apps, schema_editor):\n    apps.get_model("fixed_label", "Event").objects.update(value="rewritten")\n')
        self.addCleanup(helper.unlink)
        self.addCleanup(sys.modules.pop, 'update_gate_fixture.backfill', None)
        variants = [
            ('', 'work = backfill.run\n    work(apps, schema_editor)'),
            ('work = backfill.run', 'work(apps, schema_editor)'),
            ('bridge = backfill', 'work = bridge.run\n    again = work\n    again(apps, schema_editor)'),
            ('', 'getattr(backfill, "run")(apps, schema_editor)'),
            ('', 'work = getattr(backfill, "run")\n    work(apps, schema_editor)'),
            ('', 'message = f"{backfill.run(apps, schema_editor)}"'),
            ('', 'getattr(apps, "get_model")("fixed_label", "Event").objects.update(value="rewritten")'),
            ('', 'lookup = apps.get_model\n    lookup("fixed_label", "Event").objects.update(value="rewritten")'),
        ]
        for index, (global_alias, call) in enumerate(variants):
            with self.subTest(call=call, global_alias=global_alias):
                source = '''from django.db import migrations
from update_gate_fixture import backfill
{global_alias}
def forward(apps, schema_editor):
    Cold = apps.get_model("fixed_label", "ColdEvent")
    Cold.objects.count()
    {call}
class Migration(migrations.Migration):
    dependencies = [("fixed_label", "0002_backfill")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
'''.format(global_alias=global_alias, call=call)
                loader, relative = self.load_candidate('0003_callable_alias_' + str(index), source)
                with self.assertRaisesRegex(gate.GateError, 'data_migration'):
                    gate.lint_migrations(loader, [relative], self.root, REGISTRY)
                value = reviewed([{'table': 'postgres:hot_events', 'risk_class': 'data_migration'}])
                (self.root / relative).write_text(source + '\nUPDATE_SAFETY = ' + repr(value) + '\n')
                self.assertEqual(gate.lint_migrations(loader, [relative], self.root, REGISTRY), [
                    {'path': relative, 'tables': ['postgres:hot_events'], 'risk_classes': ['data_migration'],
                     'large_volume_rehearsal': True}])

    def test_runpython_global_and_assembled_sql_require_hot_table_review(self):
        variants = [
            ('SQL = "UPDATE hot_events SET value=\'rewritten\'"', 'schema_editor.execute(SQL)'),
            ('SQL = "UPDATE hot_events SET value=\'rewritten\'"\nQUERY = SQL', 'schema_editor.execute(QUERY)'),
            ('SQL = "UPDATE hot_events SET value=\'rewritten\'"', 'execute = schema_editor.execute\n    execute(SQL)'),
            ('PREFIX = "UPDATE hot_events"', 'query = PREFIX\n    query += " SET value=\'rewritten\'"\n    schema_editor.execute(query)'),
            ('', 'schema_editor.execute(" ".join(["UPDATE", "hot_events", "SET value=\'rewritten\'"]))'),
            ('SQL = "DO $$ BEGIN EXECUTE \'UPDATE hot_\' || \'events SET value=NULL\'; END $$;"', 'schema_editor.execute(SQL)'),
            ('', 'schema_editor.execute(build_unknown_sql())'),
        ]
        for index, (globals_source, body) in enumerate(variants):
            with self.subTest(body=body, globals_source=globals_source):
                source = '''from django.db import migrations
{globals_source}
def forward(apps, schema_editor):
    Cold = apps.get_model("fixed_label", "ColdEvent")
    Cold.objects.count()
    {body}
class Migration(migrations.Migration):
    dependencies = [("fixed_label", "0002_backfill")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
'''.format(globals_source=globals_source, body=body)
                loader, relative = self.load_candidate('0003_global_sql_' + str(index), source)
                with self.assertRaisesRegex(gate.GateError, 'data_migration'):
                    gate.lint_migrations(loader, [relative], self.root, REGISTRY)

    def test_runsql_all_statement_forms_and_comments_require_review(self):
        variants = [
            "SELECT 1; -- cold_events\nDO $$ BEGIN EXECUTE 'UPDATE hot_' || 'events SET value=NULL'; END $$;",
            "UPDATE cold_events SET id=id; DO $body$ BEGIN EXECUTE 'UPDATE hot_' || 'events SET value=NULL'; END $body$;",
            "UPDATE cold_events SET id=id; CALL unknown_backfill();",
            "UPDATE cold_events SET id=id; UPDATE unregistered_table SET value=1;",
            ["UPDATE cold_events SET id=id;", ("UPDATE hot_events SET value=%s", ['rewritten'])],
        ]
        for index, sql in enumerate(variants):
            with self.subTest(sql=sql):
                source = 'from django.db import migrations\nclass Migration(migrations.Migration):\n    dependencies=[("fixed_label", "0002_backfill")]\n    operations=[migrations.RunSQL(' + repr(sql) + ')]\n'
                loader, relative = self.load_candidate('0003_sql_statements_' + str(index), source)
                with self.assertRaisesRegex(gate.GateError, 'data_migration'):
                    gate.lint_migrations(loader, [relative], self.root, REGISTRY)
                value = reviewed([{'table': 'postgres:hot_events', 'risk_class': 'data_migration'}])
                (self.root / relative).write_text(source + '\nUPDATE_SAFETY = ' + repr(value) + '\n')
                self.assertTrue(gate.lint_migrations(loader, [relative], self.root, REGISTRY)[0]['large_volume_rehearsal'])
        for sql in ('-- hot_events unchanged\nUPDATE cold_events SET id=id;',
                    '/* hot_events; /* nested comment */ */ UPDATE "cold_events" SET id=id;',
                    "UPDATE cold_events SET value='hot_events; -- literal'; SELECT 1;",
                    'SELECT 1; -- cold_events', '-- comment only hot_events', 'SELECT \'hot_events\';'):
            with self.subTest(safe_sql=sql):
                self.assertEqual(self.risks(migrations.RunSQL(sql)), [{"risk_class": "data_migration", "operation": "RunSQL"}])

    def test_runsql_comments_do_not_erase_actual_hot_statement(self):
        for sql in ("SELECT '--literal'; UPDATE hot_events SET value=NULL;", "/* cold_events */ UPDATE hot_events SET value=NULL;",
                    'UPDATE cold_events SET id=id; UPDATE "public"."hot_events" SET value=NULL;'):
            with self.subTest(sql=sql):
                self.assertEqual(self.risks(migrations.RunSQL(sql)), [{"risk_class": "data_migration", "operation": "RunSQL"}])

    def test_runsql_small_statements_still_require_review(self):
        for sql in ('INSERT INTO cold_events (id) VALUES (1);',
                    'SELECT COUNT(*) FROM cold_events;',
                    'CREATE INDEX cold_id_idx ON cold_events (id);',
                    ['SELECT 1;', ('UPDATE cold_events SET id=%s WHERE id=%s;', [2, 1])]):
            with self.subTest(sql=sql):
                self.assertEqual(self.risks(migrations.RunSQL(sql)), [{"risk_class": "data_migration", "operation": "RunSQL"}])

    def test_runsql_relation_lists_require_review(self):
        for sql in ('SELECT * FROM cold_events c, hot_events h;',
                    'TRUNCATE cold_events, hot_events;',
                    'DELETE FROM cold_events USING hot_events WHERE cold_events.id=hot_events.id;',
                    'INSERT INTO cold_events (id) SELECT id FROM hot_events;'):
            with self.subTest(sql=sql):
                self.assertEqual(self.risks(migrations.RunSQL(sql)), [{"risk_class": "data_migration", "operation": "RunSQL"}])

    def test_runpython_even_bounded_alias_requires_review(self):
        source = '''from django.db import migrations
SQL = "-- hot_events is only a comment\\nUPDATE cold_events SET id=id;"
def seed(apps, schema_editor):
    Cold = apps.get_model("fixed_label", "ColdEvent")
    Cold.objects.count()
def forward(apps, schema_editor):
    work = seed
    work(apps, schema_editor)
    schema_editor.execute(SQL)
class Migration(migrations.Migration):
    dependencies = [("fixed_label", "0002_backfill")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
'''
        loader, relative = self.load_candidate('0003_bounded_alias', source)
        with self.assertRaisesRegex(gate.GateError, "data_migration"):
            gate.lint_migrations(loader, [relative], self.root, REGISTRY)

    def test_loader_linter_accepts_reviewed_backfill_and_outputs_rehearsal(self):
        path = self.package / "migrations/0002_backfill.py"
        previous = path.read_text()
        operation = self.loader.disk_migrations[("fixed_label", "0002_backfill")].operations[0]
        try:
            path.write_text(previous + "\nUPDATE_SAFETY = " + repr(reviewed(self.risks(operation))) + "\n")
            rows = gate.lint_migrations(self.loader, ["update_gate_fixture/migrations/0002_backfill.py"], self.root, REGISTRY)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["large_volume_rehearsal"])
        finally:
            path.write_text(previous)

    def test_runsql_hot_table_fails_and_noop_passes(self):
        self.assertTrue(self.risks(migrations.RunSQL('UPDATE "hot_events" SET value=NULL')))
        self.assertEqual(self.risks(migrations.RunSQL(migrations.RunSQL.noop, migrations.RunSQL.noop)), [])

    def test_foreign_key_without_index_still_requires_constraint_review(self):
        risks = self.risks(migrations.AddField("event", "parent", models.ForeignKey(
            "fixed_label.ColdEvent", null=True, db_index=False, on_delete=models.CASCADE)))
        self.assertEqual({r["risk_class"] for r in risks}, {"constraint"})

    def test_table_rewrite_and_separate_state_database_detected(self):
        self.assertTrue(self.risks(migrations.RenameModel("Event", "RenamedEvent")))
        self.assertTrue(self.risks(migrations.RemoveField("event", "value")))
        operation = migrations.SeparateDatabaseAndState(database_operations=[migrations.RunSQL("UPDATE hot_events SET value=NULL")], state_operations=[])
        self.assertTrue(self.risks(operation))

    def test_existing_table_alias_is_not_fresh(self):
        fresh = set()
        state = self.state.clone()
        operation = migrations.CreateModel("NewEvent", [("id", models.AutoField(primary_key=True))], options={"db_table": "hot_events"})
        self.assertEqual(gate.operation_risks(operation, "fixed_label", state, REGISTRY, fresh), [])
        operation.state_forwards("fixed_label", state)
        self.assertEqual(gate.operation_risks(migrations.AddIndex("newevent", models.Index(fields=["id"], name="new_index")),
                                             "fixed_label", state, REGISTRY, fresh),
                         [dict(table='postgres:hot_events', risk_class='index', operation='AddIndex')])

    def test_genuinely_new_physical_table_is_fresh(self):
        fresh = set()
        state = self.state.clone()
        migrations.DeleteModel('Event').state_forwards('fixed_label', state)
        operation = migrations.CreateModel('NewEvent', [('id', models.AutoField(primary_key=True))], options={'db_table': 'hot_events'})
        self.assertEqual(gate.operation_risks(operation, 'fixed_label', state, REGISTRY, fresh), [])
        operation.state_forwards('fixed_label', state)
        self.assertEqual(gate.operation_risks(migrations.AddIndex('newevent', models.Index(fields=['id'], name='new_index')),
                                             'fixed_label', state, REGISTRY, fresh), [])

    def test_loader_linter_rejects_new_backfill_without_declaration(self):
        with self.assertRaisesRegex(gate.GateError, "data_migration.*safe patterns"):
            gate.lint_migrations(self.loader, ["update_gate_fixture/migrations/0002_backfill.py"], self.root, REGISTRY)

    def test_only_exact_noop_pairs_are_exempt(self):
        noop = migrations.RunPython.noop
        sql_noop = migrations.RunSQL.noop
        for op in (migrations.RunPython(noop, noop), migrations.RunSQL(sql_noop, sql_noop)):
            self.assertEqual(self.risks(op), [])
        for op in (migrations.RunPython(noop), migrations.RunPython(noop, lambda apps, editor: None),
                   migrations.RunPython(lambda apps, editor: None, noop), migrations.RunSQL(sql_noop),
                   migrations.RunSQL(sql_noop, 'DELETE FROM cold_events'), migrations.RunSQL('SELECT 1', sql_noop)):
            with self.subTest(operation=op):
                self.assertEqual(self.risks(op)[0]['risk_class'], 'data_migration')
        class HiddenOperation(migrations.RunPython):
            pass
        self.assertEqual(self.risks(HiddenOperation(noop, noop))[0]['risk_class'], 'data_migration')

    def test_runsql_noop_requires_plain_string_identity_and_checks_nested_state(self):
        class MisleadingSQL(str):
            def __eq__(self, other):
                return True
        op = migrations.RunSQL(MisleadingSQL('UPDATE hot_events SET value=NULL'), migrations.RunSQL.noop)
        self.assertEqual(self.risks(op), [{'risk_class': 'data_migration', 'operation': 'RunSQL'}])
        op = migrations.RunSQL(migrations.RunSQL.noop, migrations.RunSQL.noop,
            state_operations=[migrations.AddField('event', 'extra', models.IntegerField(default=0))])
        self.assertIn('not_null', {row['risk_class'] for row in self.risks(op)})

    def test_state_only_created_alias_does_not_certify_an_empty_physical_table(self):
        state = self.state.clone()
        migrations.DeleteModel('Event').state_forwards('fixed_label', state)
        aliases = [migrations.CreateModel('Alias', [('id', models.AutoField(primary_key=True))], options={'db_table': 'hot_events'}),
                   migrations.AddIndex('alias', models.Index(fields=['id'], name='state_alias_index'))]
        for wrapper in (migrations.RunSQL(migrations.RunSQL.noop, migrations.RunSQL.noop, state_operations=aliases),
                        migrations.SeparateDatabaseAndState(state_operations=aliases)):
            with self.subTest(wrapper=wrapper):
                self.assertEqual(gate.operation_risks(wrapper, 'fixed_label', state, REGISTRY),
                                 [{'table': 'postgres:hot_events', 'risk_class': 'index', 'operation': 'AddIndex'}])

    def test_custom_schema_operations_are_not_implicitly_classified(self):
        class HiddenAlterField(migrations.AlterField):
            pass
        with self.assertRaisesRegex(gate.GateError, 'unclassified custom migration operation'):
            self.risks(HiddenAlterField('event', 'value', models.CharField(max_length=20, null=True)))

    def test_arbitrary_data_operation_payload_is_never_inspected_or_executed(self):
        from unittest.mock import patch
        with patch.object(gate.inspect, 'getsource', side_effect=AssertionError('arbitrary code must not be inspected')):
            self.assertEqual(self.risks(migrations.RunPython(print, migrations.RunPython.noop)),
                             [{'risk_class': 'data_migration', 'operation': 'RunPython'}])
            self.assertEqual(self.risks(migrations.RunSQL('not even valid SQL')),
                             [{'risk_class': 'data_migration', 'operation': 'RunSQL'}])

    def test_separate_state_operations_are_not_a_data_operation_exemption(self):
        op = migrations.SeparateDatabaseAndState(database_operations=[],
            state_operations=[migrations.RunSQL('SELECT 1')])
        self.assertEqual(self.risks(op), [{'risk_class': 'data_migration', 'operation': 'RunSQL'}])

    def test_index_and_constraint_changes_require_high_volume_review(self):
        for op in (migrations.RemoveIndex('event', 'existing'), migrations.RenameIndex('event', 'new', old_name='old'),
                   migrations.RemoveConstraint('event', 'existing')):
            with self.subTest(operation=op):
                self.assertTrue(self.risks(op))

    def test_not_null_python_default_without_db_default_requires_review(self):
        for field in (models.IntegerField(), models.IntegerField(default=0), models.IntegerField(default=lambda: 0)):
            with self.subTest(field=field):
                self.assertIn('not_null', {r['risk_class'] for r in self.risks(migrations.AddField('event', 'extra', field))})

    def test_unmanaged_schema_alias_is_resolved_to_registered_physical_table(self):
        state = self.state.clone()
        migrations.CreateModel('Alias', [('id', models.AutoField(primary_key=True)),
            ('value', models.CharField(max_length=20, null=True))],
            options={'managed': False, 'db_table': 'hot_events'}).state_forwards('fixed_label', state)
        for operation in (migrations.AlterField('alias', 'value', models.IntegerField(null=True)),
                          migrations.AddIndex('alias', models.Index(fields=['value'], name='alias_idx'))):
            with self.subTest(operation=operation):
                risks = gate.operation_risks(operation, 'fixed_label', state, REGISTRY)
                self.assertTrue(risks)
                self.assertEqual({r['table'] for r in risks}, {'postgres:hot_events'})

    def test_column_storage_name_change_requires_review(self):
        risks = self.risks(migrations.AlterField('event', 'value', models.CharField(max_length=20, null=True, db_column='renamed_value')))
        self.assertIn('table_rewrite', {r['risk_class'] for r in risks})

    def test_real_makemigrations_command_fails_on_unmigrated_model(self):
        # Two fresh processes avoid the app/model and bytecode import caches.
        runner = '''import os,sys
sys.path.insert(0,sys.argv[1])
from django.conf import settings
settings.configure(INSTALLED_APPS=["update_gate_fixture.apps.FixtureConfig"], DATABASES={"default":{"ENGINE":"django.db.backends.sqlite3","NAME":":memory:"}}, SECRET_KEY="fixture", DEFAULT_AUTO_FIELD="django.db.models.AutoField")
import django; django.setup()
from django.core.management import call_command
call_command("makemigrations",check=True,dry_run=True,verbosity=0)
'''
        # The existing second migration has no state changes, so this is clean.
        command = [sys.executable, "-I", "-B", "-c", runner, str(self.root)]
        clean = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
        model = self.package / "models.py"
        previous = model.read_text()
        try:
            model.write_text(previous.replace("class Event(models.Model):", "class Event(models.Model):\n    extra=models.IntegerField(null=True)"))
            changed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(changed.returncode, 1, changed.stdout + changed.stderr)
        finally:
            model.write_text(previous)


class TestProductionOSSRegistry(unittest.TestCase):
    def test_actual_installed_apps_and_migration_loader_match_reviewed_mapping(self):
        runner = '''import sys
sys.path.insert(0,sys.argv[1])
import importlib.util
from pathlib import Path
root=Path(sys.argv[2]); spec=importlib.util.spec_from_file_location("gate",root/"scripts/check_update_safety.py")
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
gate.django_gate(root,[],gate.document(root/"deploy/update/migration-apps.json"),gate.document(root/"deploy/update/high-volume-tables.json"))
'''
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": "tfc.settings.settings", "EE_LICENSE_KEY": "", "ENV_TYPE": "local",
               "LITELLM_MODE": "PRODUCTION", "NO_STARTUP_DB_MUTATIONS": "true", "FI_SKIP_CH25_MIGRATION": "1"}
        process = subprocess.run([sys.executable, "-I", "-B", "-c", runner, str(ROOT / "futureagi"), str(ROOT)],
                                 env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)


if __name__ == "__main__":
    # Local fixture invocation explicitly names TestLoaderFixtures. Running this
    # entire module still requires the complete backend env, just like pytest.
    from django.conf import settings
    if not settings.configured:
        settings.configure(INSTALLED_APPS=[], SECRET_KEY="fixture", DEFAULT_AUTO_FIELD="django.db.models.AutoField",
                           DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
        import django
        django.setup()
    unittest.main()
