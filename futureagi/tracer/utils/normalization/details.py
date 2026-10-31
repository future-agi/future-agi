"""Bounded event/link storage; constants and allowlists match fi-collector."""

import math
import re
from typing import Any

from . import CONTRACT

MAX_EVENTS = 64
MAX_LINKS = 32
MAX_STRING = 4096
MAX_BYTES = 16 << 10


def _attributes(source, allowed, budget):
    result = {}
    for key in allowed:
        value = source.get(key)
        if not isinstance(value, (str, int, float, bool)):
            continue
        size = len(key) + 32
        if isinstance(value, str):
            encoded = value.encode("utf-8", errors="replace")
            if len(encoded) > MAX_STRING:
                continue
            size += len(encoded)
        elif isinstance(value, float) and not math.isfinite(value):
            continue
        if size > budget:
            continue
        budget -= size
        result[key] = value
    return result, budget


def store_details(span: dict[str, Any]) -> None:
    """Do not retain free-text error bodies, tracestate or arbitrary nested data.

    Links remain data on the authorized owning span, not navigable target rows.
    A later link reader must separately authorize the target project.
    """
    budget = MAX_BYTES
    events = []
    source_events = span.get("events") or []
    for event in source_events[:MAX_EVENTS]:
        name = event.get("name") or ""
        size = len(name.encode("utf-8", errors="replace"))
        if size > MAX_STRING or size > budget:
            continue
        budget -= size
        source = event.get("attributes") or {}
        attrs, budget = _attributes(source, CONTRACT["event_attribute_keys"], budget)
        events.append(
            {
                "name": name,
                "timestamp": event.get("timestamp"),
                "attributes": attrs,
                "dropped_attributes_count": event.get("dropped_attributes_count", 0)
                + len(source)
                - len(attrs),
            }
        )
    if span.get("attributes", {}).get("fi.normalization.profile"):
        span["events"] = events
    attrs = span.setdefault("attributes", {})
    dropped = len(source_events) - len(events) + span.get("dropped_events_count", 0)
    if dropped and attrs.get("fi.normalization.profile"):
        attrs["fi.otel.dropped_events_count"] = dropped
    source_links = span.get("links") or []
    links = []
    for link in source_links[:MAX_LINKS]:
        tid, sid = link.get("trace_id") or "", link.get("span_id") or ""
        if not re.fullmatch(r"[0-9a-fA-F]{32}", tid) or not re.fullmatch(
            r"[0-9a-fA-F]{16}", sid
        ):
            continue
        if int(tid, 16) == 0 or int(sid, 16) == 0:
            continue
        source = link.get("attributes") or {}
        values, budget = _attributes(source, CONTRACT["link_attribute_keys"], budget)
        links.append(
            {
                "trace_id": tid.lower(),
                "span_id": sid.lower(),
                "attributes": values,
                "dropped_attributes_count": link.get("dropped_attributes_count", 0)
                + len(source)
                - len(values),
            }
        )
    if links:
        attrs["fi.otel.links"] = links
    else:
        attrs.pop("fi.otel.links", None)
    dropped = len(source_links) - len(links) + span.get("dropped_links_count", 0)
    if dropped:
        attrs["fi.otel.dropped_links_count"] = dropped


def needs_detailed_pricing(attributes: dict[str, Any]) -> bool:
    if not attributes.get("fi.normalization.profile"):
        return False
    return any(
        attributes.get(key) not in (None, 0, "0", "")
        for key in CONTRACT["unpriced_usage_keys"]
    )
