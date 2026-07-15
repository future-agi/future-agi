"""Regression tests for CORS origin policy (issue #1133).

Credentialed CORS must never be combined with a wildcard origin outside
local/test. These exercise the pure policy helper so no Django setup is needed.
"""

from tfc.settings.cors import resolve_cors_origins


def test_local_default_allows_wildcard():
    # Developer convenience is preserved for local/test with no allowlist.
    allow_all, allowed = resolve_cors_origins(True, [], ("", ""))
    assert allow_all is True
    assert allowed == []


def test_non_local_never_wildcards_without_allowlist():
    # #1133: outside local, an empty allowlist must fail closed, not wildcard.
    allow_all, allowed = resolve_cors_origins(False, [], ("", ""))
    assert allow_all is False
    assert allowed == []


def test_non_local_derives_frontend_origin_from_app_url():
    # A default self-hosted stack (APP_URL set, no explicit list) still works,
    # and does so without enabling the wildcard.
    allow_all, allowed = resolve_cors_origins(False, [], ("localhost:3031", ""))
    assert allow_all is False
    assert allowed == ["https://localhost:3031", "http://localhost:3031"]


def test_explicit_and_scheme_qualified_hosts():
    allow_all, allowed = resolve_cors_origins(
        False,
        ["https://app.example.com"],
        ("https://app.futureagi.com", "app.example.com"),
    )
    assert allow_all is False
    # Scheme-qualified hosts pass through as-is; duplicates are collapsed.
    assert allowed == [
        "https://app.example.com",
        "https://app.futureagi.com",
        "http://app.example.com",
    ]
