"""Connection affinity for synchronous ORM helpers called by data migrations.

Django's model validation, serializer relation fields and many-to-many managers
ask the router for a connection even when the outer queryset used ``using``.
A delegated onboarding write must keep those implicit ORM operations in the
migration's transaction too. This context does not redirect explicit ``using``
queries or raw SQL; callers must bind those themselves. It is never enabled by
ordinary requests and is limited to the two writable migration aliases.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

_alias = ContextVar("migration_delegate_database", default=None)


def delegated_database_alias():
    return _alias.get()


def migration_database(function):
    """Honor a helper's keyword-only ``using=None`` without changing live calls."""

    @wraps(function)
    def bound(*args, **kwargs):
        using = kwargs.get("using")
        if using is None:
            return function(*args, **kwargs)
        if using not in {"default", "default_direct"}:
            raise ValueError("Migration helpers require a writable migration alias")
        token = _alias.set(using)
        try:
            return function(*args, **kwargs)
        finally:
            _alias.reset(token)

    return bound


@contextmanager
def executor_alias_scope(schema_editor):
    """Keep a RunPython body's implicit ORM on its executor connection.

    Direct callers passing None retain their existing routing. This scope adds
    no transaction boundary and does not override explicit ORM aliases.
    """
    if schema_editor is None:
        yield
        return
    using = schema_editor.connection.alias
    if using not in {"default", "default_direct"}:
        raise ValueError("Migration helpers require a writable migration alias")
    token = _alias.set(using)
    try:
        yield
    finally:
        _alias.reset(token)
