"""One request-owned wall deadline for interactive trace/span list actions."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import Any

import structlog
from django.db import DatabaseError, connection, transaction
from rest_framework import status

from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
)
from tracer.services.postgres_read_policy import (
    ApplicationPostgresReadError,
    application_postgres_reads,
)

logger = structlog.get_logger(__name__)


@contextmanager
def bounded_list_postgres_reads(deadline: ReadDeadline):
    """Keep request checks separate from uncapped PostgreSQL execution."""
    try:
        with application_postgres_reads(
            connection=connection,
            atomic=transaction.atomic,
            check_request=lambda: deadline.remaining_ms(floor_ms=1),
        ):
            yield
    except DatabaseError as exc:
        raise ApplicationPostgresReadError("List PostgreSQL read unavailable") from exc


def bounded_list_request(
    *,
    wall_ms: int,
    resource: str,
    unavailable_message: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Own one list wall across ORM scope, ClickHouse, and response formatting."""

    if wall_ms <= 0:
        raise ValueError("list request wall must be positive")

    def decorate(view_method):
        @wraps(view_method)
        def wrapped(view, request, *args, **kwargs):
            # Bounded export actions already own a wall before delegating to a
            # list action. Reuse it instead of resetting the clock.
            deadline = kwargs.get("read_deadline")
            if deadline is None:
                deadline = ReadDeadline.start(wall_ms)
                kwargs["read_deadline"] = deadline

            try:
                with bounded_list_postgres_reads(deadline):
                    return view_method(view, request, *args, **kwargs)
            except (ReadDeadlineExceeded, DatabaseError) as exc:
                logger.warning(
                    "observe_list_request_read_unavailable",
                    resource=resource,
                    error_type=type(exc).__name__,
                )
                return view._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    unavailable_message,
                    code="service_unavailable",
                )

        # Preserve the historical one-hop inspection escape hatch. The real
        # closure still invokes ``view_method`` (including validated_request),
        # while direct unit callers and inspect.unwrap retain their old target.
        wrapped.__wrapped__ = getattr(view_method, "__wrapped__", view_method)
        return wrapped

    return decorate


__all__ = [
    "bounded_list_postgres_reads",
    "bounded_list_request",
]
