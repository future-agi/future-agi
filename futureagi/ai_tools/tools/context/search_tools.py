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
    "create": {
        "create",
        "make",
        "add",
        "new",
        "build",
        "set",
        "setup",
        "register",
        "save",
    },
    "save": {"save", "create", "store", "persist"},
    "export": {"export", "download", "csv", "extract", "dump"},
    "assign": {"assign", "allocate", "assignee", "assigned"},
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
    "create": "create",
    "make": "create",
    "new": "create",
    "build": "create",
    "register": "create",
    "add": "create",
    "save": "create",
    "list": "list",
    "show": "list",
    "view": "list",
    "all": "list",
    "find": "list",
    "get": "get",
    "fetch": "get",
    "retrieve": "get",
    "read": "get",
    "update": "update",
    "edit": "update",
    "change": "update",
    "modify": "update",
    "rename": "update",
    "set": "update",
    "delete": "delete",
    "remove": "delete",
    "destroy": "delete",
    "drop": "delete",
    "search": "search",
    "assign": "assign",
    "export": "export",
    "download": "export",
}
# Leading verbs that actually occur in tool names (used to detect an action
# mismatch worth demoting). NOTE: ordering of query tokens matters — the FIRST
# query token found here wins verb detection, so an explicit action like "save"
# is detected before an entity word like "view" (which is also a list-synonym),
# keeping "save a filtered view" aligned with create_saved_view, not list_*.
_VERB_CANON = set(_VERB_TO_PREFIX) | {
    "create",
    "list",
    "get",
    "update",
    "delete",
    "add",
    "submit",
    "search",
    "assign",
    "export",
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


# Words that signal the user wants the WHOLE landscape ("what tools do I have",
# "show me everything") rather than one specific tool — triggers the capability
# overview so the model can browse the full surface and drill in by category.
_OVERVIEW_WORDS = {
    "all",
    "everything",
    "anything",
    "capabilities",
    "capability",
    "overview",
    "available",
    "catalog",
    "landscape",
}


def _capability_overview(limit_examples: int = 6) -> tuple[list[str], int, int]:
    """Group every registered tool by category with counts + example names.

    Lets the model see the FULL capability landscape (not just keyword hits) and
    re-query with a ``category`` filter to load a specific area. This is the
    "what tools do I have" map that backs the smarter discovery flow.
    """
    from collections import defaultdict

    from ai_tools.registry import registry

    by_cat: dict[str, list[str]] = defaultdict(list)
    for t in registry.list_all():
        if t.name == "search_tools":
            continue
        by_cat[t.category or "other"].append(t.name)
    lines = []
    for cat in sorted(by_cat):
        names = sorted(by_cat[cat])
        ex = ", ".join(f"`{n}`" for n in names[:limit_examples])
        more = (
            f" … (+{len(names) - limit_examples} more)"
            if len(names) > limit_examples
            else ""
        )
        lines.append(f"- **{cat}** ({len(names)}): {ex}{more}")
    total = sum(len(v) for v in by_cat.values())
    return lines, total, len(by_cat)


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
        "by its exact name — it loads automatically. Ask broadly (e.g. 'what "
        "tools do I have', 'show all capabilities') to get the full category map "
        "of every tool you have, then pass category='<name>' to list one area. "
        "Never claim an action is impossible without searching here first."
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
            for pname in schema.get("properties") or {}:
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
            # No keyword hit — don't dead-end. Show the FULL capability map (all
            # categories + counts + examples) so the model sees what exists and
            # can re-query by category or with a better term.
            ov_lines, ov_total, ov_cats = _capability_overview()
            return ToolResult(
                content=section(
                    f'No exact match for "{params.query}" — here is your full '
                    f"toolset ({ov_total} tools across {ov_cats} categories)",
                    "\n".join(ov_lines) + "\n\n_Re-run search_tools with a category "
                    "(e.g. category='simulation') or a word from one of the "
                    "tool names above — the tool loads on call._",
                ),
                data={
                    "query": params.query,
                    "tools": [],
                    "categories": ov_cats,
                    "total_tools": ov_total,
                },
            )

        lines = []
        data_tools = []
        for _score, tool in top:
            schema = tool.input_schema
            props = schema.get("properties", {}) if isinstance(schema, dict) else {}
            required = schema.get("required", []) if isinstance(schema, dict) else []
            param_bits = []
            for pname in list(props.keys())[:8]:
                pspec = props.get(pname) if isinstance(props, dict) else None
                pdesc = (
                    (pspec.get("description") or "").strip()
                    if isinstance(pspec, dict)
                    else ""
                )
                star = "*" if pname in required else ""
                # Include the description for REQUIRED params so the caller sees
                # what to pass and where an id comes from — enough to decide "I
                # can call this now" vs "I must fetch this via another tool
                # first" (per the eval-mapping / id-chaining cases).
                if pname in required and pdesc:
                    param_bits.append(f"{pname}{star}: {pdesc[:160]}")
                else:
                    param_bits.append(f"{pname}{star}")
            params_str = "; ".join(param_bits) if param_bits else "(no params)"
            # Show the FULL (capped) description, not just the first sentence.
            # The "how to use this" guidance — e.g. "provide the id from
            # list_X / get_X first" — almost always lives past sentence 1, so
            # truncating to the first sentence left Falcon unable to see that a
            # prerequisite call is needed, and it tried to run the tool without
            # the required id. Keeping the whole description lets it chain.
            desc = (tool.description or "").strip()
            if len(desc) > 500:
                desc = desc[:500].rstrip() + "…"
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
            "\n".join(lines) + "\n\n_Call any tool above by its exact name — it loads "
            "automatically. `*` marks required parameters. **If a required "
            "parameter (e.g. an id) isn't known yet, call the matching "
            "`list_*` / `get_*` tool FIRST to retrieve it, then call the "
            "target tool** — read each tool's description above for which "
            "prerequisite call supplies its ids._",
        )
        # Broad "what tools do I have / show me everything" intent → also append
        # the full capability map so the model can browse the whole surface, not
        # just the keyword hits above.
        if _OVERVIEW_WORDS & set(q_tokens):
            ov_lines, ov_total, ov_cats = _capability_overview()
            content += "\n\n" + section(
                f"Your full toolset ({ov_total} tools across {ov_cats} categories)",
                "\n".join(ov_lines)
                + "\n\n_Re-run search_tools with category='<name>' to list a "
                "whole category's tools._",
            )
        return ToolResult(
            content=content, data={"query": params.query, "tools": data_tools}
        )
