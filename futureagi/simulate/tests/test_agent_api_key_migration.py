"""PostgreSQL-only cases.

Uses real independent READ COMMITTED sessions and nondefault executor alias;
these are not a substitute for a manual two-connection proof.
No provider clients, live data or external credentials are involved.
"""

import importlib
import threading
from contextlib import ExitStack, contextmanager

import pytest
from django.db import connections, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import ModelState, ProjectState

from integrations.services.credentials import CredentialManager
from tfc.migration_database import delegated_database_alias

pytestmark = pytest.mark.django_db(
    transaction=True, databases={"default", "default_direct"}
)
MIGRATION = "simulate.migrations.0103_encrypt_agent_definition_api_key"


@contextmanager
def table(alias):
    connection = connections[alias]
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL independent READ COMMITTED sessions")
    state = ProjectState()
    state.add_model(
        ModelState(
            "simulate",
            "AgentDefinition",
            [
                ("id", models.AutoField(primary_key=True)),
                ("api_key", models.CharField(max_length=255, null=True)),
                ("encrypted_api_key", models.TextField(null=True)),
                ("deleted", models.BooleanField(default=False)),
                ("organization_id", models.IntegerField(null=True)),
                ("workspace_id", models.IntegerField(null=True)),
            ],
            options={"db_table": "sim_apikey_migration_test"},
        )
    )
    model = state.apps.get_model("simulate", "AgentDefinition")
    with connection.schema_editor() as editor:
        editor.create_model(model)
    observer = connection.copy(alias=alias)
    observer.ensure_connection()
    observer.set_autocommit(True)
    try:
        with connection.cursor() as cursor, observer.cursor() as other:
            cursor.execute("SELECT pg_backend_pid()")
            other.execute("SELECT pg_backend_pid()")
            assert cursor.fetchone()[0] != other.fetchone()[0]
            other.execute(
                "SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL READ COMMITTED"
            )
        yield state.apps, model._base_manager.using(alias), connection, observer
    finally:
        observer.close()
        with connection.schema_editor() as editor:
            editor.delete_model(model)


def raw(observer, pk):
    with observer.cursor() as cursor:
        cursor.execute(
            "SELECT api_key, encrypted_api_key FROM sim_apikey_migration_test WHERE id = %s",
            [pk],
        )
        return cursor.fetchone()


def decrypt(blob):
    assert blob.startswith("enc::")
    return CredentialManager.decrypt(blob[5:].encode())["v"]


def run(function, apps, connection):
    def forbid(*args):
        raise AssertionError("Non-executor ORM access")

    # Non-atomic migration: its own bounded batch transactions own commits.
    with ExitStack() as stack:
        for alias in connections:
            if alias != connection.alias:
                stack.enter_context(connections[alias].execute_wrapper(forbid))
        with connection.schema_editor(atomic=False) as editor:
            function(apps, editor)
    assert delegated_database_alias() is None


