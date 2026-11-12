"""DNS fixtures classify URLs without replacing the process socket boundary."""

import socket
from unittest.mock import patch

from agentcc.services import url_safety
from tfc.utils import ssrf_guard


def test_validation_uses_scoped_dns_fixture(offline_validation_dns):
    original = socket.getaddrinfo
    for module in (url_safety, ssrf_guard):
        assert module.socket is not socket
        answer = module.socket.getaddrinfo("api.example.com", 443)
        assert answer[0][4] == ("93.184.216.34", 443)
    assert socket.getaddrinfo is original
    assert offline_validation_dns.call_count == 2


def test_explicit_rebinding_fixture_still_wins(offline_validation_dns):
    with patch("socket.getaddrinfo", return_value=[]) as explicit:
        assert url_safety.socket.getaddrinfo("rebinding.invalid", 443) == []
        explicit.assert_called_once_with("rebinding.invalid", 443)
    offline_validation_dns.assert_not_called()


def test_provider_dns_override_is_not_process_wide(provider_dns):
    original = socket.getaddrinfo
    calls = provider_dns({"fixture.invalid": ["192.0.2.1"]})
    assert url_safety.socket.getaddrinfo("fixture.invalid")[0][4][0] == "192.0.2.1"
    calls.assert_called_once()
    assert socket.getaddrinfo is original
