"""Our REST connector (K2): the HTTP credential check against the vendor and
the vendor's API description against upstream.spec.sha256. Its tools are
served by agentcc-connectors and are not called here."""

from __future__ import annotations

from conftest import (
    GMAIL,
    GMAIL_BEARER,
    PROFILE_PATH,
    SPEC_PATH,
    argv,
    checks,
    entry,
    env_with,
    gmail_host,
    provider_of,
    report_of,
    run,
)

KEY = "gmail/futureagi-rest"


def _run(net, tmp_path, **fields):
    out = tmp_path / "out"
    code, text = run(
        argv(out, "--provider", KEY),
        env_with({KEY: entry("fa-ci-gmail", bearer=GMAIL_BEARER, **fields)}),
        net,
    )
    return code, text, out


def test_rest_verifies_the_credential_and_the_spec(fake, net, tmp_path):
    fake.hosts[GMAIL] = gmail_host()
    code, text, out = _run(net, tmp_path)
    assert code == 0, text
    by_path = {s.path: s for s in fake.requests(GMAIL)}
    assert set(by_path) == {PROFILE_PATH, SPEC_PATH}
    assert by_path[PROFILE_PATH].headers["authorization"] == f"Bearer {GMAIL_BEARER}"
    # the public spec is fetched without the credential
    assert "authorization" not in by_path[SPEC_PATH].headers
    # no MCP session: tools are not listed
    assert [s for s in fake.requests(GMAIL) if s.method == "POST"] == []
    p = provider_of(report_of(out), KEY)
    assert p["status"] == "pass"
    assert checks(p, "verify")[0] == {
        "check": "verify",
        "status": "pass",
        "http": "GET",
        "httpStatus": 200,
    }
    spec = checks(p, "spec")[0]
    assert spec["status"] == "pass" and spec["liveSha256"] == spec["pinnedSha256"]
    report_text = (out / "drift-report.json").read_text()
    assert (
        "someone@vendor.example" not in report_text and GMAIL_BEARER not in report_text
    )


def test_rest_spec_change_is_drift(fake, net, tmp_path):
    fake.hosts[GMAIL] = gmail_host(
        spec=b'{"kind": "discovery#restDescription", "version": "v2"}'
    )
    code, _, out = _run(net, tmp_path)
    assert code == 1
    spec = checks(provider_of(report_of(out), KEY), "spec")[0]
    assert spec["status"] == "drift" and spec["liveSha256"] != spec["pinnedSha256"]


def test_rest_credential_refused_fails(fake, net, tmp_path):
    host = gmail_host()
    host.get[PROFILE_PATH] = (401, {}, b"")
    fake.hosts[GMAIL] = host
    code, _, out = _run(net, tmp_path)
    assert code == 1
    v = checks(provider_of(report_of(out), KEY), "verify")[0]
    assert (v["status"], v["reason"], v["httpStatus"]) == (
        "fail",
        "credential_refused",
        401,
    )


def test_rest_tool_arguments_are_reported_unused(fake, net, tmp_path):
    fake.hosts[GMAIL] = gmail_host()
    code, _, out = _run(net, tmp_path, arguments={"search_threads": {"q": "is:unread"}})
    assert code == 0
    c = checks(provider_of(report_of(out), KEY), "call")[0]
    assert c == {
        "check": "call",
        "status": "skipped",
        "reason": "tool_served_by_agentcc_connectors",
    }


def test_a_provider_with_nothing_checked_is_not_a_pass(
    fake, net, tmp_path, monkeypatch
):
    """K2 with no HTTP verify and no spec checks nothing: incomplete, exit 1."""
    from connector_smoke import catalog

    real_auth = catalog.Provider.auth.fget
    real_record = catalog.Provider.upstream_spec.fget

    def auth(self):
        a = dict(real_auth(self))
        a.pop("verify", None)
        return a

    monkeypatch.setattr(catalog.Provider, "auth", property(auth))
    monkeypatch.setattr(
        catalog.Provider,
        "upstream_spec",
        property(lambda self: None if self.app == "gmail" else real_record(self)),
    )
    fake.hosts[GMAIL] = gmail_host()
    code, text, out = _run(net, tmp_path)
    assert code == 1
    assert "INCOMPLETE" in text
    rep = report_of(out)
    assert provider_of(rep, KEY)["status"] == "incomplete"
    assert rep["summary"]["incomplete"] == 1
    assert fake.requests(GMAIL) == []


def test_a_tool_verify_with_only_the_spec_is_not_an_l4_pass(
    fake, net, tmp_path, monkeypatch
):
    """The plan's K2 shape, auth.verify {tool: get_profile}: the tool is served
    by agentcc-connectors, so no request carries the account's credential.
    The public spec alone proves nothing about the account: incomplete, exit 1,
    never a pass on the named test account."""
    from connector_smoke import catalog

    real_auth = catalog.Provider.auth.fget

    def auth(self):
        a = dict(real_auth(self))
        if self.app == "gmail":
            a["verify"] = {"tool": "get_profile"}
        return a

    monkeypatch.setattr(catalog.Provider, "auth", property(auth))
    fake.hosts[GMAIL] = gmail_host()
    code, text, out = _run(net, tmp_path)
    assert code == 1, text
    assert "INCOMPLETE" in text and "credential_not_exercised" in text
    rep = report_of(out)
    p = provider_of(rep, KEY)
    assert p["status"] == "incomplete" and rep["summary"]["pass"] == 0
    assert checks(p, "verify") == [
        {
            "check": "verify",
            "status": "skipped",
            "tool": "get_profile",
            "reason": "tool_served_by_agentcc_connectors",
        }
    ]
    assert checks(p, "spec")[0]["status"] == "pass"
    assert checks(p, "credential") == [
        {
            "check": "credential",
            "status": "incomplete",
            "reason": "credential_not_exercised",
        }
    ]
    # the only request was the public spec, without the credential
    seen = fake.requests(GMAIL)
    assert [(s.method, s.path) for s in seen] == [("GET", SPEC_PATH)]
    assert "authorization" not in seen[0].headers


def test_spec_drift_without_an_exercised_credential_says_so(
    fake, net, tmp_path, monkeypatch
):
    """Drift on a provider whose credential was never exercised stays drift,
    and the report says the account was not checked."""
    from connector_smoke import catalog

    real_auth = catalog.Provider.auth.fget

    def auth(self):
        a = dict(real_auth(self))
        a["verify"] = {"tool": "get_profile"}
        return a

    monkeypatch.setattr(catalog.Provider, "auth", property(auth))
    fake.hosts[GMAIL] = gmail_host(spec=b'{"kind": "discovery#restDescription"}')
    code, _, out = _run(net, tmp_path)
    assert code == 1
    p = provider_of(report_of(out), KEY)
    assert p["status"] == "drift"
    assert checks(p, "credential")[0]["reason"] == "credential_not_exercised"
