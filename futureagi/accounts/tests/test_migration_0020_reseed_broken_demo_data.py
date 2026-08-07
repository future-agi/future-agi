"""Regression tests for accounts.0020_reseed_broken_demo_data (issue #671).

On a fresh OSS install, model_hub and tracer are not available in the
migration state, so the migration's cross-app model lookups
(``apps.get_model("model_hub", "Dataset")`` etc.) raised LookupError and
crashed the entire migration, leaving the backend unhealthy.

These tests exercise the real call path Django uses to run a data migration
— ``Migration.apply`` through the actual RunPython operation with the real
``StateApps`` built at accounts.0019 — and assert the migration tolerates the
missing apps instead of raising.
"""

from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

APP = "accounts"
MIGRATE_FROM = "0019_merge_20260407_1927"
MIGRATE_TO = "0020_reseed_broken_demo_data"


class ReseedBrokenDemoDataMigrationTest(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connections["default"])

    def test_state_before_0020_lacks_cross_app_models(self):
        """The premise of issue #671: at accounts.0019 neither model_hub nor
        tracer is present in the migration state."""
        apps = self.executor.loader.project_state((APP, MIGRATE_FROM)).apps
        with self.assertRaises(LookupError):
            apps.get_model("model_hub", "Dataset")
        with self.assertRaises(LookupError):
            apps.get_model("tracer", "Project")

    def test_0020_applies_when_cross_app_models_are_unavailable(self):
        """Applying 0020 through Django's migration machinery must not raise
        when model_hub/tracer are missing from the migration state.

        Before the fix the RunPython body called ``apps.get_model``
        unguarded, which raised LookupError and aborted the migration.
        """
        state = self.executor.loader.project_state((APP, MIGRATE_FROM))
        migration = self.executor.loader.disk_migrations[(APP, MIGRATE_TO)]
        with connections["default"].schema_editor() as schema_editor:
            migration.apply(state, schema_editor)
