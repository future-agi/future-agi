"""Algorithm-only M2 tests with a fake historical manager, NOT database proof.

Run directly with python -I. Real Fernet and alias scope; mocked transactions.
The PostgreSQL two-connection suite remains mandatory and NOT_RUN locally.
"""
# ruff: noqa: E402

import copy
import importlib
import io
import sys
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from django.db.migrations import AddField, RemoveField, RunPython
from django.db.migrations.exceptions import IrreversibleError

from simulate.tests._agent_api_key_offline import settings
from tfc.migration_database import delegated_database_alias

M2 = importlib.import_module(
    "simulate.migrations.0103_encrypt_agent_definition_api_key"
)


class Rows:
    def __init__(self, store, selected=None):
        self.store = store
        self.selected = list(store.records) if selected is None else selected

    def using(self, alias):
        assert alias == self.store.alias == delegated_database_alias()
        return self

    def order_by(self, field):
        assert field == "pk"
        return Rows(self.store, sorted(self.store.records))

    def filter(self, **kwargs):
        if "pk__gt" in kwargs:
            return Rows(
                self.store, [pk for pk in self.selected if pk > kwargs["pk__gt"]]
            )
        return Rows(self.store, [kwargs["pk"]])

    def exclude(self, **kwargs):
        records = self.store.records
        if "encrypted_api_key__isnull" in kwargs:
            return Rows(
                self.store, [pk for pk in self.selected if records[pk][1] is not None]
            )
        return Rows(
            self.store,
            [
                pk
                for pk in self.selected
                if records[pk][1] != kwargs["encrypted_api_key"]
            ],
        )

    def exists(self):
        return bool(self.selected)

    def select_for_update(self):
        assert self.store.in_atomic
        assert delegated_database_alias() == self.store.alias
        return self

    def __getitem__(self, item):
        return [
            SimpleNamespace(
                pk=pk,
                api_key=self.store.records[pk][0],
                encrypted_api_key=self.store.records[pk][1],
            )
            for pk in self.selected[item]
        ]

    def update(self, **kwargs):
        assert self.store.in_atomic
        assert set(kwargs) == {"api_key", "encrypted_api_key"}
        assert kwargs["api_key"] is None
        for pk in self.selected:
            self.store.records[pk] = (kwargs["api_key"], kwargs["encrypted_api_key"])

    def iterator(self, chunk_size):
        assert delegated_database_alias() == self.store.alias
        return iter(self[:])


