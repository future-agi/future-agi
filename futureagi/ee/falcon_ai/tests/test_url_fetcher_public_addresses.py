"""Links in a chat message are fetched from public internet addresses only.

Falcon AI reads the pages a person links in a message. A page is fetched only
when every address its name resolves to is public, on the first request and on
every redirect. A link to a loopback, private, link-local or metadata address,
or to a name that resolves to one, is not fetched. A link to a name that no
public page has (a single label, or an ending private networks use for their
own services) is not even looked up. Every link that could not be fetched
gives the same one line, whatever the reason, and nothing else.

Network-free: name lookups and both connection pool classes are faked.
"""

import ipaddress
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import urlparse

import httpx
import pytest

from ee.falcon_ai import url_fetcher

PUBLIC_IP = "93.184.216.34"
OTHER_PUBLIC_IP = "93.184.215.14"
PUBLIC_LINK = "https://news.example.com/latest"
# Stands for whatever a non-public address would answer with. It must never
# reach the text Falcon AI is given.
INSIDE_TEXT = "inside-only-text"


def _not_fetched(link):
    return f"[This link could not be fetched: {link}]"


def _addresses(pools):
    return [request["address"] for request in pools.requests]


def _response(status, headers=None, body=b""):
    response = MagicMock()
    response.status = status
    response.headers = headers or {}
    response.stream.return_value = iter([body] if body else [])
    return response


class _Names(dict):
    """Name to answers. ``looked_up`` records every name that was asked for."""

    def __init__(self):
        super().__init__()
        self.looked_up = []


@pytest.fixture
def names(monkeypatch):
    """Name lookups answer from this table, the way a resolver would; an
    address answers as itself."""
    table = _Names()

    def fake_getaddrinfo(host, *args, **kwargs):
        table.looked_up.append(host)
        try:
            ipaddress.ip_address(host)
        except ValueError:
            answers = table.get(host)
            if not answers:
                raise socket.gaierror(f"no such name: {host}") from None
        else:
            answers = [host]
        return [(None, None, None, None, (answer, 0)) for answer in answers]

    monkeypatch.setattr("tfc.utils.ssrf_guard.socket.getaddrinfo", fake_getaddrinfo)
    return table


@pytest.fixture
def pools(monkeypatch):
    """Fake connection pools. Queue responses in ``responses``; every request
    lands in ``requests`` with the address its pool was opened to."""
    state = SimpleNamespace(requests=[], responses=[])

    def pool_factory(scheme):
        def construct(address, *args, **kwargs):
            pool = MagicMock()

            def request(method, path, headers, redirect, preload_content):
                state.requests.append(
                    {
                        "scheme": scheme,
                        "address": address,
                        "method": method,
                        "path": path,
                        "headers": dict(headers),
                    }
                )
                return state.responses.pop(0)

            pool.request.side_effect = request
            return pool

        return construct

    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.urllib3.HTTPConnectionPool",
        MagicMock(side_effect=pool_factory("http")),
    )
    monkeypatch.setattr(
        "tfc.utils.ssrf_guard.urllib3.HTTPSConnectionPool",
        MagicMock(side_effect=pool_factory("https")),
    )
    return state


@pytest.fixture
def unchecked_client(monkeypatch):
    """A client that connects wherever a link points and follows redirects by
    itself. It records every link it is handed; a link must never reach it."""
    handed = []

    def get(url, **kwargs):
        handed.append(url)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text=INSIDE_TEXT,
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", get)
    return handed


@pytest.fixture
def hosted(settings, monkeypatch):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "US")
    settings.CLOUD_DEPLOYMENT = "US"


@pytest.fixture
def self_hosted(settings, monkeypatch):
    monkeypatch.delenv("CLOUD_DEPLOYMENT", raising=False)
    settings.CLOUD_DEPLOYMENT = ""


