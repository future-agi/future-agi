"""Regression tests for the CORS origin policy (issue #1133)."""

from tfc.settings.settings import _cors_policy


def test_the_wildcard_is_local_only():
    assert _cors_policy(True, [], ("", "")) == (True, [])
    # #1133: outside local/test an empty allowlist fails closed instead of wildcarding.
    assert _cors_policy(False, [], ("", "")) == (False, [])


def test_frontend_hosts_back_the_allowlist():
    # Self-hosted default (APP_URL set, no explicit list) works without the wildcard.
    localhost = ["https://localhost:3031", "http://localhost:3031"]
    assert _cors_policy(False, [], ("localhost:3031", "")) == (False, localhost)
    # Full origins pass through; duplicates collapse.
    explicit, hosts = ["https://a.example"], ("https://b.example/", "a.example")
    expected = ["https://a.example", "https://b.example", "http://a.example"]
    assert _cors_policy(False, explicit, hosts) == (False, expected)
