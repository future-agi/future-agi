"""What leaves the product when a recorded MCP tool call is exported.

One closed list of columns, the same for SIEM audit events, SIEM spans and the
explorer's CSV/JSON download: identifiers, outcome, timing, size and cost of a
call. Never its arguments, result, error text, evidence, guardrail detail, raw
session id, user or OAuth client identity, whatever content mode the call was
recorded under or the org consented to capture. Content export to a collector
needs the gateway's credential masker (internal/mcp/telemetry) and is not part
of this path.

The export is an allowlisted metadata projection (TEXT_COLUMNS, INT_COLUMNS,
BOOL_COLUMNS): ids; org, server, tool and key identifiers; method, outcome and
error class, origin and code; timings, sizes and costs; approval, attempt and
limit outcomes. Each text value is bounded by its column's length and shape
and must be one the gateway's span scalar sanitizer (telemetry.SanitizeText,
ported in siem_scalar) would export unchanged; the org's privacy redactor
(siem_redactor) then applies to every one, as the gateway applies it to its
span scalars. A value failing any of these is exported as "[redacted]", never
truncated. Numbers and booleans are exported as such; started_at is formatted
here, never copied. Callers pass the org's redactor (siem_redactor.org_redactor);
without one, no org policy applies.
"""

import calendar
import hashlib
import re
from datetime import UTC

from agentcc.services.siem_scalar import REDACTED, sanitize

SCHEMA = "agentcc.siem.v1"
EVENT_TYPE = "mcp.tool_call"

# A gateway enum value (outcome, billing mode, ...); new values need no change.
_ENUM = re.compile(r"[a-z][a-z0-9_.-]*")
# The gateway's session hash: hex SHA-256 of the session id.
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
# Every exported text column: its maximum length and, for a fixed format, a
# narrower shape. The gateway's own scalar bound is 256.
TEXT_COLUMNS = {
    # ids
    "event_id": (64, None),
    "tool_call_id": (64, None),
    "request_id": (64, None),
    "session_hash": (64, _HEX_SHA256),
    "agent_id": (128, None),
    "agent_run_id": (128, None),
    "agent_step": (32, None),
    # org, server, tool and key identifiers
    "api_key_id": (255, None),
    "principal_kind": (16, _ENUM),
    "virtual_server_id": (64, None),
    "server_id": (64, None),
    "tool": (255, None),
    "upstream_tool": (255, None),
    "tool_revision": (64, None),
    # method and transport
    "via": (32, _ENUM),
    "protocol_version": (16, None),
    # outcome and error class, origin and code
    "outcome": (16, _ENUM),
    "error_class": (32, None),
    "error_origin": (16, _ENUM),
    "error_code": (64, None),
    # approval, attempt and limit outcomes
    "approval_id": (64, None),
    "billing_mode": (16, _ENUM),
    "credential_source": (16, _ENUM),
    "budget_scope": (128, None),
    "content_mode": (8, _ENUM),
}

INT_COLUMNS = frozenset(
    {
        "latency_ms",
        "upstream_latency_ms",
        "gateway_overhead_ms",
        "bytes_in",
        "bytes_out",
        "upstream_cost_micros",
        "billed_cost_micros",
        "markup_micros",
    }
)
BOOL_COLUMNS = frozenset({"is_error", "dispatched"})
COLUMNS = (
    "event_id",
    "tool_call_id",
    "request_id",
    "started_at",
    "api_key_id",
    "principal_kind",
    "virtual_server_id",
    "server_id",
    "tool",
    "upstream_tool",
    "tool_revision",
    "via",
    "protocol_version",
    "session_hash",
    "agent_id",
    "agent_run_id",
    "agent_step",
    "outcome",
    "is_error",
    "error_class",
    "error_origin",
    "error_code",
    "approval_id",
    "latency_ms",
    "upstream_latency_ms",
    "gateway_overhead_ms",
    "bytes_in",
    "bytes_out",
    "billing_mode",
    "credential_source",
    "upstream_cost_micros",
    "billed_cost_micros",
    "markup_micros",
    "dispatched",
    "budget_scope",
    "content_mode",
)


def scalar(value, redactor=None):
    """``value`` as an exported text dimension: itself, "" or "[redacted]"."""
    if value is None or value == "":
        return ""
    return sanitize(str(value), redactor)


def column_value(column, value, redactor=None):
    """``value`` of text column ``column``, bounded by its length and shape."""
    if value is None or value == "":
        return ""
    text = str(value)
    limit, shape = TEXT_COLUMNS[column]
    if len(text) > limit or (shape is not None and not shape.fullmatch(text)):
        return REDACTED
    return sanitize(text, redactor)


def timestamp(value):
    if value is None:
        return ""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def audit_values(row, redactor=None):
    """The closed columns of one AgentccToolCall row, in COLUMNS order."""
    values = {}
    for column in COLUMNS:
        raw = getattr(row, column)
        if column == "started_at":
            values[column] = timestamp(raw)
        elif column in INT_COLUMNS:
            values[column] = max(int(raw or 0), 0)
        elif column in BOOL_COLUMNS:
            values[column] = bool(raw)
        else:
            values[column] = column_value(column, raw, redactor)
    return values


def _succeeded(row):
    # The span status and log severity follow the recorded outcome, bounded
    # but before the org's redactor, which may hide the outcome's text.
    return column_value("outcome", row.outcome) == "success"


def envelope(stream, delivery_id, organization_id, sent_at, redactor=None):
    return {
        "schema": SCHEMA,
        "stream": stream,
        "delivery_id": str(delivery_id),
        "organization_id": scalar(organization_id, redactor),
        "sent_at": timestamp(sent_at),
    }


