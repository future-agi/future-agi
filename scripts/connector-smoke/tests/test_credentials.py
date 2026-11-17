"""Credential placement as the manifest says (AM-C12 a, AM-C20): Close's
secret Close-API-Key from the keys entry and its constant Close-Scope from
the catalog; query and URL-variable parts; every entry value used or refused."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import (
    CLOSE,
    CLOSE_KEY,
    argv,
    checks,
    close_host,
    entry,
    env_with,
    provider_of,
    report_of,
    run,
)
from connector_smoke import catalog, keys
from connector_smoke.credentials import Skip, Unsupported, resolve

KEY = "close/close-mcp"


def test_close_sends_its_secret_part_and_the_catalog_constant(fake, net, tmp_path):
    fake.hosts[CLOSE] = close_host()
    out = tmp_path / "out"
    code, text = run(
        argv(out, "--provider", KEY),
        env_with({KEY: entry("fa-ci-close", parts={"Close-API-Key": CLOSE_KEY})}),
        net,
    )
    assert code == 0, text
    seen = fake.requests(CLOSE)
    assert seen and all(s.headers.get("close-api-key") == CLOSE_KEY for s in seen)
    # Close-Scope comes from the catalog, never the entry
    assert all(s.headers.get("close-scope") == "mcp.read" for s in seen)
    assert all("authorization" not in s.headers for s in seen)
    p = provider_of(report_of(out), KEY)
    assert p["status"] == "pass" and p["testAccount"] == "fa-ci-close"
    assert checks(p, "session")[0]["status"] == "pass"
    # the manifest declares no tools, so no annotation was compared
    assert checks(p, "annotations") == [
        {
            "check": "annotations",
            "status": "skipped",
            "reason": "no_read_only_tools_declared",
        }
    ]
    assert (
        CLOSE_KEY not in (out / "drift-report.json").read_text()
        and CLOSE_KEY not in text
    )


@pytest.mark.parametrize(
    "parts, reason",
    [
        (
            {"Close-API-Key": CLOSE_KEY, "Close-Scope": "mcp.write_destructive"},
            "part_is_catalog_constant:Close-Scope",
        ),
        ({}, "part_missing:Close-API-Key"),
        (
            {"Close-API-Key": CLOSE_KEY, "X-Other": "value-of-another-part"},
            "part_unknown",
        ),
    ],
)
def test_entry_parts_that_do_not_fit_are_refused_before_any_request(
    parts, reason, fake, net, tmp_path
):
    fake.hosts[CLOSE] = close_host()
    out = tmp_path / "out"
    code, text = run(
        argv(out, "--provider", KEY),
        env_with({KEY: entry("fa-ci-close", parts=parts)}),
        net,
    )
    assert code == 1
    assert fake.requests(CLOSE) == []
    p = provider_of(report_of(out), KEY)
    assert p["status"] == "fail"
    assert checks(p, "credential") == [
        {"check": "credential", "status": "fail", "reason": reason}
    ]
    assert CLOSE_KEY not in text


def _provider(
    auth: dict, url: str = "https://mcp.example.com/mcp", url_variables=None
) -> catalog.Provider:
    transport = {"kind": "http", "url": url}
    if url_variables:
        transport["urlVariables"] = url_variables
    return catalog.Provider(
        app="demo",
        id="demo-mcp",
        path=Path("demo-mcp.yaml"),
        record={"kind": "vendor_mcp", "transport": transport, "auth": auth},
    )


def _entry(**fields) -> keys.KeyEntry:
    return keys.KeyEntry(
        app="demo", provider="demo-mcp", test_account="fa-ci-demo", **fields
    )


def test_query_header_prefix_and_url_variable_parts():
    auth = {
        "kind": "api_key",
        "credentialParts": [
            {"in": "query", "name": "api_key", "secret": True},
            {"in": "header", "name": "X-Token", "secret": True, "prefix": "Token "},
            {"in": "url_variable", "name": "secretPath", "secret": True},
            {"in": "header", "name": "X-Region", "secret": False},
        ],
    }
    p = _provider(auth, url="https://mcp.example.com/{secretPath}/mcp")
    cred = resolve(
        p,
        _entry(
            parts={
                "api_key": "query value/1",
                "X-Token": "header-value-123",
                "secretPath": "a/b c",
                "X-Region": "eu",
            }
        ),
    )
    assert (
        cred.url(p.transport["url"])
        == "https://mcp.example.com/a%2Fb%20c/mcp?api_key=query+value%2F1"
    )
    assert cred.headers == {"X-Token": "Token header-value-123", "X-Region": "eu"}
    assert set(cred.secrets) == {"query value/1", "header-value-123", "a/b c"}


def test_bearer_kinds_send_the_access_token():
    for kind in ("oauth2", "mcp_oauth"):
        cred = resolve(_provider({"kind": kind}), _entry(bearer="bearer-value-xyz"))
        assert cred.headers == {"Authorization": "Bearer bearer-value-xyz"}


def test_url_variables_are_declared_and_checked():
    declared = {
        "account": {
            "description": "Account",
            "pattern": "^[a-z0-9-]{1,40}$",
            "scope": "org",
        }
    }
    p = _provider(
        {"kind": "none"},
        url="https://{account}.example.com/mcp",
        url_variables=declared,
    )
    assert (
        resolve(p, _entry(url_variables={"account": "acme"})).url(p.transport["url"])
        == "https://acme.example.com/mcp"
    )
    with pytest.raises(Skip, match="url_variable_invalid:account"):
        resolve(p, _entry(url_variables={"account": "evil.com/x"}))
    with pytest.raises(Skip, match="url_variables_missing:account"):
        resolve(p, _entry()).url(p.transport["url"])
    with pytest.raises(Skip, match="url_variable_unknown"):
        resolve(p, _entry(url_variables={"account": "acme", "other": "x"}))


@pytest.mark.parametrize(
    "auth, fields, reason",
    [
        ({"kind": "oauth2"}, {}, "bearer_missing"),
        (
            {"kind": "oauth2"},
            {"bearer": "b-value-123", "parts": {"x": "y"}},
            "parts_given_for_oauth2",
        ),
        (
            {
                "kind": "api_key",
                "credentialParts": [{"in": "header", "name": "K", "secret": True}],
            },
            {"bearer": "b-value-123", "parts": {"K": "v"}},
            "bearer_given_for_api_key",
        ),
        ({"kind": "none"}, {"bearer": "b-value-123"}, "bearer_given_for_none"),
        (
            {
                "kind": "api_key",
                "credentialParts": [
                    {"in": "header", "name": "Mcp-Session-Id", "secret": True}
                ],
            },
            {"parts": {"Mcp-Session-Id": "v"}},
            "part_header_reserved:Mcp-Session-Id",
        ),
        (
            {
                "kind": "api_key",
                "credentialParts": [
                    {"in": "header", "name": "Mcp-Param-Q", "secret": True}
                ],
            },
            {"parts": {"Mcp-Param-Q": "v"}},
            "part_header_reserved:Mcp-Param-Q",
        ),
    ],
)
def test_entries_that_do_not_fit_the_auth_kind_are_refused(auth, fields, reason):
    with pytest.raises(Skip) as err:
        resolve(_provider(auth), _entry(**fields))
    assert err.value.reason == reason
    assert not isinstance(err.value, Unsupported)


@pytest.mark.parametrize("kind", ["basic", "service_account", "partner", "local_setup"])
def test_other_auth_kinds_are_an_unsupported_skip(kind):
    with pytest.raises(Unsupported, match=f"auth_kind_unsupported:{kind}"):
        resolve(_provider({"kind": kind}), _entry())
