"""Deterministic, database-free preparation for each ASGI worker."""

from django.db.migrations.loader import MigrationLoader
from django.urls import URLResolver, get_resolver


def warm_http_urlconf() -> int:
    """Materialize the complete HTTP URL graph before a worker serves traffic.

    Django otherwise imports the root URLconf on the first request handled by
    each process.  This project has a broad route graph, so that lazy import can
    consume most of an interactive request's ten-second wall.  Resolving URL
    patterns performs no database work; it simply moves deterministic Python
    imports to worker startup and makes import failures fail readiness.
    """

    pending = [get_resolver()]
    pattern_count = 0
    while pending:
        resolver = pending.pop()
        patterns = tuple(resolver.url_patterns)
        pattern_count += len(patterns)
        pending.extend(
            pattern for pattern in patterns if isinstance(pattern, URLResolver)
        )
    return pattern_count


def warm_migration_modules() -> int:
    """Import this image's migration modules before a worker serves traffic.

    The startup schema guard's first check builds Django's migration graph,
    which imports every migration module. Loading from disk without a
    connection reads no database, so do it here instead of in a request.
    """
    return len(MigrationLoader(None, ignore_no_migrations=True).disk_migrations)
