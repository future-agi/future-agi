"""Read-only startup schema checks for Django HTTP requests."""

from enum import Enum
from threading import Lock
from time import monotonic

import structlog
from django.db import DEFAULT_DB_ALIAS, DatabaseError, InterfaceError, connections
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

logger = structlog.get_logger(__name__)
RECHECK_SECONDS = 5


class SchemaState(Enum):
    READY = "ready"
    PENDING = "pending"
    UNAVAILABLE = "unavailable"


class SchemaReadinessChecker:
    """Check this image's migrations on the default DB, never applying them.

    Replica lag is a separate, pre-existing concern. Once ready, stay ready for
    this process's lifetime; later database outages belong to normal diagnostics.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._state = SchemaState.PENDING
        self._next_check = 0.0

    def check(self) -> SchemaState:
        """Cache readiness permanently and throttle other results to five seconds."""
        # Fast path once ready: every API request calls this, so skip the lock.
        if self._state is SchemaState.READY:
            return SchemaState.READY
        with self._lock:
            if self._state is SchemaState.READY or monotonic() < self._next_check:
                return self._state
            try:
                self._state = self._read_state()
            except (DatabaseError, InterfaceError):
                logger.warning("startup_schema_database_unavailable", exc_info=True)
                self._state = SchemaState.UNAVAILABLE
            if self._state is SchemaState.PENDING:
                # At most one line per worker per recheck, so a stalled
                # bootstrap is visible in the API's own logs.
                logger.info(
                    "startup_schema_pending", retry_after_seconds=RECHECK_SECONDS
                )
            self._next_check = monotonic() + RECHECK_SECONDS
            return self._state

    def _read_state(self) -> SchemaState:
        connection = connections[DEFAULT_DB_ALIAS]
        if not MigrationRecorder(connection).has_table():
            return SchemaState.PENDING
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        # Django resolves squashed/replaced migrations against the recorded set.
        # Extra applied migrations must not block old pods during an upgrade.
        if loader.graph.nodes.keys() <= loader.applied_migrations.keys():
            return SchemaState.READY
        return SchemaState.PENDING


schema_readiness = SchemaReadinessChecker()
