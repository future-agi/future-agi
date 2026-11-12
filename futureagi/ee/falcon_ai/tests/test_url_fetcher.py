"""URL context regression tests using real HTTPX requests to a local server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from ee.falcon_ai.url_fetcher import fetch_url_content, fetch_urls_from_message


@pytest.fixture
def url_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("User-Agent")))
            if self.path.startswith("/redirect/"):
                remaining = int(self.path.rsplit("/", 1)[1])
                self.send_response(302)
                target = f"/redirect/{remaining - 1}" if remaining > 1 else "/page"
                self.send_header("Location", target)
                self.end_headers()
                return

            pages = {
                "/page": (
                    "text/html; charset=utf-8",
                    b"<html><script>hidden</script><p>Fetched page text</p></html>",
                ),
                "/plain": ("text/plain", b"Plain page text"),
            }
            if self.path not in pages:
                self.send_error(404)
                return
            content_type, body = pages[self.path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_message_url_returns_page_text(url_server):
    base_url, requests = url_server
    url = f"{base_url}/page"

    assert fetch_urls_from_message(f"Please read {url}") == (
        f"[URL: {url}]\nFetched page text"
    )
    assert requests == [("/page", "FalconAI/1.0")]


def test_message_without_urls_needs_no_fetch(url_server):
    _, requests = url_server

    assert fetch_urls_from_message("Please help with my dataset") == ""
    assert requests == []


def test_plain_text_content(url_server):
    base_url, _ = url_server
    url = f"{base_url}/plain"

    assert fetch_url_content(url) == f"[URL: {url}]\nPlain page text"


def test_follows_up_to_five_redirects(url_server):
    base_url, requests = url_server
    url = f"{base_url}/redirect/5"

    assert fetch_url_content(url) == f"[URL: {url}]\nFetched page text"
    assert [path for path, _ in requests] == [
        "/redirect/5",
        "/redirect/4",
        "/redirect/3",
        "/redirect/2",
        "/redirect/1",
        "/page",
    ]


def test_more_than_five_redirects_returns_failure(url_server):
    base_url, requests = url_server
    url = f"{base_url}/redirect/6"

    assert fetch_url_content(url) == (
        f"[Failed to fetch URL: {url} — Exceeded maximum allowed redirects.]"
    )
    assert len(requests) == 6
    assert all(path != "/page" for path, _ in requests)


def test_http_error_returns_failure(url_server):
    base_url, requests = url_server
    url = f"{base_url}/missing"

    content = fetch_url_content(url)

    assert content.startswith(f"[Failed to fetch URL: {url} — ")
    assert "404 Not Found" in content
    assert requests == [("/missing", "FalconAI/1.0")]
