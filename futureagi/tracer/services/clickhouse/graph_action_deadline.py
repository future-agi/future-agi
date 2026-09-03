"""One request-owned wall deadline for latency-critical Observe graph actions."""

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

GRAPH_ACTION_WALL_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
logger = structlog.get_logger(__name__)


class GraphActionUnavailable(RuntimeError):
    """A graph action exhausted its wall or PostgreSQL read budget."""


def start_graph_action_deadline() -> ReadDeadline:
    """Start the single wall clock before any graph-action database read."""

    return ReadDeadline.start(GRAPH_ACTION_WALL_DEADLINE_MS)


def graph_action_remaining_ms(
    deadline: ReadDeadline,
    cap_ms: int | None = None,
    *,
    floor_ms: int = 1,
) -> int:
    """Return only the action's remaining wall, mapped to its public boundary."""

    try:
        return deadline.remaining_ms(cap_ms, floor_ms=floor_ms)
    except ReadDeadlineExceeded as exc:
        raise GraphActionUnavailable("Graph action request deadline exceeded") from exc


def finish_graph_action_response(deadline: ReadDeadline, response: Any) -> Any:
    """Refuse to publish a normal response after the action wall expired."""

    graph_action_remaining_ms(deadline)
    return response


def bounded_graph_action_request(
    *,
    resource: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Start the graph wall before validation and finish after validation."""

    def decorate(view_method):
        @wraps(view_method)
        def wrapped(view, request, *args, **kwargs):
            deadline = kwargs.get("_graph_action_deadline")
            if deadline is None:
                deadline = start_graph_action_deadline()
                kwargs["_graph_action_deadline"] = deadline

            try:
                response = view_method(view, request, *args, **kwargs)
                return finish_graph_action_response(deadline, response)
            except GraphActionUnavailable as exc:
                logger.warning(
                    "graph_action_request_deadline_exceeded",
                    resource=resource,
                    error_type=type(exc).__name__,
                )
                return view._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Graph data is temporarily unavailable. Please retry.",
                    code="service_unavailable",
                )

        # Preserve the pre-existing one-hop unwrapped action contract without
        # bypassing validated_request on the real public call path.
        wrapped.__wrapped__ = getattr(view_method, "__wrapped__", view_method)
        return wrapped

    return decorate


def _execute_graph_action_pg_query_with_deadline(
    deadline: ReadDeadline,
    timeout_cap_ms: int | None,
    execute,
    sql,
    params,
    many,
    context,
):
    """Shrink each PostgreSQL statement timeout against the shared wall."""

    timeout_ms = graph_action_remaining_ms(deadline, timeout_cap_ms)
    context["cursor"].cursor.execute(
        "SELECT set_config('statement_timeout', %s, true)",
        (str(timeout_ms),),
    )
    result = execute(sql, params, many, context)
    graph_action_remaining_ms(deadline)
    return result


@contextmanager
def graph_action_postgres_budget(
    deadline: ReadDeadline,
    *,
    timeout_cap_ms: int | None = None,
):
    """Bound every scoped/config ORM read by the same graph-action deadline."""

    transaction_started = False

    def execute_with_remaining_timeout(execute, sql, params, many, context):
        nonlocal transaction_started
        if (
            not getattr(connection, "in_atomic_block", False)
            and not transaction_started
        ):
            stack.enter_context(transaction.atomic())
            transaction_started = True
        return _execute_graph_action_pg_query_with_deadline(
            deadline,
            timeout_cap_ms,
            execute,
            sql,
            params,
            many,
            context,
        )

    try:
        if connection.vendor != "postgresql":
            yield
            graph_action_remaining_ms(deadline)
            return

        # Keep installing the wrapper connection-lazy. The first actual ORM
        # statement opens one transaction, receives SET LOCAL
        # statement_timeout, and leaves the transaction alive for every later
        # statement in this action phase. Pure validation and mocked unit-test
        # paths never open a database socket.
        with ExitStack() as stack:
            stack.enter_context(
                connection.execute_wrapper(execute_with_remaining_timeout)
            )
            yield
            graph_action_remaining_ms(deadline)
    except GraphActionUnavailable:
        raise
    except (DatabaseError, ReadDeadlineExceeded) as exc:
        raise GraphActionUnavailable(
            "Graph action PostgreSQL read exceeded its request budget"
        ) from exc


__all__ = [
    "GRAPH_ACTION_WALL_DEADLINE_MS",
    "GraphActionUnavailable",
    "bounded_graph_action_request",
    "finish_graph_action_response",
    "graph_action_postgres_budget",
    "graph_action_remaining_ms",
    "start_graph_action_deadline",
]
