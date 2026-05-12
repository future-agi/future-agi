"""
Brave Web Search tool for AI agents.

Uses the Brave Search API to search the web and return structured results.
Requires BRAVE_SEARCH_API_KEY environment variable.
Free tier: 2000 queries/month at https://brave.com/search/api/
"""

import os
from typing import Optional

import requests
import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)

BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveSearchInput(PydanticBaseModel):
    query: str = Field(
        default="",
        description="Search query to look up on the web",
    )
    count: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of results to return (default 5, max 20)",
    )
    freshness: Optional[str] = Field(
        default=None,
        description=(
            "Filter by content freshness. Options: "
            "'pd' (past day), 'pw' (past week), 'pm' (past month), "
            "'py' (past year). Leave empty for all time."
        ),
    )


@register_tool
class BraveSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the web using Brave Search to find current information, "
        "verify facts, look up recent events, or gather evidence. "
        "Returns titles, URLs, and descriptions of matching web pages."
    )
    category = "web"
    input_model = BraveSearchInput

    def execute(self, params: BraveSearchInput, context: ToolContext) -> ToolResult:
        query = (params.query or "").strip()
        if not query:
            return ToolResult(
                content=(
                    "Web search needs a query. Ask the user for the exact current fact "
                    "to verify, or use workspace/docs search if the question is about local data."
                ),
                data={"requires_query": True, "results": []},
            )

        api_key = os.getenv("BRAVE_SEARCH_API_KEY")
        if not api_key:
            return ToolResult(
                content=(
                    "Web search is unavailable in this local environment because "
                    "`BRAVE_SEARCH_API_KEY` is not configured. I did not invent web "
                    "results. Use docs, knowledge base, or trace data instead."
                ),
                data={"query": query, "results": [], "configuration_missing": True},
            )

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        }

        query_params = {
            "q": query,
            "count": params.count,
        }
        if params.freshness:
            query_params["freshness"] = params.freshness

        try:
            response = requests.get(
                BRAVE_API_URL,
                headers=headers,
                params=query_params,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.Timeout:
            return ToolResult.error(
                "Brave Search request timed out. Try again.",
                error_code="TIMEOUT",
            )
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response else "unknown"
            return ToolResult.error(
                f"Brave Search API error (HTTP {status})",
                error_code="API_ERROR",
            )
        except Exception as e:
            logger.error("brave_search_error", error=str(e))
            return ToolResult.error(
                f"Web search failed: {str(e)}",
                error_code="INTERNAL_ERROR",
            )

        # Parse results
        web_results = data.get("web", {}).get("results", [])

        if not web_results:
            return ToolResult(
                content=f"No web results found for: **{query}**",
                data={"query": query, "results": []},
            )

        # Format results for LLM consumption
        lines = [f"## Web Search Results for: {query}\n"]
        result_data = []

        for i, result in enumerate(web_results[: params.count], 1):
            title = result.get("title", "Untitled")
            url = result.get("url", "")
            description = result.get("description", "No description")
            # Clean HTML tags from description
            description = (
                description.replace("<strong>", "**")
                .replace("</strong>", "**")
                .replace("<em>", "_")
                .replace("</em>", "_")
            )

            lines.append(f"### {i}. {title}")
            lines.append(f"**URL:** {url}")
            lines.append(f"{description}\n")

            result_data.append(
                {
                    "title": title,
                    "url": url,
                    "description": description,
                }
            )

        return ToolResult(
            content="\n".join(lines),
            data={"query": query, "results": result_data},
        )