def audit_events(rows, redactor=None):
    return [{"event_type": EVENT_TYPE, **audit_values(row, redactor)} for row in rows]


# ---------------------------------------------------------------- OTLP JSON
# OTLP/HTTP's JSON encoding (opentelemetry-proto, proto3 JSON mapping): ids as
# lowercase hex, 64-bit integers and nanosecond times as strings, enums as
# numbers.
SPAN_KIND_SERVER = 2
SPAN_KIND_CLIENT = 3
STATUS_UNSET = 0
STATUS_ERROR = 2
_SCOPE = {"name": "agentcc.mcp.siem", "version": "1"}
_RESOURCE = {
    "attributes": [{"key": "service.name", "value": {"stringValue": "agentcc-gateway"}}]
}


def _value(value):
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": value}


def _attributes(pairs):
    return [{"key": k, "value": _value(v)} for k, v in pairs if v != ""]


def span_ids(row):
    """Deterministic trace and span ids, so a retried export repeats itself."""
    seed = f"agentcc-siem:{row.organization_id}:{row.event_id}".encode()
    digest = hashlib.sha256(seed).hexdigest()
    return digest[:32], digest[32:48], digest[48:64]


def _nanos(value):
    if value is None:
        return 0
    value = value.astimezone(UTC)
    return calendar.timegm(value.utctimetuple()) * 10**9 + value.microsecond * 1000


def _span_attributes(row, values, redactor):
    # The gateway's execute_tool attribute set (internal/mcp/hook_trace.go).
    return _attributes(
        [
            ("gen_ai.operation.name", "execute_tool"),
            ("gen_ai.span.kind", "tool"),
            ("mcp.method.name", "tools/call"),
            (
                "gen_ai.tool.name",
                values["upstream_tool"] or values["tool"] or "unresolved",
            ),
            ("gen_ai.tool.call.id", values["tool_call_id"] or values["event_id"]),
            ("mcp.outcome", values["outcome"]),
            ("agentcc.billing_mode", values["billing_mode"]),
            ("mcp.billed_cost_micros", values["billed_cost_micros"]),
            ("mcp.upstream_cost_micros", values["upstream_cost_micros"]),
            ("mcp.markup_micros", values["markup_micros"]),
            ("agentcc.cost_usd", values["billed_cost_micros"] / 1e6),
            ("mcp.dispatched", values["dispatched"]),
            ("mcp.content_mode", values["content_mode"]),
            ("mcp.session.id", values["session_hash"]),
            ("agentcc.mcp.server_id", values["server_id"]),
            ("agentcc.mcp.virtual_server", values["virtual_server_id"]),
            ("agent.id", values["agent_id"]),
            ("agent.run_id", values["agent_run_id"]),
            ("agent.step", values["agent_step"]),
            ("error.type", values["error_class"]),
            ("mcp.protocol.version", values["protocol_version"]),
            ("agentcc.org_id", scalar(row.organization_id, redactor)),
            ("agentcc.api_key_id", values["api_key_id"]),
            ("mcp.error.code", values["error_code"]),
            ("mcp.error.origin", values["error_origin"]),
        ]
    )


def spans(rows, redactor=None):
    """OTLP JSON resourceSpans: a SERVER span per call, and a CLIENT child for
    a call the gateway sent upstream, timed by the recorded latencies."""
    out = []
    for row in rows:
        values = audit_values(row, redactor)
        trace_id, server_id, client_id = span_ids(row)
        start = _nanos(row.started_at)
        end = start + values["latency_ms"] * 1_000_000
        attributes = _span_attributes(row, values, redactor)
        status = {"code": STATUS_UNSET if _succeeded(row) else STATUS_ERROR}
        out.append(
            {
                "traceId": trace_id,
                "spanId": server_id,
                "name": "execute_tool",
                "kind": SPAN_KIND_SERVER,
                "startTimeUnixNano": str(start),
                "endTimeUnixNano": str(end),
                "attributes": attributes,
                "status": status,
            }
        )
        if values["dispatched"]:
            child_end = min(start + values["upstream_latency_ms"] * 1_000_000, end)
            out.append(
                {
                    "traceId": trace_id,
                    "spanId": client_id,
                    "parentSpanId": server_id,
                    "name": "execute_tool",
                    "kind": SPAN_KIND_CLIENT,
                    "startTimeUnixNano": str(start),
                    "endTimeUnixNano": str(child_end),
                    "attributes": attributes,
                    "status": status,
                }
            )
    return {
        "resourceSpans": [
            {"resource": _RESOURCE, "scopeSpans": [{"scope": _SCOPE, "spans": out}]}
        ]
    }


def logs(rows, observed_at, redactor=None):
    """OTLP JSON resourceLogs: one log record per call, its audit event as
    attributes, correlated to the call's SERVER span."""
    observed = str(_nanos(observed_at))
    records = []
    for row in rows:
        values = audit_values(row, redactor)
        trace_id, server_id, _ = span_ids(row)
        success = _succeeded(row)
        records.append(
            {
                "timeUnixNano": str(_nanos(row.started_at)),
                "observedTimeUnixNano": observed,
                "severityNumber": 9 if success else 13,
                "severityText": "INFO" if success else "WARN",
                "body": {"stringValue": EVENT_TYPE},
                "attributes": _attributes(
                    (f"agentcc.{k}", v) for k, v in values.items()
                ),
                "traceId": trace_id,
                "spanId": server_id,
            }
        )
    return {
        "resourceLogs": [
            {
                "resource": _RESOURCE,
                "scopeLogs": [{"scope": _SCOPE, "logRecords": records}],
            }
        ]
    }
