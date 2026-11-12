"""DB-free real Django model regressions; no application settings file is loaded.

The standalone runner configures only Django's empty registry and dummy backend.
Under pytest, the existing application registry and settings are left intact.
"""

import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import django
from django.apps import AppConfig, apps
from django.conf import settings
from django.db import connections, models
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ModelState, ProjectState


def setUpModule():
    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=[],
            DATABASES={"default": {"ENGINE": "django.db.backends.dummy"}},
            USE_TZ=True,
        )
        django.setup()


def historical(label="migration_erase_probe", name="Entry", fields=None):
    state = ProjectState()
    state.add_model(
        ModelState(
            label,
            name,
            fields or [("id", models.AutoField(primary_key=True))],
        )
    )
    return state.apps.get_model(label, name)


class HistoricalModelKeysTests(unittest.TestCase):
    def setUp(self):
        from accounts.services.hard_erase import HardEraseRefused
        from accounts.services.hard_erase_keys import row_keys
        from accounts.services.hard_erase_migrations import _migration_call

        self.row_keys = row_keys
        self.refused = HardEraseRefused
        self.migration_call = _migration_call
        self.connection = connections["default"]

    def in_migration(self, callback, connection=None):
        wrapped = self.migration_call(lambda executor: callback())
        return wrapped(SimpleNamespace(connection=connection or self.connection))

    def test_stateapps_without_installed_model_has_no_pickle_lookup_failure(self):
        row = historical()(id=7)
        self.assertIsNot(row._meta.apps, apps)
        self.assertEqual(
            self.in_migration(lambda: self.row_keys([row], "default")), frozenset()
        )
        self.assertEqual(row.pk, 7)

    def test_actual_migration_recorder_model_has_no_pickle_lookup_failure(self):
        row = MigrationRecorder.Migration(id=8, app="prism", name="probe")
        self.assertIsNot(row._meta.apps, apps)
        self.assertEqual(
            self.in_migration(lambda: self.row_keys([row], "default")), frozenset()
        )

    def test_missing_model_outside_executor_scope_is_refused(self):
        with self.assertRaises(self.refused):
            self.row_keys([historical()(id=9)], "default")

    def test_foreign_connection_scope_does_not_authorize_missing_model(self):
        with self.assertRaises(self.refused):
            self.in_migration(
                lambda: self.row_keys([historical()(id=10)], "default"),
                connection=object(),
            )

    def test_migration_snapshot_preserves_keys_and_does_not_mutate_unsaved_row(self):
        org = UUID(int=11)
        person = UUID(int=12)
        cls = historical(
            fields=[
                ("id", models.AutoField(primary_key=True)),
                ("organization_id", models.UUIDField()),
                ("user_id", models.UUIDField()),
            ]
        )
        row = cls(organization_id=org, user_id=person)
        original_state = row._state
        self.assertEqual(
            self.in_migration(lambda: self.row_keys([row], "default")),
            frozenset({("organization", str(org))}),
        )
        self.assertIsNone(row.pk)
        self.assertIs(row._state, original_state)

    def test_registered_historical_and_live_models_keep_same_protection_keys(self):
        from accounts.services import hard_erase

        label = "registered_erase_probe"
        module = ModuleType(label)
        module.__file__ = __file__
        config = AppConfig(label, module)
        config.apps = apps
        org, person = UUID(int=13), UUID(int=14)
        with patch.dict(apps.app_configs, {label: config}), patch.dict(
            apps.all_models, {label: {}}
        ):
            config.models = apps.all_models[label]

            class Entry(models.Model):
                organization_id = models.UUIDField()
                user_id = models.UUIDField()

                class Meta:
                    app_label = label

            state = ProjectState()
            state.add_model(ModelState.from_model(Entry))
            old_model = state.apps.get_model(label, "Entry")
            expected = frozenset({("organization", str(org))})
            try:
                for model in (Entry, old_model):
                    with self.subTest(historical=model is old_model):
                        row = model(id=15, organization_id=org, user_id=person)
                        with patch.object(
                            hard_erase, "_ownership_graph", wraps=hard_erase._ownership_graph
                        ) as graph:
                            self.assertEqual(self.row_keys([row], "default"), expected)
                        snapshot = graph.call_args.args[0][0]
                        self.assertIs(type(snapshot), Entry)
                        self.assertIsNot(snapshot, row)
                        self.assertIsNot(snapshot._state, row._state)
            finally:
                apps.clear_cache()
        apps.clear_cache()

    def test_runtime_model_copy_lookup_failure_is_not_suppressed(self):
        from accounts.services import hard_erase_keys

        row = SimpleNamespace(_meta=SimpleNamespace(apps=apps))
        with patch.object(hard_erase_keys, "copy", side_effect=LookupError("probe")):
            with self.assertRaisesRegex(LookupError, "probe"):
                self.in_migration(lambda: self.row_keys([row], "default"))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    setUpModule()
    unittest.main()
