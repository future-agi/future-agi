"""Per-tool MCP stats for the Tools list, from the org's own tool-call log.

The gateway's in-memory tool counters reset when it restarts and are not kept
per organization, so the Tools list reads the calls the app stored instead
(``AgentccToolCall``, the table Logs > Tool calls reads), over the last
``WINDOW_DAYS`` days.

A stored call counts for a listed tool by its server and upstream name: the
``tool`` column holds the name the client called, and a virtual server can
expose a tool under another name.
"""

import datetime

from django.db.models import Avg, Count, Max, Q
from django.utils import timezone

from agentcc.models import AgentccToolCall

WINDOW_DAYS = 30

Outcome = AgentccToolCall.Outcome
# The calls that failed: the tool's own error, a transport error, a timeout.
# A cancelled call is the caller's doing: a call, not an error. A denied call
# never reached the tool (the org's own policy refused it), so it is not a
# call of the tool at all.
ERROR_OUTCOMES = (Outcome.IS_ERROR, Outcome.ERROR, Outcome.TIMEOUT)


def with_tool_stats(organization, tools):
    """``tools`` with ``stats`` set on each item, replacing any the gateway
    sent. One grouped query for the whole list, none for an empty one."""
    if not tools:
        return tools
    since = timezone.now() - datetime.timedelta(days=WINDOW_DAYS)
    usage = _usage(organization, since)
    return [
        {
            **tool,
            "stats": usage.get((tool.get("server_id"), tool.get("upstream_name")))
            or _stats(),
        }
        for tool in tools
    ]


def _usage(organization, since):
    """Per (server id, upstream name): the org's calls since ``since``.

    The organization and start-time filter is served by the tool-call
    table's ``agentcc_tc_org_started_idx`` (organization, -started_at)."""
    rows = (
        AgentccToolCall.objects.filter(organization=organization, started_at__gte=since)
        .exclude(outcome=Outcome.DENIED)
        .exclude(server_id="")
        .exclude(upstream_tool="")
        .values("server_id", "upstream_tool")
        .annotate(
            call_count=Count("id"),
            error_count=Count("id", filter=Q(outcome__in=ERROR_OUTCOMES)),
            avg_latency_ms=Avg("latency_ms"),
            last_called=Max("started_at"),
        )
        .order_by()
    )
    return {
        (row["server_id"], row["upstream_tool"]): _stats(
            row["call_count"],
            row["error_count"],
            row["avg_latency_ms"],
            row["last_called"],
        )
        for row in rows
    }


def _stats(calls=0, errors=0, avg_latency_ms=None, last_called=None):
    return {
        "call_count": calls,
        "error_count": errors,
        "avg_latency_ms": (
            round(avg_latency_ms, 2) if avg_latency_ms is not None else None
        ),
        "error_rate": round(errors / calls, 4) if calls else 0,
        "last_called": last_called.isoformat() if last_called else None,
        "window_days": WINDOW_DAYS,
    }