NOT_FETCHED = [
    pytest.param("https://127.0.0.1:8080/status", {}, id="loopback-address"),
    pytest.param("https://[::1]/status", {}, id="ipv6-loopback"),
    pytest.param(
        "https://0177.0.0.1/",
        {"0177.0.0.1": ["127.0.0.1"]},
        id="address-written-in-octal",
    ),
    pytest.param(
        "https://127.1/", {"127.1": ["127.0.0.1"]}, id="address-written-in-short-form"
    ),
    pytest.param("https://0.0.0.0/", {}, id="unspecified-address"),
    pytest.param("https://[::]/", {}, id="ipv6-unspecified-address"),
    pytest.param("https://10.0.0.7/reports", {}, id="private-10"),
    pytest.param("https://172.16.4.2/reports", {}, id="private-172-16"),
    pytest.param("https://192.168.1.20/reports", {}, id="private-192-168"),
    pytest.param("https://100.64.0.9/", {}, id="shared-address-space"),
    pytest.param("https://169.254.10.10/", {}, id="link-local"),
    pytest.param("https://169.254.169.254/", {}, id="metadata-address"),
    pytest.param("https://[fd00::12]/", {}, id="ipv6-unique-local"),
    pytest.param("https://[::ffff:10.0.0.7]/", {}, id="ipv4-mapped-ipv6"),
    pytest.param(
        "https://[64:ff9b::a9fe:a9fe]/", {}, id="nat64-form-of-a-link-local-address"
    ),
    pytest.param(
        "https://[2002:a9fe:a9fe::]/", {}, id="6to4-form-of-a-link-local-address"
    ),
    pytest.param(
        "https://notes.example.com/",
        {"notes.example.com": ["192.168.7.7"]},
        id="public-looking-name-with-a-private-answer",
    ),
    pytest.param(
        "https://mixed.example.com/",
        {"mixed.example.com": [PUBLIC_IP, "10.1.2.3"]},
        id="name-with-one-public-and-one-private-answer",
    ),
    pytest.param("https://[fe80::1]/", {}, id="ipv6-link-local"),
    pytest.param("https://168.63.129.16/", {}, id="metadata-address-in-public-space"),
    pytest.param(
        "https://user@10.0.0.7/reports", {}, id="user-info-before-a-private-address"
    ),
    pytest.param("https://unknown.example.com/", {}, id="name-with-no-answer"),
]

# No public page has one of these names. Each is refused before any lookup,
# so the test answers every lookup with a public address to show that the
# answer plays no part.
NOT_LOOKED_UP = [
    pytest.param("https://localhost/status", id="localhost"),
    pytest.param("https://localhost./status", id="localhost-with-a-trailing-dot"),
    pytest.param("https://reports/", id="single-label"),
    pytest.param("https://reports:8080/api/items", id="single-label-with-a-port"),
    pytest.param("https://reports./", id="single-label-with-a-trailing-dot"),
    pytest.param("https://2130706433/", id="address-written-as-one-number"),
    pytest.param("https://0x7f000001/", id="address-written-in-hexadecimal"),
    pytest.param("https://reports.default.svc/", id="svc-ending"),
    pytest.param(
        "https://reports.default.svc.cluster.local/", id="cluster-local-ending"
    ),
    pytest.param(
        "https://reports.default.svc.cluster.local./",
        id="cluster-local-ending-with-a-trailing-dot",
    ),
    pytest.param(
        "https://REPORTS.Default.SVC.Cluster.Local/",
        id="cluster-local-ending-in-capitals",
    ),
    pytest.param("https://metadata.google.internal/", id="internal-ending"),
    pytest.param("https://printer.local/", id="local-ending"),
    pytest.param("https://app.localhost/", id="localhost-ending"),
    pytest.param(
        "https://reports.default\u3002svc/",
        id="private-ending-after-an-ideographic-dot",
    ),
    pytest.param(
        "https://printer.local\u3002/",
        id="private-ending-with-an-ideographic-trailing-dot",
    ),
]


@pytest.mark.parametrize(("link", "answers"), NOT_FETCHED)
def test_a_link_to_a_non_public_address_is_not_fetched(
    link, answers, names, pools, unchecked_client
):
    names.update(answers)
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, INSIDE_TEXT.encode())
    )

    result = url_fetcher.fetch_url_content(link)

    assert unchecked_client == []
    assert names.looked_up == [urlparse(link).hostname]
    assert pools.requests == []
    assert result == _not_fetched(link)


@pytest.mark.parametrize("link", NOT_LOOKED_UP)
def test_a_link_to_a_name_no_public_page_has_is_not_looked_up(
    link, names, pools, unchecked_client, monkeypatch
):
    monkeypatch.setattr(names, "get", lambda host: [PUBLIC_IP])
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, INSIDE_TEXT.encode())
    )

    result = url_fetcher.fetch_url_content(link)

    assert unchecked_client == []
    assert names.looked_up == []
    assert pools.requests == []
    assert result == _not_fetched(link)


def test_a_public_name_with_a_trailing_dot_is_fetched(names, pools, unchecked_client):
    link = "https://news.example.com./latest"
    names["news.example.com."] = [PUBLIC_IP]
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, b"Plain notes")
    )

    result = url_fetcher.fetch_url_content(link)

    assert unchecked_client == []
    assert _addresses(pools) == [PUBLIC_IP]
    assert result == f"[URL: {link}]\nPlain notes"


