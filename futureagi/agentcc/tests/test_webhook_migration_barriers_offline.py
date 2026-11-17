"""Additional fail-closed barriers, DB-free with the actual v1 codec."""

import importlib
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.conf import settings

from agentcc.migrations import _webhook_credentials_v1 as storage
from agentcc.services import webhook_credential_storage as codec
from agentcc.tests._webhook_migration_fake import fake_database, record
from tfc.migration_database import delegated_database_alias

M2 = importlib.import_module("agentcc.migrations.0076_encrypt_webhook_credentials")
M3 = importlib.import_module("agentcc.migrations.0077_remove_webhook_plaintext")


class BarrierTests(unittest.TestCase):
    def setUp(self):
        settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
        self.output = io.StringIO()
        capture = redirect_stdout(self.output)
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)

    def test_final_schema_and_all_row_authentication(self):
        for failure in (None, "legacy-column", "nullable", "corrupt", "version"):
            with (
                self.subTest(failure=failure),
                fake_database(storage, [record(1, "malformed"), record(2, {})]) as (
                    store,
                    apps,
                    editor,
                ),
            ):
                M2.forwards(apps, editor)
                columns = [
                    SimpleNamespace(name=name, null_ok=False)
                    for name in storage.STORAGE_FIELDS
                ]
                if failure == "legacy-column":
                    columns.append(SimpleNamespace(name="secret", null_ok=False))
                if failure == "nullable":
                    columns[0].null_ok = True
                editor.connection.introspection = SimpleNamespace(
                    get_table_description=lambda *args, columns=columns: columns
                )
                for row in store.records.values():
                    del row.headers
                    del row.secret
                if failure == "corrupt":
                    store.records[record(2).pk].encrypted_headers = b"corrupt"
                if failure == "version":
                    store.records[record(2).pk].credential_storage_version = 0
                if failure:
                    with self.assertRaisesRegex(
                        ValueError, "^Webhook credential migration refused$"
                    ):
                        M3.check_after_remove(apps, editor)
                else:
                    M3.check_after_remove(apps, editor)
                self.assertIsNone(delegated_database_alias())

    def test_failures_and_counts_never_emit_values_or_ciphertext(self):
        original = "synthetic-private-original-marker"
        with (
            fake_database(storage, [record(1, original, original)]) as (
                store,
                apps,
                editor,
            ),
            self.assertNoLogs(level="DEBUG"),
        ):
            M2.forwards(apps, editor)
            row = store.records[record(1).pk]
            ciphertexts = [row.encrypted_headers, row.encrypted_secret]
            row.encrypted_headers, row.encrypted_secret = (
                row.encrypted_secret,
                row.encrypted_headers,
            )
            with self.assertRaises(ValueError) as caught:
                M2.forwards(apps, editor)
            self.assertEqual(str(caught.exception), storage.REFUSAL)
            self.assertIsNone(caught.exception.__context__)
            self.assertIsNone(caught.exception.__cause__)
            rendered = self.output.getvalue() + repr(caught.exception)
            for sensitive in [
                original,
                row.url,
                str(row.pk),
                str(row.organization_id),
                *(value.decode() for value in ciphertexts),
            ]:
                self.assertNotIn(sensitive, rendered)

    def test_no_coercion_or_metaclass_equality_on_non_json_objects(self):
        class HostileType(type):
            def __eq__(self, other):
                raise AssertionError("arbitrary equality")

        class Hostile(metaclass=HostileType):
            def __bool__(self):
                raise AssertionError("arbitrary truth")

            def __str__(self):
                raise AssertionError("arbitrary coercion")

        for original in [
            Hostile(),
            {"X": Hostile()},
            [["X", Hostile()], ["X", "replacement"]],
        ]:
            with self.assertRaisesRegex(
                ValueError, "^Webhook credential migration refused$"
            ):
                storage.expected("headers", original)

    def test_active_malformed_cannot_be_passed_off_as_empty(self):
        row = record(1, "malformed")
        for field, value in [("headers", {}), ("secret", "")]:
            setattr(
                row,
                "encrypted_" + field,
                codec.encrypt_for_storage(storage.context(row, field), value),
            )
        row.credential_storage_version = 1
        with fake_database(storage, [row]) as (store, apps, editor):
            with self.assertRaises(ValueError):
                M2.forwards(apps, editor)
            self.assertEqual(store.updates, 0)

    def test_census_mismatch_and_affected_row_mismatch_refuse(self):
        from agentcc.tests._webhook_migration_fake import Rows

        for failure in ("raw-census", "affected-row"):
            with fake_database(storage, [record(1, {})]) as (store, apps, editor):
                if failure == "raw-census":
                    target = patch.object(
                        editor.connection.cursor, "fetchone", return_value=(2,)
                    )
                else:
                    target = patch.object(Rows, "update", return_value=0)
                with target, self.assertRaises(ValueError):
                    M2.forwards(apps, editor)
                self.assertIsNone(store.records[record(1).pk].encrypted_headers)
                self.assertIsNone(delegated_database_alias())

    def test_preserved_original_conflict_and_wrong_key_refuse(self):
        for failure in ("original", "key"):
            with fake_database(storage, [record(1, "malformed")]) as (
                store,
                apps,
                editor,
            ):
                M2.forwards(apps, editor)
                if failure == "original":
                    store.records[record(1).pk].headers = "different"
                else:
                    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
                with self.assertRaises(ValueError):
                    M2.forwards(apps, editor)
                self.assertEqual(store.updates, 1)
