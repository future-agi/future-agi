"""The design a build proposes, read from an agent's contract, and the contract with a review's changes applied."""

from __future__ import annotations

import copy
from typing import Any

KINDS = ("table", "tool", "rule", "use_case")

SETTINGS_SOURCE_KINDS = frozenset({"provider", "phone", "livekit"})


def _columns(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return sorted(str(name) for name in properties)
    items = schema.get("items")
    if isinstance(items, dict) and isinstance(items.get("properties"), dict):
        return sorted(str(name) for name in items["properties"])
    if schema and all(isinstance(kind, str) for kind in schema.values()):
        return [str(name) for name in schema]
    return []


def _tool(spec: dict[str, Any], status: str, source: str) -> dict[str, Any]:
    from simulate.services.harness_tool_reach import tool_arguments

    inputs_from = spec.get("inputs_from")
    return {
        "id": str(spec.get("name") or ""),
        "name": str(spec.get("name") or ""),
        "description": str(spec.get("description") or ""),
        "args": [
            {"name": argument["name"], "type": argument["type"] or ""}
            for argument in tool_arguments(spec)
        ],
        "requires": _texts(spec.get("requires")),
        "inputs_from": (
            {str(arg): str(origin) for arg, origin in inputs_from.items()}
            if isinstance(inputs_from, dict)
            else {}
        ),
        "effects": str(spec.get("effects") or ""),
        "refuses": str(spec.get("refuses") or ""),
        "tables_read": _texts(spec.get("tables_read")),
        "tables_changed": _texts(spec.get("tables_changed")),
        "status": status,
        "source": source,
    }


def _texts(value: Any) -> list[str]:
    return (
        [str(one) for one in value if str(one).strip()]
        if isinstance(value, list)
        else []
    )


def _numbered(texts: Any, prefix: str) -> list[tuple[str, str]]:
    return [
        (f"{prefix}-{index}", str(text))
        for index, text in enumerate(texts if isinstance(texts, list) else [], 1)
    ]


def _actors(contract: dict[str, Any]) -> list[dict[str, str]]:
    actors = contract.get("actors")
    return [
        {
            "id": str(one.get("id") or one["name"]),
            "name": str(one["name"]),
            "role": str(one.get("role") or ""),
        }
        for one in (actors if isinstance(actors, list) else [])
        if isinstance(one, dict) and one.get("name")
    ]


def _unsupported(contract: dict[str, Any]) -> list[dict[str, Any]]:
    from simulate.services.harness_tool_reach import tool_status

    reaches: dict[str, list[str]] = {}
    for dependency in contract.get("dependencies") or []:
        if not isinstance(dependency, dict) or not dependency.get("name"):
            continue
        for tool in _texts(dependency.get("used_by")):
            reaches.setdefault(tool, []).append(str(dependency["name"]))
    return [
        {"tool": row["tool"], "reaches": reaches.get(row["tool"], [])}
        for row in tool_status(contract)
        if row["status"] == "unsupported"
    ]


def build_proposal(
    contract: dict[str, Any],
    scenario_count: int,
    source_kind: str | None = None,
) -> dict[str, Any]:
    from simulate.services.harness_tool_reach import tool_status

    tool_source = (
        "agent_settings" if source_kind in SETTINGS_SOURCE_KINDS else "agent_code"
    )
    schema = contract.get("data_schema") or {}
    statuses = {row["tool"]: row["status"] for row in tool_status(contract)}
    return {
        "scenario_count": max(int(scenario_count or 0), 0),
        "brief": {
            "description": str(contract.get("one_liner") or ""),
            "channel": "voice" if contract.get("modality") == "voice" else "chat",
        },
        "actors": _actors(contract),
        "unsupported": _unsupported(contract),
        "tables": [
            {
                "id": str(name),
                "name": str(name),
                "columns": _columns(body),
                "source": "agent_reading",
            }
            for name, body in (schema.items() if isinstance(schema, dict) else [])
        ],
        "tools": [
            _tool(spec, statuses.get(str(spec["name"]), "unknown"), tool_source)
            for spec in contract.get("tools") or []
            if isinstance(spec, dict) and spec.get("name")
        ],
        "rules": [
            {"id": item_id, "text": text, "source": "agent_prompt"}
            for item_id, text in _numbered(contract.get("hard_constraints"), "rule")
            if text.strip()
        ],
        "use_cases": [
            {"id": item_id, "text": text, "source": "agent_reading"}
            for item_id, text in _numbered(contract.get("real_use_cases"), "use-case")
            if text.strip()
        ],
    }


def _removed_texts(numbered: list[tuple[str, str]], removed: set[str]) -> set[str]:
    return {text for item_id, text in numbered if item_id in removed}


def _kept_details(details: Any, removed: set[str], reworded: dict[str, str]) -> Any:
    if not isinstance(details, list):
        return details
    kept = []
    for item in details:
        text = item.get("text") if isinstance(item, dict) else None
        if not isinstance(text, str):
            kept.append(item)
            continue
        if text in removed:
            continue
        kept.append({**item, "text": reworded[text]} if text in reworded else item)
    return kept


def _without(values: Any, removed: set[str]) -> Any:
    if not isinstance(values, list):
        return values
    return [one for one in values if not (isinstance(one, str) and one in removed)]


def _drop_tools_and_tables(
    edited: dict[str, Any], tools: set[str], tables: set[str]
) -> None:
    edited["tools"] = [
        (
            {
                **tool,
                "tables_read": _without(tool.get("tables_read"), tables),
                "tables_changed": _without(tool.get("tables_changed"), tables),
            }
            if isinstance(tool, dict)
            else tool
        )
        for tool in edited.get("tools") or []
        if not (isinstance(tool, dict) and tool.get("name") in tools)
    ]
    for field in ("tool_entrypoints", "tool_entrypoint_details"):
        if isinstance(edited.get(field), list):
            edited[field] = [
                entry
                for entry in edited[field]
                if not (isinstance(entry, dict) and entry.get("tool") in tools)
            ]
    if isinstance(edited.get("dependencies"), list):
        edited["dependencies"] = [
            (
                {**dependency, "used_by": _without(dependency.get("used_by"), tools)}
                if isinstance(dependency, dict)
                else dependency
            )
            for dependency in edited["dependencies"]
        ]
    for field in ("data_schema", "data_schema_columns"):
        if isinstance(edited.get(field), dict):
            edited[field] = {
                name: body for name, body in edited[field].items() if name not in tables
            }


def _review_amendments(
    rules: list[tuple[str, str]],
    cases: list[tuple[str, str]],
    removed: dict[str, set[str]],
    rewording: dict[str, str],
) -> list[str]:
    return [
        *(
            f"use case removed - {text}: removed in the design review"
            for item_id, text in cases
            if item_id in removed["use_case"]
        ),
        *(
            f"rule removed - {text}: removed in the design review"
            for item_id, text in rules
            if item_id in removed["rule"]
        ),
        *(
            f"rule reworded - {text}: reworded in the design review to: "
            f"{rewording[item_id]}"
            for item_id, text in rules
            if item_id in rewording and item_id not in removed["rule"]
        ),
    ]


def apply_edits_to_contract(
    contract: dict[str, Any], edits: list[dict[str, Any]]
) -> dict[str, Any]:
    """The contract with every change of a review applied wherever it is named."""
    edited = copy.deepcopy(contract)
    rules = _numbered(edited.get("hard_constraints"), "rule")
    cases = _numbered(edited.get("real_use_cases"), "use-case")
    removed: dict[str, set[str]] = {kind: set() for kind in KINDS}
    rewording: dict[str, str] = {}
    for edit in edits:
        if edit["op"] == "remove":
            removed[edit["kind"]].add(str(edit.get("id")))
        elif edit["op"] == "edit" and edit["kind"] == "rule":
            rewording[str(edit.get("id"))] = edit["text"]
    removed_rules = _removed_texts(rules, removed["rule"])
    reworded = {
        text: rewording[item_id]
        for item_id, text in rules
        if item_id in rewording and item_id not in removed["rule"]
    }
    edited["hard_constraints"] = [
        rewording.get(item_id, text)
        for item_id, text in rules
        if item_id not in removed["rule"]
    ]
    edited["hard_constraint_details"] = _kept_details(
        edited.get("hard_constraint_details"), removed_rules, reworded
    )
    edited["real_use_cases"] = [
        text for item_id, text in cases if item_id not in removed["use_case"]
    ]
    edited["real_use_case_details"] = _kept_details(
        edited.get("real_use_case_details"),
        _removed_texts(cases, removed["use_case"]),
        {},
    )
    _drop_tools_and_tables(edited, removed["tool"], removed["table"])
    changes = _review_amendments(rules, cases, removed, rewording)
    if changes:
        held = edited.get("amendments")
        edited["amendments"] = [*(held if isinstance(held, list) else []), *changes]
    for field in ("hard_constraint_details", "real_use_case_details"):
        if edited[field] is None and field not in contract:
            del edited[field]
    return edited