def test_a_public_ipv6_address_is_fetched(names, pools, unchecked_client):
    link = "https://[2606:4700:4700::1111]/latest"
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, b"Plain notes")
    )

    result = url_fetcher.fetch_url_content(link)

    assert unchecked_client == []
    assert _addresses(pools) == ["2606:4700:4700::1111"]
    assert result == f"[URL: {link}]\nPlain notes"


def test_a_redirect_that_ends_at_a_name_no_public_page_has_gives_nothing_back(
    names, pools, unchecked_client
):
    names.update(
        {
            "news.example.com": [PUBLIC_IP],
            "reports.default.svc.cluster.local": [OTHER_PUBLIC_IP],
        }
    )
    pools.responses.append(
        _response(302, {"Location": "https://reports.default.svc.cluster.local/"})
    )
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, INSIDE_TEXT.encode())
    )

    result = url_fetcher.fetch_url_content(PUBLIC_LINK)

    assert unchecked_client == []
    assert result == _not_fetched(PUBLIC_LINK)


@pytest.mark.parametrize(
    "target",
    [
        pytest.param("https://inside.example.com/reports", id="name"),
        pytest.param("https://10.0.0.7/reports", id="address"),
        pytest.param("https://localhost/reports", id="loopback-name"),
        pytest.param("//inside.example.com/reports", id="scheme-relative"),
        pytest.param("https://mixed.example.com/reports", id="name-with-mixed-answers"),
    ],
)
def test_a_public_link_that_redirects_to_a_non_public_address_stops_there(
    target, names, pools, unchecked_client
):
    names.update(
        {
            "news.example.com": [PUBLIC_IP],
            "inside.example.com": ["10.0.0.7"],
            "localhost": ["127.0.0.1"],
            "mixed.example.com": [PUBLIC_IP, "10.1.2.3"],
        }
    )
    pools.responses.append(_response(302, {"Location": target}))
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, INSIDE_TEXT.encode())
    )

    result = url_fetcher.fetch_url_content(PUBLIC_LINK)

    assert unchecked_client == []
    assert _addresses(pools) == [PUBLIC_IP]
    assert result == _not_fetched(PUBLIC_LINK)


def test_a_relative_redirect_on_a_public_host_is_followed(
    names, pools, unchecked_client
):
    names["news.example.com"] = [PUBLIC_IP]
    pools.responses.append(_response(303, {"Location": "/archive"}))
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, b"Plain notes")
    )

    result = url_fetcher.fetch_url_content(PUBLIC_LINK)

    assert unchecked_client == []
    assert [request["path"] for request in pools.requests] == ["/latest", "/archive"]
    assert _addresses(pools) == [PUBLIC_IP, PUBLIC_IP]
    assert result == f"[URL: {PUBLIC_LINK}]\nPlain notes"


def test_a_chain_of_more_redirects_than_the_limit_is_not_fetched(
    names, pools, unchecked_client
):
    names["news.example.com"] = [PUBLIC_IP]
    for hop in range(6):
        pools.responses.append(_response(308, {"Location": f"/hop-{hop}"}))

    result = url_fetcher.fetch_url_content(PUBLIC_LINK)

    assert unchecked_client == []
    assert len(pools.requests) == 5
    assert result == _not_fetched(PUBLIC_LINK)


def test_every_redirect_in_a_chain_is_checked(names, pools, unchecked_client):
    names.update(
        {
            "news.example.com": [PUBLIC_IP],
            "www.example.org": [OTHER_PUBLIC_IP],
            "inside.example.com": ["10.0.0.7"],
        }
    )
    pools.responses.append(
        _response(301, {"Location": "https://www.example.org/latest"})
    )
    pools.responses.append(
        _response(307, {"Location": "https://inside.example.com/reports"})
    )
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, INSIDE_TEXT.encode())
    )

    result = url_fetcher.fetch_url_content(PUBLIC_LINK)

    assert unchecked_client == []
    assert _addresses(pools) == [PUBLIC_IP, OTHER_PUBLIC_IP]
    assert result == _not_fetched(PUBLIC_LINK)


def test_a_redirect_from_https_to_http_is_not_followed(names, pools, unchecked_client):
    names.update(
        {"news.example.com": [PUBLIC_IP], "www.example.org": [OTHER_PUBLIC_IP]}
    )
    pools.responses.append(_response(302, {"Location": "http://www.example.org/notes"}))
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, b"Plain notes")
    )

    result = url_fetcher.fetch_url_content(PUBLIC_LINK)

    assert unchecked_client == []
    assert _addresses(pools) == [PUBLIC_IP]
    assert result == _not_fetched(PUBLIC_LINK)


