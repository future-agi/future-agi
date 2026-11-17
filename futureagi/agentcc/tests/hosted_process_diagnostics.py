"""Synthetic process diagnostics: migration identity/alias/SQLSTATE, never SQL/data."""

import re
import sys


def install():
    from django.db.backends.utils import CursorWrapper
    from django.db.migrations.executor import MigrationExecutor

    execute = CursorWrapper.execute
    apply = MigrationExecutor.apply_migration

    def observed_execute(cursor, sql, params=None):
        try:
            return execute(cursor, sql, params)
        except Exception as error:
            state = getattr(error.__cause__, "sqlstate", "") or ""
            if not re.fullmatch(r"[0-9A-Z]{5}", state):
                state = "unknown"
            alias = (
                cursor.db.alias
                if cursor.db.alias in ("default", "default_direct")
                else "other"
            )
            print(
                f"synthetic-sql-failure alias={alias} state={state}",
                file=sys.stderr,
                flush=True,
            )
            raise

    def observed_apply(executor, state, migration, *args, **kwargs):
        print(
            f"synthetic-migration {migration.app_label}.{migration.name}",
            file=sys.stderr,
            flush=True,
        )
        return apply(executor, state, migration, *args, **kwargs)

    CursorWrapper.execute = observed_execute
    MigrationExecutor.apply_migration = observed_apply
