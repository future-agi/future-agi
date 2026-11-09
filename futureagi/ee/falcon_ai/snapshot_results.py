"""Response projections for known simulation tool results stored before read-side projection.

This is not a general text/credential detector. Only the catalog's producers of
AgentDefinition, AgentVersion and RunTest representations, and their two Falcon
result fields, are in scope. Never call this on the message persistence path.
"""

import json
import re

from simulate.utils.snapshot_projection import (
    SNAPSHOT_SECRET_KEYS as _SNAPSHOT_SECRET_KEYS,
)
from simulate.utils.snapshot_projection import scrub_snapshot_secrets

# mcp_server/catalog/tools.generated.json -> /simulate/ response serializers.
_SNAPSHOT_RESULT_TOOLS = frozenset(
    {
        "list_agents",
        "get_agent",
        "create_agent",
        "update_agent",
        "create_agent_version",
        "list_simulation_tests",
        "get_simulation_test",
        "create_simulation_test",
    }
)
_RESULT_FIELDS = ("result_full", "result_summary")
_PLACEHOLDER = "[Historical simulation result omitted]"
# Producer caps: result_full=2000 characters, summary<=200. Leave headroom for
# historical variants, but bound JSON parsing and textual recovery work.
_MAX_RESULT_CHARS = 8192
_MEMBER = re.compile(r'"(?:' + "|".join(sorted(_SNAPSHOT_SECRET_KEYS)) + r')"\s*:\s*')
_DECODER = json.JSONDecoder()


def _contains_closed_key(text):
    return any(key in text for key in _SNAPSHOT_SECRET_KEYS)


def _redact_incomplete_json(text):
    """Remove complete exact-key JSON members from a bounded broken fragment.

    Grammar: a literal double-quoted closed key, JSON whitespace, colon,
    whitespace, one complete JSON value (string/number/bool/null/object/array).
    raw_decode respects escaped quotes and nested containers. A following value
    delimiter or end is required. Remove an optional trailing comma. Incomplete
    values, free-text summaries, and any residual closed name fail closed to a
    value-free placeholder. This does not recognize arbitrary escaped aliases.
    """
    chunks = []
    cursor = 0
    for match in _MEMBER.finditer(text):
        if match.start() < cursor:
            continue
        try:
            _, end = _DECODER.raw_decode(text, match.end())
        except (ValueError, RecursionError):
            continue
        if end < len(text) and text[end] not in " \t\r\n,}]":
            continue
        while end < len(text) and text[end] in " \t\r\n":
            end += 1
        if end < len(text) and text[end] == ",":
            end += 1
        chunks.append(text[cursor : match.start()])
        cursor = end
    chunks.append(text[cursor:])
    redacted = "".join(chunks)
    return _PLACEHOLDER if _contains_closed_key(redacted) else redacted


def _scrub_result(value):
    if not isinstance(value, str):
        # Tool results are not edit forms: use the existing nested projection
        # to remove all 11 keys, rather than retain root-only display masks.
        return scrub_snapshot_secrets([value])[0]
    if len(value) > _MAX_RESULT_CHARS:
        return _PLACEHOLDER if _contains_closed_key(value) else value
    try:
        parsed = json.loads(value)
        scrubbed = scrub_snapshot_secrets([parsed])[0]
    except (ValueError, RecursionError):
        return _redact_incomplete_json(value)
    # Retain exact bytes when nothing changed, including whitespace and unicode.
    if scrubbed == parsed:
        return value
    return json.dumps(scrubbed, ensure_ascii=False)


def scrub_tool_result(call):
    """Copy a known tool's two result fields; leave other producers untouched."""
    if (
        not isinstance(call, dict)
        or call.get("tool_name") not in _SNAPSHOT_RESULT_TOOLS
    ):
        return call
    return {
        key: _scrub_result(value) if key in _RESULT_FIELDS else value
        for key, value in call.items()
    }


def scrub_message_tool_calls(calls):
    if not isinstance(calls, list):
        return calls
    return [scrub_tool_result(call) for call in calls]


def scrub_snapshot_result_event(event):
    """Project the producer's tool_call_result event for live send or replay."""
    if not isinstance(event, dict) or event.get("type") != "tool_call_result":
        return event
    return {**event, "data": scrub_tool_result(event.get("data"))}
