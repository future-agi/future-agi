"""Original-wire capabilities, consumed once in the background dispatch order.

AgentLoop's protected parser has no call-id parameter on _execute_tool. Background
execution is sequential, so the instance-local dispatcher binds the next raw
provider call id to a ContextVar (also propagated by asyncio.to_thread). A tool
cannot authorize itself by validating the dictionary left by the generic parser.
"""

import json
from contextvars import ContextVar
from dataclasses import dataclass

from ee.agenthub.traceerroragent.background_payload_schemas import (
    DIMENSIONS,
    FINDING_FIELDS,
    PRIORITY,
    SCORE,
    TEXT,
    obj,
)
from ee.agenthub.traceerroragent.background_payloads import (
    refuse,
    require_payload,
    validate_payload,
    witness_for,
)

UUID = {"type": "string", "format": "uuid"}
OPTIONAL_ID = {"anyOf": [UUID, {"type": "null"}]}
NULLABLE_TEXT = {"anyOf": [TEXT, {"type": "null"}]}
SCORE_FIELDS = {
    "trace_id": UUID,
    "overall_score": SCORE,
    "insights": TEXT,
    "recommended_priority": PRIORITY,
    **{name + "_score": SCORE for name in DIMENSIONS},
    **{name + "_reason": TEXT for name in DIMENSIONS},
}
TOOL_SCHEMAS = {
    "explore_trace_legacy": obj({"trace_id": UUID}),
    "read_trace_span": obj(
        {"trace_id": UUID, "span_id": TEXT, "exact": {"type": "boolean"}},
        ["trace_id", "span_id"],
    ),
    "submit_trace_scores": obj(
        {**SCORE_FIELDS, "analysis_id": OPTIONAL_ID}, list(SCORE_FIELDS)
    ),
    "submit_trace_finding": obj(
        {
            "trace_id": UUID,
            **FINDING_FIELDS,
            "immediate_fix": NULLABLE_TEXT,
            "trace_impact": NULLABLE_TEXT,
            "analysis_id": OPTIONAL_ID,
        },
        ["trace_id", *FINDING_FIELDS],
    ),
}
_ACTIVE = ContextVar("background_wire_dispatch", default=None)


def background_tool_definitions(tools, owner):
    """Advertise the enforced required fields, without mutating shared tools."""
    definitions = []
    for tool in tools:
        name = tool["function"]["name"]
        if name not in TOOL_SCHEMAS:
            refuse(owner)
        definitions.append(
            {
                **tool,
                "function": {**tool["function"], "parameters": TOOL_SCHEMAS[name]},
            }
        )
    # A transport cannot mutate the frozen validator schemas through a request.
    return json.loads(json.dumps(definitions))


def _identical(left, right):
    # Python equality aliases True == 1; provenance must preserve JSON types.
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
            right, sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError):
        return False


@dataclass
class WireCall:
    call_id: str
    name: str
    payload: object
    consumed: bool = False


def _state(owner):
    witness = witness_for(owner)
    if not hasattr(witness, "wire_calls"):
        witness.wire_calls = {}
        witness.wire_pending = []
        witness.wire_text = []
    return witness


def validate_falcon_wire(raw, dialect, owner):
    """Before normalization, including its Anthropic input -> JSON conversion."""
    witness = _state(owner)
    if witness.wire_pending:
        refuse(witness)
    try:
        if dialect == "openai":
            message = raw["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            content = message.get("content")
            items = [
                (c["id"], c["function"]["name"], c["function"]["arguments"], True)
                for c in calls
            ]
        else:
            blocks = raw["content"]
            items = [
                (b["id"], b["name"], b["input"], False)
                for b in blocks
                if b["type"] == "tool_use"
            ]
            content = "".join(b["text"] for b in blocks if b["type"] == "text")
        # Validate the WHOLE batch before releasing even its first tool to
        # AgentLoop. A malformed sibling cannot leave a submitted draft behind.
        batch = []
        for call_id, name, arguments, decode in items:
            if (
                type(call_id) is not str
                or not call_id.strip()
                or call_id in witness.wire_calls
                or any(c.call_id == call_id for c in batch)
            ):
                refuse(witness)
            if name not in TOOL_SCHEMAS:
                refuse(witness)
            if decode and type(arguments) is not str:
                refuse(witness)
            payload = validate_payload(
                arguments, TOOL_SCHEMAS[name], witness, "tool:" + name, decode=decode
            )
            batch.append(WireCall(call_id, name, payload))
        if content is not None and content != "":
            text = validate_payload(content, TEXT, witness, "falcon-wire-text")
            witness.wire_text.append(text)
        elif not batch:
            refuse(witness)
        for call in batch:
            witness.wire_calls[call.call_id] = call
            witness.wire_pending.append(call.call_id)
    except BaseException:
        witness.failed = True
        raise


def install_falcon_dispatch(agent, witness):
    """Bind original provider ids without changing shared AgentLoop source."""
    _state(witness)
    original = agent._execute_tool

    async def dispatch(tool, decoded):
        if not witness.wire_pending:
            refuse(witness)
        call_id = witness.wire_pending.pop(0)
        call = witness.wire_calls[call_id]
        data = require_payload(call.payload, witness, "tool:" + tool.name)
        if call.name != tool.name or not _identical(data, decoded) or call.consumed:
            refuse(witness)
        token = _ACTIVE.set((witness, call_id))
        try:
            result = await original(tool, decoded)
            if not call.consumed:
                refuse(witness)
            return result
        except BaseException:
            witness.failed = True
            raise
        finally:
            _ACTIVE.reset(token)

    agent._execute_tool = dispatch


def consume_tool_payload(tool, decoded, owner):
    witness = witness_for(owner)
    active = _ACTIVE.get()
    if active is None or active[0] is not witness:
        refuse(witness)
    call = witness.wire_calls[active[1]]
    data = require_payload(call.payload, witness, "tool:" + tool.name)
    if call.name != tool.name or call.consumed or not _identical(data, decoded):
        refuse(witness)
    call.consumed = True
    return call.payload


def require_falcon_text(text, owner):
    witness = _state(owner)
    if witness.wire_pending or any(not c.consumed for c in witness.wire_calls.values()):
        refuse(witness)
    original = "".join(
        require_payload(p, witness, "falcon-wire-text") for p in witness.wire_text
    )
    if text != original or not original:
        refuse(witness)
    return validate_payload(original, TEXT, witness, "falcon")
