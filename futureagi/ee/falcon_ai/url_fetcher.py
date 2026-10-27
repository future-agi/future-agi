"""Fetch and extract text content from URLs."""

import re
from html.parser import HTMLParser

import httpx
import structlog

logger = structlog.get_logger(__name__)

URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')
MAX_CONTENT_LENGTH = 50000


class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self._skip = False
        self._skip_tags = {"script", "style", "nav", "footer", "header"}

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._skip = True

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._skip = False
        if tag in ("p", "div", "h1", "h2", "h3", "h4", "li", "br", "tr"):
            self.text_parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.text_parts.append(data.strip())

    def get_text(self):
        return " ".join(p for p in self.text_parts if p).strip()


def extract_urls(text):
    """Extract URLs from text."""
    return URL_PATTERN.findall(text)


def fetch_url_content(url, timeout=15):
    """Fetch a URL and extract readable text content."""
    try:
        resp = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            max_redirects=5,
            headers={"User-Agent": "FalconAI/1.0"},
        )
        resp.raise_for_status()

        content_type = (
            resp.headers.get("content-type", "").split(";")[0].strip().lower()
        )

        # JSON
        if "json" in content_type:
            return f"[URL: {url}]\n```json\n{resp.text[:MAX_CONTENT_LENGTH]}\n```"

        # Plain text / markdown
        if "text/plain" in content_type or "text/markdown" in content_type:
            return f"[URL: {url}]\n{resp.text[:MAX_CONTENT_LENGTH]}"

        # Jupyter notebook (.ipynb)
        if url.endswith(".ipynb") or "nbviewer" in url:
            try:
                import json

                nb = json.loads(resp.text)
                cells_text = []
                for cell in nb.get("cells", []):
                    source = "".join(cell.get("source", []))
                    cell_type = cell.get("cell_type", "")
                    if cell_type == "markdown":
                        cells_text.append(source)
                    elif cell_type == "code":
                        cells_text.append(f"```python\n{source}\n```")
                return (
                    f"[Notebook: {url}]\n"
                    + "\n\n".join(cells_text)[:MAX_CONTENT_LENGTH]
                )
            except Exception:
                pass

        # HTML
        if "html" in content_type:
            extractor = HTMLTextExtractor()
            extractor.feed(resp.text)
            text = extractor.get_text()
            return f"[URL: {url}]\n{text[:MAX_CONTENT_LENGTH]}"

        # GitHub raw files
        if "raw.githubusercontent.com" in url:
            return f"[GitHub: {url}]\n```\n{resp.text[:MAX_CONTENT_LENGTH]}\n```"

        return f"[URL: {url}]\n{resp.text[:MAX_CONTENT_LENGTH]}"
    except Exception as e:
        logger.warning("url_fetch_failed", url=url, error=str(e))
        return f"[Failed to fetch URL: {url} — {str(e)}]"


def fetch_urls_from_message(message, max_urls=3):
    """Extract URLs from a message and fetch their content."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    urls = extract_urls(message)
    if not urls:
        return ""

    urls = urls[:max_urls]

    # Single URL — no need for thread pool overhead
    if len(urls) == 1:
        content = fetch_url_content(urls[0])
        return content if content else ""

    results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch_url_content, url): url for url in urls}
        for future in as_completed(futures, timeout=30):
            try:
                content = future.result()
                if content:
                    results.append(content)
            except Exception:
                pass

    return "\n\n".join(results)
