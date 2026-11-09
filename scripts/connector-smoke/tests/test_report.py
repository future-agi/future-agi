"""The report and the output never hold a credential, a live value or an
email; where they would, nothing is written."""

from __future__ import annotations

import json

import pytest
from conftest import LINEAR, LINEAR_BEARER, argv, entry, env_with, linear_host, run
from connector_smoke import report

KEY = "linear/linear-mcp"


def test_vendor_echoing_the_token_and_emails_never_reaches_the_report(
    fake, net, tmp_path
):
    echo = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "issues": [
                            {
                                "id": "ISS-1",
                                "title": f"token {LINEAR_BEARER}",
                                "state": {"name": "x"},
                                "assignee": {"email": "someone@vendor.example"},
                                "labels": [],
                            }
                        ]
                    }
                ),
            }
        ],
        "isError": False,
    }
    fake.hosts[LINEAR] = linear_host(
        results={**linear_host().results, "list_issues": echo}
    )
    out = tmp_path / "out"
    code, text = run(
        argv(out, "--provider", KEY), env_with({KEY: entry(bearer=LINEAR_BEARER)}), net
    )
    assert code == 0
    written = (out / "drift-report.json").read_text()
    for value in (LINEAR_BEARER, "someone@vendor.example", "ISS-1", "token "):
        assert value not in written and value not in text


def test_a_report_that_would_hold_a_credential_is_not_written(fake, net, tmp_path):
    """A credential equal to a tool name the report must carry: the write is
    refused, and nothing is written (fail closed)."""
    bearer = "list_issues"
    fake.hosts[LINEAR] = linear_host(require={"Authorization": f"Bearer {bearer}"})
    out = tmp_path / "out"
    code, text = run(
        argv(out, "--provider", KEY), env_with({KEY: entry(bearer=bearer)}), net
    )
    assert code == 4
    assert "refused" in text
    assert not out.exists()


@pytest.mark.parametrize(
    "text, code",
    [
        ("value fixture-secret-123", "holds_a_credential_value"),
        ("sk-ant-" + "a" * 30, "holds_a_credential_shape"),
        ("mail someone@vendor.example", "holds_an_email"),
    ],
)
def test_check_safe(text, code):
    with pytest.raises(report.UnsafeReport) as err:
        report.check_safe(text, ["fixture-secret-123"])
    assert err.value.code == code


def test_placeholder_email_is_not_an_email():
    report.check_safe("recorded user-1@example.com", [])


def test_write_is_atomic_and_only_the_report(tmp_path):
    path = report.write(tmp_path / "out", '{"format": "connector-smoke-report/v1"}\n')
    assert path.name == "drift-report.json"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["drift-report.json"]


def test_status_is_the_worst_check():
    r = report.ProviderResult(
        app="a", provider="p", kind="vendor_mcp", test_account="fa-ci"
    )
    r.add("session", "pass")
    r.add("call", "skipped", reason="writes_not_allowed")
    assert r.finish().status == "pass"
    r.add("toolsHash", "drift")
    assert r.finish().status == "drift"
    r.add("verify", "fail", reason="credential_refused")
    assert r.finish().status == "fail"
    empty = report.ProviderResult(
        app="a", provider="p", kind="vendor_mcp", test_account="fa-ci"
    )
    empty.add("call", "skipped", reason="writes_not_allowed")
    assert empty.finish().status == "incomplete"  # nothing checked is not a pass


def test_a_pass_needs_a_check_that_carried_the_credential():
    """L4 is auth.verify against the test account: a provider whose only
    passing checks never sent the credential is not a pass."""

    def result(*checks):
        r = report.ProviderResult(
            app="a", provider="p", kind="rest", test_account="fa-ci"
        )
        for name, status in checks:
            r.add(name, status)
        return r

    spec_only = result(("spec", "pass"), ("verify", "skipped")).finish()
    assert spec_only.status == "incomplete"
    assert [c.as_dict() for c in spec_only.checks][-1] == {
        "check": "credential",
        "status": "incomplete",
        "reason": "credential_not_exercised",
    }
    # finishing again neither adds a second marker nor changes the status
    assert spec_only.finish().status == "incomplete"
    assert sum(c.check == "credential" for c in spec_only.checks) == 1
    # a check added later that carries the credential lifts the marker
    spec_only.add("verify", "pass")
    assert spec_only.finish().status == "pass"
    assert all(c.check != "credential" for c in spec_only.checks)
    assert result(("spec", "pass"), ("verify", "pass")).finish().status == "pass"
    assert result(("session", "pass"), ("toolsHash", "pass")).finish().status == "pass"
    for other in ("toolsHash", "annotations", "call", "spec", "cassetteKeys"):
        assert result((other, "pass")).finish().status == "incomplete", other
    # a failed check says what went wrong; no marker is added beside it
    refused = result(("verify", "fail")).finish()
    assert refused.status == "fail" and [c.check for c in refused.checks] == ["verify"]


def test_an_unexpected_error_is_a_failed_check_with_its_type_only(
    fake, net, tmp_path, monkeypatch
):
    """An exception's text may hold a URL with a key in it: only its type is
    reported, and the other providers still run."""
    from connector_smoke import cli

    def boom(provider, entry, opts):
        raise RuntimeError("https://mcp.linear.app/mcp?api_key=fixture-leaked-value")

    monkeypatch.setattr(cli.smoke, "run", boom)
    out = tmp_path / "out"
    code, text = run(
        argv(out, "--provider", KEY), env_with({KEY: entry(bearer=LINEAR_BEARER)}), net
    )
    assert code == 1
    written = (out / "drift-report.json").read_text()
    assert "fixture-leaked-value" not in written + text
    p = json.loads(written)["providers"][0]
    assert p["status"] == "fail"
    assert p["checks"] == [
        {"check": "run", "status": "fail", "reason": "internal_error:RuntimeError"}
    ]
