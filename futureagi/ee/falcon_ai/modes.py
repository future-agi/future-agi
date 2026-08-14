import structlog

from ai_tools.registry import registry as tool_registry

logger = structlog.get_logger(__name__)

CORE_TOOLS = [
    "whoami",
    "list_workspaces",
    "search",
    "read_schema",
    "read_taxonomy",
    "search_docs",
]

# Common tools included in every mode — cross-cutting capabilities
COMMON_TOOLS = [
    "list_datasets",
    "get_dataset",
    "list_projects",
    "search_traces",
    "get_trace",
    "list_eval_templates",
    "list_evaluations",
    "list_prompt_templates",
    "get_prompt_template",
    "list_agents",
    "list_experiments",
    "get_cost_breakdown",
    "list_users",
    "get_user",
    "save_memory",
    "list_memories",
    "delete_memory",
]

# All categories for auto/general mode
ALL_CATEGORIES = [
    "context",
    "datasets",
    "annotations",
    "evaluations",
    "tracing",
    "experiments",
    "agents",
    "simulation",
    "prompts",
    "optimization",
    "users",
    "usage",
    "docs",
    "visualization",
]

MODES = {
    "general": {
        "categories": ALL_CATEGORIES,
        "description": "All tools available — auto mode",
    },
    "datasets": {
        "categories": ["context", "datasets", "annotations"],
        "description": "Dataset management",
    },
    "evaluations": {
        "categories": ["context", "evaluations", "web"],
        "description": "Evaluation analysis",
    },
    "tracing": {
        "categories": ["context", "tracing"],
        "description": "Trace debugging",
    },
    "experiments": {
        "categories": ["context", "experiments"],
        "description": "Experiment management",
    },
    "agents": {
        "categories": ["context", "agents", "simulation"],
        "description": "Agent testing",
    },
    "prompts": {
        "categories": ["context", "prompts", "optimization"],
        "description": "Prompt engineering",
    },
    "admin": {
        "categories": ["context", "users", "usage"],
        "description": "Administration",
    },
    "imagine": {
        "categories": ["context", "tracing", "visualization"],
        "description": "AI-powered trace visualization builder",
    },
}

PAGE_TO_MODE = {
    "datasets": "datasets",
    "data": "datasets",
    "evaluations": "evaluations",
    "evals": "evaluations",
    "tracing": "tracing",
    "observe": "tracing",
    "experiments": "experiments",
    "agents": "agents",
    "simulation": "agents",
    "prompts": "prompts",
    "develop": "prompts",
    "settings": "admin",
    "users": "admin",
    "imagine": "imagine",
}

KEYWORDS = {
    "datasets": ["dataset", "rows", "columns", "data", "synthetic"],
    "evaluations": [
        "eval",
        "evaluation",
        "score",
        "faithfulness",
        "hallucination",
        "eval template",
        "composite eval",
        "code eval",
        "agent eval",
        "ground truth",
    ],
    "tracing": ["trace", "span", "latency", "error rate", "debug"],
    "experiments": ["experiment", "a/b test", "variant"],
    "agents": ["agent", "simulation", "scenario", "persona"],
    "prompts": ["prompt engineering", "prompt version", "optimize prompt"],
    "admin": ["user", "api key", "cost", "billing", "member"],
}


def detect_mode(page_context, user_message):
    """Detect mode without an LLM call (fast, deterministic)."""
    # Forced modes — some pages always use a specific mode regardless of message content
    if page_context == "imagine":
        return "imagine"

    # Check if message mentions multiple domains → use general (cross-domain)
    message_lower = user_message.lower()
    matched_modes = set()
    for mode, keywords in KEYWORDS.items():
        if any(kw in message_lower for kw in keywords):
            matched_modes.add(mode)

    # If multiple domains mentioned, use general mode (has all key tools)
    if len(matched_modes) > 1:
        return "general"

    # Single domain match
    if len(matched_modes) == 1:
        return matched_modes.pop()

    # Fall back to page context
    if page_context and page_context in PAGE_TO_MODE:
        return PAGE_TO_MODE[page_context]

    return "general"


