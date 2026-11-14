"""A lagging read replica for read-after-write regression tests.

`lagging_replica` registers a real `replica` alias for the duration of one
test: a second connection to the test database that `tfc.routers` sends
opted-in model reads to. `freeze()` opens a REPEATABLE READ transaction on it,
so from then on it sees only the rows committed before the freeze — the state
of a streaming standby that has not yet applied the latest WAL. Writes still go
to `default` and commit immediately (the tests are transactional).

Use it as:

    @pytest.mark.django_db(transaction=True)
    def test_x(lagging_replica, ...):
        ...  # rows the replica has already applied
        lagging_replica.freeze()
        ...  # code under test: writes on default, routed reads on replica

A read routed to the replica after the freeze cannot see anything written
after it, so a path that re-reads its own write through the router fails here
exactly as it would against a lagging standby.
"""

from __future__ import annotations

from contextlib import contextmanager

import psycopg
import pytest
from django.db import connections, transaction
from django.test.utils import CaptureQueriesContext

REPLICA_ALIAS = "replica"

# The model class names this suite opts in to the router, as
# READ_REPLICA_OPT_IN lists them.
OPTED_IN_MODELS = (
    "ObservationSpan",
    "Trace",
    "EvalLogger",
    "EndUser",
    "CustomEvalConfig",
    "ErrorMemory",
    "TraceAnnotation",
    "EvalTaskLogger",
    "TraceErrorAnalysisTask",
)


class LaggingReplica:
    alias = REPLICA_ALIAS

    def __init__(self) -> None:
        self._atomic = None

    @property
    def connection(self):
        return connections[self.alias]

    def freeze(self) -> None:
        """Stop applying writes: later commits on `default` stay invisible."""
        if self._atomic is not None:
            return
        atomic = transaction.atomic(using=self.alias)
        atomic.__enter__()
        self._atomic = atomic
        with self.connection.cursor() as cursor:
            # The first statement of a REPEATABLE READ transaction takes the
            # snapshot every later read in it uses.
            cursor.execute("SELECT 1")

    def thaw(self) -> None:
        """Catch up: drop the snapshot so the replica sees every commit."""
        if self._atomic is None:
            return
        transaction.set_rollback(True, using=self.alias)
        self._atomic.__exit__(None, None, None)
        self._atomic = None

    @contextmanager
    def capture(self):
        """Record the queries that reached the replica inside the block."""
        with CaptureQueriesContext(self.connection) as ctx:
            yield ctx


@pytest.fixture
def lagging_replica(transactional_db, settings, monkeypatch):
    default = connections["default"].settings_dict
    # `settings.DATABASES` is the dict `connections` reads aliases from, so
    # adding the key registers the alias; the router checks the same dict.
    settings.DATABASES[REPLICA_ALIAS] = {
        **default,
        "OPTIONS": {
            **default.get("OPTIONS", {}),
            "isolation_level": psycopg.IsolationLevel.REPEATABLE_READ,
        },
        "TEST": {**default.get("TEST", {}), "MIRROR": "default"},
    }
    settings.READ_REPLICA_OPT_IN = list(OPTED_IN_MODELS)
    replica = LaggingReplica()
    # Django's test case only lets the aliases it declared open connections;
    # this alias is registered after it starts, so connect it here. Keep the
    # connection (and a frozen snapshot) across the close_old_connections()
    # calls request handlers and workers make, as a standby keeps its state.
    replica.connection.connect()
    monkeypatch.setattr(
        replica.connection, "close_if_unusable_or_obsolete", lambda: None
    )
    try:
        yield replica
    finally:
        # Release the snapshot before transactional_db flushes the tables.
        replica.thaw()
        connections[REPLICA_ALIAS].close()
        del connections[REPLICA_ALIAS]
        settings.DATABASES.pop(REPLICA_ALIAS, None)
