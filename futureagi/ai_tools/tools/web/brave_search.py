"""Brave web search tool for AI agents."""

import html
import os
import re
from urllib.parse import urlparse

import requests
import structlog
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

from ai_tools.base import BaseTool, ToolContext, ToolResult
from ai_tools.registry import register_tool

logger = structlog.get_logger(__name__)

BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
BRAVE_LLM_CONTEXT_URL = "https://api.search.brave.com/res/v1/llm/context"
BRAVE_DEFAULT_COUNTRY = "US"
BRAVE_DEFAULT_SEARCH_LANG = "en"
MAX_RESULTS_FOR_LLM = 3
MAX_SNIPPET_CHARS = 180
MAX_EXTRA_SNIPPET_CHARS = 120
RECENT_QUERY_TERMS = (
    "latest",
    "current",
    "today",
    "newest",
    "recent",
    "now",
    "this year",
    "announced",
    "released",
)
QUERY_STOPWORDS = {
    "about",
    "after",
    "before",
    "best",
    "check",
    "current",
    "does",
    "find",
    "for",
    "from",
    "has",
    "have",
    "into",
    "latest",
    "look",
    "model",
    "new",
    "newest",
    "now",
    "official",
    "recent",
    "released",
    "search",
    "the",
    "today",
    "verify",
    "what",
    "when",
    "where",
    "which",
    "with",
}
DOMAIN_STOPWORDS = {
    "api",
    "app",
    "blog",
    "com",
    "docs",
    "help",
    "io",
    "net",
    "news",
    "org",
    "support",
    "www",
}


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"</?(strong|em|mark|b|i)>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _infer_freshness(query: str, freshness: str | None) -> str | None:
    if freshness:
        return freshness
    lowered = query.lower()
    if any(term in lowered for term in RECENT_QUERY_TERMS):
        return "py"
    return None


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").removeprefix("www.")
    except Exception:
        return ""


