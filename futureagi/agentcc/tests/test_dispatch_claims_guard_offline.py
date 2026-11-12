"""Dispatch claims under the installed hard-erase SQL guard, without a database.

Django's real insert compiler, the real CursorWrapper and the real guard that
AccountsConfig.ready installs all run here. Only the PostgreSQL driver cursor and
the transaction context manager are doubles, so no connection is ever opened.
The PostgreSQL contract for the same behaviour is test_dispatch_claims_guard_pg.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import DatabaseError, IntegrityError, connection, connections
from django.db.backends.utils import CursorWrapper
from django.db.models.sql.subqueries import InsertQuery
from django.utils import timezone

from accounts.services import hard_erase_sql, hard_erase_sql_policy
from accounts.services.hard_erase import HardEraseRefused
from agentcc.models.dispatch_claim import AgentccDispatchClaim
from agentcc.services import dispatch_claims

TABLE = "agentcc_dispatch_claim"
DEFAULT_UNIQUE = "agentcc_dispatch_claim_idempotency_key_key"


class DriverCursor:
    """Stands in for the psycopg cursor: records what reaches the driver."""

    def __init__(self, events):
        self.events = events
        self.statements = []
        self.fail = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.events.append("execute")
        self.statements.append((str(sql), params))
        if self.fail is not None:
            raise self.fail
        return self

    def fetchone(self):
        return (1,)

    def close(self):
        return None


class RecordingAtomic:
    """Records enter/exit and the exception that crosses the durable block."""

    def __init__(self, events, calls, kwargs):
        self.events, self.calls, self.kwargs = events, calls, kwargs

    def __enter__(self):
        self.calls.append(self.kwargs)
        self.events.append("enter")

    def __exit__(self, exc_type, exc, traceback):
        self.events.append(f"exit:{exc_type.__name__ if exc_type else 'ok'}")
        return False


@pytest.fixture
def harness():
    wrapper = connections["default"]
    events, calls = [], []
    cursor = DriverCursor(events)
    transaction_double = SimpleNamespace(
        atomic=lambda **kwargs: RecordingAtomic(events, calls, kwargs)
    )
    with (
        patch.object(
            wrapper, "cursor", side_effect=lambda: CursorWrapper(cursor, wrapper)
        ),
        patch.object(wrapper, "get_autocommit", return_value=True),
        patch.object(dispatch_claims, "transaction", transaction_double),
    ):
        yield SimpleNamespace(
            wrapper=wrapper, cursor=cursor, events=events, atomic_calls=calls
        )
    # DatabaseErrorWrapper marks the shared wrapper when a driver error is mapped.
    wrapper.errors_occurred = False


def insert_compiler():
    """Django's own insert compiler for one claim row, built but not executed."""
    model = AgentccDispatchClaim
    query = InsertQuery(model)
    query.insert_values(
        [field for field in model._meta.concrete_fields if field.name != "id"],
        [model(idempotency_key="control-key", created_at=timezone.now())],
    )
    return query.get_compiler(using="default")


def unique_violation(constraint, sqlstate="23505"):
    class DriverViolation(connection.Database.IntegrityError):
        diag = SimpleNamespace(constraint_name=constraint, table_name=TABLE)

    DriverViolation.sqlstate = sqlstate
    DriverViolation.pgcode = sqlstate
    return DriverViolation("duplicate key value violates unique constraint")


def test_claim_table_is_in_the_installed_guard_census():
    wrapper = connections["default"]
    assert hard_erase_sql.guard_execute in wrapper.execute_wrappers
    assert TABLE in hard_erase_sql._TABLES
    # The census premise stays: no policy exemption carries this table.
    assert TABLE not in hard_erase_sql_policy.EXCLUDED_FIELDS
    assert hard_erase_sql_policy.model_requires_protection(
        AgentccDispatchClaim.__module__,
        AgentccDispatchClaim.__name__,
        TABLE,
        AgentccDispatchClaim._meta.concrete_fields,
    )


def test_guard_refuses_the_claim_statement_sent_raw_but_not_via_the_compiler(harness):
    sql, params = insert_compiler().as_sql()[0]
    assert sql.lstrip().upper().startswith("INSERT INTO")
    assert f'"{TABLE}"' in sql
    with pytest.raises(HardEraseRefused):
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
    assert harness.cursor.statements == []
    insert_compiler().execute_sql()
    assert [statement for statement, _ in harness.cursor.statements] == [sql]


def test_first_claim_reaches_the_driver_under_the_installed_guard(harness):
    assert dispatch_claims.claim_dispatch_once("offline-first") is True
    assert len(harness.cursor.statements) == 1
    sql, params = harness.cursor.statements[0]
    assert sql.lstrip().upper().startswith("INSERT INTO")
    assert f'"{TABLE}"' in sql
    assert "offline-first" in params
    assert [call.get("durable") for call in harness.atomic_calls] == [True]


def test_true_is_returned_only_after_the_durable_block_exits(harness):
    assert dispatch_claims.claim_dispatch_once("offline-order") is True
    assert harness.events == ["enter", "execute", "exit:ok"]


def test_duplicate_key_is_false_and_caught_outside_the_durable_block(harness):
    harness.cursor.fail = unique_violation(DEFAULT_UNIQUE)
    assert dispatch_claims.claim_dispatch_once("offline-dup") is False
    # The failed insert crossed the block so the transaction rolled back there.
    assert harness.events == ["enter", "execute", "exit:IntegrityError"]


@pytest.mark.parametrize(
    "failure",
    [
        unique_violation("agentcc_dispatch_claim_pkey"),
        unique_violation(DEFAULT_UNIQUE, sqlstate="23502"),
        unique_violation(None),
        connection.Database.OperationalError("connection lost"),
    ],
    ids=["other-constraint", "other-sqlstate", "unknown-constraint", "operational"],
)
def test_every_other_database_error_propagates(harness, failure):
    harness.cursor.fail = failure
    with pytest.raises(DatabaseError):
        dispatch_claims.claim_dispatch_once("offline-error")
    assert harness.events[-1].startswith("exit:") and harness.events[-1] != "exit:ok"


def test_integrity_error_without_a_driver_cause_propagates(harness):
    def fail(execute, sql, params, many, context):
        raise IntegrityError("injected without a driver cause")

    harness.wrapper.execute_wrappers.append(fail)
    try:
        with pytest.raises(IntegrityError, match="injected"):
            dispatch_claims.claim_dispatch_once("offline-no-cause")
    finally:
        harness.wrapper.execute_wrappers.remove(fail)


def test_open_atomic_block_is_refused_before_any_statement(harness):
    with patch.object(harness.wrapper, "in_atomic_block", True):
        with pytest.raises(RuntimeError, match="autocommit"):
            dispatch_claims.claim_dispatch_once("offline-nested")
    assert harness.cursor.statements == [] and harness.events == []


@pytest.mark.parametrize(
    "key",
    [None, True, 0, b"dispatch", [], {}, "", " \t\n", "x" * 256, "bad\x00key"],
    ids=[
        "none",
        "boolean",
        "number",
        "bytes",
        "list",
        "dict",
        "empty",
        "blank",
        "too-long",
        "nul",
    ],
)
def test_invalid_keys_raise_before_any_database_work(harness, key):
    with pytest.raises(ValueError):
        dispatch_claims.claim_dispatch_once(key)
    assert harness.cursor.statements == [] and harness.events == []


def test_maximum_length_unicode_key_is_claimed_under_the_guard(harness):
    key = "\U0001f30d" * 255
    assert dispatch_claims.claim_dispatch_once(key) is True
    assert key in harness.cursor.statements[0][1]
