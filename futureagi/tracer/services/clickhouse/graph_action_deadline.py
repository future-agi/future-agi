"""One request-owned wall deadline for latency-critical Observe graph actions."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
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
from tracer.services.postgres_read_policy import application_postgres_reads

GRAPH_ACTION_WALL_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
logger = structlog.get_logger(__name__)


class GraphActionUnavailable(RuntimeError):
    """A graph action exhausted its wall or could not complete a read."""


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
    """Preserve a completed response; admission checks belong before reads."""

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
                    "graph_action_request_read_unavailable",
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


@contextmanager
def graph_action_postgres_budget(
    deadline: ReadDeadline,
    *,
    timeout_cap_ms: int | None = None,
):
    """Check request admission without a PostgreSQL statement cap."""
    del timeout_cap_ms  # Compatibility only; no longer a statement ceiling.
    try:
        with application_postgres_reads(
            connection=connection,
            atomic=transaction.atomic,
            check_request=lambda: graph_action_remaining_ms(deadline),
        ):
            yield
    except GraphActionUnavailable:
        raise
    except ReadDeadlineExceeded as exc:
        raise GraphActionUnavailable("Graph action request deadline exceeded") from exc
    except DatabaseError as exc:
        raise GraphActionUnavailable(
            "Graph action PostgreSQL read unavailable"
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
