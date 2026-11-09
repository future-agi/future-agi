"""RED-first, DB-free algorithm tests; real codec, fake historical ORM only."""

import copy
import importlib
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.conf import settings
from django.db.migrations.exceptions import IrreversibleError

from agentcc.services import webhook_credential_storage as codec
from agentcc.services.webhook_headers import UnsupportedHeaders, header_map
from agentcc.tests._webhook_migration_fake import fake_database, record
from tfc.migration_database import delegated_database_alias

ROOT = Path(__file__).resolve().parents[1]
NAMES = [
    "0075_webhook_credential_columns",
    "0076_encrypt_webhook_credentials",
    "0077_remove_webhook_plaintext",
]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            (ROOT / "migrations" / (NAMES[1] + ".py")).exists(),
            "fenced conversion is absent",
        )
        self.helper = importlib.import_module(
            "agentcc.migrations._webhook_credentials_v1"
        )
        self.m2 = importlib.import_module("agentcc.migrations." + NAMES[1])
        settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
        self.output = io.StringIO()
        capture = redirect_stdout(self.output)
        capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)

    def run_rows(self, records, alias="default_direct"):
        return fake_database(self.helper, records, alias)

    def read(self, row, field):
        context = codec.StorageContext(
            str(row.organization_id), str(row.pk), field, row.url
        )
        return codec.decrypt_for_delivery(
            context,
            storage_version=row.credential_storage_version,
            ciphertext=bytes(getattr(row, "encrypted_" + field)),
        )

    def refused(self, function, *args):
        with self.assertRaisesRegex(
            ValueError, "^Webhook credential migration refused$"
        ) as caught:
            function(*args)
        self.assertIsNone(caught.exception.__context__)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(delegated_database_alias())

    def test_all_legacy_header_classes_and_exact_secrets(self):
        active = [
            None,
            {},
            [],
            "",
            False,
            0,
            0.0,
            -0.0,
            {"X": {"nested": [True, 1, 1.0]}},
            [["X", 1], ["x", 2], ["X", 3]],
        ]
        malformed = [
            True,
            1,
            -2.5,
            "truthy",
            [["X"]],
            [["X", 1, 2]],
            {"": "original"},
            [[False, "original"]],
        ]
        values = active + malformed
        rows = [
            record(i + 1, value, ["", " enc::raw 雪 ", "********"][i % 3])
            for i, value in enumerate(values)
        ]
        for alias in ("default", "default_direct"):
            with self.run_rows(rows, alias) as (store, apps, editor):
                self.m2.forwards(apps, editor)
                for before in rows:
                    after = store.records[before.pk]
                    self.assertEqual(after.headers, before.headers)
                    self.assertEqual(after.secret, before.secret)
                    self.assertTrue(after.encrypted_headers and after.encrypted_secret)
                    self.assertEqual(after.credential_storage_version, 1)
                    self.assertEqual(self.read(after, "secret"), before.secret)
                    context = codec.StorageContext(
                        str(after.organization_id), str(after.pk), "headers", after.url
                    )
                    try:
                        expected = header_map(before.headers)
                    except UnsupportedHeaders:
                        with self.assertRaises(
                            codec.WebhookCredentialStorageError
                        ) as failure:
                            self.read(after, "headers")
                        self.assertEqual(
                            failure.exception.code, codec.ErrorCode.NEEDS_REENTRY
                        )
                        codec.verify_preserved_for_migration(
                            context,
                            before.headers,
                            storage_version=1,
                            ciphertext=after.encrypted_headers,
                        )
                    else:
                        self.assertEqual(
                            json.dumps(self.read(after, "headers")),
                            json.dumps(expected),
                        )
                snapshot = copy.deepcopy(store.records)
                self.m2.forwards(apps, editor)
                self.assertEqual(store.records, snapshot)
                self.assertIn("verified", self.output.getvalue())
                self.assertNotIn("fixture.invalid", self.output.getvalue())

    def test_unpreservable_originals_refuse_without_clear(self):
        cyclic = []
        cyclic.append(cyclic)
        cases = [
            (float("nan"), ""),
            (float("inf"), ""),
            ({1: "x"}, ""),
            ((["X", 1],), ""),
            (b"", ""),
            (cyclic, ""),
            ({}, None),
            ({}, 1),
        ]
        for headers, secret in cases:
            with (
                self.subTest(
                    kind=type(headers).__name__, secret_kind=type(secret).__name__
                ),
                self.run_rows([record(1, headers, secret)]) as (store, apps, editor),
            ):
                self.refused(self.m2.forwards, apps, editor)
                self.assertIsNone(store.records[record(1).pk].encrypted_headers)
                self.assertEqual(store.updates, 0)

    def test_partial_pair_version_empty_and_corruption_refuse(self):
        for field, value in [
            ("encrypted_headers", b"partial"),
            ("credential_storage_version", 1),
        ]:
            row = record(1, {})
            setattr(row, field, value)
            with self.run_rows([row]) as (store, apps, editor):
                self.refused(self.m2.forwards, apps, editor)
                self.assertEqual(store.records[row.pk], row)
        for field, value in [
            ("encrypted_headers", b""),
            ("encrypted_secret", b"corrupt"),
            ("credential_storage_version", 0),
            ("credential_storage_version", True),
            ("headers", {"X": "different"}),
        ]:
            with self.run_rows([record(1, {"X": "initial"})]) as (store, apps, editor):
                self.m2.forwards(apps, editor)
                setattr(store.records[record(1).pk], field, value)
                before = copy.deepcopy(store.records)
                self.refused(self.m2.forwards, apps, editor)
                self.assertEqual(before, store.records)

    def test_exact_json_type_and_order_conflict(self):
        for original, conflicting in [
            ({"X": True}, {"X": 1}),
            ({"X": 1}, {"X": 1.0}),
            ({"X": 0.0}, {"X": -0.0}),
            ({"X": 1, "x": 2}, {"x": 2, "X": 1}),
        ]:
            with self.run_rows([record(1, original)]) as (store, apps, editor):
                self.m2.forwards(apps, editor)
                store.records[record(1).pk].headers = conflicting
                self.refused(self.m2.forwards, apps, editor)

    def test_failure_rolls_back_current_batch_then_resumes(self):
        rows = [record(i, {"X": "synthetic"}) for i in range(1, 5)]
        with (
            self.run_rows(rows) as (store, apps, editor),
            patch.object(self.helper, "BATCH_SIZE", 2),
        ):

            def fail(current):
                if current.updates == 4:
                    raise RuntimeError("synthetic sensitive driver error")

            store.on_update = fail
            self.refused(self.m2.forwards, apps, editor)
            self.assertTrue(store.records[rows[0].pk].encrypted_headers)
            self.assertIsNone(store.records[rows[2].pk].encrypted_headers)
            for row in rows:
                self.assertEqual(store.records[row.pk].headers, row.headers)
            committed = store.records[rows[0].pk].encrypted_headers
            store.on_update = None
            self.m2.forwards(apps, editor)
            self.assertEqual(store.records[rows[0].pk].encrypted_headers, committed)

    def test_full_verification_catches_late_mutation_and_no_clear(self):
        rows = [record(1, {}), record(2, {})]
        with (
            self.run_rows(rows) as (store, apps, editor),
            patch.object(self.helper, "BATCH_SIZE", 1),
        ):

            def mutate(current):
                if current.updates == 2:
                    current.records[rows[0].pk].encrypted_headers = b"corrupt"

            store.on_update = mutate
            self.refused(self.m2.forwards, apps, editor)
            self.assertEqual([r.headers for r in store.records.values()], [{}, {}])

    def test_preserved_valid_empty_never_reactivated(self):
        row = record(1, {})
        for field, original in [("headers", {}), ("secret", "")]:
            context = codec.StorageContext(
                str(row.organization_id), str(row.pk), field, row.url
            )
            setattr(
                row,
                "encrypted_" + field,
                codec.preserve_for_migration(context, original),
            )
        row.credential_storage_version = 1
        with self.run_rows([row]) as (store, apps, editor):
            self.m2.forwards(apps, editor)
            self.assertEqual(store.records[row.pk], row)
            self.assertEqual(store.updates, 0)

    def test_missing_key_and_verification_failure_are_value_free(self):
        with (
            self.run_rows([record(1, "malformed")]) as (store, apps, editor),
            patch.object(
                codec,
                "verify_preserved_for_migration",
                side_effect=RuntimeError("raw original or ciphertext"),
            ),
        ):
            self.refused(self.m2.forwards, apps, editor)
            self.assertEqual(store.updates, 0)
        settings.INTEGRATION_ENCRYPTION_KEY = ""
        with self.run_rows([record(1, {})]) as (store, apps, editor):
            self.refused(self.m2.forwards, apps, editor)
            self.assertEqual(store.updates, 0)

    def test_context_binding_orphans_and_invalid_identity(self):
        for field, value in [
            ("url", "https://fixture.invalid/changed"),
            ("organization_id", record(2).organization_id),
            ("pk", record(2).pk),
        ]:
            with self.run_rows([record(1, {})]) as (store, apps, editor):
                self.m2.forwards(apps, editor)
                setattr(store.records[record(1).pk], field, value)
                self.refused(self.m2.forwards, apps, editor)
        for field, value in [
            ("url", ""),
            ("organization_id", None),
            ("pk", "not-uuid"),
        ]:
            row = record(1, {})
            setattr(row, field, value)
            with self.run_rows([row]) as (_, apps, editor):
                self.refused(self.m2.forwards, apps, editor)
        with self.run_rows([record(1, {})]) as (store, apps, editor):
            store.orgs.clear()
            self.refused(self.m2.forwards, apps, editor)

    def test_reverse_guards_and_preclear_reverification(self):
        m1, _, m3 = [
            importlib.import_module("agentcc.migrations." + name) for name in NAMES
        ]
        with self.run_rows([record(1, {})]) as (store, apps, editor):
            m1.forwards(apps, editor)
            self.m2.forwards(apps, editor)
            m3.verify_before_remove(apps, editor)
            for backwards in [m1.backwards, self.m2.backwards, m3.backwards]:
                with self.assertRaises(IrreversibleError):
                    backwards(apps, editor)
                self.assertIsNone(delegated_database_alias())
            store.records[record(1).pk].secret = "changed"
            self.refused(m3.verify_before_remove, apps, editor)

    def test_memoryview_adapter_and_rotation_preserve_both_domains(self):
        from agentcc.services.gateway_key_crypto import KeyTransition

        target = Fernet.generate_key().decode()
        transition = KeyTransition(settings.INTEGRATION_ENCRYPTION_KEY, target)
        with self.run_rows([record(1, {"X": "value"}), record(2, "malformed")]) as (
            store,
            apps,
            editor,
        ):
            self.m2.forwards(apps, editor)
            for row in store.records.values():
                for field in ["encrypted_headers", "encrypted_secret"]:
                    rotated, kind = transition.convert(
                        memoryview(getattr(row, field)), wrapped=False
                    )
                    self.assertEqual(kind, "source")
                    self.assertEqual(
                        transition.verifier().convert(rotated, wrapped=False)[1],
                        "target",
                    )
                    setattr(row, field, memoryview(rotated))
            settings.INTEGRATION_ENCRYPTION_KEY = target
            self.m2.forwards(apps, editor)
            self.assertEqual(store.updates, 2)
