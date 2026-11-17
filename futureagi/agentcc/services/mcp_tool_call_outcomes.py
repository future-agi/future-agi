"""How one recorded tool call went through the gateway, for the explorer.

Read from the call's own columns: the gateway's checks and the upstream
attempt (an MCP tools/call makes at most one), the approval it named (looked up
in the caller's organization only; another org's approval reads as not
found) and the limit that refused it, if one did (meter/outcome.go's
budget, rate-limit and credit classes, and the call-depth guard).
"""

LIMIT_CLASSES = {
    "budget": "budget",
    "rate_limited": "rate_limit",
    "insufficient_credits": "credits",
}
LIMIT_CODES = {"tool_call_depth_exceeded": "depth"}
UPSTREAM_RESULTS = {
    "success": "ok",
    "is_error": "tool_error",
    "error": "error",
    "timeout": "timeout",
    "cancelled": "cancelled",
}


def _time(value):
    return value.isoformat() if value else None


def attempts(row):
    if row.outcome == "denied":
        return [{"step": "gateway", "result": "denied", "code": row.error_code}]
    steps = [{"step": "gateway", "result": "passed"}]
    if row.dispatched:
        steps.append(
            {
                "step": "upstream",
                "result": UPSTREAM_RESULTS.get(row.outcome, row.outcome or "unknown"),
                "latency_ms": row.upstream_latency_ms,
                "credential_source": row.credential_source,
                "fallback_from": row.fallback_from,
            }
        )
    else:
        steps.append({"step": "upstream", "result": "not_sent"})
    return steps


def approval(row, approvals):
    if not row.approval_id:
        return None
    found = approvals.get(row.approval_id)
    if found is None:
        return {"approval_id": row.approval_id, "status": None}
    return {
        "approval_id": row.approval_id,
        "status": found.status,
        "requested_at": _time(found.requested_at),
        "decided_at": _time(found.decided_at),
        "expires_at": _time(found.expires_at),
    }


def limits(row):
    stopped = None
    if row.outcome == "denied":
        stopped = LIMIT_CLASSES.get(row.error_class) or LIMIT_CODES.get(row.error_code)
    return {
        "stopped_by": stopped,
        "code": row.error_code if stopped else "",
        "budget_scope": row.budget_scope,
    }


def outcomes(row, approvals):
    return {
        "attempts": attempts(row),
        "approval": approval(row, approvals),
        "limits": limits(row),
    }


def approvals_for(organization, rows):
    """The organization's approvals the rows name, by approval_id."""
    from agentcc.models import AgentccMCPApproval

    ids = {row.approval_id for row in rows if row.approval_id}
    if organization is None or not ids:
        return {}
    return {
        found.approval_id: found
        for found in AgentccMCPApproval.objects.filter(
            organization=organization, approval_id__in=ids
        )
    }
