"""DB-test-only historical/corruption SQL fixtures through the real executor.

Some tests intentionally exercise SQL (upsert/EXPLAIN) or manufacture corrupt
ledger history; replacing their statement with a normal model save changes the
assertion. Use an ephemeral RunPython migration, with no recorder entry, for
only those fixture statements. The normal guard stays installed. This helper
is never imported by application code and grants no ambient runtime authority.
"""

from contextlib import contextmanager
from types import SimpleNamespace

from django.db import migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState


class FixtureExecutor(MigrationExecutor):
    def __init__(self, connection):
        self.connection = connection
        self.progress_callback = None
        self.recorder = SimpleNamespace(record_unapplied=lambda *args: None)

    def record_migration(self, migration):
        # Ephemeral fixture: do not claim any schema migration was installed.
        pass


def _own_connection(connection):
    """The connection the fixture runs on.

    django.db.connection stands for the default alias's connection, and the
    fixture runs on that connection itself. The imports are here because the
    fixture flow test compiles this file without its Django imports.
    """
    from django.db import connections
    from django.utils.connection import ConnectionProxy

    if isinstance(connection, ConnectionProxy):
        return connections[connection.alias]
    return connection


@contextmanager
def migration_fixture_cursor(connection):
    connection = _own_connection(connection)
    with connection.cursor() as cursor:

        class FixtureCursor:
            def execute(self, sql, params=None):
                result = []

                def apply(apps, schema_editor):
                    assert schema_editor.connection is connection
                    result.append(cursor.execute(sql, params))

                migration = migrations.Migration("hard_erase_fixture", "accounts")
                migration.operations = [migrations.RunPython(apply)]
                FixtureExecutor(connection).apply_migration(ProjectState(), migration)
                return result[0]

            def __getattr__(self, name):
                return getattr(cursor, name)

        yield FixtureCursor()
