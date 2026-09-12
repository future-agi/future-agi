"""MCP Server constants."""

TOOL_GROUPS = {
    "context": {
        "name": "Context & Navigation",
        "description": "User profile, workspace management, schema discovery, search",
    },
    "evaluations": {
        "name": "Evaluations",
        "description": "Run, compare, and analyze LLM evaluations; manage eval templates and composite evals",
    },
    "datasets": {
        "name": "Datasets & Knowledge Bases",
        "description": "Create, manage, query datasets; manage knowledge bases for RAG",
    },
    "annotations": {
        "name": "Annotations",
        "description": "Human annotation tasks, labels, and review workflows",
    },
    "optimization": {
        "name": "Prompt Optimization",
        "description": "Optimize prompts using algorithms (random search, bayesian, metaprompt, etc.)",
    },
    "observability": {
        "name": "Observability / Traces",
        "description": "Search traces, projects, error analysis, alerts, and annotations",
    },
    "error_feed": {
        "name": "Error Feed",
        "description": (
            "Browse the error feed: list and drill into error clusters, "
            "analyze project-wide traces to populate the feed, and submit "
            "findings/scores that feed into it."
        ),
    },
    "experiments": {
        "name": "Experiments",
        "description": "Create and analyze A/B experiments with variant comparison",
    },
    "agents": {
        "name": "Agents & Simulation",
        "description": "Browse agents, scenarios and test executions, and run saved simulation tests",
    },
    "simulation": {
        "name": "Simulation",
        "description": "Agent definitions, versions, personas, scenarios, simulator agents, test runs, and call analysis",
    },
    "prompts": {
        "name": "Prompt Workbench",
        "description": "Manage prompt templates, versions, labels, folders, simulations, and evaluations",
    },
    "users": {
        "name": "Users & Workspaces",
        "description": "User management, workspace operations, organization settings, and API key management",
    },
    "usage": {
        "name": "Usage & Costs",
        "description": "Cost analytics and billing information",
    },
    "gateway": {
        "name": "AI Gateway",
        "description": "Gateway configuration, request logs, and analytics",
    },
    "dashboards": {
        "name": "Dashboards",
        "description": "Dashboards, widgets, metric discovery, and chart data queries",
    },
}

DEFAULT_TOOL_GROUPS = [
    "context",
    "evaluations",
    "datasets",
    "annotations",
    "optimization",
    "observability",
    "error_feed",
    "experiments",
    "agents",
    "simulation",
    "prompts",
    "users",
    "usage",
    "gateway",
    "dashboards",
]

RATE_LIMITS = {
    "free": {"per_minute": 200, "per_day": 5000, "concurrent_sessions": 5},
    "pro": {"per_minute": 100, "per_day": 10_000, "concurrent_sessions": 5},
    "enterprise": {"per_minute": 500, "per_day": 100_000, "concurrent_sessions": None},
}

SESSION_STATUS_CHOICES = [
    ("active", "Active"),
    ("idle", "Idle"),
    ("disconnected", "Disconnected"),
    ("revoked", "Revoked"),
]

TRANSPORT_CHOICES = [
    ("streamable_http", "Streamable HTTP"),
    ("sse", "SSE"),  # Legacy
    ("stdio", "Stdio"),
]

CONNECTION_MODE_CHOICES = [
    ("remote", "Remote"),
    ("stdio", "Stdio"),
]

RESPONSE_STATUS_CHOICES = [
    ("success", "Success"),
    ("error", "Error"),
    ("rate_limited", "Rate Limited"),
]
