"""search_tools — smart capability/tool discovery for Falcon + MCP.

Falcon now exposes hundreds of tools (native + DRF-bridged APIs). Only ~40 are
sent to the model on any given turn (mode filtering), so the model frequently
cannot see the tool it actually needs — leading to "I can't do that",
hallucinated success, or fumbling through a generic MCP activity catalog
(root cause behind several TH-5467 tickets).

`search_tools` lets the model ask "what tools do I have for X?" and get back a
ranked, deduplicated list of matching tools (from the FULL registry, not just
the loaded subset) with their descriptions and key parameters. The model then
calls the returned tool by name; the agent loop auto-loads it on demand
(deferred loading falls back to the global registry — see ee/falcon_ai/agent.py).

This is the "tool search tool" pattern: one small always-available tool that
unlocks the entire capability surface without paying the token cost of sending
every schema every turn.
"""

import re

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.formatting import section
from ai_tools.registry import register_tool

# Lightweight synonym expansion so intent words match tool vocabulary.
_SYNONYMS = {
    "create": {"create", "make", "add", "new", "build", "set", "setup", "register"},
    "make": {"create", "make", "add", "new", "build"},
    "add": {"add", "create", "append", "insert", "attach"},
    "new": {"new", "create", "make"},
    "list": {"list", "show", "get", "find", "view", "all", "see", "display"},
    "show": {"show", "list", "get", "view", "display"},
    "get": {"get", "show", "fetch", "retrieve", "view", "read", "detail", "details"},
    "find": {"find", "search", "list", "lookup", "locate"},
    "delete": {"delete", "remove", "destroy", "drop", "archive"},
    "remove": {"remove", "delete", "destroy"},
    "update": {"update", "edit", "change", "modify", "rename", "set"},
    "edit": {"edit", "update", "change", "modify"},
    "rename": {"rename", "update", "change"},
    "eval": {"eval", "evaluation", "evaluate", "scorer", "metric", "judge"},
    "evaluation": {"evaluation", "eval", "evaluate", "metric", "judge"},
    "dataset": {"dataset", "data", "datasets", "rows", "columns"},
    "trace": {"trace", "traces", "span", "spans", "tracing", "observe"},
    "prompt": {"prompt", "prompts", "template", "templates", "workbench"},
    "agent": {"agent", "agents", "definition", "simulator"},
    "annotation": {"annotation", "annotations", "queue", "label", "labels", "annotate"},
    "experiment": {"experiment", "experiments", "run"},
    "knowledge": {"knowledge", "kb", "knowledgebase", "corpus", "rag"},
    "alert": {"alert", "alerts", "monitor", "monitoring"},
    "dashboard": {"dashboard", "dashboards", "widget", "chart"},
    "user": {"user", "users", "member", "members", "team", "people", "workspace"},
    "score": {"score", "scores", "rating", "result", "verdict"},
    "optimize": {"optimize", "optimization", "optimisation", "fix", "improve"},
    "scenario": {"scenario", "scenarios", "persona", "personas"},
    "api": {"api", "apikey", "key", "keys", "secret", "secrets"},
}

_STOP = {
    "a",
    "an",
    "the",
    "to",
    "of",
    "for",
    "in",
    "on",
    "my",
    "me",
    "i",
    "with",
    "and",
    "or",
    "is",
    "are",
    "how",
    "do",
    "can",
    "please",
    "want",
    "need",
    "this",
    "that",
    "it",
    "from",
    "by",
    "all",
    "what",
    "which",
    "via",
}


