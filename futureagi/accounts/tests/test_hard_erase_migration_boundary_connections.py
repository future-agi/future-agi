"""The migration boundary is open on the connection an executor runs on.

Django's migration executor is given a connection. ``django.db.connection``
stands for the default alias's connection, so an executor built on it runs
on that connection, and the boundary is open there while a migration is
applied or unapplied. An executor built on any other connection opens the
boundary on that connection only. No database is used.
"""

import pytest
from django.db import DEFAULT_DB_ALIAS, connection, connections

from accounts.services.hard_erase_migrations import (
    install_migration_guard,
    migration_sql_allowed,
)

STEPS = ["apply_migration", "unapply_migration"]


class _Executor:
    """What the boundary needs of a migration executor: its connection and
    the two calls the boundary wraps."""

    def __init__(self, executor_connection, watched):
        self.connection = executor_connection
        self.watched = watched
        self.seen = []

    def _look(self):
        self.seen.append([migration_sql_allowed(one) for one in self.watched])
        return "done"

    def apply_migration(self):
        return self._look()

    def unapply_migration(self):
        return self._look()


install_migration_guard(_Executor)


@pytest.fixture
def default_connection():
    return connections[DEFAULT_DB_ALIAS]


@pytest.fixture
def other_connection():
    # A second connection object for the same alias. It is never opened.
    return connections.create_connection(DEFAULT_DB_ALIAS)


@pytest.mark.parametrize("step", STEPS)
def test_an_executor_built_on_django_db_connection_opens_the_default_boundary(
    step, default_connection
):
    executor = _Executor(connection, [default_connection])

    assert getattr(executor, step)() == "done"

    assert executor.seen == [[True]]
    assert migration_sql_allowed(default_connection) is False


@pytest.mark.parametrize("step", STEPS)
def test_an_executor_built_on_the_default_connection_opens_its_boundary(
    step, default_connection
):
    executor = _Executor(default_connection, [default_connection])

    assert getattr(executor, step)() == "done"

    assert executor.seen == [[True]]
    assert migration_sql_allowed(default_connection) is False


@pytest.mark.parametrize("step", STEPS)
def test_the_boundary_is_open_on_the_executors_connection_only(
    step, default_connection, other_connection
):
    executor = _Executor(connection, [default_connection, other_connection])

    getattr(executor, step)()

    assert executor.seen == [[True, False]]


@pytest.mark.parametrize("step", STEPS)
def test_an_executor_built_on_another_connection_keeps_to_that_connection(
    step, default_connection, other_connection
):
    executor = _Executor(other_connection, [other_connection, default_connection])

    getattr(executor, step)()

    assert executor.seen == [[True, False]]
    assert migration_sql_allowed(other_connection) is False
