"""navigate_to_page — agent-driven "take me there" navigation (Phase 4C).

Reconciles the navigation fork in PHASES.md 4C: the frontend has handled a
`navigate` WS event (useFalconSocket -> pendingNavigation -> DashboardLayout
router.push) since launch, but no backend ever emitted it. This tool makes
that path real: the model calls ``navigate_to_page`` with an in-app route,
the tool validates it against the SAME route-verified whitelist the prompt's
deep-link table uses (prompt_builder.py LINKS guideline), and the agent loop
emits the ``navigate`` event (ee/falcon_ai/agent.py, alongside widget_render).

Security: the path is server-validated against an explicit whitelist —
exact list pages plus detail templates whose ``<id>`` must be one safe path
segment. External URLs, scheme-relative URLs, query strings, fragments, and
path traversal are all rejected, so a jailbroken prompt cannot turn this
into an open redirect. ee/falcon_ai/tests/test_navigation.py keeps this
table in sync with the prompt's LINKS whitelist.
"""

import json
import re

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool

# List pages (no id) — must stay in sync with the prompt LINKS guideline
# (ee/falcon_ai/prompt_builder.py) — enforced by test_navigation.py.
LIST_ROUTES = (
    "/dashboard/develop",
    "/dashboard/observe",
    "/dashboard/evaluations",
    "/dashboard/tasks",
    "/dashboard/annotations",
    "/dashboard/alerts",
    "/dashboard/knowledge",
    "/dashboard/dashboards",
    "/dashboard/workbench",
    "/dashboard/simulate/scenarios",
)

# Detail pages — entity type -> route template. ``<id>`` is the entity's id
# (one safe path segment). Same table as the prompt's deep-link whitelist.
DETAIL_ROUTES = {
    "dataset": "/dashboard/develop/<id>",
    "experiment": "/dashboard/develop/experiment/<id>/data",
    "evaluation": "/dashboard/evaluations/<id>",
    "eval_task": "/dashboard/tasks/<id>",
    "trace_project": "/dashboard/observe/<id>",
    "dashboard": "/dashboard/dashboards/<id>",
    "prompt": "/dashboard/workbench/create/<id>",
    "knowledge_base": "/dashboard/knowledge/<id>",
    "agent_definition": "/dashboard/simulate/agent-definitions/<id>",
    "agent_version": "/dashboard/agents/playground/<id>",
    "scenario": "/dashboard/simulate/scenarios/<id>",
    "run_test": "/dashboard/simulate/test/<id>",
    "annotation_queue": "/dashboard/annotations/queues/<id>",
    "error_cluster": "/dashboard/error-feed/<id>",
}

# Friendly page names the model (or a user) may use instead of a raw path.
PAGE_ALIASES = {
    "alerts": "/dashboard/alerts",
    "datasets": "/dashboard/develop",
    "develop": "/dashboard/develop",
    "observe": "/dashboard/observe",
    "tracing": "/dashboard/observe",
    "traces": "/dashboard/observe",
    "evaluations": "/dashboard/evaluations",
    "evals": "/dashboard/evaluations",
    "tasks": "/dashboard/tasks",
    "annotations": "/dashboard/annotations",
    "knowledge": "/dashboard/knowledge",
    "dashboards": "/dashboard/dashboards",
    "workbench": "/dashboard/workbench",
    "prompts": "/dashboard/workbench",
    "scenarios": "/dashboard/simulate/scenarios",
    "simulate": "/dashboard/simulate/scenarios",
}

# One safe path segment: alphanumeric/underscore/hyphen, no dots, no slashes,
# capped at 64 chars — covers UUIDs and slug ids, blocks traversal payloads.
_ID_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"

_DETAIL_PATTERNS = [
    re.compile(
        "^"
        + "".join(
            _ID_SEGMENT if part == "<id>" else re.escape(part)
            for part in re.split(r"(<id>)", template)
        )
        + "$"
    )
    for template in DETAIL_ROUTES.values()
]


def validate_path(raw: str) -> str | None:
    """Return the canonical whitelisted path, or None if not allowed.

    Fails CLOSED: anything that is not exactly a whitelisted list page or a
    detail template with a single safe id segment is rejected — external
    URLs, scheme-relative ("//evil.com"), query strings, fragments,
    backslashes, whitespace, and ".." traversal all return None.
    """
    if not isinstance(raw, str):
        return None
    path = raw.strip()
    if not path:
        return None

    # Friendly alias ("alerts") -> list route.
    if not path.startswith("/"):
        alias = path.lower().strip().replace(" ", "_")
        path = PAGE_ALIASES.get(alias, path)
        if not path.startswith("/"):
            return None

    # Hard rejects before any matching.
    if "://" in path or path.startswith("//"):
        return None
    if any(ch in path for ch in ("?", "#", "\\")) or ".." in path:
        return None
    if any(ch.isspace() for ch in path):
        return None

    # Canonicalize trailing slash.
    if len(path) > 1:
        path = path.rstrip("/")

    if path in LIST_ROUTES:
        return path
    for pattern in _DETAIL_PATTERNS:
        if pattern.match(path):
            return path
    return None


class NavigateToPageInput(PydanticBaseModel):
    path: str = Field(
        description=(
            "The in-app route to open. Must be one of the route-verified "
            "paths: a list page ("
            + ", ".join(LIST_ROUTES)
            + ") or a detail page ("
            + " · ".join(f"{k} {v}" for k, v in DETAIL_ROUTES.items())
            + ") with <id> replaced by a real entity id from a previous tool "
            "result. A bare page name like 'alerts' also works. Anything "
            "outside this whitelist is rejected — never pass external URLs."
        )
    )


@register_tool
class NavigateToPageTool(BaseTool):
    name = "navigate_to_page"
    description = (
        "Navigate the user's browser to a page inside the FutureAGI app "
        "('take me there'). Use when the user asks to go to / open / show "
        "them a page — e.g. 'take me to the alerts page', 'open my datasets', "
        "'go to that trace project'. The UI performs the navigation "
        "immediately. Only whitelisted in-app routes are allowed (list pages "
        "like /dashboard/alerts, /dashboard/develop, /dashboard/observe, "
        "/dashboard/evaluations, /dashboard/dashboards … and entity detail "
        "pages with a real id). For detail pages, get the entity id from a "
        "list_*/get_* tool first — never invent ids. Do NOT navigate unless "
        "the user asked to go somewhere."
    )
    category = "context"
    execution_policy = "read"  # navigation has no data side effects
    input_model = NavigateToPageInput

    def execute(
        self, params: NavigateToPageInput, context: ToolContext
    ) -> ToolResult:
        path = validate_path(params.path)
        if path is None:
            return ToolResult.error(
                f"Path not allowed: {params.path!r}. Only route-verified "
                "in-app paths can be opened. Allowed list pages: "
                + ", ".join(LIST_ROUTES)
                + ". Allowed detail pages: "
                + " · ".join(f"{k} {v}" for k, v in DETAIL_ROUTES.items())
                + ". Replace <id> with a REAL id from a previous tool result.",
                error_code="VALIDATION_ERROR",
            )
        return ToolResult(
            content=json.dumps(
                {
                    "navigated": True,
                    "path": path,
                    "note": "The user's browser is navigating to this page now.",
                }
            ),
            data={"path": path},
        )
