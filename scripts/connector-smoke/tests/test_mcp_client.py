"""The MCP client the smoke speaks: both protocol eras, JSON and SSE answers,
paged tools/list, and a refused credential."""

from __future__ import annotations

import time

import pytest
from conftest import (
    LINEAR,
    LINEAR_BEARER,
    argv,
    checks,
    entry,
    env_with,
    linear_host,
    provider_of,
    report_of,
    run,
)
from fakemcp import STATELESS

KEY = "linear/linear-mcp"


def _run(net, tmp_path, bearer=LINEAR_BEARER):
    out = tmp_path / "out"
    code, text = run(
        argv(out, "--provider", KEY), env_with({KEY: entry(bearer=bearer)}), net
    )
    return code, text, out


def test_legacy_era_keeps_a_session_and_ends_it(fake, net, tmp_path):
    fake.hosts[LINEAR] = linear_host()
    code, _, out = _run(net, tmp_path)
    assert code == 0
    seen = fake.requests(LINEAR)
    assert [s.rpc for s in seen[:4]] == [
        "server/discover",
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    after_init = seen[2:]
    assert all(s.headers.get("mcp-session-id") == "fake-session-1" for s in after_init)
    assert all(
        s.headers.get("mcp-protocol-version") == "2025-11-25" for s in after_init
    )
    assert seen[-1].method == "DELETE"
    assert provider_of(report_of(out), KEY)["era"] == "2025-11-25"


def test_stateless_era_mirrors_headers_and_holds_no_session(fake, net, tmp_path):
    fake.hosts[LINEAR] = linear_host(era=STATELESS)
    code, _, out = _run(net, tmp_path)
    assert code == 0
    seen = fake.requests(LINEAR)
    assert all(s.method == "POST" for s in seen)
    assert all("mcp-session-id" not in s.headers for s in seen)
    assert all(s.headers.get("mcp-protocol-version") == STATELESS for s in seen)
    assert all(s.headers.get("mcp-method") == s.rpc for s in seen)
    assert {s.headers.get("mcp-name") for s in seen if s.rpc == "tools/call"} == {
        "list_issues"
    }
    p = provider_of(report_of(out), KEY)
    assert p["era"] == STATELESS
    # the hash does not depend on the era
    assert checks(p, "toolsHash")[0]["status"] == "pass"


def test_sse_answers_and_paged_tools_list(fake, net, tmp_path):
    fake.hosts[LINEAR] = linear_host(sse=True, page_size=1)
    code, _, out = _run(net, tmp_path)
    assert code == 0
    assert len([s for s in fake.requests(LINEAR) if s.rpc == "tools/list"]) == 4
    c = checks(provider_of(report_of(out), KEY), "toolsHash")[0]
    assert c["status"] == "pass" and c["liveCount"] == 4


def test_refused_credential_fails_the_session(fake, net, tmp_path):
    fake.hosts[LINEAR] = linear_host()
    code, text, out = _run(net, tmp_path, bearer="fixture-wrong-bearer-value")
    assert code == 1
    p = provider_of(report_of(out), KEY)
    assert p["status"] == "fail"
    assert checks(p, "session") == [
        {"check": "session", "status": "fail", "reason": "credential_refused"}
    ]
    assert fake.calls(LINEAR) == []
    assert (
        "fixture-wrong-bearer-value"
        not in text + (out / "drift-report.json").read_text()
    )


def test_unreachable_host_fails_with_a_code(fake, net, tmp_path):
    # no fake host configured: every request answers 404
    code, _, out = _run(net, tmp_path)
    assert code == 1
    reason = checks(provider_of(report_of(out), KEY), "session")[0]["reason"]
    assert reason in ("no_answer_http_404", "initialize_failed")


def test_timeout_option_bounds_each_request(fake, net, tmp_path):
    fake.hosts[LINEAR] = linear_host(delay=1.5)
    out = tmp_path / "out"
    started = time.monotonic()
    code, _ = run(
        argv(out, "--provider", KEY, "--timeout", "0.3"),
        env_with({KEY: entry(bearer=LINEAR_BEARER)}),
        net,
    )
    assert code == 1
    assert time.monotonic() - started < 1.4  # gave up before the server answered
    p = provider_of(report_of(out), KEY)
    assert checks(p, "session") == [
        {"check": "session", "status": "fail", "reason": "timeout"}
    ]


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "601", "soon"])
def test_timeout_option_refuses_bad_values(value, fake, net, route, tmp_path):
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as err:
        run(
            argv(out, "--timeout", value),
            env_with({KEY: entry(bearer=LINEAR_BEARER)}),
            net,
        )
    assert err.value.code == 2
    assert fake.requests() == [] and not out.exists()


@pytest.mark.parametrize(
    "era, reason",
    [
        ("2024-11-05", "era_unsupported:2024-11-05"),
        ("2027-01-01", "era_unsupported:2027-01-01"),
    ],
)
def test_an_era_the_client_does_not_speak_is_skipped_by_name(
    era, reason, fake, net, tmp_path, monkeypatch
):
    """An L0-valid era outside the client's own is a named skip, before any
    request: never a failed session on a healthy server."""
    from connector_smoke import catalog

    real = catalog.Provider.transport.fget

    def transport(self):
        t = dict(real(self))
        if self.app == "linear":
            t["era"] = era
        return t

    monkeypatch.setattr(catalog.Provider, "transport", property(transport))
    fake.hosts[LINEAR] = linear_host()
    code, text, out = _run(net, tmp_path)
    assert code == 0, text
    assert fake.requests(LINEAR) == []
    assert f"SKIPPED    {KEY}: {reason}" in text
    assert not out.exists()  # nothing was smoked on the test account


@pytest.mark.parametrize("era", ["auto", "2026-07-28", "2025-11-25", "2025-06-18"])
def test_the_eras_the_client_speaks_are_smoked(era):
    from pathlib import Path

    from connector_smoke import smoke
    from connector_smoke.catalog import Provider

    record = {"kind": "vendor_mcp", "transport": {"kind": "http", "era": era}}
    p = Provider(app="a", id="p", path=Path("p.yaml"), record=record)
    assert smoke.unsupported(p) is None
