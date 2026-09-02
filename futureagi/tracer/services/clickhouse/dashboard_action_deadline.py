"""One request-owned wall for interactive dashboard query actions."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from functools import wraps
from typing import Any

import structlog
from django.conf import settings
from django.db import DatabaseError, connection, transaction
from rest_framework import status

from tracer.services.clickhouse.read_budget import (
    ReadDeadline,
    ReadDeadlineExceeded,
)

DASHBOARD_ACTION_WALL_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
logger = structlog.get_logger(__name__)


class DashboardActionUnavailable(RuntimeError):
    """A dashboard action exhausted its wall or PostgreSQL read budget."""


def start_dashboard_action_deadline() -> ReadDeadline:
    """Start the single wall before request-contract validation."""

    return ReadDeadline.start(DASHBOARD_ACTION_WALL_DEADLINE_MS)


def dashboard_action_remaining_ms(
    deadline: ReadDeadline,
    cap_ms: int | None = None,
    *,
    floor_ms: int = 1,
) -> int:
    """Return only the shared wall remaining, mapped to the public boundary."""

    try:
        return deadline.remaining_ms(cap_ms, floor_ms=floor_ms)
    except ReadDeadlineExceeded as exc:
        raise DashboardActionUnavailable(
            "Dashboard action request deadline exceeded"
        ) from exc


def _execute_dashboard_postgres_query_with_deadline(
    deadline: ReadDeadline,
    execute,
    sql,
    params,
    many,
    context,
):
    """Give one PostgreSQL statement only the request wall that remains."""

    timeout_ms = dashboard_action_remaining_ms(deadline)
    try:
        context["cursor"].cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (str(timeout_ms),),
        )
        result = execute(sql, params, many, context)
    except DashboardActionUnavailable:
        raise
    except DatabaseError as exc:
        if connection.in_atomic_block:
            transaction.set_rollback(True)
        raise DashboardActionUnavailable(
            "Dashboard PostgreSQL read exceeded its request budget"
        ) from exc
    dashboard_action_remaining_ms(deadline)
    return result


@contextmanager
def bounded_dashboard_postgres_reads(deadline: ReadDeadline):
    """Bound validation, scope, config, and formatting ORM reads by one wall."""

    if connection.vendor != "postgresql":
        yield
        dashboard_action_remaining_ms(deadline)
        return

    transaction_started = False
    with ExitStack() as stack:

        def execute_with_remaining_timeout(execute, sql, params, many, context):
            nonlocal transaction_started
            # Installing the execute wrapper is connection-lazy. Open an atomic
            # block only when the first actual ORM statement arrives so invalid
            # request validation never opens a database connection.
            if not connection.in_atomic_block and not transaction_started:
                try:
                    stack.enter_context(transaction.atomic())
                except DatabaseError as exc:
                    raise DashboardActionUnavailable(
                        "Dashboard PostgreSQL transaction could not start"
                    ) from exc
                transaction_started = True
            return _execute_dashboard_postgres_query_with_deadline(
                deadline,
                execute,
                sql,
                params,
                many,
                context,
            )

        stack.enter_context(connection.execute_wrapper(execute_with_remaining_timeout))
        try:
            yield
            dashboard_action_remaining_ms(deadline)
        except DashboardActionUnavailable:
            raise
        except (DatabaseError, ReadDeadlineExceeded) as exc:
            raise DashboardActionUnavailable(
                "Dashboard PostgreSQL read exceeded its request budget"
            ) from exc


def bounded_dashboard_action_request(
    *,
    resource: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Own one dispatch-to-response wall for a dashboard read action."""

    def decorate(view_method):
        @wraps(view_method)
        def wrapped(view, request, *args, **kwargs):
            deadline = kwargs.get("_dashboard_action_deadline")
            if deadline is None:
                deadline = start_dashboard_action_deadline()
                kwargs["_dashboard_action_deadline"] = deadline

            try:
                with bounded_dashboard_postgres_reads(deadline):
                    response = view_method(view, request, *args, **kwargs)
                # Response-contract validation and transaction close are part
                # of the same public action wall.
                dashboard_action_remaining_ms(deadline)
                return response
            except (
                DashboardActionUnavailable,
                DatabaseError,
                ReadDeadlineExceeded,
            ) as exc:
                logger.warning(
                    "dashboard_action_request_deadline_exceeded",
                    resource=resource,
                    error_type=type(exc).__name__,
                )
                return view._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Dashboard data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )

        # Keep historical one-hop and inspect.unwrap test boundaries pointed at
        # the original DRF action while the real runtime closure still invokes
        # validated_request inside the request wall.
        wrapped.__wrapped__ = getattr(view_method, "__wrapped__", view_method)
        return wrapped

    return decorate


__all__ = [
    "DASHBOARD_ACTION_WALL_DEADLINE_MS",
    "DashboardActionUnavailable",
    "bounded_dashboard_action_request",
    "bounded_dashboard_postgres_reads",
    "dashboard_action_remaining_ms",
    "start_dashboard_action_deadline",
]
