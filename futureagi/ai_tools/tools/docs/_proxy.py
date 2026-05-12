"""Shared proxy for calling the docs-agent MCP server.

Uses a session pool to amortize the MCP initialize handshake across calls.
Each session is a lightweight string ID — the pool just avoids re-initializing
on every single tool call while staying safe for concurrent use.
"""

import json
import os
import re
from collections import deque
from pathlib import Path

import httpx
import structlog

logger = structlog.get_logger(__name__)

DOCS_AGENT_URL = os.environ.get("DOCS_AGENT_URL", "http://docs-agent:3002/mcp").strip()
DOCS_AGENT_API_KEY = os.environ.get("DOCS_AGENT_API_KEY", "")
DOCS_AGENT_ORIGIN = os.environ.get("DOCS_AGENT_ORIGIN", "https://docs.futureagi.com").strip()
DOCS_AGENT_MODE = os.environ.get("DOCS_AGENT_MODE", "").strip().lower()
LOCAL_DOCS_DIR = os.environ.get("FALCON_LOCAL_DOCS_DIR", "/app/internal-docs")
PRODUCT_DOCS_HOST = "docs-api.futureagi.com"


class MCPSessionPool:
    """Pool of MCP session IDs. Thread/async safe via deque (atomic append/pop)."""

    def __init__(self, max_size=5):
        self._pool = deque(maxlen=max_size)

    def acquire(self):
        """Get a session ID from the pool, or None if empty."""
        try:
            return self._pool.pop()
        except IndexError:
            return None

    def release(self, session_id):
        """Return a session ID to the pool."""
        if session_id:
            self._pool.append(session_id)

    def discard(self, session_id):
        """Drop a session (e.g. expired). Don't return to pool."""
        pass  # nothing to do — it's just not returned


_pool = MCPSessionPool()


def _headers(session_id=None):
    h = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if DOCS_AGENT_ORIGIN:
        h["Origin"] = DOCS_AGENT_ORIGIN
    if DOCS_AGENT_API_KEY:
        h["X-API-Key"] = DOCS_AGENT_API_KEY
    if session_id:
        h["mcp-session-id"] = session_id
    return h


def _initialize(client):
    """Create a new MCP session. Returns session ID."""
    resp = client.post(
        DOCS_AGENT_URL,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "falcon-ai", "version": "1.0.0"},
            },
        },
        headers=_headers(),
    )
    resp.raise_for_status()
    sid = resp.headers.get("mcp-session-id", "")

    # Send initialized notification
    client.post(
        DOCS_AGENT_URL,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=_headers(sid),
    )
    return sid


def _parse_response(resp):
    """Extract text content from an MCP response (JSON or SSE)."""
    content_type = resp.headers.get("content-type", "")

    if "text/event-stream" in content_type:
        for line in resp.text.split("\n"):
            if line.startswith("data: "):
                data = json.loads(line[6:])
                text_parts = [
                    b["text"]
                    for b in data.get("result", {}).get("content", [])
                    if b.get("type") == "text"
                ]
                if text_parts:
                    return "\n".join(text_parts)
        return None

    data = resp.json()
    result = data.get("result", {})
    text_parts = [
        b["text"] for b in result.get("content", []) if b.get("type") == "text"
    ]
    return "\n".join(text_parts) if text_parts else json.dumps(result)


def _local_doc_files():
    docs_dir = Path(LOCAL_DOCS_DIR)
    if not docs_dir.exists():
        return []
    return sorted(
        path
        for path in docs_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".mdx"}
    )


def _score_local_doc(path: Path, text: str, query_words: set[str]) -> int:
    searchable_path = str(path.relative_to(LOCAL_DOCS_DIR)).lower()
    title = path.stem.lower().replace("-", " ").replace("_", " ")
    lowered = text.lower()
    score = 0
    for word in query_words:
        if word in searchable_path:
            score += 8
        if word in title:
            score += 6
        score += min(lowered.count(word), 5)
    return score


def _snippet(text: str, query_words: set[str], max_chars: int = 420) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""
    lowered = compact.lower()
    first_hit = min(
        [lowered.find(word) for word in query_words if lowered.find(word) >= 0]
        or [0]
    )
    start = max(first_hit - 120, 0)
    snippet = compact[start : start + max_chars]
    return ("..." if start else "") + snippet + (
        "..." if start + max_chars < len(compact) else ""
    )


def _search_local_docs(arguments: dict) -> str | None:
    query = str(arguments.get("query") or arguments.get("question") or "").strip()
    limit = int(arguments.get("limit") or 5)
    query_words = {
        word
        for word in re.split(r"[\s\-_,./:]+", query.lower())
        if len(word) > 2
    }
    if not query_words:
        query_words = {"testing", "falcon"}

    matches = []
    for path in _local_doc_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        score = _score_local_doc(path, text, query_words)
        if score:
            rel_path = str(path.relative_to(LOCAL_DOCS_DIR))
            matches.append(
                {
                    "score": score,
                    "title": path.stem.replace("-", " ").replace("_", " ").title(),
                    "path": rel_path,
                    "heading": rel_path,
                    "snippet": _snippet(text, query_words),
                }
            )

    matches.sort(key=lambda item: (-item["score"], item["path"]))
    return json.dumps({"results": matches[:limit]}) if matches else None