# Canonical action verbs — the leading token of most tool names — and the
# query words that map to each. Lets search_tools align "create a knowledge
# base" with create_knowledge_base instead of list_knowledge_bases.
_VERB_TO_PREFIX = {
    "create": "create", "make": "create", "new": "create", "build": "create",
    "register": "create", "add": "create",
    "list": "list", "show": "list", "view": "list", "all": "list", "find": "list",
    "get": "get", "fetch": "get", "retrieve": "get", "read": "get",
    "update": "update", "edit": "update", "change": "update", "modify": "update",
    "rename": "update", "set": "update",
    "delete": "delete", "remove": "delete", "destroy": "delete", "drop": "delete",
    "search": "search",
}
# Leading verbs that actually occur in tool names (used to detect an action
# mismatch worth demoting).
_VERB_CANON = set(_VERB_TO_PREFIX) | {
    "create", "list", "get", "update", "delete", "add", "submit", "search",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _expand(tokens: list[str]) -> set[str]:
    out: set[str] = set()
    for t in tokens:
        if t in _STOP:
            continue
        out.add(t)
        out |= _SYNONYMS.get(t, set())
    return out


class SearchToolsInput(PydanticBaseModel):
    query: str = Field(
        description=(
            "What you want to do, in natural language — e.g. 'create a knowledge "
            "base', 'list queue items', 'update project sampling rate', 'add a "
            "column to a dataset'. Returns the matching FutureAGI tools (from the "
            "full catalog) you can then call by name."
        )
    )
    category: str | None = Field(
        default=None,
        description=(
            "Optional category filter: tracing, datasets, evaluations, prompts, "
            "annotations, annotation_queues, agents, simulation, experiments, "
            "optimization, users, agentcc, context, web, docs."
        ),
    )
    limit: int = Field(default=12, ge=1, le=40, description="Max tools to return.")


@register_tool
class SearchToolsTool(BaseTool):
    name = "search_tools"
    description = (
        "Discover which FutureAGI tools/capabilities exist for a task. Use this "
        "FIRST whenever you are unsure a capability exists, before telling the "
        "user something can't be done, or when you need a tool that isn't already "
        "loaded. Searches the COMPLETE tool catalog (hundreds of tools spanning "
        "datasets, evaluations, traces, prompts, agents, annotations, alerts, "
        "dashboards, gateway, users, and more) and returns the best matches with "
        "their exact names and parameters. Matches on tool names, descriptions, "
        "AND parameter names (so 'sampling rate' finds the tool whose parameter "
        "is sampling_rate), and aligns your action verb (create/list/update/"
        "delete) with the right tool. After calling this, call the returned tool "
        "by its exact name — it loads automatically. Never claim an action is "
        "impossible without searching here first."
    )
    category = "context"
    input_model = SearchToolsInput

    def execute(self, params: SearchToolsInput, context: ToolContext) -> ToolResult:
        from ai_tools.registry import registry

        q_raw = params.query.lower()
        q_tokens = _tokens(params.query)
        q_expanded = _expand(q_tokens)
        cat = (params.category or "").strip().lower() or None

        # Detect the user's intended action verb so we can align it with the
        # tool's leading verb (create_/list_/get_/update_/delete_…). This stops
        # "create a dataset" from ranking list_datasets above create_dataset.
        q_verb = next((t for t in q_tokens if t in _VERB_CANON), None)
        if q_verb:
            q_verb = _VERB_TO_PREFIX.get(q_verb, q_verb)

        scored = []
        for tool in registry.list_all():
            if tool.name == "search_tools":
                continue
            if cat and tool.category != cat:
                continue

            name_tokens = set(_tokens(tool.name))
            desc_tokens = set(_tokens(tool.description))
            score = 0.0

            # Strong: query words matching the tool NAME.
            score += 6.0 * len(q_expanded & name_tokens)
            # Medium: query words present in the description.
            score += 1.0 * len(q_expanded & desc_tokens)

            # Parameter-name match: query words that hit a tool's PARAMETER
            # names — e.g. "sampling rate" surfaces rename_trace_project (param
            # `sampling_rate`) even though the tool name says nothing about it.
            schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
            param_tokens = set()
            for pname in (schema.get("properties") or {}):
                param_tokens |= set(_tokens(pname))
            score += 2.5 * len(q_expanded & param_tokens)

            # Category token match (e.g. "eval" → category 'evaluations').
            if q_expanded & set(_tokens(tool.category)):
                score += 2.0
            # Phrase / substring bonus: whole query appears in name or desc.
            flat_name = tool.name.replace("_", " ")
            if q_raw in flat_name or flat_name in q_raw:
                score += 5.0
            for tok in q_tokens:
                if len(tok) >= 4 and tok in tool.name:
                    score += 1.5

            # Verb alignment: matching action verb on the tool's leading token.
            tool_verb = next(iter(_tokens(tool.name)), "")
            if q_verb and tool_verb:
                if tool_verb == q_verb:
                    score += 4.0
                elif tool_verb in _SYNONYMS.get(q_verb, set()):
                    score += 2.0
                elif tool_verb in _VERB_CANON:
                    # query wants one action but tool is a different action verb
                    # (e.g. asked to "create" but this is delete_*) — slight demote
                    score -= 1.5

            if score > 0:
                scored.append((score, tool))

        scored.sort(key=lambda x: (-x[0], x[1].name))
        top = scored[: params.limit]

        if not top:
            return ToolResult(
                content=section(
                    "No matching tools",
                    f'No tools matched "{params.query}". Try broader terms, or '
                    "describe the entity (dataset, eval, trace, prompt, agent, "
                    "annotation, alert, dashboard, user).",
                ),
                data={"query": params.query, "tools": []},
            )

        lines = []
        data_tools = []
        for _score, tool in top:
            schema = tool.input_schema
            props = schema.get("properties", {}) if isinstance(schema, dict) else {}
            required = schema.get("required", []) if isinstance(schema, dict) else []
            param_bits = []
            for pname in list(props.keys())[:6]:
                param_bits.append(f"{pname}{'*' if pname in required else ''}")
            params_str = ", ".join(param_bits) if param_bits else "(no params)"
            desc = (tool.description or "").strip().split(". ")[0][:140]
            lines.append(
                f"- **`{tool.name}`** ({tool.category}) — {desc}\n"
                f"    params: {params_str}"
            )
            data_tools.append(
                {
                    "name": tool.name,
                    "category": tool.category,
                    "params": list(props.keys()),
                    "required": required,
                }
            )

        content = section(
            f"Tools matching “{params.query}” ({len(top)})",
            "\n".join(lines)
            + "\n\n_Call any tool above by its exact name — it loads automatically. "
            "`*` marks required parameters._",
        )
        return ToolResult(
            content=content, data={"query": params.query, "tools": data_tools}
        )
