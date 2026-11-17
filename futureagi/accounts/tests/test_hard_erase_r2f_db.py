"""DB-REQUIRED actual registry completeness; never included in offline runner."""

import unittest
from unittest.mock import Mock, patch

from django.apps import apps
from django.apps.registry import Apps
from django.db import connections, models

from accounts.tests.hard_erase_census_oracle import INVENTORY, requires_protection


class RegisteredCredentialCensusTests(unittest.TestCase):
    def assert_unscoped_mutations_refused(self, table):
        from accounts.services.hard_erase import HardEraseRefused
        from accounts.services.hard_erase_sql import guard_execute

        for statement in (
            f'DELETE FROM "{table}"',
            f'UPDATE "{table}" SET id=id',
            f'COPY "{table}" FROM STDIN',
        ):
            for many in (False, True):
                with self.subTest(table=table, statement=statement, many=many):
                    driver = Mock()
                    with self.assertRaisesRegex(HardEraseRefused, "raw SQL cannot mutate"):
                        guard_execute(driver, statement, [], many,
                                      {"connection": connections["default"]})
                    driver.assert_not_called()

    def test_ready_hook_external_eval_config_is_inventoried_and_raw_mutations_refuse(self):
        from accounts.services import hard_erase_sql
        from accounts.services.hard_erase_sql_policy import CREDENTIAL_EXCLUSIONS
        from tracer.models.external_eval_config import ExternalEvalConfig

        table = ExternalEvalConfig._meta.db_table
        self.assertEqual(table, "tracer_external_eval_config")
        self.assertIn("credentials", {f.name for f in ExternalEvalConfig._meta.concrete_fields})
        self.assertNotIn(table, CREDENTIAL_EXCLUSIONS)
        rows = [row for row in INVENTORY if row["table"] == table]
        self.assertEqual(len(rows), 1)
        self.assertIn("credentials", rows[0]["fields"])
        self.assertIn(table, hard_erase_sql._TABLES)
        self.assert_unscoped_mutations_refused(table)

    def test_real_class_prepared_covers_late_inherited_credentials_and_auto_relation(self):
        from accounts.services import hard_erase_sql

        self.assertTrue(hard_erase_sql._installed)
        registry = Apps(installed_apps=[])
        # Use Django's real ModelBase and signal, without creating database tables
        # or leaking these test models into the product's live app registry.
        with patch.object(hard_erase_sql, "apps", registry), patch.object(
            hard_erase_sql, "_TABLES", hard_erase_sql._TABLES
        ):
            class CredentialBase(models.Model):
                credentials = models.JSONField(default=dict)

                class Meta:
                    abstract = True
                    app_label = "late_registry_test"
                    apps = registry

            class LateConfiguration(CredentialBase):
                peers = models.ManyToManyField("self", symmetrical=False)

                class Meta:
                    app_label = "late_registry_test"
                    apps = registry
                    db_table = "late_registry_configuration"

            through = LateConfiguration._meta.get_field("peers").remote_field.through
            self.assertIs(through._meta.auto_created, LateConfiguration)
            self.assertIs(registry.get_registered_model(
                "late_registry_test", "LateConfiguration"), LateConfiguration)
            for model in (LateConfiguration, through):
                self.assertIn(model._meta.db_table.lower(), hard_erase_sql._TABLES)
                self.assert_unscoped_mutations_refused(model._meta.db_table)

    def test_every_concrete_credential_model_is_protected_or_justified(self):
        from accounts.services.hard_erase_sql import _TABLES
        from accounts.services.hard_erase_sql_policy import (
            CREDENTIAL_EXCLUSIONS,
            EXCLUDED_FIELDS,
        )

        candidates = set()
        excluded_fields_seen = {}
        selected = set()
        models = [
            m
            for m in apps.get_models(include_auto_created=True)
            if not m._meta.abstract and not m._meta.proxy
        ]
        for model in models:
            table = model._meta.db_table.lower()
            fields = model._meta.concrete_fields
            excluded_fields_seen[table] = {field.name for field in fields}
            if requires_protection(model.__module__, model.__name__, table, fields, {}):
                candidates.add(table)
            if requires_protection(
                model.__module__, model.__name__, table, fields, EXCLUDED_FIELDS
            ):
                selected.add(model)
                self.assertIn(table, _TABLES, model.__name__)
        for model in models:
            if model._meta.auto_created in selected:
                self.assertIn(model._meta.db_table.lower(), _TABLES)
        self.assertTrue(candidates)
        for table, reason in CREDENTIAL_EXCLUSIONS.items():
            self.assertTrue(isinstance(reason, str) and reason.strip())
            if table not in excluded_fields_seen:
                continue
            self.assertIn(table, candidates)
            self.assertTrue(set(EXCLUDED_FIELDS[table]) <= excluded_fields_seen[table])
        for row in INVENTORY:
            registered = [
                m
                for m in models
                if (m.__module__, m.__name__) == (row["module"], row["model"])
            ]
            if not registered:
                # An installed application must not silently lose its inventory model.
                self.assertFalse(
                    any(
                        m.__module__.split(".")[0] == row["module"].split(".")[0]
                        for m in models
                    )
                )
                continue
            self.assertEqual(len(registered), 1)
            model = registered[0]
            self.assertEqual(model._meta.db_table, row["table"])
            self.assertTrue(
                set(row["fields"]) <= {f.name for f in model._meta.concrete_fields}
            )
            self.assertIn(row["table"], _TABLES)
