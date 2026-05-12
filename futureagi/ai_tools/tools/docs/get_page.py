import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool
from ai_tools.tools.docs._proxy import call_docs_agent

logger = structlog.get_logger(__name__)


class GetPageInput(PydanticBaseModel):
    path: str = Field(
        default="",
        description=(
            "Documentation page path, e.g., 'tracing/auto-overview' "
            "or 'dataset/overview'. Omit to list likely documentation pages."
        )
    )


@register_tool
class GetPageTool(BaseTool):
    name = "get_docs_page"
    description = (
        "Get a specific documentation page by path. Use when you know "
        "the exact page the user needs."
    )
    category = "docs"
    input_model = GetPageInput

    def execute(self, params: GetPageInput, context: ToolContext) -> ToolResult:
        if not params.path:
            result = call_docs_agent("search_docs", {"query": "testing falcon", "limit": 5})
            if result:
                return ToolResult(
                    content=(
                        "No docs path was provided. Here are matching documentation "
                        "pages; call `get_docs_page` again with one of their paths.\n\n"
                        f"{result}"
                    ),
                    data={"source": "docs-agent", "requires_path": True},
                )
            return ToolResult(
                content=(
                    "No docs path was provided and docs search is unavailable. "
                    "Try `search_docs` with a topic such as `testing`, `datasets`, "
                    "or `tracing`."
                ),
                data={"source": "unavailable", "requires_path": True},
            )

        try:
            result = call_docs_agent(
                "get_page",
                {"path": params.path},
            )
            if result is None:
                return ToolResult(
                    content=(
                        "Documentation service is currently unavailable. "
                        "No matching local docs page was found. "
                        "Try `search_docs` or visit https://docs.futureagi.com."
                    ),
                    data={"source": "unavailable", "path": params.path},
                )
            return ToolResult(content=result, data={"source": "docs-agent"})
        except Exception as e:
            logger.error("get_docs_page_error", error=str(e))
            return ToolResult(
                content=(
                    f"Failed to fetch documentation page: {e}. "
                    "Try `search_docs` or visit https://docs.futureagi.com for help."
                ),
                data={"source": "unavailable", "path": params.path, "error": str(e)},
            )
