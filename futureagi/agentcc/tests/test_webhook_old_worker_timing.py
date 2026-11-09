"""Real PostgreSQL old-worker insert timing, reusing the accepted M1/M2/M3 fixture.

NOT_RUN until executed with main's actual PostgreSQL runner. These tests do not
replace the deployment fence or main's manual independent-connection criterion.
"""

from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from django.db import IntegrityError

from agentcc.tests import test_webhook_migration_postgres as fixture

pytestmark = pytest.mark.django_db(
    transaction=True, databases={"default", "default_direct"}
)


def insert_old_worker(observer, pk):
    """The old schema's INSERT, on an independent committed connection."""
    assert observer.get_autocommit()
    with observer.cursor() as cursor:
        cursor.execute(
            f'INSERT INTO "{fixture.TABLE}" '
            "(id, organization_id, url, headers, secret, deleted, is_active) "
            "VALUES (%s, %s, %s, %s::jsonb, %s, false, true)",
            [
                pk,
                UUID(int=100),
                "https://fixture.invalid/late",
                '{"X":"late-fixture"}',
                "late-signing",
            ],
        )


def assert_plaintext_retained(observer, pk):
    row = fixture.raw(observer, pk)
    assert row is not None and row[1] == "late-signing"
    with observer.cursor() as cursor:
        columns = {
            column.name
            for column in observer.introspection.get_table_description(
                cursor, fixture.TABLE
            )
        }
    assert {"headers", "secret"} <= columns


@pytest.mark.parametrize(
    "position,pk", [("below", UUID(int=0)), ("above", UUID(int=999))]
)
def test_old_worker_insert_during_m2_refuses_census_drift(
    position, pk, settings, monkeypatch
):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    monkeypatch.setattr(fixture.storage, "BATCH_SIZE", 1)
    with fixture.database("default_direct") as (state, connection, observer):
        fixture.seed(state, connection.alias)
        state = fixture.apply(0, state, connection)
        inserted = False

        def witness(execute, sql, params, many, context):
            nonlocal inserted
            result = execute(sql, params, many, context)
            if (
                not inserted
                and sql.lstrip().startswith("UPDATE")
                and fixture.TABLE in sql
            ):
                inserted = True
                insert_old_worker(observer, pk)
            return result

        with (
            connection.execute_wrapper(witness),
            pytest.raises(ValueError, match="Webhook credential migration refused"),
        ):
            fixture.run(fixture.MODULES[1].forwards, state, connection)
        assert inserted
        assert_plaintext_retained(observer, pk)
        if position == "below":
            assert fixture.raw(observer, pk)[2:] == (None, None, None)
        # A fenced resume must include the inserted row, not drop its original.
        fixture.run(fixture.MODULES[1].forwards, state, connection)
        assert fixture.raw(observer, pk)[2] and fixture.raw(observer, pk)[3]
        assert_plaintext_retained(observer, pk)


def test_old_worker_insert_after_final_census_before_m3_ddl_rolls_back(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()
    late = UUID(int=999)
    with fixture.database("default_direct") as (state, connection, observer):
        fixtures = fixture.seed(state, connection.alias)
        state = fixture.apply(0, state, connection)
        fixture.run(fixture.MODULES[1].forwards, state, connection)
        before = [fixture.raw(observer, row.pk) for row in fixtures]
        inserted = False

        def before_drop(execute, sql, params, many, context):
            nonlocal inserted
            if (
                not inserted
                and sql.lstrip().startswith("ALTER TABLE")
                and "DROP COLUMN" in sql
                and fixture.TABLE in sql
            ):
                inserted = True
                assert connection.in_atomic_block
                # M3 verify-all and final census have returned; no DDL has run.
                insert_old_worker(observer, late)
            return execute(sql, params, many, context)

        with (
            connection.execute_wrapper(before_drop),
            pytest.raises((IntegrityError, ValueError)),
        ):
            fixture.apply(2, state, connection)
        assert inserted
        assert [fixture.raw(observer, row.pk) for row in fixtures] == before
        assert_plaintext_retained(observer, late)
        assert fixture.raw(observer, late)[2:] == (None, None, None)
