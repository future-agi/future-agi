"""Raw-cursor name boundary, without global ORM lifecycle hooks.

Django's own INSERT/UPDATE compilers remain unmodified. Their direct SQL dispatch
is distinguished from raw cursors at execution; no query snapshots, locks, model
hooks or grant checks run for ordinary writes. Collector deletion still requires
its transaction-bound erase authority. New connections inherit the raw guard.
"""

import sys
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from threading import Lock

from django.apps import apps
from django.db import connections, models
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.utils import CursorWrapper
from django.db.migrations.executor import MigrationExecutor
from django.db.models.signals import class_prepared
from django.db.models.sql import compiler
from django.db.models.sql.query import Query
from django.db.models.sql.subqueries import InsertQuery, UpdateQuery

from accounts.services.hard_erase_backfill import (
    execute_membership_backfill,
    membership_backfill_kind,
)
from accounts.services.hard_erase_cursor import copy_sql, install_cursor_guard
from accounts.services.hard_erase_migrations import (
    install_migration_guard,
    migration_sql_allowed,
)
from accounts.services.hard_erase_sql_exceptions import exact_sql_exception
from accounts.services.hard_erase_sql_policy import (
    _lex,
    protected_models,
    protected_mutations,
)

_SQL_AUTH = ContextVar("hard_erase_sql_authority", default=None)
_installed = False
_TABLES = frozenset()
_TABLES_LOCK = Lock()
_original_raw_delete = models.QuerySet._raw_delete


@contextmanager
def sql_authority(using, kind):
    connection = connections[using]
    token = _SQL_AUTH.set((connection, kind))
    try:
        yield
    finally:
        _SQL_AUTH.reset(token)


def _orm_write(connection):
    """Recognize only Django's direct compiler -> cursor execution path.

    This does not patch compilers or grant ambient authority: an application
    callback, signal, execute-wrapper or raw helper encountered before the exact
    framework compiler code terminates recognition. Raw SQL from those callers
    still gets the name policy, even while another ORM call is on the stack.
    Direct ORM select_for_update also retains its ordinary row-lock semantics;
    raw SELECT FOR UPDATE still fails the pure-read rule. This matters to the
    eraser's own preflight, which locks rows before granting erase authority.
    No frame/query references escape this function.
    """
    frame = sys._getframe(1)
    try:
        while frame is not None:
            if frame.f_code in {
                compiler.SQLCompiler.execute_sql.__code__,
                compiler.SQLInsertCompiler.execute_sql.__code__,
            }:
                owner = frame.f_locals.get("self")
                return (
                    isinstance(owner, compiler.SQLCompiler)
                    and owner.connection is connection
                    and (
                        isinstance(owner.query, (InsertQuery, UpdateQuery))
                        or (
                            type(owner.query) is Query and owner.query.select_for_update
                        )
                    )
                )
            module = frame.f_globals.get("__name__", "")
            if module != __name__ and not module.startswith("django.db.backends."):
                return False
            frame = frame.f_back
        return False
    finally:
        del frame


def guard_execute(execute, sql, params, many, context):
    from accounts.services.hard_erase import _refuse

    connection = context["connection"]
    if migration_sql_allowed(connection):
        return execute(sql, params, many, context)
    if _orm_write(connection):
        return execute(sql, params, many, context)
    authority = _SQL_AUTH.get()
    try:
        mutations = protected_mutations(sql, _TABLES)
    except ValueError as exc:
        _refuse(str(exc))
    if mutations:
        if exact_sql_exception(sql, many, connection):
            return execute(sql, params, many, context)
        if membership_backfill_kind(sql) is not None:
            return execute_membership_backfill(execute, sql, params, many, context)
        if (
            not authority
            or authority[0] is not connection
            or not connection.in_atomic_block
        ):
            _refuse("raw SQL cannot mutate protected application tables")
        if authority[1] != "erase" and not (
            authority[1] == "seal" and all(op == "update" for op, _, _ in mutations)
        ):
            _refuse("this boundary is not a protected-table mutation permit")
    return execute(sql, params, many, context)


def qualify_driver(connection, method, args, kwargs):
    from accounts.services.hard_erase import _refuse

    if migration_sql_allowed(connection):
        return
    try:
        if method in {"copy", "copy_expert", "copy_from", "copy_to"}:
            sql = copy_sql(method, args, kwargs, _lex)
        elif method == "stream":
            sql = args[0] if args else kwargs.get("query")
        else:
            # callproc and unknown driver calls can execute hidden mutations.
            sql = "CALL unqualified_driver_operation()"
    except ValueError as exc:
        _refuse(str(exc))
    guard_execute(lambda *unused: None, sql, None, False, {"connection": connection})


def guarded_raw_delete(self, using):
    from accounts.services.hard_erase import _refuse

    if (
        _SQL_AUTH.get() != (connections[using], "erase")
        or not connections[using].in_atomic_block
    ):
        _refuse("QuerySet._raw_delete requires a checked deletion boundary")
    return _original_raw_delete(self, using)


def _protect_prepared_model(sender, **kwargs):
    """Extend the census before a late model can issue its first raw write.

    class_prepared precedes register_model, so rescanning apps.get_models() here
    would miss the sender. A through model can also be prepared before inherited
    credential fields are copied onto its owner; revisit its local M2M tables
    when the owner itself is prepared. Historical migration registries must not
    add obsolete names to the live policy. No per-save ORM hook is installed.
    """
    global _TABLES
    if sender._meta.apps is not apps:
        return
    candidates = [sender]
    if sender._meta.auto_created:
        candidates.append(sender._meta.auto_created)
    for field in sender._meta.local_many_to_many:
        through = field.remote_field.through
        if hasattr(through, "_meta"):
            candidates.append(through)
    tables = frozenset(
        model._meta.db_table.lower() for model in protected_models(candidates)
    )
    if tables:
        # Publish immutable snapshots: readers never iterate a mutating set, and
        # concurrent late model preparation cannot lose another table addition.
        with _TABLES_LOCK:
            _TABLES = _TABLES | tables


# Subscribe before the initial census. Models loaded by later AppConfig.ready
# hooks (including ExternalEvalConfig) must not require another installation.
class_prepared.connect(
    _protect_prepared_model,
    weak=False,
    dispatch_uid="accounts.hard_erase_sql.credential_census",
)


def install_sql_guard():
    global _installed, _TABLES
    if _installed:
        return
    _TABLES = frozenset(
        model._meta.db_table.lower()
        for model in protected_models(apps.get_models(include_auto_created=True))
    )
    original_init = BaseDatabaseWrapper.__init__

    @wraps(original_init)
    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.execute_wrappers.append(guard_execute)

    BaseDatabaseWrapper.__init__ = initialize
    for connection in connections.all(initialized_only=True):
        if guard_execute not in connection.execute_wrappers:
            connection.execute_wrappers.append(guard_execute)
    install_cursor_guard(CursorWrapper, qualify_driver)
    models.QuerySet._raw_delete = guarded_raw_delete
    install_migration_guard(MigrationExecutor)
    _installed = True
