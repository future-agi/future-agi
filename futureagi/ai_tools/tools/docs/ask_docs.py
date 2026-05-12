import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool
from ai_tools.tools.docs._proxy import call_docs_agent

logger = structlog.get_logger(__name__)


class AskDocsInput(PydanticBaseModel):
    question: str = Field(
        default="How do I use Future AGI?",
        description="Question about Future AGI platform features, setup, or usage"
    )


@register_tool
class AskDocsTool(BaseTool):
    name = "ask_docs"
    description = (
        "Ask a question about the Future AGI platform and get a detailed "
        "answer from the documentation. Returns AI-generated answers with "
        "source references."
    )
    category = "docs"
    input_model = AskDocsInput

    def execute(self, params: AskDocsInput, context: ToolContext) -> ToolResult:
        try:
            result = call_docs_agent(
                "ask_docs",
                {"question": params.question},
            )
            if result is None:
                return ToolResult(
                    content=(
                        "Documentation assistant is currently unavailable. "
                        "Please try again later or visit https://docs.futureagi.com"
                    ),
                    is_error=True,
                    error_code="SERVICE_UNAVAILABLE",
                )
            return ToolResult(content=result, data={"source": "docs-agent"})
        except Exception as e:
            logger.error("ask_docs_error", error=str(e))
            return ToolResult(
                content=(
                    f"Documentation query failed: {e}. "
                    "Visit https://docs.futureagi.com for help."
                ),
                is_error=True,
            )
