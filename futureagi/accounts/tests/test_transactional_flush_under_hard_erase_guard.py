"""Exercise real PostgreSQL teardown with the installed hard-erase guard.

Run this entire module serially, in collection order. The first two tests share
only an in-memory, module-scoped marker; their database state must not survive
transactional teardown. Selecting the second test alone fails rather than
silently claiming that teardown removed a row which was never inserted.
"""

from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction

from accounts.models.organization import Organization
from accounts.services.hard_erase import HardEraseRefused
from accounts.services.hard_erase_sql import _SQL_AUTH, _TABLES, guard_execute

pytestmark = pytest.mark.django_db(transaction=True, databases=["default"])


@pytest.fixture(autouse=True)
def _require_postgresql_guard(transactional_db):
    primary = connections["default"]
    assert primary.vendor == "postgresql", "This regression requires PostgreSQL"
    assert guard_execute in primary.execute_wrappers
    assert Organization._meta.db_table.lower() in _TABLES
    assert _SQL_AUTH.get() is None


@pytest.fixture(scope="module")
def teardown_marker():
    return {"name": f"h1-flush-{uuid4().hex}", "created": False}


def test_01_transactional_test_creates_protected_row(teardown_marker):
    name = teardown_marker["name"]
    assert Organization.objects.using("default").filter(name=name).count() == 0
    Organization.objects.using("default").create(name=name)
    assert Organization.objects.using("default").filter(name=name).count() == 1
    teardown_marker["created"] = True
    assert _SQL_AUTH.get() is None
    # No explicit cleanup: only TransactionTestCase's real teardown may erase it.


def test_02_next_transactional_test_has_no_leaked_row(teardown_marker):
    assert teardown_marker["created"] is True, "Run the ordered pair together"
    assert (
        Organization.objects.using("default")
        .filter(name=teardown_marker["name"])
        .count()
        == 0
    ), "The preceding transactional test leaked its protected row"
    assert _SQL_AUTH.get() is None


def test_raw_truncate_outside_flush_is_refused():
    primary = connections["default"]
    table = primary.ops.quote_name(Organization._meta.db_table)
    with transaction.atomic(using="default"):
        with primary.cursor() as cursor:
            with pytest.raises(
                HardEraseRefused,
                match="^Hard erase refused: raw SQL cannot mutate protected application tables$",
            ):
                cursor.execute(f"TRUNCATE TABLE {table} CASCADE")
    assert _SQL_AUTH.get() is None


def test_explicit_flush_is_atomic_scoped_and_keeps_cascade():
    primary = connections["default"]
    table = primary.ops.quote_name(Organization._meta.db_table)
    seen = []

    def observe_truncate(execute, sql, params, many, context):
        if sql.lstrip().upper().startswith("TRUNCATE"):
            authority = _SQL_AUTH.get()
            assert authority is not None
            assert table in sql
            seen.append(
                (
                    authority[0] is primary,
                    authority[1],
                    primary.in_atomic_block,
                    "CASCADE" in sql.upper(),
                    context["connection"] is primary,
                )
            )
        return execute(sql, params, many, context)

    with primary.execute_wrapper(observe_truncate):
        call_command(
            "flush",
            database="default",
            interactive=False,
            verbosity=0,
            inhibit_post_migrate=True,
        )
    assert seen == [(True, "erase", True, True, True)]
    assert _SQL_AUTH.get() is None
    # A successful flush must not authorize the next raw statement.
    with transaction.atomic(using="default"):
        with primary.cursor() as cursor:
            with pytest.raises(
                HardEraseRefused,
                match="^Hard erase refused: raw SQL cannot mutate protected application tables$",
            ):
                cursor.execute(f"TRUNCATE TABLE {table} CASCADE")


def test_failed_flush_releases_authority(monkeypatch):
    primary = connections["default"]

    def fail_sql_flush(sql_list):
        assert sql_list
        assert _SQL_AUTH.get() == (primary, "erase")
        raise RuntimeError("synthetic flush driver failure")

    # Restore the driver method before this test's real transactional teardown.
    with monkeypatch.context() as patch:
        patch.setattr(primary.ops, "execute_sql_flush", fail_sql_flush)
        with pytest.raises(CommandError) as caught:
            call_command(
                "flush",
                database="default",
                interactive=False,
                verbosity=0,
                inhibit_post_migrate=True,
            )
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert caught.value.__cause__.args == ("synthetic flush driver failure",)
    assert _SQL_AUTH.get() is None


def test_non_flush_management_command_has_no_authority():
    primary = connections["default"]
    table = primary.ops.quote_name(Organization._meta.db_table)
    seen = []

    class ProbeCommand(BaseCommand):
        requires_system_checks = []

        def handle(self, *args, **options):
            seen.append(_SQL_AUTH.get())
            assert _SQL_AUTH.get() is None
            with transaction.atomic(using="default"):
                with primary.cursor() as cursor:
                    with pytest.raises(
                        HardEraseRefused,
                        match="^Hard erase refused: raw SQL cannot mutate protected application tables$",
                    ):
                        cursor.execute(f"TRUNCATE TABLE {table} CASCADE")

    original_handle = ProbeCommand.handle
    call_command(ProbeCommand(), verbosity=0)
    assert ProbeCommand.handle is original_handle
    assert seen == [None]
    assert _SQL_AUTH.get() is None
