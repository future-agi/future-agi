"""URL context regressions with mocked DNS and network boundaries."""

import gzip
import io
import ipaddress
from types import SimpleNamespace

import httpx
import pytest
import urllib3

from ee.falcon_ai.url_fetcher import fetch_url_content, fetch_urls_from_message


@pytest.fixture
def url_network(monkeypatch):
    """Keep the real guard, HTTPX API and decoding; stub only DNS/network I/O.

    The HTTPX stub also makes the pre-guard implementation deterministic: unsafe
    requests return synthetic text rather than reaching a real internal service.
    """
    network = SimpleNamespace(
        requests=[],
        pools=[],
        lookups=[],
        addresses={},
        routes={
            "/page": (
                200,
                {"Content-Type": "text/html; charset=utf-8"},
                b"<html><script>hidden</script><p>Fetched page text</p></html>",
            ),
            "/plain": (200, {"Content-Type": "text/plain"}, b"Plain page text"),
            "/missing": (404, {}, b"Not found"),
        },
    )

    def resolve(host, port, *args, **kwargs):
        network.lookups.append(host)
        try:
            default_ip = str(ipaddress.ip_address(host))
        except ValueError:
            default_ip = "93.184.216.34"
        return [
            (None, None, None, None, (ip, 0))
            for ip in network.addresses.get(host, [default_ip])
        ]

    def response_for(path, headers):
        network.requests.append((path, headers.get("User-Agent")))
        if path.startswith("/redirect/"):
            remaining = int(path.rsplit("/", 1)[1])
            target = f"/redirect/{remaining - 1}" if remaining > 1 else "/page"
            return 302, {"Location": target}, b""
        return network.routes[path]

    real_httpx_request = httpx.HTTPTransport.handle_request

    def httpx_request(transport, request):
        if request.url.scheme not in ("http", "https"):
            # Retain HTTPX's own protocol rejection in the before-fix run.
            return real_httpx_request(transport, request)
        status, headers, body = response_for(request.url.path, request.headers)
        return httpx.Response(status, headers=headers, content=body)

    def pool_request(pool, method, path, *, headers, redirect, preload_content):
        network.pools.append(pool)
        assert method == "GET"
        assert redirect is False
        assert preload_content is False
        status, response_headers, body = response_for(path, headers)
        return urllib3.HTTPResponse(
            status=status,
            headers=response_headers,
            body=io.BytesIO(body),
            preload_content=False,
        )

    monkeypatch.setattr("tfc.utils.ssrf_guard.socket.getaddrinfo", resolve)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", httpx_request)
    monkeypatch.setattr(urllib3.HTTPConnectionPool, "request", pool_request)
    return network


def test_message_url_returns_page_text(url_network):
    url = "https://public.example/page"

    assert fetch_urls_from_message(f"Please read {url}") == (
        f"[URL: {url}]\nFetched page text"
    )
    assert url_network.requests == [("/page", "FalconAI/1.0")]


def test_message_without_urls_needs_no_fetch(url_network):
    assert fetch_urls_from_message("Please help with my dataset") == ""
    assert url_network.requests == []
    assert url_network.lookups == []


def test_plain_text_content(url_network):
    url = "https://public.example/plain"

    assert fetch_url_content(url) == f"[URL: {url}]\nPlain page text"


def test_follows_redirects_within_guard_limit(url_network):
    # safe_fetch permits five requests total: four redirects and the final page.
    url = "https://public.example/redirect/4"

    assert fetch_url_content(url) == f"[URL: {url}]\nFetched page text"
    assert [path for path, _ in url_network.requests] == [
        "/redirect/4",
        "/redirect/3",
        "/redirect/2",
        "/redirect/1",
        "/page",
    ]


def test_redirect_limit_returns_failure(url_network):
    url = "https://public.example/redirect/5"

    assert fetch_url_content(url) == (
        f"[Failed to fetch URL: {url} — Too many redirects.]"
    )
    assert len(url_network.requests) == 5
    assert all(path != "/page" for path, _ in url_network.requests)


def test_http_error_returns_failure(url_network):
    url = "https://public.example/missing"

    content = fetch_url_content(url)

    assert content.startswith(f"[Failed to fetch URL: {url} — ")
    assert "404" in content
    assert url_network.requests == [("/missing", "FalconAI/1.0")]


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "169.254.169.254",
        "[::1]",
        "[fe80::1]",
        "[fd00::1]",
        "[::ffff:127.0.0.1]",
        "[::ffff:10.0.0.5]",
        "[::ffff:169.254.169.254]",
    ],
)
def test_message_rejects_internal_destinations_before_fetch(url_network, host):
    url = f"http://{host}/page"

    result = fetch_urls_from_message(f"Please read {url}")

    assert result.startswith(f"[Failed to fetch URL: {url} — ")
    assert "private/internal address" in result
    assert url_network.requests == []


