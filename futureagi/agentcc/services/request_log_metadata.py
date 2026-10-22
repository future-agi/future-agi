"""Request-log metadata: the gateway keys log ingestion reads back from it, and
the options for the Application, Service and Custom Tags filters."""

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db.models.fields.json import KeyTextTransform

from agentcc.models import AgentccRequestLog
from agentcc.models.request_log import RequestLogTag

TAG_SEPARATOR = ":"

# Options are read from the org's most recent requests, so the picker costs the
# same however much history the org has.
METADATA_VALUES_SAMPLE_SIZE = 10_000
METADATA_VALUES_LIMIT = 100

# A log entry's guardrail.tool_calls verdict has no column of its own; it is
# kept as JSON text under this key. The gateway reserves the guardrail_ prefix,
# so a caller's metadata never collides with it.
GUARDRAIL_TOOL_CALLS_KEY = "guardrail_tool_calls"

_USD_PLACES = Decimal("1e-8")  # the decimal(16, 8) money columns


@dataclass(frozen=True)
class MetadataValues:
    application: list[str]
    service: list[str]
    tags: list[str]


def _distinct_values(queryset, key):
    """Values with a comma are skipped: the filter params are comma-separated."""
    return list(
        queryset.annotate(value=KeyTextTransform(key, "metadata"))
        .exclude(value__isnull=True)
        .exclude(value="")
        .exclude(value__contains=",")
        .order_by("value")
        .values_list("value", flat=True)
        .distinct()[:METADATA_VALUES_LIMIT]
    )


def get_metadata_values(queryset, tag_keys):
    """Tag values in the latest requests of an already org-scoped queryset."""
    recent_ids = queryset.order_by("-started_at").values("id")[
        :METADATA_VALUES_SAMPLE_SIZE
    ]
    recent = AgentccRequestLog.no_workspace_objects.filter(id__in=recent_ids)
    return MetadataValues(
        application=_distinct_values(recent, RequestLogTag.APPLICATION.value),
        service=_distinct_values(recent, RequestLogTag.SERVICE.value),
        tags=[
            f"{key}{TAG_SEPARATOR}{value}"
            for key in tag_keys
            for value in _distinct_values(recent, key)
        ],
    )


def parse_usd(value):
    """A USD amount as a decimal(16, 8) value, or None when it is not one."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value)).quantize(_USD_PLACES)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not amount.is_finite() or abs(amount) >= Decimal("1e8"):
        return None
    return amount


def legacy_billing(metadata):
    """The billing columns a v1 log entry carries only as gateway metadata keys.

    A v1 log entry carries billing in the cost plugin's metadata:
    billing_mode, cost_base (the upstream cost), cost_markup_percent,
    cost_source, auto_decision (set when model auto chose the model) and
    usage_estimated. Returns only the columns the metadata has a value for.
    """
    if not isinstance(metadata, dict):
        return {}
    columns = {}
    for key in ("billing_mode", "cost_source"):
        if metadata.get(key):
            columns[key] = str(metadata[key])
    base = parse_usd(metadata.get("cost_base"))
    if base is not None:
        columns["cost_upstream"] = base
        percent = parse_usd(metadata.get("cost_markup_percent"))
        if percent is not None:
            columns["cost_markup"] = parse_usd(base * percent / 100)
    if metadata.get("auto_decision"):
        columns["auto_routed"] = True
    if metadata.get("usage_estimated") == "true":
        columns["usage_estimated"] = True
    return columns


def tool_calls_metadata(tool_calls):
    """The guardrail.tool_calls verdict as the metadata value that stores it."""
    return json.dumps(tool_calls, sort_keys=True, separators=(",", ":"))


def read_tool_calls(metadata):
    """The stored guardrail.tool_calls verdict, or None."""
    raw = metadata.get(GUARDRAIL_TOOL_CALLS_KEY) if isinstance(metadata, dict) else None
    if not raw:
        return None
    try:
        tool_calls = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return tool_calls if isinstance(tool_calls, dict) else None
