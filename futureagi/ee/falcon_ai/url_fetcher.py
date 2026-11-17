"""Fetch and extract text content from URLs."""

import asyncio
import ipaddress
import re
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
import structlog

from tfc.utils.ssrf_guard import safe_fetch

logger = structlog.get_logger(__name__)

URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')
MAX_CONTENT_LENGTH = 50000
# The most of a page that is read. A larger page counts as not fetched.
MAX_RESPONSE_BYTES = 1024 * 1024
# Endings that private networks use for the names of their own services.
NON_PUBLIC_NAME_SUFFIXES = (
    ".svc",
    ".cluster.local",
    ".internal",
    ".local",
    ".localhost",
)
MAX_LINKS_PER_MESSAGE = 3
# The longest a message waits for its links. After that it goes on without them.
LINK_FETCH_TIME_LIMIT_SECONDS = 20
# Links are fetched on a few threads of their own, so a slow page holds up
# nothing else.
_LINK_FETCH_POOL = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="falcon-link-fetch"
)


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


def _could_not_fetch(url):
    return f"[This link could not be fetched: {url}]"


def _names_a_public_host(url):
    """Whether a link's host is an address or a name a public page can have.

    An address is left to the address check. A name needs a dot and an ending
    that private networks do not use for their own services.
    """
    host = (urlparse(url).hostname or "").rstrip(".").lower()
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    try:
        # The form the resolver looks up: every kind of dot becomes ".".
        host = host.encode("idna").decode("ascii").rstrip(".")
    except UnicodeError:
        return False
    return "." in host and not host.endswith(NON_PUBLIC_NAME_SUFFIXES)


def fetch_url_content(url, timeout=15):
    """Fetch a URL and extract readable text content."""
    try:
        # A name no public page can have is not looked up at all.
        if not _names_a_public_host(url):
            raise ValueError("the link's host is not a public name")
        # Public addresses only: the address check connects to the address it
        # checked and checks every redirect again before following it.
        resp = safe_fetch(
            url,
            method="GET",
            timeout=timeout,
            total_timeout=timeout,
            max_bytes=MAX_RESPONSE_BYTES,
            headers={"User-Agent": "FalconAI/1.0"},
        )
        resp.raise_for_status()
        # A page that was reached through redirects counts only when the last
        # one is a public name too.
        if not _names_a_public_host(resp.final_url):
            raise ValueError("the link's host is not a public name")
        # Preserve HTTPX's charset decoding, without any HTTPX network I/O.
        # safe_fetch already decompresses; do not forward Content-Encoding.
        response_text = httpx.Response(
            resp.status_code,
            headers={"Content-Type": resp.headers.get("content-type", "")},
            content=resp.content,
        ).text

        content_type = (
            resp.headers.get("content-type", "").split(";")[0].strip().lower()
        )

        # JSON
        if "json" in content_type:
            return f"[URL: {url}]\n```json\n{response_text[:MAX_CONTENT_LENGTH]}\n```"

        # Plain text / markdown
        if "text/plain" in content_type or "text/markdown" in content_type:
            return f"[URL: {url}]\n{response_text[:MAX_CONTENT_LENGTH]}"

        # Jupyter notebook (.ipynb)
        if url.endswith(".ipynb") or "nbviewer" in url:
            try:
                import json

                nb = json.loads(response_text)
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
            extractor.feed(response_text)
            text = extractor.get_text()
            return f"[URL: {url}]\n{text[:MAX_CONTENT_LENGTH]}"

        # GitHub raw files
        if "raw.githubusercontent.com" in url:
            return f"[GitHub: {url}]\n```\n{response_text[:MAX_CONTENT_LENGTH]}\n```"

        return f"[URL: {url}]\n{response_text[:MAX_CONTENT_LENGTH]}"
    except Exception as e:
        # One line for every link that could not be fetched, whatever the reason.
        logger.warning("url_fetch_failed", url=url, error=type(e).__name__)
        return _could_not_fetch(url)


def fetch_urls_from_message(message, max_urls=MAX_LINKS_PER_MESSAGE):
    """Extract URLs from a message and fetch their content."""
    from concurrent.futures import as_completed

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


async def fetch_urls_for_chat(message, time_limit=None):
    """What the links in a message add to its context, within a time limit.

    The fetch runs on a few threads of its own, so a slow page holds up
    nothing else. A message whose links take longer than the limit goes on
    with one line per link saying it could not be fetched.
    """
    links = extract_urls(message)[:MAX_LINKS_PER_MESSAGE]
    if not links:
        return ""
    if time_limit is None:
        time_limit = LINK_FETCH_TIME_LIMIT_SECONDS
    fetched = asyncio.get_running_loop().run_in_executor(
        _LINK_FETCH_POOL, fetch_urls_from_message, message
    )
    try:
        return await asyncio.wait_for(fetched, timeout=time_limit)
    except TimeoutError:
        logger.warning("url_fetch_time_limit", links=len(links))
        return "\n\n".join(_could_not_fetch(link) for link in links)