@pytest.mark.parametrize("addresses", [["10.0.0.5"], ["93.184.216.34", "10.0.0.5"]])
def test_rejects_hostname_with_any_private_dns_record(url_network, addresses):
    url_network.addresses["public.example"] = addresses

    result = fetch_url_content("https://public.example/page")

    assert "private/internal address" in result
    assert url_network.requests == []


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1/page",
        "http://10.0.0.5/page",
        "http://169.254.169.254/page",
        "http://[::ffff:169.254.169.254]/page",
        "http://metadata.google.internal/page",
    ],
)
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_revalidates_later_redirect_hops(url_network, target, status):
    url_network.addresses["metadata.google.internal"] = ["169.254.169.254"]
    url_network.routes["/start"] = (302, {"Location": "/next"}, b"")
    url_network.routes["/next"] = (status, {"Location": target}, b"")
    url = "https://public.example/start"

    result = fetch_urls_from_message(f"Please read {url}")

    assert "private/internal address" in result
    assert [path for path, _ in url_network.requests] == ["/start", "/next"]


@pytest.mark.parametrize("url", ["ftp://public.example/page", "file:///page"])
def test_rejects_non_http_scheme_before_fetch(url_network, url):
    result = fetch_url_content(url)

    assert result.startswith(f"[Failed to fetch URL: {url} — ")
    assert "Unsupported URL scheme" in result
    assert url_network.requests == []


def test_rejects_redirect_to_non_http_scheme(url_network):
    url_network.routes["/start"] = (302, {"Location": "ftp://public.example/page"}, b"")

    result = fetch_url_content("https://public.example/start")

    assert "Redirect target is not a valid URL" in result
    assert url_network.requests == [("/start", "FalconAI/1.0")]


@pytest.mark.parametrize("timeout", [15, 2.5])
def test_public_request_pins_ip_preserves_tls_name_and_timeout(url_network, timeout):
    url = "https://public.example/page"

    assert "Fetched page text" in fetch_url_content(url, timeout=timeout)
    assert url_network.lookups == ["public.example"]
    assert len(url_network.pools) == 1
    pool = url_network.pools[0]
    assert pool.host == "93.184.216.34"
    assert pool.assert_hostname == "public.example"
    assert pool.conn_kw["server_hostname"] == "public.example"
    assert pool.timeout.connect_timeout == timeout
    assert pool.timeout.read_timeout == timeout


def test_oversized_response_is_rejected_before_extraction(url_network):
    url_network.routes["/large"] = (
        200,
        {"Content-Type": "text/plain"},
        b"x" * (1024 * 1024 + 1),
    )

    result = fetch_url_content("https://public.example/large")

    assert "URL body exceeds 1048576 byte limit" in result


def test_multiple_message_urls_still_guard_each_fetch(url_network):
    public_url = "https://public.example/plain"
    private_url = "http://10.0.0.5/page"

    result = fetch_urls_from_message(f"Compare {public_url} with {private_url}")

    assert f"[URL: {public_url}]\nPlain page text" in result
    assert "private/internal address" in result
    assert url_network.requests == [("/plain", "FalconAI/1.0")]


def test_declared_charset_is_preserved(url_network):
    url_network.routes["/latin"] = (
        200,
        {"Content-Type": "text/plain; charset=iso-8859-1"},
        b"caf\xe9",
    )

    assert fetch_url_content("https://public.example/latin") == (
        "[URL: https://public.example/latin]\ncafé"
    )


def test_response_at_byte_limit_is_allowed_and_text_is_truncated(url_network):
    url_network.routes["/large"] = (
        200,
        {"Content-Type": "text/plain"},
        b"x" * (1024 * 1024),
    )

    assert fetch_url_content("https://public.example/large") == (
        "[URL: https://public.example/large]\n" + "x" * 50000
    )


@pytest.mark.parametrize("size", [10, 1024 * 1024 + 1])
def test_compressed_body_is_decoded_once_and_size_cap_applies(url_network, size):
    url_network.routes["/gzip"] = (
        200,
        {"Content-Type": "text/plain", "Content-Encoding": "gzip"},
        gzip.compress(b"x" * size),
    )

    result = fetch_url_content("https://public.example/gzip")

    if size == 10:
        assert result == "[URL: https://public.example/gzip]\n" + "x" * 10
    else:
        assert "URL body exceeds 1048576 byte limit" in result
