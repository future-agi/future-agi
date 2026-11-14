"""Usage/topology-only replay of the checked-in, sanitized VS Code capture."""

import json
from importlib.resources import files

from tracer.utils.normalization import normalize_attributes


def captured_usage_rows(*, normalized=False):
    payload = json.loads(
        files("tracer.utils.normalization")
        .joinpath("vscode-copilot-1.0.100-1.json")
        .read_text()
    )
    rows = []
    for resource in payload["resourceSpans"]:
        for scope in resource["scopeSpans"]:
            for span in scope["spans"]:
                attrs = {
                    attr["key"]: next(iter(attr["value"].values()))
                    for attr in span["attributes"]
                }
                if normalized:
                    attrs = normalize_attributes(
                        attrs, scope["scope"], name=span["name"]
                    )
                # Before this PR, this scope version was unqualified and the
                # operation adapter did not recognize invoke_agent.
                legacy_kind = {"chat": "LLM", "execute_tool": "TOOL"}.get(
                    attrs.get("gen_ai.operation.name"), "UNKNOWN"
                )
                prompt = int(attrs.get("gen_ai.usage.input_tokens", 0))
                completion = int(attrs.get("gen_ai.usage.output_tokens", 0))
                rows.append(
                    {
                        "id": span["spanId"],
                        "parent_span_id": span.get("parentSpanId", ""),
                        "name": span["name"],
                        "observation_type": attrs.get(
                            "fi.span.kind", legacy_kind
                        ).lower(),
                        "prompt_tokens": prompt,
                        "completion_tokens": completion,
                        "total_tokens": prompt + completion,
                        "cost": 0.0,
                        "attrs_string": {
                            key: str(value) for key, value in attrs.items()
                        },
                    }
                )
    return rows


def usage_tree(rows):
    """Keep every physical row, attaching children to the first parent version."""
    nodes = [
        {
            "observation_span": {**row, "span_attributes": row.get("attrs_string", {})},
            "children": [],
        }
        for row in rows
    ]
    by_id = {}
    for node in nodes:
        by_id.setdefault(node["observation_span"]["id"], node)
    roots = []
    for node in nodes:
        parent = by_id.get(node["observation_span"].get("parent_span_id"))
        if parent is not None:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots
