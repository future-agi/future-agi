import json

import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool
from ai_tools.tools.docs._proxy import call_docs_agent

logger = structlog.get_logger(__name__)

DOCS_BASE = "https://docs.futureagi.com"


class SearchDocsInput(PydanticBaseModel):
    query: str = Field(
        default="Future AGI",
        description="Search query for the Future AGI documentation",
    )
    limit: int = Field(default=5, description="Maximum number of results to return")


def _format_results(raw: str) -> str:
    """Convert raw JSON results into clean markdown for the LLM."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Already markdown or plain text
        return raw

    results = data.get("results", []) if isinstance(data, dict) else []
    if not results:
        return "No documentation pages found for this query."

    lines = [f"## Documentation Results ({len(results)} pages)\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        path = r.get("path", "")
        heading = r.get("heading", "")
        snippet = r.get("snippet", "")
        url = (
            f"{DOCS_BASE}/{path.replace('.mdx', '').replace('docs/', '')}"
            if path
            else ""
        )

        lines.append(f"### {i}. {title}")
        if heading and heading != title:
            lines.append(f"**Section:** {heading}")
        if url:
            lines.append(f"**Link:** {url}")
        if snippet:
            # Clean up snippet — remove excess newlines
            clean = snippet.replace("\\n", "\n").strip()
            # Take first ~300 chars
            if len(clean) > 300:
                clean = clean[:297] + "..."
            lines.append(f"\n{clean}\n")

    return "\n".join(lines)


@register_tool
class SearchDocsTool(BaseTool):
    name = "search_docs"
    description = (
        "Search the Future AGI documentation for answers about platform "
        "features, setup guides, API references, and best practices. "
        "Use this when users ask 'how do I...', 'what is...', or any "
        "product question."
    )
    category = "docs"
    input_model = SearchDocsInput

    def execute(self, params: SearchDocsInput, context: ToolContext) -> ToolResult:
        try:
            result = call_docs_agent(
                "search_docs",
                {"query": params.query, "limit": params.limit},
            )
            if result is None:
                return ToolResult(
                    content=(
                        "Documentation search is currently unavailable. "
                        "Please try again later or visit https://docs.futureagi.com"
                    ),
                    is_error=True,
                    error_code="SERVICE_UNAVAILABLE",
                )
            formatted = _format_results(result)
            return ToolResult(content=formatted, data={"source": "docs-agent"})
        except Exception as e:
            logger.error("search_docs_error", error=str(e))
            return ToolResult(
                content=(
                    f"Documentation search failed: {e}. "
                    "Visit https://docs.futureagi.com for help."
                ),
                is_error=True,
            )
