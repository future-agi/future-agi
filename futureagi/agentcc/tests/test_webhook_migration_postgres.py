"""RL 2013 two-session cases: AUTHORED, NOT_RUN locally; main cloud route only.

Synthetic isolated historical tables, real PostgreSQL DDL/transactions. These
cases do not qualify the full application graph, actual fence or backup custody.
Main must additionally record manual distinct-session proof at the composed cut.
"""

import importlib
import threading
from contextlib import ExitStack, contextmanager
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from django.db import connections, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import ModelState, ProjectState

from agentcc.migrations import _webhook_credentials_v1 as storage
from tfc.migration_database import delegated_database_alias

pytestmark = pytest.mark.django_db(
    transaction=True, databases={"default", "default_direct"}
)
MODULES = [
    importlib.import_module("agentcc.migrations." + name)
    for name in (
        "0075_webhook_credential_columns",
        "0076_encrypt_webhook_credentials",
        "0077_remove_webhook_plaintext",
    )
]
TABLE = "webhook_credential_migration_test"


@contextmanager
def database(alias):
    connection = connections[alias]
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL independent sessions required")
    state = ProjectState()
    state.add_model(
        ModelState(
            "accounts",
            "Organization",
            [("id", models.UUIDField(primary_key=True))],
            options={"db_table": TABLE + "_org"},
        )
    )
    state.add_model(
        ModelState(
            "prism",
            "AgentccWebhook",
            [
                ("id", models.UUIDField(primary_key=True)),
                (
                    "organization",
                    models.ForeignKey(
                        "accounts.Organization", on_delete=models.CASCADE
                    ),
                ),
                ("url", models.TextField()),
                ("headers", models.JSONField(default=dict)),
                ("secret", models.CharField(max_length=255, default="")),
                ("deleted", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"db_table": TABLE},
        )
    )
    original = state.clone()
    with connection.schema_editor() as editor:
        editor.create_model(state.apps.get_model("accounts", "Organization"))
        editor.create_model(state.apps.get_model("prism", "AgentccWebhook"))
    observer = connections["default"].copy(alias="webhook_observer")
    observer.ensure_connection()
    observer.set_autocommit(True)
    try:
        with connection.cursor() as cursor, observer.cursor() as other:
            cursor.execute("SELECT pg_backend_pid(), current_database()")
            other.execute("SELECT pg_backend_pid(), current_database()")
            executor_identity, observer_identity = cursor.fetchone(), other.fetchone()
            assert executor_identity[0] != observer_identity[0]
            assert executor_identity[1] == observer_identity[1]
            other.execute(
                "SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL READ COMMITTED"
            )
            other.execute(f'SELECT COUNT(*) FROM "{TABLE}"')
            assert other.fetchone() == (0,)
        yield state, connection, observer
    finally:
        observer.close()
        # Drop works with either the pre- or post-removal table definition.
        with connection.schema_editor() as editor:
            editor.delete_model(original.apps.get_model("prism", "AgentccWebhook"))
            editor.delete_model(original.apps.get_model("accounts", "Organization"))


@contextmanager
def executor_only(connection):
    def forbidden(*args):
        raise AssertionError("Non-executor migration SQL")

    with ExitStack() as stack:
        for alias in connections:
            if alias != connection.alias:
                stack.enter_context(connections[alias].execute_wrapper(forbidden))
        yield
    assert delegated_database_alias() is None


def apply(index, state, connection):
    migration = MODULES[index].Migration("webhook_test", "prism")
    with (
        executor_only(connection),
        connection.schema_editor(atomic=migration.atomic) as editor,
    ):
        return migration.apply(state, editor)


def run(function, state, connection):
    try:
        with (
            executor_only(connection),
            connection.schema_editor(atomic=False) as editor,
        ):
            function(state.apps, editor)
    finally:
        assert delegated_database_alias() is None


def seed(state, alias, count=4):
    orgs = state.apps.get_model("accounts", "Organization")._base_manager.using(alias)
    for index in [100, 101]:
        orgs.create(pk=UUID(int=index))
    rows = state.apps.get_model("prism", "AgentccWebhook")._base_manager.using(alias)
    result = []
    for index in range(1, count + 1):
        result.append(
            rows.create(
                pk=UUID(int=index),
                organization_id=UUID(int=100 + index % 2),
                url="https://fixture.invalid/exact?Case=X",
                headers=[
                    {},
                    {"X": "synthetic-header-marker"},
                    "malformed",
                    [["X", 1], ["X", 2]],
                ][(index - 1) % 4],
                secret="" if index % 2 else "synthetic-signing-marker",
                deleted=bool(index % 2),
                is_active=not bool(index % 2),
            )
        )
    return result


def raw(observer, pk):
    with observer.cursor() as cursor:
        cursor.execute(
            f'SELECT headers, secret, encrypted_headers, encrypted_secret, credential_storage_version FROM "{TABLE}" WHERE id = %s',
            [pk],
        )
        return cursor.fetchone()


@pytest.mark.parametrize("alias", ["default_direct", "default"])
def test_visibility_resume_reverse_clear_last_and_constraints(
    alias, settings, monkeypatch
):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    monkeypatch.setattr(storage, "BATCH_SIZE", 1)
    with database(alias) as (state, connection, observer):
        fixtures = seed(state, alias)
        state = apply(0, state, connection)
        before = [raw(observer, row.pk) for row in fixtures]
        updates = 0

        def witness(execute, sql, params, many, context):
            nonlocal updates
            result = execute(sql, params, many, context)
            if sql.lstrip().startswith("UPDATE"):
                updates += 1
                row = fixtures[updates - 1]
                assert connection.in_atomic_block
                assert delegated_database_alias() == alias
                own = raw(connection, row.pk)
                assert own[:2] == before[updates - 1][:2]
                assert own[2] and own[3] and own[4] == 1
                assert raw(observer, row.pk) == before[updates - 1]
                if updates == 2:
                    committed = raw(observer, fixtures[0].pk)
                    assert committed[2] and committed[:2] == before[0][:2]
                    raise RuntimeError("synthetic forced mid-run driver failure")
            return result

        with (
            connection.execute_wrapper(witness),
            pytest.raises(ValueError, match="Webhook credential migration refused"),
        ):
            run(MODULES[1].forwards, state, connection)
        committed = raw(observer, fixtures[0].pk)
        assert committed[2] and committed[:2] == before[0][:2]
        for index in range(1, 4):
            assert raw(observer, fixtures[index].pk) == before[index]
        # M1 reverse must also refuse after an unrecorded partial M2.
        for module in MODULES[:2]:
            with pytest.raises(IrreversibleError):
                run(module.backwards, state, connection)
        run(MODULES[1].forwards, state, connection)
        assert raw(observer, fixtures[0].pk) == committed
        completed = [raw(observer, row.pk) for row in fixtures]
        run(MODULES[1].forwards, state, connection)
        assert [raw(observer, row.pk) for row in fixtures] == completed
        for previous, current in zip(before, completed, strict=True):
            assert previous[:2] == current[:2]  # plaintext intentionally retained
            for ciphertext in current[2:4]:
                assert b"synthetic-header-marker" not in bytes(ciphertext)
                assert b"synthetic-signing-marker" not in bytes(ciphertext)
        run(MODULES[2].verify_before_remove, state, connection)
        pre_m3 = state.clone()
        final = apply(2, state, connection)
        with observer.cursor() as cursor:
            names = {
                column.name
                for column in observer.introspection.get_table_description(
                    cursor, TABLE
                )
            }
            assert not {"headers", "secret"} & names
            cursor.execute(
                f'SELECT COUNT(*) FROM "{TABLE}" WHERE encrypted_headers IS NULL OR encrypted_secret IS NULL OR octet_length(encrypted_headers)=0 OR octet_length(encrypted_secret)=0 OR credential_storage_version<>1'
            )
            assert cursor.fetchone() == (0,)
        run(MODULES[2].check_after_remove, final, connection)
        with pytest.raises(IrreversibleError):
            run(MODULES[2].backwards, final, connection)
        with pytest.raises(IrreversibleError):
            with executor_only(connection), connection.schema_editor() as editor:
                MODULES[2].Migration("webhook_test", "prism").unapply(pre_m3, editor)
        with observer.cursor() as cursor:
            assert "secret" not in {
                column.name
                for column in observer.introspection.get_table_description(
                    cursor, TABLE
                )
            }


@pytest.mark.parametrize(
    "fault", ["corrupt", "conflict", "missing-key", "preserve-verification"]
)
def test_failed_preclear_keeps_every_original(fault, settings, monkeypatch):
    key = Fernet.generate_key().decode()
    settings.INTEGRATION_ENCRYPTION_KEY = key
    with database("default_direct") as (state, connection, observer):
        fixtures = seed(state, connection.alias)
        state = apply(0, state, connection)
        run(MODULES[1].forwards, state, connection)
        rows = state.apps.get_model("prism", "AgentccWebhook")._base_manager.using(
            connection.alias
        )
        if fault == "corrupt":
            rows.filter(pk=fixtures[-1].pk).update(encrypted_secret=b"corrupt")
        elif fault == "conflict":
            rows.filter(pk=fixtures[-1].pk).update(secret="synthetic-conflict")
        elif fault == "missing-key":
            settings.INTEGRATION_ENCRYPTION_KEY = ""
        else:

            def fail(*args, **kwargs):
                raise ValueError("synthetic verification failure")

            monkeypatch.setattr(storage.codec, "verify_preserved_for_migration", fail)
        before = [raw(observer, row.pk) for row in fixtures]
        with pytest.raises(ValueError, match="Webhook credential migration refused"):
            apply(2, state, connection)
        assert [raw(observer, row.pk) for row in fixtures] == before
        assert delegated_database_alias() is None


def test_all_runpython_empty_paths_and_reverse_refusals(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    with database("default_direct") as (state, connection, observer):
        state = apply(0, state, connection)
        state = apply(1, state, connection)
        state = apply(2, state, connection)
        for module in MODULES:
            with pytest.raises(IrreversibleError):
                run(module.backwards, state, connection)


def test_json_null_and_competing_session_row_lock(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    with database("default_direct") as (state, connection, observer):
        fixture = seed(state, connection.alias, count=1)[0]
        with connection.cursor() as cursor:
            cursor.execute(
                f'UPDATE "{TABLE}" SET headers = %s::jsonb WHERE id = %s',
                ["null", fixture.pk],
            )
        state = apply(0, state, connection)
        outcomes = []

        def compete():
            competitor = connections["default"].copy(alias="webhook_competitor")
            try:
                with competitor.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '200ms'")
                    try:
                        cursor.execute(
                            f'UPDATE "{TABLE}" SET secret = %s WHERE id = %s',
                            ["synthetic-competing-write", fixture.pk],
                        )
                    except Exception as error:
                        outcomes.append(
                            getattr(error.__cause__, "sqlstate", None)
                            or getattr(error.__cause__, "pgcode", None)
                        )
            finally:
                competitor.close()

        def witness(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if sql.lstrip().startswith("UPDATE"):
                thread = threading.Thread(target=compete)
                thread.start()
                thread.join(timeout=5)
                assert not thread.is_alive()
                assert outcomes == ["55P03"]
                assert raw(observer, fixture.pk)[2:] == (None, None, None)
            return result

        with connection.execute_wrapper(witness):
            run(MODULES[1].forwards, state, connection)
        run(MODULES[2].verify_before_remove, state, connection)