def _ask_local_docs(arguments: dict) -> str | None:
    raw = _search_local_docs(
        {
            "query": arguments.get("question") or arguments.get("query") or "",
            "limit": 5,
        }
    )
    if not raw:
        return None
    results = json.loads(raw).get("results", [])
    if not results:
        return None
    lines = [
        "The docs-agent service is unavailable, so this answer is based on local internal docs.",
        "",
    ]
    for item in results:
        lines.append(f"### {item['title']}")
        lines.append(f"Source: `{item['path']}`")
        if item.get("snippet"):
            lines.append(item["snippet"])
        lines.append("")
    return "\n".join(lines).strip()


def _get_local_doc(arguments: dict) -> str | None:
    page_path = str(arguments.get("path") or "").strip().lstrip("/")
    if not page_path:
        return None

    docs_dir = Path(LOCAL_DOCS_DIR)
    if not docs_dir.exists():
        return None

    candidates = []
    raw_path = Path(page_path)
    if raw_path.suffix.lower() in {".md", ".mdx"}:
        candidates.append(docs_dir / raw_path)
    else:
        candidates.extend(
            [
                docs_dir / f"{page_path}.md",
                docs_dir / f"{page_path}.mdx",
                docs_dir / page_path / "README.md",
                docs_dir / page_path / "index.md",
                docs_dir / page_path / "README.mdx",
                docs_dir / page_path / "index.mdx",
            ]
        )

    try:
        base_dir = docs_dir.resolve()
    except OSError:
        return None

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved != base_dir and base_dir not in resolved.parents:
            continue
        if not resolved.is_file():
            continue
        try:
            text = resolved.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel_path = str(resolved.relative_to(base_dir))
        if len(text) > 12000:
            text = text[:12000].rstrip() + "\n\n[Truncated local docs page.]"
        return f"Source: `{rel_path}`\n\n{text}"

    raw_results = _search_local_docs({"query": page_path, "limit": 5})
    if not raw_results:
        return None
    results = json.loads(raw_results).get("results", [])
    if not results:
        return None
    lines = [
        "Exact local docs page was not found. Closest local docs matches:",
        "",
    ]
    for item in results:
        lines.append(f"- `{item['path']}`: {item.get('snippet', '')}")
    return "\n".join(lines)


def call_local_docs(tool_name: str, arguments: dict) -> str | None:
    if tool_name == "search_docs":
        return _search_local_docs(arguments)
    if tool_name == "ask_docs":
        return _ask_local_docs(arguments)
    if tool_name == "get_page":
        return _get_local_doc(arguments)
    return None


_product_session_token: str | None = None


def _has_http_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _is_product_docs_api(url: str) -> bool:
    if DOCS_AGENT_MODE == "rest":
        return True
    if PRODUCT_DOCS_HOST in url:
        return True
    return not url.rstrip("/").endswith("/mcp")


def _product_base_url() -> str:
    base = DOCS_AGENT_URL.rstrip("/")
    if base.endswith("/mcp"):
        base = base[: -len("/mcp")]
    return base


def _product_url(path: str) -> str:
    return f"{_product_base_url()}/{path.lstrip('/')}"


def _product_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if DOCS_AGENT_ORIGIN:
        headers["Origin"] = DOCS_AGENT_ORIGIN
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _success_data(resp: httpx.Response) -> dict:
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("success") is False:
        error = data.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(message or "docs agent request failed")
    payload = data.get("data") if isinstance(data, dict) else data
    return payload if isinstance(payload, dict) else {"value": payload}


def _product_session(client: httpx.Client, force_refresh: bool = False) -> str:
    global _product_session_token
    if _product_session_token and not force_refresh:
        return _product_session_token

    resp = client.post(
        _product_url("/session/init"),
        json={},
        headers=_product_headers(),
    )
    data = _success_data(resp)
    token = data.get("token")
    if not token:
        raise RuntimeError("docs agent did not return a session token")
    _product_session_token = token
    return token


def _post_product_api(
    client: httpx.Client,
    path: str,
    payload: dict,
    *,
    retry_on_auth: bool = True,
) -> dict:
    token = _product_session(client)
    resp = client.post(
        _product_url(path),
        json=payload,
        headers=_product_headers(token),
    )
    if retry_on_auth and resp.status_code in {401, 403}:
        token = _product_session(client, force_refresh=True)
        resp = client.post(
            _product_url(path),
            json=payload,
            headers=_product_headers(token),
        )
    return _success_data(resp)