@pytest.mark.parametrize("alias", ["default_direct", "default"])
def test_exact_mixed_backfill_visibility_and_idempotence(alias):
    migration = importlib.import_module(MIGRATION)
    with table(alias) as (apps, rows, connection, observer):
        values = [" ordinary raw ", "enc::literal-not-ciphertext", "雪" * 100, "", None]
        seeded = [
            rows.create(
                api_key=value,
                deleted=bool(i % 2),
                organization_id=i,
                workspace_id=None if i % 2 else i,
            )
            for i, value in enumerate(values)
        ]
        duplicate = rows.create(
            api_key="same-raw",
            encrypted_api_key="enc::"
            + CredentialManager.encrypt({"v": "same-raw"}).decode(),
        )
        original = duplicate.encrypted_api_key
        seen = []

        def witness(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if sql.lstrip().startswith("UPDATE"):
                assert connection.in_atomic_block
                assert delegated_database_alias() == alias
                # Executor sees own writes; observer still sees old values.
                changed = rows.get(pk=seeded[0].pk)
                if changed.encrypted_api_key:
                    assert changed.api_key is None
                    assert decrypt(changed.encrypted_api_key) == values[0]
                    assert raw(observer, seeded[0].pk) == (values[0], None)
                    seen.append(True)
            return result

        with connection.execute_wrapper(witness):
            run(migration.forwards, apps, connection)
        assert seen
        for row, value in zip(seeded, values, strict=True):
            clear, encrypted = raw(observer, row.pk)
            assert clear is None
            assert decrypt(encrypted) == value if value else encrypted is None
        assert raw(observer, duplicate.pk) == (None, original)
        before = list(rows.order_by("pk").values())
        run(migration.forwards, apps, connection)
        assert list(rows.order_by("pk").values()) == before
        with pytest.raises(IrreversibleError, match="encrypted agent credentials"):
            run(migration.backwards, apps, connection)
        assert delegated_database_alias() is None
        assert list(rows.order_by("pk").values()) == before


@pytest.mark.parametrize("failure", ["conflict", "corrupt", "missing-key", "forced"])
def test_failed_batch_rolls_back_completed_batches_survive_and_resume(
    failure, monkeypatch, settings
):
    migration = importlib.import_module(MIGRATION)
    monkeypatch.setattr(migration, "BATCH_SIZE", 1)
    with table("default_direct") as (apps, rows, connection, observer):
        first = rows.create(api_key="first-synthetic")
        second = rows.create(api_key="second-synthetic")
        good_key = settings.INTEGRATION_ENCRYPTION_KEY
        if failure == "conflict":
            rows.filter(pk=second.pk).update(
                encrypted_api_key="enc::"
                + CredentialManager.encrypt({"v": "disagreeing-synthetic"}).decode()
            )
        elif failure == "corrupt":
            rows.filter(pk=second.pk).update(encrypted_api_key="enc::invalid")
        second_before = raw(observer, second.pk)
        count = 0

        def fail(execute, sql, params, many, context):
            nonlocal count
            result = execute(sql, params, many, context)
            if sql.lstrip().startswith("UPDATE"):
                count += 1
                if count == 1 and failure == "missing-key":
                    settings.INTEGRATION_ENCRYPTION_KEY = ""
                if count == 2 and failure == "forced":
                    assert raw(observer, second.pk) == second_before
                    raise RuntimeError("forced batch failure")
            return result

        with (
            connection.execute_wrapper(fail),
            pytest.raises((ValueError, RuntimeError)),
        ):
            run(migration.forwards, apps, connection)
        settings.INTEGRATION_ENCRYPTION_KEY = good_key
        assert raw(observer, first.pk)[0] is None
        assert decrypt(raw(observer, first.pk)[1]) == "first-synthetic"
        assert raw(observer, second.pk) == second_before
        assert delegated_database_alias() is None
        if failure in {"conflict", "corrupt"}:
            rows.filter(pk=second.pk).update(encrypted_api_key=None)
        run(migration.forwards, apps, connection)
        assert decrypt(raw(observer, second.pk)[1]) == "second-synthetic"


def test_reverse_empty_only_and_alias_restored():
    migration = importlib.import_module(MIGRATION)
    with table("default_direct") as (apps, rows, connection, observer):
        rows.create(api_key=None, encrypted_api_key=None)
        run(migration.backwards, apps, connection)
        rows.create(api_key=None, encrypted_api_key="enc::invalid")
        with pytest.raises(IrreversibleError):
            run(migration.backwards, apps, connection)
        assert delegated_database_alias() is None


def test_row_lock_prevents_competing_write_until_batch_commit():
    migration = importlib.import_module(MIGRATION)
    with table("default_direct") as (apps, rows, connection, observer):
        row = rows.create(api_key="before-competitor")
        outcomes = []

        def competitor():
            competing = connection.copy(alias="default_direct")
            try:
                with competing.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '200ms'")
                    try:
                        cursor.execute(
                            "UPDATE sim_apikey_migration_test SET api_key = %s WHERE id = %s",
                            ["competing-synthetic", row.pk],
                        )
                    except Exception as error:
                        outcomes.append(
                            getattr(error.__cause__, "sqlstate", None)
                            or getattr(error.__cause__, "pgcode", None)
                        )
            finally:
                competing.close()

        def witness(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if sql.lstrip().startswith("UPDATE"):
                thread = threading.Thread(target=competitor)
                thread.start()
                thread.join(timeout=5)
                assert not thread.is_alive()
                assert outcomes == ["55P03"]
                assert raw(observer, row.pk) == ("before-competitor", None)
            return result

        with connection.execute_wrapper(witness):
            run(migration.forwards, apps, connection)
        assert raw(observer, row.pk)[0] is None
        assert decrypt(raw(observer, row.pk)[1]) == "before-competitor"
