"""When a gateway session first and last saw a request, and whether it has gone idle.

Ingestion creates every session active (log_ingestion._ensure_sessions_exist)
and a session is closed in the console by the Close action. A session that
gets no request for its idle timeout also reads closed, with the reason
"idle". That is derived when the session is read, from its newest request, so
nothing has to run on a schedule: a later request with the same session id
reads active again, while a session someone closed stays closed.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import (
    BooleanField,
    Case,
    DateTimeField,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Value,
    When,
)
from django.utils import timezone

from agentcc.models.request_log import AgentccRequestLog
from agentcc.models.session import AgentccSession

# The default, as for MCP sessions (the gateway's mcp session_ttl) and the
# common web-analytics session window.
DEFAULT_IDLE_TIMEOUT_SECONDS = 30 * 60

IDLE = "idle"
CLOSED_BY_USER = "closed"


def idle_timeout_seconds() -> int:
    """The idle timeout: AGENTCC_SESSION_IDLE_TIMEOUT_SECONDS, else 30 minutes."""
    value = getattr(settings, "AGENTCC_SESSION_IDLE_TIMEOUT_SECONDS", None)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_IDLE_TIMEOUT_SECONDS
    return value


def _request_time(order: str, **scope) -> Subquery:
    logs = AgentccRequestLog.no_workspace_objects.filter(
        organization_id=OuterRef("organization_id"),
        session_id=OuterRef("session_id"),
        deleted=False,
        started_at__isnull=False,
        **scope,
    ).order_by(order)
    return Subquery(logs.values("started_at")[:1], output_field=DateTimeField())


def _session_request_time(order: str) -> Case:
    """The started_at of the session's first request in ``order``, counted as
    the session's own stats count it: its org and id, and its workspace when
    it has one."""
    return Case(
        When(workspace__isnull=True, then=_request_time(order)),
        default=_request_time(order, workspace_id=OuterRef("workspace_id")),
        output_field=DateTimeField(),
    )


def with_activity(queryset: QuerySet) -> QuerySet:
    """Annotate ``first_request_at``, ``last_request_at`` and ``is_idle``.

    The requests are counted as the session's own stats count them
    (``AgentccSessionViewSet._session_logs``). ``first_request_at`` is when
    the session started: the session row's created_at is when ingestion
    stored it, one time for a whole batch of sessions
    (log_ingestion._ensure_sessions_exist). With no request, both are None
    and the idle clock runs from the session's creation. "Now" is read here,
    at query time.
    """
    cutoff = timezone.now() - timedelta(seconds=idle_timeout_seconds())
    return queryset.annotate(
        first_request_at=_session_request_time("started_at"),
        last_request_at=_session_request_time("-started_at"),
    ).annotate(
        is_idle=Case(
            When(
                Q(last_request_at__lt=cutoff)
                | Q(last_request_at__isnull=True, created_at__lt=cutoff),
                then=Value(True),
            ),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )


def filter_status(queryset: QuerySet, status: str) -> QuerySet:
    """Sessions whose status, as read (idle ones closed), is ``status``."""
    closed = Q(status=AgentccSession.CLOSED) | Q(is_idle=True)
    if status == AgentccSession.CLOSED:
        return queryset.filter(closed)
    if status == AgentccSession.ACTIVE:
        return queryset.exclude(closed)
    return queryset.filter(status=status)


def closed_reason(session) -> str:
    """Why it reads closed: "closed" (someone closed it), "idle" or ""."""
    if session.status == AgentccSession.CLOSED:
        return CLOSED_BY_USER
    if getattr(session, "is_idle", False):
        return IDLE
    return ""


def effective_status(session) -> str:
    """The session's status as read: an idle session reads closed."""
    return AgentccSession.CLOSED if closed_reason(session) else session.status


__all__ = [
    "CLOSED_BY_USER",
    "DEFAULT_IDLE_TIMEOUT_SECONDS",
    "IDLE",
    "closed_reason",
    "effective_status",
    "filter_status",
    "idle_timeout_seconds",
    "with_activity",
]
