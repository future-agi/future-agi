"""One request-owned wall deadline for interactive trace/span list actions."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from functools import wraps
from typing import Any

import structlog
from django.db import OperationalError, connection, transaction
from rest_framework import status

from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
)

logger = structlog.get_logger(__name__)


def _mark_transaction_for_rollback() -> None:
    """Keep an action-owned atomic block from committing after a read failure."""

    if connection.in_atomic_block:
        transaction.set_rollback(True)


def _execute_list_postgres_query_with_deadline(
    deadline: ReadDeadline,
    execute,
    sql,
    params,
    many,
    context,
):
    """Give one PostgreSQL statement only the request wall that remains."""

    timeout_ms = deadline.remaining_ms(floor_ms=1)
    try:
        # Use the driver's cursor so the timeout statement does not recursively
        # enter this Django execute wrapper. ``is_local=true`` prevents the
        # setting from escaping the request-owned transaction.
        context["cursor"].cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (str(timeout_ms),),
        )
    except Exception as exc:
        # This is constant SQL whose only runtime input is an integer derived
        # from the deadline. A failure here means the read cannot be bounded.
        _mark_transaction_for_rollback()
        raise ReadDeadlineExceeded(
            "List PostgreSQL timeout could not be installed"
        ) from exc

    try:
        result = execute(sql, params, many, context)
    except ReadDeadlineExceeded:
        raise
    except OperationalError as exc:
        # PostgreSQL reports statement_timeout through Django's database error
        # boundary. Keep the public response typed and free of driver/SQL text.
        _mark_transaction_for_rollback()
        raise ReadDeadlineExceeded(
            "List PostgreSQL read exceeded its request deadline"
        ) from exc

    deadline.remaining_ms(floor_ms=1)
    return result


@contextmanager
def bounded_list_postgres_reads(deadline: ReadDeadline):
    """Bound every PostgreSQL statement in one list action by one wall clock."""

    if connection.vendor != "postgresql":
        yield
        deadline.remaining_ms(floor_ms=1)
        return

    transaction_started = False
    with ExitStack() as stack:

        def execute_with_remaining_timeout(execute, sql, params, many, context):
            nonlocal transaction_started
            # Installing an execute wrapper is connection-lazy. Open the
            # transaction only when a real ORM statement arrives, preserving
            # mock-only/unit early exits while keeping set_config transaction
            # local in production.
            if not connection.in_atomic_block and not transaction_started:
                transaction_started = True
                try:
                    stack.enter_context(transaction.atomic())
                except OperationalError as exc:
                    transaction_started = False
                    raise ReadDeadlineExceeded(
                        "List PostgreSQL transaction could not start"
                    ) from exc
                except Exception:
                    transaction_started = False
                    raise
            return _execute_list_postgres_query_with_deadline(
                deadline,
                execute,
                sql,
                params,
                many,
                context,
            )

        stack.enter_context(connection.execute_wrapper(execute_with_remaining_timeout))
        yield
        deadline.remaining_ms(floor_ms=1)


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
                    response = view_method(view, request, *args, **kwargs)
                # Check after the PostgreSQL transaction closes too: a slow
                # commit/rollback may not extend a nominal response past the
                # request wall.
                deadline.remaining_ms(floor_ms=1)
                return response
            except (ReadDeadlineExceeded, OperationalError) as exc:
                logger.warning(
                    "observe_list_request_deadline_exceeded",
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