def filter_tools_for_message(tools, user_message, recent_tool_names=None, max_tools=40):
    """Filter tools to the most relevant for the user's message.

    Strategy:
    1. Always include core discovery tools
    2. Always include tools used in recent turns (continuity)
    3. Score all other tools by keyword relevance
    4. Cap at max_tools (default 40 — enough for complex workflows)
    """
    if len(tools) <= max_tools:
        return tools  # No filtering needed

    recent_tool_names = set(recent_tool_names or [])

    # Normalize message for matching — split on spaces, hyphens, underscores
    import re

    msg_words = set(re.split(r"[\s\-_,./]+", user_message.lower()))
    msg_words = {w for w in msg_words if len(w) > 2}  # Drop tiny words

    # Core tools: always included regardless of message
    FILTER_CORE_TOOLS = {
        "whoami",
        "search",
        "save_memory",
        "list_memories",
        "list_datasets",
        "list_projects",
        "search_traces",
        "list_experiments",
        "list_prompt_templates",
        "list_eval_templates",
        "get_cost_breakdown",
        "search_docs",
        "ask_docs",
        "list_agents",
        "list_annotation_labels",
        "list_annotation_queues",
        "list_alert_monitors",
        "list_knowledge_bases",
    }

    must_include = []
    candidates = []

    for tool in tools:
        if tool.name in recent_tool_names or tool.name in FILTER_CORE_TOOLS:
            must_include.append(tool)
        else:
            candidates.append(tool)

    # Score candidates by relevance to user message
    def relevance_score(tool):
        score = 0
        # Split tool name into words
        name_words = set(tool.name.lower().replace("_", " ").split())
        desc_lower = (tool.description or "").lower()

        # Name word overlap (strong signal)
        overlap = name_words & msg_words
        score += len(overlap) * 5

        # Partial name match (e.g., "eval" matches "evaluation")
        for mw in msg_words:
            for nw in name_words:
                if mw in nw or nw in mw:
                    score += 2

        # Description keyword match
        for word in msg_words:
            if word in desc_lower:
                score += 1

        # Boost tools in the same category as detected mode keywords
        tool_category = getattr(tool, "category", "")
        if tool_category:
            cat_words = set(tool_category.lower().split("_"))
            if cat_words & msg_words:
                score += 3

        return score

    scored = [(relevance_score(t), t) for t in candidates]
    scored.sort(key=lambda x: -x[0])

    # Take scored candidates to fill remaining slots
    remaining_slots = max_tools - len(must_include)
    top_candidates = [t for s, t in scored[:remaining_slots]]

    return must_include + top_candidates


def load_tools_for_mode(mode, active_skill=None):
    """Load tools for the detected mode. Returns deduplicated list."""
    tools = []
    seen = set()

    # Core tools (always loaded)
    for name in CORE_TOOLS:
        tool = tool_registry.get(name)
        if tool and name not in seen:
            tools.append(tool)
            seen.add(name)

    # Common tools (always loaded in every mode)
    for name in COMMON_TOOLS:
        tool = tool_registry.get(name)
        if tool and name not in seen:
            tools.append(tool)
            seen.add(name)

    # Mode-specific tools (by category)
    mode_config = MODES.get(mode, MODES["general"])
    for category in mode_config.get("categories", []):
        for tool in tool_registry.list_by_category(category):
            if tool.name not in seen:
                tools.append(tool)
                seen.add(tool.name)

    # Extra tools for this mode (explicit tool names, not categories)
    for name in mode_config.get("extra_tools", []):
        tool = tool_registry.get(name)
        if tool and name not in seen:
            tools.append(tool)
            seen.add(name)

    # Skill-specific tools
    if active_skill and hasattr(active_skill, "tool_names"):
        skill_tool_names = active_skill.tool_names or []
        for name in skill_tool_names:
            tool = tool_registry.get(name)
            if tool and name not in seen:
                tools.append(tool)
                seen.add(name)

    # Cap tool count to avoid exceeding LLM context limits
    MAX_TOOLS = 300
    if len(tools) > MAX_TOOLS:
        logger.warning("tool_count_exceeded", count=len(tools), max=MAX_TOOLS)
        tools = tools[:MAX_TOOLS]

    return tools
