"""
Tests for accounts.authentication.get_client_ip.

Valid X-Forwarded-For values must resolve exactly as before (first entry
wins, same private IPv4 ranges). Malformed values must never raise: bad
entries are skipped and REMOTE_ADDR is the fallback.
"""

import json
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory

from accounts.authentication import AuthMonitoringMiddleware, get_client_ip

REMOTE = "203.0.113.7"


def _request(forwarded_for=None, remote_addr=REMOTE, path="/api/health/"):
    req = RequestFactory().get(path)
    if forwarded_for is not None:
        req.META["HTTP_X_FORWARDED_FOR"] = forwarded_for
    if remote_addr is None:
        req.META.pop("REMOTE_ADDR", None)
    else:
        req.META["REMOTE_ADDR"] = remote_addr
    return req


@pytest.mark.unit
class TestValidHeadersUnchanged:
    """Valid headers resolve to the same (ip, is_routable) as before."""

    @pytest.mark.parametrize(
        "forwarded_for, expected",
        [
            ("1.2.3.4", ("1.2.3.4", True)),
            ("1.2.3.4, 10.0.0.1", ("1.2.3.4", True)),
            ("  8.8.8.8  ,  10.0.0.1", ("8.8.8.8", True)),
            ("10.1.2.3, 8.8.8.8", ("10.1.2.3", False)),
            ("10.0.0.0", ("10.0.0.0", False)),
            ("10.255.255.255", ("10.255.255.255", False)),
            ("11.0.0.0", ("11.0.0.0", True)),
            ("172.15.255.255", ("172.15.255.255", True)),
            ("172.16.0.0", ("172.16.0.0", False)),
            ("172.31.255.255", ("172.31.255.255", False)),
            ("172.32.0.0", ("172.32.0.0", True)),
            ("192.168.0.0", ("192.168.0.0", False)),
            ("192.168.255.255", ("192.168.255.255", False)),
            ("192.169.0.0", ("192.169.0.0", True)),
            ("127.0.0.1", ("127.0.0.1", True)),
            ("2001:DB8::1", ("2001:DB8::1", True)),
            ("fd00::1, 1.2.3.4", ("fd00::1", True)),
        ],
    )
    def test_forwarded_for(self, forwarded_for, expected):
        assert get_client_ip(_request(forwarded_for)) == expected

    def test_no_forwarded_for_uses_remote_addr(self):
        assert get_client_ip(_request()) == (REMOTE, True)

    def test_empty_forwarded_for_uses_remote_addr(self):
        assert get_client_ip(_request("")) == (REMOTE, True)

    def test_private_remote_addr(self):
        assert get_client_ip(_request(remote_addr="192.168.1.10")) == (
            "192.168.1.10",
            False,
        )


@pytest.mark.unit
class TestMalformedHeadersDoNotRaise:
    """Malformed headers are parsed defensively instead of raising."""

    @pytest.mark.parametrize(
        "forwarded_for, expected",
        [
            # IPv4 with a port: the address part is used.
            ("1.2.3.4:5555", ("1.2.3.4", True)),
            ("10.0.0.5:443, 8.8.8.8", ("10.0.0.5", False)),
            # IPv4-mapped IPv6 is a valid address.
            ("::ffff:1.2.3.4", ("::ffff:1.2.3.4", True)),
            # Bracketed IPv6, with and without a port.
            ("[2001:db8::1]:8080", ("2001:db8::1", True)),
            ("[2001:db8::1]", ("2001:db8::1", True)),
            # Garbage first entries are skipped for the next valid one.
            ("a.b.c.d, 8.8.4.4", ("8.8.4.4", True)),
            ("1.2.3., 8.8.4.4", ("8.8.4.4", True)),
            (", 8.8.4.4", ("8.8.4.4", True)),
            ("unknown, 192.168.1.1", ("192.168.1.1", False)),
        ],
    )
    def test_recovers_address(self, forwarded_for, expected):
        assert get_client_ip(_request(forwarded_for)) == expected

    @pytest.mark.parametrize(
        "forwarded_for",
        [
            "a.b.c.d",
            "1.2.3.",
            "1.2.3.4.5",
            "999.1.1.1",
            "garbage",
            ",",
            " , , ",
            "[::1",
            "1.2.3.4:5:6",
            "\x00",
        ],
    )
    def test_falls_back_to_remote_addr(self, forwarded_for):
        assert get_client_ip(_request(forwarded_for)) == (REMOTE, True)

    def test_missing_remote_addr(self):
        assert get_client_ip(_request(remote_addr=None)) == ("unknown", False)

    def test_malformed_header_and_missing_remote_addr(self):
        assert get_client_ip(_request("a.b.c.d", remote_addr=None)) == (
            "unknown",
            False,
        )


@pytest.mark.unit
class TestMiddlewareWithMalformedForwardedFor:
    """AuthMonitoringMiddleware serves requests with malformed headers."""

    @pytest.fixture(autouse=True)
    def _non_oss(self):
        with patch("accounts.authentication.is_oss", return_value=False):
            yield

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        cache.clear()
        yield
        cache.clear()

    def _middleware(self):
        return AuthMonitoringMiddleware(lambda r: HttpResponse("OK", status=200))

    @pytest.mark.parametrize(
        "forwarded_for", ["1.2.3.4:5555", "::ffff:1.2.3.4", "a.b.c.d", "1.2.3."]
    )
    def test_request_is_served(self, forwarded_for):
        resp = self._middleware()(_request(forwarded_for))
        assert resp.status_code == 200

    def test_block_applies_to_address_without_port(self):
        cache.set("blocked_ip_198.51.100.9", True, 3600)
        req = _request("198.51.100.9:5555", path="/api/accounts/token/")
        resp = self._middleware()(req)
        assert resp.status_code == 403
        assert json.loads(resp.content)["result"]["error_code"] == "LOGIN_IP_BLOCKED"
