"""Connection-scoped SQL maintenance only during Django migration execution.

Wrap the executor's apply/unapply lifecycle, including schema-editor exit and
its deferred SQL. Do not use command names, caller frames, migration filenames,
SQL text, or pre/post-migrate signals (whose interval leaks on command failure).
Non-atomic migrations need this boundary too; it is distinct from erase authority
and never permits QuerySet._raw_delete or disables the ordinary cursor guard.
"""

from contextvars import ContextVar
from functools import wraps
from threading import get_ident

_MIGRATION = ContextVar("hard_erase_migration", default=None)


class _Scope:
    def __init__(self, connection):
        self.connection = connection
        self.thread = get_ident()
        self.active = True


def migration_sql_allowed(connection):
    scope = _MIGRATION.get()
    return (
        scope is not None
        and scope.active
        and scope.connection is connection
        and scope.thread == get_ident()
    )


def _migration_call(method):
    @wraps(method)
    def execute(self, *args, **kwargs):
        scope = _Scope(self.connection)
        token = _MIGRATION.set(scope)
        try:
            return method(self, *args, **kwargs)
        finally:
            # Revoke even copied contexts, then restore an enclosing migration.
            scope.active = False
            _MIGRATION.reset(token)

    execute._hard_erase_migration = True
    return execute


def install_migration_guard(executor_class):
    """Install once on Django's MigrationExecutor, not on management commands."""
    for name in ("apply_migration", "unapply_migration"):
        method = getattr(executor_class, name)
        if not getattr(method, "_hard_erase_migration", False):
            setattr(executor_class, name, _migration_call(method))