def _tokens(value: str) -> set[str]:
    tokens = set()
    for token in re.findall(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", value.lower()):
        if len(token) < 3 and not any(ch.isdigit() for ch in token):
            continue
        if token in QUERY_STOPWORDS:
            continue
        tokens.add(token)
        if len(token) > 4 and token.endswith("s"):
            tokens.add(token[:-1])
    return tokens


def _source_type(query: str, url: str, title: str) -> str:
    query_tokens = _tokens(query)
    host = _hostname(url)
    host_tokens = {
        token
        for token in re.split(r"[\W_]+", host.lower())
        if len(token) >= 3 and token not in DOMAIN_STOPWORDS
    }

    if query_tokens & host_tokens:
        return "primary"
    return "secondary_or_unknown"


def _sort_key(item: dict) -> tuple[int, int]:
    priority = {
        "primary": 0,
        "secondary_or_unknown": 1,
    }
    return priority.get(item["source_type"], 3), item["rank"]


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
    freshness: str | None = Field(
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
        "Search the web using Brave Search to verify facts, look up recent events, "
        "or gather evidence. Returns source URLs, source dates when available, "
        "and extracted grounding snippets optimized for LLM use. For latest/current "
        "claims, pass a freshness filter such as 'pm' or 'py'."
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
        params.query = query

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
            "Cache-Control": "no-cache",
            "X-Subscription-Token": api_key,
        }

        freshness = _infer_freshness(query, params.freshness)
        use_llm_context = os.getenv("BRAVE_SEARCH_USE_LLM_CONTEXT", "").lower() in {
            "1",
            "true",
            "yes",
        }
        if use_llm_context:
            context_result = self._execute_llm_context(params, headers, freshness)
            if context_result is not None:
                return context_result

        return self._execute_web_search(params, headers, freshness)

    def _request_json(self, url: str, headers: dict, query_params: dict) -> dict:
        response = requests.get(
            url,
            headers=headers,
            params=query_params,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def _execute_llm_context(
        self,
        params: BraveSearchInput,
        headers: dict,
        freshness: str | None,
    ) -> ToolResult | None:
        query_params = {
            "q": params.query,
            "count": params.count,
            "country": BRAVE_DEFAULT_COUNTRY,
            "search_lang": BRAVE_DEFAULT_SEARCH_LANG,
            "maximum_number_of_urls": min(params.count, MAX_RESULTS_FOR_LLM),
            "maximum_number_of_tokens": 1800,
            "maximum_number_of_snippets": min(params.count * 2, 10),
            "maximum_number_of_snippets_per_url": 1,
            "context_threshold_mode": "balanced",
        }
        if freshness:
            query_params["freshness"] = freshness

        try:
            data = self._request_json(BRAVE_LLM_CONTEXT_URL, headers, query_params)
        except requests.exceptions.Timeout:
            return ToolResult.error(
                "Brave LLM Context request timed out. Try again.",
                error_code="TIMEOUT",
            )
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response else "unknown"
            logger.warning(
                "brave_llm_context_unavailable",
                status=status,
                query=params.query,
            )
            return None
        except Exception as exc:
            logger.warning(
                "brave_llm_context_error", error=str(exc), query=params.query
            )
            return None

        generic_results = data.get("grounding", {}).get("generic", []) or []
        sources = data.get("sources", {}) or {}

        if not generic_results:
            return None

        lines = [f"## Web Search Results for: {params.query}\n"]
        result_data = []
        ranked_results = []

        for rank, result in enumerate(generic_results[: params.count], 1):
            url = result.get("url", "")
            source_meta = sources.get(url, {}) if url else {}
            title = _clean_text(
                result.get("title") or source_meta.get("title") or "Untitled"
            )
            hostname = source_meta.get("hostname") or ""
            age = source_meta.get("age")
            snippets = [
                _truncate(_clean_text(snippet), MAX_SNIPPET_CHARS)
                for snippet in (result.get("snippets") or [])[:1]
                if _clean_text(snippet)
            ]
            ranked_results.append(
                {
                    "rank": rank,
                    "title": title,
                    "url": url,
                    "hostname": hostname or _hostname(url),
                    "age": age,
                    "source_type": _source_type(params.query, url, title),
                    "snippets": snippets,
                }
            )

        for index, result in enumerate(
            sorted(ranked_results, key=_sort_key)[:MAX_RESULTS_FOR_LLM], 1
        ):
            age = result["age"]
            lines.append(f"### {index}. {result['title']}")
            lines.append(f"Source type: {result['source_type']}")
            lines.append(f"URL: {result['url']}")
            if age:
                if isinstance(age, list):
                    lines.append(f"Date: {', '.join(str(item) for item in age[:2])}")
                else:
                    lines.append(f"Date: {age}")
            for snippet in result["snippets"]:
                lines.append(f"Snippet: {snippet}")
            lines.append("")

            result_data.append(
                {
                    "title": result["title"],
                    "url": result["url"],
                    "hostname": result["hostname"],
                    "age": result["age"],
                    "source_type": result["source_type"],
                    "snippets": result["snippets"],
                }
            )

        return ToolResult(
            content="\n".join(lines),
            data={
                "query": params.query,
                "source": "brave_llm_context",
                "freshness": freshness,
                "results": result_data,
            },
        )

    def _execute_web_search(
        self,
        params: BraveSearchInput,
        headers: dict,
        freshness: str | None,
    ) -> ToolResult:
        query_params = {
            "q": params.query,
            "count": params.count,
            "country": BRAVE_DEFAULT_COUNTRY,
            "search_lang": BRAVE_DEFAULT_SEARCH_LANG,
            "safesearch": "moderate",
            "spellcheck": "1",
            "text_decorations": "0",
            "extra_snippets": "1",
            "include_fetch_metadata": "1",
            "result_filter": "web",
        }
        if freshness:
            query_params["freshness"] = freshness

        try:
            data = self._request_json(BRAVE_WEB_SEARCH_URL, headers, query_params)
        except requests.exceptions.Timeout:
            return ToolResult.error(
                "Brave Search request timed out. Try again.",
                error_code="TIMEOUT",
            )
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response else "unknown"
            return ToolResult.error(
                f"Brave Search API error (HTTP {status})",
                error_code="API_ERROR",
            )
        except Exception as exc:
            logger.error("brave_search_error", error=str(exc))
            return ToolResult.error(
                f"Web search failed: {str(exc)}",
                error_code="INTERNAL_ERROR",
            )

        web_results = data.get("web", {}).get("results", [])

        if not web_results:
            return ToolResult(
                content=f"No web results found for: **{params.query}**",
                data={"query": params.query, "freshness": freshness, "results": []},
            )

        lines = [f"## Web Search Results for: {params.query}\n"]
        result_data = []
        ranked_results = []

        for rank, result in enumerate(web_results[: params.count], 1):
            title = _clean_text(result.get("title") or "Untitled")
            url = result.get("url", "")
            description = _truncate(
                _clean_text(result.get("description") or "No description"),
                MAX_SNIPPET_CHARS,
            )
            age = result.get("age") or result.get("page_age")
            extra_snippets = [
                _truncate(_clean_text(snippet), MAX_EXTRA_SNIPPET_CHARS)
                for snippet in (result.get("extra_snippets") or [])[:1]
                if _clean_text(snippet)
            ]
            ranked_results.append(
                {
                    "rank": rank,
                    "title": title,
                    "url": url,
                    "hostname": _hostname(url),
                    "age": age,
                    "source_type": _source_type(params.query, url, title),
                    "description": description,
                    "extra_snippets": extra_snippets,
                }
            )

        for index, result in enumerate(
            sorted(ranked_results, key=_sort_key)[:MAX_RESULTS_FOR_LLM], 1
        ):
            lines.append(f"### {index}. {result['title']}")
            lines.append(f"Source type: {result['source_type']}")
            lines.append(f"URL: {result['url']}")
            if result["age"]:
                lines.append(f"Date: {result['age']}")
            lines.append(f"Snippet: {result['description']}")
            lines.append("")

            result_data.append(
                {
                    "title": result["title"],
                    "url": result["url"],
                    "hostname": result["hostname"],
                    "age": result["age"],
                    "source_type": result["source_type"],
                    "description": result["description"],
                    "extra_snippets": result["extra_snippets"],
                }
            )

        return ToolResult(
            content="\n".join(lines),
            data={
                "query": params.query,
                "source": "brave_web_search",
                "freshness": freshness,
                "results": result_data,
            },
        )
