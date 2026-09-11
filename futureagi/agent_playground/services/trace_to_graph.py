"""
Service to convert a Trace (with its ObservationSpans) into an Agent Playground Graph.

Extracts LLM spans from a trace, converts them to prompt nodes,
and wires them together based on the original parent-child hierarchy.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

import structlog
from django.core.exceptions import ValidationError
from django.db import transaction

from agent_playground.models.choices import RESERVED_NAME_RE, GraphVersionStatus
from agent_playground.models.graph import Graph
from agent_playground.models.graph_dataset import GraphDataset
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node_template import NodeTemplate
from agent_playground.utils.version_content import update_version_content
from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.develop_dataset import Dataset
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace

logger = structlog.get_logger(__name__)


def _chspan_to_legacy_dict(span) -> dict[str, Any]:
    """Build the legacy ``.values(...)`` dict shape this module used to get
    from Django ``ObservationSpan.no_workspace_objects.filter(...).values(...)``.

    ``model_parameters`` and ``span_attributes`` aren't flat CHSpan fields:
    they round-trip through ``attributes_extra`` (see adapter.py:330) and
    the typed-maps respectively. ``input`` / ``output`` come back as JSON
    strings from CH and are decoded so the downstream consumers see the
    same Python dicts they did from PG's JSONField.

    The ``no_workspace_objects`` legacy manager bypassed workspace filtering;
    the CH spans store does not encode workspace at all, so the manager
    semantics naturally match (no extra filter needed).
    """
    import json as _json

    from tracer.services.clickhouse.v2.span_reader import CHSpanReader

    extra = CHSpanReader.attributes_extra_as_dict(span)
    # Defensive: attributes_extra_as_dict can yield a non-dict if the
    # underlying CH column is a raw String (schema 013) rather than typed
    # JSON. Treat anything that's not a dict as no overflow data.
    if not isinstance(extra, dict):
        extra = {}

    span_attributes: dict = {}
    span_attributes.update(span.attrs_string or {})
    span_attributes.update(span.attrs_number or {})
    span_attributes.update(span.attrs_bool or {})
    for k, v in extra.items():
        if k not in (
            "model_parameters",
            "input_images",
            "eval_input",
            "eval_attributes",
        ):
            span_attributes[k] = v

    def _decode_json(s):
        if not s:
            return None
        try:
            return _json.loads(s)
        except (_json.JSONDecodeError, TypeError):
            return s

    return {
        "id": span.id,
        "name": span.name,
        "observation_type": span.observation_type,
        "parent_span_id": span.parent_span_id or None,
        "input": _decode_json(span.input),
        "output": _decode_json(span.output),
        "model": span.model,
        "model_parameters": extra.get("model_parameters"),
        "span_attributes": span_attributes,
    }


def _legacy_spans_from_postgres(trace_id: str) -> list[dict[str, Any]]:
    return list(
        ObservationSpan.no_workspace_objects.filter(trace_id=trace_id, deleted=False)
        .order_by("start_time")
        .values(
            "id",
            "name",
            "observation_type",
            "parent_span_id",
            "input",
            "output",
            "model",
            "model_parameters",
            "span_attributes",
        )
    )


def _legacy_spans_from_clickhouse(trace_id: str) -> list[dict[str, Any]]:
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        # list_by_trace orders by start_time, id — matching the prior
        # `.order_by("start_time")` (ties on start_time previously broke
        # by row order; here they break by id, which is stable).
        chspans = reader.list_by_trace(trace_id)
    return [_chspan_to_legacy_dict(s) for s in chspans]


def convert_trace_to_graph(
    trace: Trace,
    user,
    organization,
    workspace,
) -> tuple[Graph, GraphVersion]:
    """
    Convert a trace into an agent playground graph.

    Extracts all LLM spans, converts each into an LLM prompt node,
    and connects them based on the original span hierarchy.

    Returns the created (Graph, GraphVersion) tuple.
    """
    trace_id = str(trace.id)

    try:
        spans = _legacy_spans_from_clickhouse(trace_id)
    except Exception:
        logger.warning(
            "trace_to_graph_clickhouse_failed, falling back to postgres",
            trace_id=trace_id,
            exc_info=True,
        )
        spans = []
    if not spans:
        spans = _legacy_spans_from_postgres(trace_id)

    llm_spans = [s for s in spans if s["observation_type"] == "llm"]
    if not llm_spans:
        raise ValidationError("This trace contains no LLM spans to iterate on.")

    # Build lookup for hierarchy walking
    span_by_id = {s["id"]: s for s in spans}
    llm_span_ids = {s["id"] for s in llm_spans}

    # Build LLM-to-LLM DAG: for each LLM span, find nearest LLM ancestor
    llm_parent_map = {}  # llm_span_id -> parent_llm_span_id or None
    for span in llm_spans:
        parent_id = _find_nearest_llm_ancestor(
            span["parent_span_id"], span_by_id, llm_span_ids
        )
        llm_parent_map[span["id"]] = parent_id

    # Get llm_prompt NodeTemplate
    try:
        llm_template = NodeTemplate.no_workspace_objects.get(name="llm_prompt")
    except NodeTemplate.DoesNotExist:
        raise ValidationError(
            "llm_prompt node template not found. Run seed_node_templates."
        ) from None

    # Generate node names — use parent agent name when LLM span name is generic
    raw_names = []
    for s in llm_spans:
        name = s["name"]
        if name in ("call_llm", "llm", "ChatCompletion", "LLM"):
            parent_name = _find_parent_agent_name(s["parent_span_id"], span_by_id)
            if parent_name:
                name = parent_name
        raw_names.append(name)
    node_names = _generate_unique_names(raw_names)

    # Assign UUIDs to each node
    node_uuids = {span["id"]: str(uuid.uuid4()) for span in llm_spans}

    # Compute positions
    positions = _compute_positions(llm_spans, llm_parent_map)

    # Build node payloads
    nodes_data = []
    for i, span in enumerate(llm_spans):
        prompt_template = _convert_span_to_prompt_template(span)

        # Each node needs a unique output port display_name to satisfy the
        # unique exposed port validation on GraphVersion.
        output_display_name = (
            "response" if len(llm_spans) == 1 else f"{node_names[i]}_response"
        )
        output_port = {
            "id": str(uuid.uuid4()),
            "key": "response",
            "display_name": output_display_name,
            "direction": "output",
            "data_schema": {"type": "string"},
            "required": False,
        }

        nodes_data.append(
            {
                "id": node_uuids[span["id"]],
                "type": "atomic",
                "name": node_names[i],
                "node_template_id": str(llm_template.id),
                "config": {},
                "position": positions[span["id"]],
                "prompt_template": prompt_template,
                "ports": [output_port],
            }
        )

    # Build node connections from LLM DAG
    node_connections_data = []
    for span in llm_spans:
        parent_llm_id = llm_parent_map.get(span["id"])
        if parent_llm_id:
            node_connections_data.append(
                {
                    "source_node_id": node_uuids[parent_llm_id],
                    "target_node_id": node_uuids[span["id"]],
                }
            )

    # Create Graph, Version, Dataset
    graph_name = f"Iterate: {trace.name or 'Untitled Trace'}"
    if len(graph_name) > 255:
        graph_name = graph_name[:252] + "..."

    with transaction.atomic():
        graph = Graph.no_workspace_objects.create(
            name=graph_name,
            description=f"Created from trace {trace_id}",
            organization=organization,
            workspace=workspace,
            created_by=user,
            is_template=False,
        )

        version = GraphVersion.no_workspace_objects.create(
            graph=graph,
            version_number=1,
            status=GraphVersionStatus.DRAFT,
        )

        dataset = Dataset.no_workspace_objects.create(
            name=graph_name,
            source=DatasetSourceChoices.GRAPH.value,
            organization=organization,
            workspace=workspace,
            user=user,
        )
        GraphDataset.no_workspace_objects.create(graph=graph, dataset=dataset)

        # Populate version with nodes and connections
        update_version_content(
            graph=graph,
            version=version,
            nodes_data=nodes_data,
            new_status=GraphVersionStatus.DRAFT,
            commit_message="Created from trace",
            node_connections_data=node_connections_data,
            user=user,
            organization=organization,
            workspace=workspace,
        )

    return graph, version


def _find_parent_agent_name(
    parent_span_id: str | None,
    span_by_id: dict[str, dict],
) -> str | None:
    """Walk up to find the nearest agent/chain parent and return its name."""
    current = parent_span_id
    visited = set()
    while current and current in span_by_id and current not in visited:
        visited.add(current)
        span = span_by_id[current]
        if span["observation_type"] in ("agent", "chain"):
            return span["name"]
        current = span.get("parent_span_id")
    return None


def _find_nearest_llm_ancestor(
    parent_span_id: str | None,
    span_by_id: dict[str, dict],
    llm_span_ids: set[str],
) -> str | None:
    """Walk up the span hierarchy to find the nearest LLM ancestor."""
    current = parent_span_id
    visited = set()
    while current and current in span_by_id and current not in visited:
        visited.add(current)
        if current in llm_span_ids:
            return current
        current = span_by_id[current].get("parent_span_id")
    return None


def _generate_unique_names(raw_names: list[str]) -> list[str]:
    """
    Sanitize span names for use as node names and ensure uniqueness.

    Replaces reserved characters (.[]{}) with underscores and
    appends _1, _2, etc. for any collisions.
    """
    used: set[str] = set()
    result: list[str] = []

    for name in raw_names:
        sanitized = RESERVED_NAME_RE.sub("_", name or "LLM Node").strip()
        if not sanitized:
            sanitized = "LLM Node"

        candidate = sanitized
        counter = 1
        while candidate in used:
            counter += 1
            candidate = f"{sanitized}_{counter}"
        used.add(candidate)
        result.append(candidate)

    return result


def _compute_positions(
    llm_spans: list[dict],
    llm_parent_map: dict[str, str | None],
) -> dict[str, dict[str, int]]:
    """
    Compute (x, y) positions for nodes using a simple layered layout.

    Roots at x=0, each subsequent level at x + 350.
    Within a level, nodes are spaced vertically at y + 250.
    """
    # Build children map
    children = defaultdict(list)
    roots = []
    for span in llm_spans:
        parent = llm_parent_map.get(span["id"])
        if parent:
            children[parent].append(span["id"])
        else:
            roots.append(span["id"])

    # BFS to assign levels
    levels: dict[str, int] = {}
    queue = [(rid, 0) for rid in roots]
    for span_id, level in queue:
        levels[span_id] = level
        for child_id in children.get(span_id, []):
            queue.append((child_id, level + 1))

    # Handle any spans not reached (disconnected)
    for span in llm_spans:
        if span["id"] not in levels:
            levels[span["id"]] = 0

    # Group by level and assign positions
    level_groups: dict[int, list[str]] = defaultdict(list)
    for span_id, level in levels.items():
        level_groups[level].append(span_id)

    positions = {}
    for level, span_ids in level_groups.items():
        for i, span_id in enumerate(span_ids):
            positions[span_id] = {"x": level * 350, "y": i * 250}

    return positions


def _convert_span_to_prompt_template(span: dict[str, Any]) -> dict[str, Any]:
    """
    Convert an LLM span's input/output/model data into a prompt_template payload
    compatible with the agent playground's VersionCreateSerializer.

    - Extracts input messages and the assistant response from output
    - Parses template variables from span_attributes and replaces
      resolved values with {{variable}} placeholders (enables port auto-creation)
    """
    template_vars = _parse_template_variables(span.get("span_attributes"))
    messages = _extract_messages(span.get("input"), template_vars)

    # Append assistant response from output
    assistant_text = _extract_assistant_response(span.get("output"))
    if assistant_text:
        messages.append(
            {
                "id": f"msg-{len(messages)}",
                "role": "assistant",
                "content": [{"type": "text", "text": assistant_text}],
            }
        )

    model_params = span.get("model_parameters") or {}

    return {
        "messages": messages,
        "model": span.get("model") or "gpt-4o-mini",
        "temperature": _safe_float(model_params.get("temperature")),
        "max_tokens": _safe_int(model_params.get("max_tokens")),
        "top_p": _safe_float(model_params.get("top_p")),
        "frequency_penalty": _safe_float(model_params.get("frequency_penalty")),
        "presence_penalty": _safe_float(model_params.get("presence_penalty")),
        "response_format": "text",
    }


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_template_variables(attrs: dict | None) -> dict[str, str] | None:
    """
    Extract template variables from span attributes.

    SDKs store them as JSON string in:
      - gen_ai.prompt.template.variables
      - llm.prompt_template.variables
    Returns {varName: resolvedValue} or None.
    """
    if not attrs or not isinstance(attrs, dict):
        return None

    import json

    raw = attrs.get("gen_ai.prompt.template.variables") or attrs.get(
        "llm.prompt_template.variables"
    )
    if not raw:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def _templatize_text(text: str, variables: dict[str, str]) -> str:
    """
    Replace resolved variable values in text with {{varName}} placeholders.

    Sorts by value length descending so longer values get replaced first
    (avoids partial matches when one value is a substring of another).
    """
    if not text or not variables:
        return text

    result = text
    entries = sorted(
        ((k, str(v)) for k, v in variables.items() if v is not None and str(v)),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )
    for name, value in entries:
        result = result.replace(value, f"{{{{{name}}}}}")
    return result


def _extract_messages(
    span_input: Any, template_vars: dict[str, str] | None
) -> list[dict[str, Any]]:
    """
    Extract messages from span input and convert to playground format.

    Span input is typically: {"messages": [{"role": "user", "content": "..."}]}
    Playground format: [{"id": "msg-0", "role": "user", "content": [{"type": "text", "text": "..."}]}]

    If template_vars is provided, resolved values in message text are replaced
    with {{variable}} placeholders so the playground auto-creates input ports.
    """
    if not span_input:
        return [_default_message()]

    if not isinstance(span_input, dict):
        if isinstance(span_input, list):
            raw_messages = span_input
        else:
            return [_default_message()]
    else:
        raw_messages = span_input.get("messages")

        # Gemini format: uses "contents" instead of "messages"
        if not raw_messages and "contents" in span_input:
            raw_messages = _convert_gemini_contents(span_input)

        if not raw_messages or not isinstance(raw_messages, list):
            # Fallback: treat entire input as a single user message
            text = _stringify(span_input)
            if template_vars:
                text = _templatize_text(text, template_vars)
            return [
                {
                    "id": "msg-0",
                    "role": "user",
                    "content": [{"type": "text", "text": text}],
                }
            ]

    messages = []
    for i, msg in enumerate(raw_messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")
        blocks = _normalize_content(content)

        # Templatize text blocks
        if template_vars:
            for block in blocks:
                if block.get("type") == "text" and block.get("text"):
                    block["text"] = _templatize_text(block["text"], template_vars)

        messages.append({"id": f"msg-{i}", "role": role, "content": blocks})

    return messages if messages else [_default_message()]


def _extract_assistant_response(span_output: Any) -> str | None:
    """
    Extract the assistant's response text from span output.

    Common formats:
    - {"choices": [{"message": {"content": "..."}}]}  (OpenAI)
    - {"content": "..."}  (direct)
    - "plain string"
    """
    if not span_output:
        return None

    if isinstance(span_output, str):
        return span_output if span_output.strip() else None

    if isinstance(span_output, dict):
        # OpenAI format: {"choices": [{"message": {"content": "..."}}]}
        choices = span_output.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0]
            if isinstance(msg, dict):
                message = msg.get("message", msg)
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content

        # Gemini format: {"content": {"role": "model", "parts": [{"text": "..."}]}}
        content = span_output.get("content")
        if isinstance(content, dict):
            parts = content.get("parts", [])
            if isinstance(parts, list):
                texts = []
                for part in parts:
                    if isinstance(part, dict) and part.get("text"):
                        texts.append(str(part["text"]))
                if texts:
                    return "\n".join(texts)

        # Direct string content
        if isinstance(content, str) and content.strip():
            return content

        # Anthropic format: {"content": [{"text": "..."}]}
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("text"):
                    texts.append(str(block["text"]))
            if texts:
                return "\n".join(texts)

    return None


def _convert_gemini_contents(span_input: dict) -> list[dict[str, Any]]:
    """
    Convert Gemini/Google AI format to standard messages list.

    Gemini input:
      {"contents": [{"role": "user", "parts": [{"text": "..."}]}],
       "config": {"system_instruction": "..."}}

    Returns: [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]
    """
    messages = []

    # Extract system instruction from config
    config = span_input.get("config") or {}
    sys_instruction = config.get("system_instruction") or span_input.get(
        "system_instruction"
    )
    if sys_instruction:
        if isinstance(sys_instruction, str):
            messages.append({"role": "system", "content": sys_instruction})
        elif isinstance(sys_instruction, dict):
            # Can be {"parts": [{"text": "..."}]}
            parts = sys_instruction.get("parts", [])
            text = " ".join(
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and p.get("text")
            )
            if text:
                messages.append({"role": "system", "content": text})

    # Convert contents
    contents = span_input.get("contents", [])
    if not isinstance(contents, list):
        return messages

    for item in contents:
        if not isinstance(item, dict):
            continue
        role = item.get("role", "user")
        # Map Gemini roles to OpenAI roles
        if role == "model":
            role = "assistant"
        parts = item.get("parts", [])
        text_parts = []
        for part in parts:
            if isinstance(part, dict):
                if part.get("text"):
                    text_parts.append(part["text"])
                elif part.get("function_call"):
                    fc = part["function_call"]
                    text_parts.append(
                        f"[Tool call: {fc.get('name', '?')}({fc.get('args', {})})]"
                    )
                elif part.get("function_response"):
                    fr = part["function_response"]
                    text_parts.append(
                        f"[Tool result: {_stringify(fr.get('response', ''))}]"
                    )
        if text_parts:
            messages.append({"role": role, "content": "\n".join(text_parts)})

    return messages


def _normalize_content(content: Any) -> list[dict[str, Any]]:
    """
    Normalize message content to the playground's content block format.

    Handles:
    - string → [{"type": "text", "text": "..."}]
    - list of content blocks → pass through with type validation
    - other → stringify
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        blocks = []
        for item in content:
            if isinstance(item, dict) and "type" in item:
                blocks.append(item)
            elif isinstance(item, dict) and "text" in item:
                blocks.append({"type": "text", "text": str(item["text"])})
            else:
                blocks.append({"type": "text", "text": _stringify(item)})
        return blocks if blocks else [{"type": "text", "text": ""}]
    return [{"type": "text", "text": _stringify(content)}]


def _default_message() -> dict[str, Any]:
    """Return a default empty user message."""
    return {
        "id": "msg-0",
        "role": "user",
        "content": [{"type": "text", "text": ""}],
    }


def _stringify(val: Any) -> str:
    """Safely stringify a value for use in message text."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    import json

    try:
        return json.dumps(val, indent=2, default=str)
    except (TypeError, ValueError):
        return str(val)