class MigrationAlgorithmTests(unittest.TestCase):
    def setUp(self):
        settings.INTEGRATION_ENCRYPTION_KEY = (
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        )
        for name in ("socket.socket", "socket.create_connection"):
            guard = patch(name, side_effect=AssertionError("network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)

    @contextmanager
    def migration(self, records, alias="default_direct"):
        store = SimpleNamespace(
            records=copy.deepcopy(records), alias=alias, in_atomic=False
        )
        model = SimpleNamespace(_base_manager=Rows(store))
        apps = SimpleNamespace(get_model=lambda app, name: model)
        editor = SimpleNamespace(connection=SimpleNamespace(alias=alias))

        @contextmanager
        def atomic(*, using):
            assert using == alias == delegated_database_alias()
            before = copy.deepcopy(store.records)
            store.in_atomic = True
            try:
                yield
            except Exception:
                store.records = before
                raise
            finally:
                store.in_atomic = False

        with (
            patch.object(M2.transaction, "atomic", atomic),
            redirect_stdout(io.StringIO()),
        ):
            yield store, apps, editor
        self.assertIsNone(delegated_database_alias())

    def test_exact_raw_prefix_empty_and_idempotent_on_both_aliases(self):
        for alias in ["default_direct", "default"]:
            raw = {
                1: (" raw 雪 ", None),
                2: ("enc::literal", None),
                3: ("", None),
                4: (None, None),
            }
            with self.migration(raw, alias) as (store, apps, editor):
                M2.forwards(apps, editor)
                for pk in [1, 2]:
                    self.assertIsNone(store.records[pk][0])
                    self.assertEqual(
                        M2.decrypt_api_key(store.records[pk][1]), raw[pk][0]
                    )
                self.assertEqual(store.records[3], (None, None))
                before = copy.deepcopy(store.records)
                M2.forwards(apps, editor)
                self.assertEqual(store.records, before)

    def test_existing_ciphertext_is_authenticated_and_conflicts_preserved(self):
        blob = M2.encrypt_api_key("same-raw")
        with self.migration({1: ("same-raw", blob)}) as (store, apps, editor):
            M2.forwards(apps, editor)
            self.assertEqual(store.records[1], (None, blob))
        for legacy, stored in [("disagrees", blob), (None, "enc::corrupt")]:
            original = {1: (legacy, stored)}
            with self.migration(original) as (store, apps, editor):
                with self.assertRaises(ValueError):
                    M2.forwards(apps, editor)
                self.assertEqual(store.records, original)
                self.assertIsNone(delegated_database_alias())

    def test_completed_batch_survives_failure_then_rerun_resumes(self):
        original = {1: ("first", None), 2: ("second", "enc::corrupt")}
        with (
            self.migration(original) as (store, apps, editor),
            patch.object(M2, "BATCH_SIZE", 1),
        ):
            with self.assertRaises(ValueError):
                M2.forwards(apps, editor)
            self.assertIsNone(store.records[1][0])
            self.assertEqual(store.records[2], original[2])
            first = store.records[1]
            store.records[2] = ("second", None)
            M2.forwards(apps, editor)
            self.assertEqual(store.records[1], first)
            self.assertEqual(M2.decrypt_api_key(store.records[2][1]), "second")

    def test_missing_key_rolls_back_current_batch(self):
        original = {1: ("preserved", None)}
        settings.INTEGRATION_ENCRYPTION_KEY = ""
        with self.migration(original) as (store, apps, editor):
            with self.assertRaisesRegex(
                # 02b85705 persistent-key preflight (ADDENDUM-2, ADDENDUM-7 a).
                ValueError,
                r"^Agent credential migration requires a valid persistent "
                r"INTEGRATION_ENCRYPTION_KEY shared by all processes and restarts; "
                r"configure it before retrying \(not an ephemeral key\)$",
            ):
                M2.forwards(apps, editor)
            self.assertEqual(store.records, original)

    def test_reverse_refuses_every_nonempty_ciphertext_including_corrupt(self):
        for blob in [M2.encrypt_api_key("retained"), "enc::corrupt"]:
            with self.migration({1: (None, blob)}) as (store, apps, editor):
                with self.assertRaises(IrreversibleError):
                    M2.backwards(apps, editor)
                self.assertEqual(store.records[1], (None, blob))
        with self.migration({1: (None, None)}) as (_, apps, editor):
            M2.backwards(apps, editor)

    def test_fenced_schema_graph_has_one_nonatomic_data_step(self):
        from tfc.migration_bridge import (
            LEGACY_APPLIED_NAMES,
            SkipDatabaseIfLegacyApplied,
        )

        m1 = importlib.import_module(
            "simulate.migrations.0102_agent_definition_encrypted_api_key"
        ).Migration
        m3 = importlib.import_module(
            "simulate.migrations.0104_remove_agent_definition_api_key"
        ).Migration
        for name, migration in (
            ("0102_agent_definition_encrypted_api_key", m1),
            ("0103_encrypt_agent_definition_api_key", M2.Migration),
            ("0104_remove_agent_definition_api_key", m3),
        ):
            self.assertEqual(len(migration.operations), 1)
            envelope = migration.operations[0]
            self.assertIsInstance(envelope, SkipDatabaseIfLegacyApplied)
            self.assertEqual(
                envelope.legacy, LEGACY_APPLIED_NAMES[("simulate", name)]
            )

        m1_operations = m1.operations[0].operations
        m2_operations = M2.Migration.operations[0].operations
        m3_operations = m3.operations[0].operations
        self.assertIsInstance(m1_operations[0], AddField)
        self.assertEqual(m1_operations[0].name, "encrypted_api_key")
        self.assertFalse(m1_operations[0].field.editable)
        self.assertIsInstance(m3_operations[0], RemoveField)
        self.assertEqual(m3_operations[0].name, "api_key")
        self.assertEqual(len(m2_operations), 1)
        self.assertIsInstance(m2_operations[0], RunPython)
        self.assertIs(m2_operations[0].reverse_code, M2.backwards)
        self.assertFalse(M2.Migration.atomic)
        self.assertFalse(m2_operations[0].atomic)
        # Batched encryption must not run inside one transaction.
        self.assertIsNot(M2.Migration.operations[0].atomic, True)


if __name__ == "__main__":
    unittest.main()
