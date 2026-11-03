"""Deterministic, database-free preparation for each ASGI worker."""

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
