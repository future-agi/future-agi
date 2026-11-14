"""Custom endpoint destination, bounded-response and credential contracts."""

import socket
from unittest.mock import MagicMock, patch

import pytest
import urllib3

from model_hub.services.custom_endpoint import (
    MAX_RESPONSE_BYTES,
    CustomEndpointError,
    post_custom_endpoint,
)

PUBLIC_IP = "93.184.216.34"
ENDPOINT = "https://model.example/v1/chat/completions?token=synthetic-query-secret"
SECRET = "synthetic-header-secret"


def answers(*ips):
    return [
        (
            socket.AF_INET6 if ":" in ip else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (ip, 443),
        )
        for ip in ips
    ]


@pytest.fixture
def transport():
    with (
        patch(
            "tfc.utils.ssrf_guard.socket.getaddrinfo", return_value=answers(PUBLIC_IP)
        ) as dns,
        patch("model_hub.services.custom_endpoint._open_pinned_pool") as open_pool,
    ):
        response = MagicMock(status=200, headers={})
        response.read1.side_effect = [b'{"output":"synthetic-ok"}', b""]
        open_pool.return_value.request.return_value = response
        yield dns, open_pool, response


def send(url=ENDPOINT, headers=None):
    return post_custom_endpoint(
        url=url,
        headers=headers or {"X-Provider-Key": SECRET},
        payload={"messages": [{"role": "user", "content": "synthetic-prompt"}]},
    )


def test_public_post_is_pinned_and_preserves_body_credentials(transport):
    dns, open_pool, response = transport
    result = send()
    assert result.status_code == 200
    assert result.text == '{"output":"synthetic-ok"}'
    dns.assert_called_once_with("model.example", None)
    assert open_pool.call_args.args[:4] == ("https", PUBLIC_IP, "model.example", 443)
    timeout = open_pool.call_args.args[4]
    assert timeout.connect_timeout == 5
    assert timeout.total == 60
    call = open_pool.return_value.request.call_args
    assert call.args == ("POST", "/v1/chat/completions?token=synthetic-query-secret")
    assert call.kwargs["headers"]["X-Provider-Key"] == SECRET
    assert call.kwargs["headers"]["Host"] == "model.example"
    assert b"synthetic-prompt" in call.kwargs["body"]
    assert call.kwargs["redirect"] is False
    assert call.kwargs["retries"] is False
    assert call.kwargs["preload_content"] is False
    response.close.assert_called_once()
    open_pool.return_value.close.assert_called_once()


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "::1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.100.100.200",
        "168.63.129.16",
        "fd00:ec2::254",
        "::ffff:127.0.0.1",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
    ],
)
def test_nonpublic_dns_answers_refused_before_connection(transport, ip):
    dns, open_pool, _ = transport
    dns.return_value = answers(ip)
    with pytest.raises(CustomEndpointError, match="refused or unavailable") as error:
        send()
    assert SECRET not in str(error.value)
    assert "synthetic-query-secret" not in str(error.value)
    open_pool.assert_not_called()


def test_mixed_dns_answer_refused_before_connection(transport):
    dns, open_pool, _ = transport
    dns.return_value = answers(PUBLIC_IP, "127.0.0.1")
    with pytest.raises(CustomEndpointError):
        send()
    open_pool.assert_not_called()


def test_rebinding_second_answer_is_never_used(transport):
    dns, open_pool, _ = transport
    dns.side_effect = [answers(PUBLIC_IP), answers("127.0.0.1")]
    send()
    assert dns.call_count == 1
    assert open_pool.call_args.args[1] == PUBLIC_IP


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_refused_without_forwarding_credentials(transport, status):
    dns, open_pool, response = transport
    response.status = status
    response.headers = {"Location": "http://169.254.169.254/metadata"}
    with pytest.raises(CustomEndpointError, match="redirects are not supported"):
        send()
    assert open_pool.return_value.request.call_count == 1
    assert dns.call_count == 1
    response.read1.assert_not_called()
    response.close.assert_called_once()


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/hosts",
        "http://user:synthetic-password@model.example/",
        "http://metadata.google.internal/",
        "http://metadata.goog./",
        "http://model.example/path\r\nX-Key: value",
        "https://model.example/#fragment",
    ],
)
def test_invalid_urls_never_open_pool(transport, url):
    _, open_pool, _ = transport
    with pytest.raises(CustomEndpointError):
        send(url)
    open_pool.assert_not_called()


@pytest.mark.parametrize(
    "header",
    [
        "hOsT",
        "Content-Length",
        "Transfer-Encoding",
        "Proxy-Authorization",
        "Connection",
    ],
)
def test_routing_and_framing_overrides_refused(transport, header):
    _, open_pool, _ = transport
    with pytest.raises(
        CustomEndpointError, match="Invalid custom model request headers"
    ):
        send(headers={header: SECRET})
    open_pool.assert_not_called()


def test_compressed_body_refused_before_decoding(transport):
    _, open_pool, response = transport
    response.headers = {"Content-Encoding": "gzip"}
    with pytest.raises(CustomEndpointError, match="Compressed custom model responses"):
        send()
    assert open_pool.return_value.request.call_args.kwargs["decode_content"] is False
    response.read1.assert_not_called()
    response.close.assert_called_once()


def test_exact_size_boundary(transport):
    _, _, response = transport
    response.read1.side_effect = [b"x" * MAX_RESPONSE_BYTES, b""]
    assert len(send().content) == MAX_RESPONSE_BYTES


def test_oversized_body_refused_and_closed(transport):
    _, open_pool, response = transport
    response.read1.side_effect = [b"x" * MAX_RESPONSE_BYTES, b"x", b""]
    with pytest.raises(CustomEndpointError, match="exceeds the 4 MiB limit"):
        send()
    assert response.read1.call_count == 2
    response.close.assert_called_once()
    open_pool.return_value.close.assert_called_once()


def test_transport_error_does_not_repeat_url_or_headers(transport):
    _, open_pool, _ = transport
    open_pool.return_value.request.side_effect = urllib3.exceptions.HTTPError(
        ENDPOINT + SECRET
    )
    with pytest.raises(CustomEndpointError) as error:
        send()
    assert str(error.value) == "Custom model endpoint request refused or unavailable."
    open_pool.return_value.close.assert_called_once()


def test_timeout_is_safe_and_closes_pool(transport):
    _, open_pool, _ = transport
    open_pool.return_value.request.side_effect = urllib3.exceptions.ReadTimeoutError(
        None, ENDPOINT, SECRET
    )
    with pytest.raises(
        CustomEndpointError, match="^Custom model endpoint request timed out.$"
    ):
        send()
    open_pool.return_value.close.assert_called_once()