def test_on_future_agi_cloud_an_http_link_is_not_fetched(
    hosted, names, pools, unchecked_client
):
    link = "http://news.example.com/latest"
    names["news.example.com"] = [PUBLIC_IP]
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, b"Plain notes")
    )

    result = url_fetcher.fetch_url_content(link)

    assert unchecked_client == []
    assert names.looked_up == []
    assert pools.requests == []
    assert result == _not_fetched(link)


def test_a_self_hosted_install_fetches_a_public_http_link(
    self_hosted, names, pools, unchecked_client
):
    link = "http://news.example.com/latest"
    names["news.example.com"] = [PUBLIC_IP]
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, b"Plain notes")
    )

    result = url_fetcher.fetch_url_content(link)

    assert unchecked_client == []
    assert [request["scheme"] for request in pools.requests] == ["http"]
    assert _addresses(pools) == [PUBLIC_IP]
    assert result == f"[URL: {link}]\nPlain notes"


def test_a_public_link_is_fetched_from_the_address_that_was_checked(
    names, pools, unchecked_client
):
    names["news.example.com"] = [PUBLIC_IP]
    pools.responses.append(
        _response(
            200,
            {"Content-Type": "text/html; charset=utf-8"},
            b"<html><body><p>Quarterly notes</p></body></html>",
        )
    )

    result = url_fetcher.fetch_url_content(PUBLIC_LINK)

    assert unchecked_client == []
    assert _addresses(pools) == [PUBLIC_IP]
    request = pools.requests[0]
    assert (request["scheme"], request["method"]) == ("https", "GET")
    assert request["path"] == "/latest"
    assert request["headers"]["User-Agent"] == "FalconAI/1.0"
    assert result == f"[URL: {PUBLIC_LINK}]\nQuarterly notes"


def test_a_public_link_that_redirects_to_another_public_page_is_fetched(
    names, pools, unchecked_client
):
    names.update(
        {"news.example.com": [PUBLIC_IP], "www.example.org": [OTHER_PUBLIC_IP]}
    )
    pools.responses.append(
        _response(301, {"Location": "https://www.example.org/notes"})
    )
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, b"Plain notes")
    )

    result = url_fetcher.fetch_url_content(PUBLIC_LINK)

    assert unchecked_client == []
    assert _addresses(pools) == [PUBLIC_IP, OTHER_PUBLIC_IP]
    assert result == f"[URL: {PUBLIC_LINK}]\nPlain notes"


def test_a_page_larger_than_the_read_limit_is_not_fetched(
    names, pools, unchecked_client, monkeypatch
):
    monkeypatch.setattr(url_fetcher, "MAX_RESPONSE_BYTES", 1024, raising=False)
    names["news.example.com"] = [PUBLIC_IP]
    pools.responses.append(_response(200, {"Content-Type": "text/plain"}, b"x" * 2048))

    result = url_fetcher.fetch_url_content(PUBLIC_LINK)

    assert unchecked_client == []
    assert _addresses(pools) == [PUBLIC_IP]
    assert result == _not_fetched(PUBLIC_LINK)


def test_a_link_that_answers_with_an_error_gives_the_same_line(
    names, pools, unchecked_client
):
    names["news.example.com"] = [PUBLIC_IP]
    pools.responses.append(_response(404, {"Content-Type": "text/plain"}, b"Not here"))

    result = url_fetcher.fetch_url_content(PUBLIC_LINK)

    assert unchecked_client == []
    assert _addresses(pools) == [PUBLIC_IP]
    assert result == _not_fetched(PUBLIC_LINK)


def test_a_message_with_a_public_and_a_non_public_link_gets_the_public_page_only(
    names, pools, unchecked_client
):
    names["news.example.com"] = [PUBLIC_IP]
    pools.responses.append(
        _response(200, {"Content-Type": "text/plain"}, b"Plain notes")
    )

    context = url_fetcher.fetch_urls_from_message(
        f"Compare {PUBLIC_LINK} with https://10.0.0.7/reports"
    )

    assert unchecked_client == []
    assert _addresses(pools) == [PUBLIC_IP]
    assert f"[URL: {PUBLIC_LINK}]\nPlain notes" in context
    assert _not_fetched("https://10.0.0.7/reports") in context
    assert INSIDE_TEXT not in context
