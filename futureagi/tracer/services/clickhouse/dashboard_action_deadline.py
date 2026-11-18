"""One request-owned wall for interactive dashboard query actions."""

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

DASHBOARD_ACTION_WALL_DEADLINE_MS = settings.INTERACTIVE_ANALYTICS_DEFAULT_WALL_MS
logger = structlog.get_logger(__name__)


class DashboardActionUnavailable(RuntimeError):
    """A dashboard action exhausted its wall or could not complete a read."""


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


@contextmanager
def bounded_dashboard_postgres_reads(deadline: ReadDeadline):
    """Keep request checks separate from uncapped PostgreSQL execution."""
    try:
        with application_postgres_reads(
            connection=connection,
            atomic=transaction.atomic,
            check_request=lambda: dashboard_action_remaining_ms(deadline),
        ):
            yield
    except DashboardActionUnavailable:
        raise
    except ReadDeadlineExceeded as exc:
        raise DashboardActionUnavailable(
            "Dashboard action request deadline exceeded"
        ) from exc
    except DatabaseError as exc:
        raise DashboardActionUnavailable(
            "Dashboard PostgreSQL read unavailable"
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
                    return view_method(view, request, *args, **kwargs)
            except (
                DashboardActionUnavailable,
                DatabaseError,
                ReadDeadlineExceeded,
            ) as exc:
                logger.warning(
                    "dashboard_action_request_read_unavailable",
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