def _format_product_search(data: dict) -> str | None:
    raw_results = data.get("results") or []
    if not isinstance(raw_results, list):
        return None

    results = []
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        snippet = (
            result.get("snippet")
            or result.get("content")
            or result.get("summary")
            or ""
        )
        results.append(
            {
                "title": result.get("title") or result.get("heading") or "Untitled",
                "path": result.get("path") or "",
                "heading": result.get("heading") or result.get("title") or "",
                "snippet": snippet,
                "sourceType": result.get("sourceType"),
                "score": result.get("score"),
            }
        )

    return json.dumps({"results": results, "query": data.get("query") or {}})


def _call_product_search(client: httpx.Client, arguments: dict) -> str | None:
    query = str(arguments.get("query") or arguments.get("question") or "").strip()
    if not query:
        return call_local_docs("search_docs", arguments)
    try:
        limit = int(arguments.get("limit") or arguments.get("topK") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 20))

    data = _post_product_api(
        client,
        "/search",
        {
            "query": query,
            "topK": limit,
            "sourceTypes": ["docs"],
            "includeContext": False,
        },
    )
    result = _format_product_search(data)
    if result and json.loads(result).get("results"):
        return result
    return call_local_docs("search_docs", arguments) or result


def _format_product_chat(data: dict) -> str | None:
    if isinstance(data.get("answer"), str):
        answer = data["answer"].strip()
    elif isinstance(data.get("response"), str):
        answer = data["response"].strip()
    elif isinstance(data.get("message"), str):
        answer = data["message"].strip()
    elif isinstance(data.get("content"), str):
        answer = data["content"].strip()
    else:
        value = data.get("value")
        answer = value.strip() if isinstance(value, str) else ""

    sources = data.get("sources") or data.get("citations") or []
    if isinstance(sources, list) and sources:
        lines = [answer, "", "Sources:"] if answer else ["Sources:"]
        for source in sources[:5]:
            if isinstance(source, dict):
                title = source.get("title") or source.get("path") or "Source"
                path = source.get("path") or source.get("url") or ""
                lines.append(f"- {title}: {path}" if path else f"- {title}")
            else:
                lines.append(f"- {source}")
        return "\n".join(lines).strip()

    if answer:
        return answer
    return json.dumps(data)


def _call_product_docs_api(tool_name: str, arguments: dict) -> str | None:
    with httpx.Client(timeout=30.0) as client:
        if tool_name == "search_docs":
            return _call_product_search(client, arguments)
        if tool_name == "ask_docs":
            question = str(
                arguments.get("question") or arguments.get("query") or ""
            ).strip()
            if not question:
                return call_local_docs(tool_name, arguments)
            data = _post_product_api(
                client,
                "/chat",
                {
                    "query": question,
                    "stream": False,
                    "includeMetadata": True,
                    "includeSuggestions": False,
                },
            )
            return _format_product_chat(data) or call_local_docs(tool_name, arguments)
        if tool_name == "get_page":
            local = call_local_docs(tool_name, arguments)
            if local:
                return local
            path = str(arguments.get("path") or "").strip()
            if path:
                return _call_product_search(client, {"query": path, "limit": 5})
    return call_local_docs(tool_name, arguments)


def call_docs_agent(tool_name: str, arguments: dict) -> str | None:
    """Call a tool on the docs-agent MCP server.

    Acquires a session from the pool (or creates one), makes the call,
    and returns the session to the pool for reuse.
    """
    if not _has_http_url(DOCS_AGENT_URL):
        logger.warning("docs_agent_invalid_url", url=DOCS_AGENT_URL)
        return call_local_docs(tool_name, arguments)

    if _is_product_docs_api(DOCS_AGENT_URL):
        try:
            return _call_product_docs_api(tool_name, arguments)
        except Exception as e:
            logger.error("docs_agent_product_call_failed", error=str(e))
            return call_local_docs(tool_name, arguments)

    session_id = _pool.acquire()

    try:
        with httpx.Client(timeout=30.0) as client:
            # Initialize if no pooled session available
            if not session_id:
                session_id = _initialize(client)

            resp = client.post(
                DOCS_AGENT_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
                headers=_headers(session_id),
            )

            # Session expired — discard, create fresh, retry
            if resp.status_code == 400:
                _pool.discard(session_id)
                session_id = _initialize(client)

                resp = client.post(
                    DOCS_AGENT_URL,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments},
                    },
                    headers=_headers(session_id),
                )

            resp.raise_for_status()
            result = _parse_response(resp)

            # Return session to pool for reuse
            _pool.release(session_id)
            return result

    except httpx.ConnectError:
        logger.warning("docs_agent_unavailable", url=DOCS_AGENT_URL)
        return call_local_docs(tool_name, arguments)
    except Exception as e:
        logger.error("docs_agent_call_failed", error=str(e))
        # Don't return broken session to pool
        _pool.discard(session_id)
        return call_local_docs(tool_name, arguments)
