"""Read-only tools by default; writes only against a sandbox tenant."""

from __future__ import annotations

import pytest
from conftest import (
    LINEAR,
    LINEAR_BEARER,
    argv,
    checks,
    entry,
    env_with,
    linear_host,
    live_tools,
    provider_of,
    report_of,
    run,
)
from connector_smoke import policy

KEY = "linear/linear-mcp"


def _run(fake, net, tmp_path, *extra, **fields):
    out = tmp_path / "out"
    code, text = run(
        argv(out, "--provider", KEY, *extra),
        env_with({KEY: entry(bearer=LINEAR_BEARER, **fields)}),
        net,
    )
    return code, text, provider_of(report_of(out), KEY)


def _skips(p, check="call"):
    return {
        (c.get("tool"), c["reason"])
        for c in checks(p, check)
        if c["status"] == "skipped"
    }


def test_readonly_by_default(fake, net, tmp_path):
    """Even a sandbox entry gets no write without --allow-writes."""
    fake.hosts[LINEAR] = linear_host()
    code, _, p = _run(fake, net, tmp_path, sandbox=True)
    assert code == 0
    assert fake.calls(LINEAR) == [
        "list_issues",
        "list_issues",
    ]  # verify, then the cassette's read
    assert [s.arguments for s in fake.requests(LINEAR) if s.rpc == "tools/call"] == [
        {},
        {"team": "ENG"},
    ]
    assert {
        ("create_issue", "writes_not_allowed"),
        ("delete_issue", "writes_not_allowed"),
    } <= _skips(p)
    assert all(
        c["write"] is False
        for c in checks(p, "call") + checks(p, "verify")
        if c["status"] == "pass"
    )
    assert p["status"] == "pass"


@pytest.mark.parametrize(
    "allow, sandbox, writes",
    [
        (False, False, False),
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_writes_need_allow_writes_and_a_sandbox_tenant(
    allow, sandbox, writes, fake, net, tmp_path
):
    fake.hosts[LINEAR] = linear_host()
    extra = ["--allow-writes"] if allow else []
    code, _, p = _run(
        fake,
        net,
        tmp_path,
        *extra,
        sandbox=sandbox,
        arguments={"delete_issue": {"id": "ISS-43"}},
    )
    assert code == 0
    called = fake.calls(LINEAR)
    if writes:
        assert called == ["list_issues", "list_issues", "create_issue", "delete_issue"]
        create = next(s for s in fake.requests(LINEAR) if s.tool == "create_issue")
        delete = next(s for s in fake.requests(LINEAR) if s.tool == "delete_issue")
        assert create.arguments == {"title": "Smoke test"}  # the cassette's, unscrubbed
        assert delete.arguments == {
            "id": "ISS-43"
        }  # the entry's: the cassette's id was scrubbed
        assert {
            c["tool"] for c in checks(p, "call") if c["status"] == "pass" and c["write"]
        } == {"create_issue", "delete_issue"}
    else:
        assert called == ["list_issues", "list_issues"]
        reason = "write_needs_sandbox" if allow else "writes_not_allowed"
        assert {("create_issue", reason), ("delete_issue", reason)} <= _skips(p)


def test_tool_not_in_manifest_is_never_called(fake, net, tmp_path):
    """archive_issue is listed live, read-only by its own annotations, and the
    entry gives it arguments: still never called."""
    fake.hosts[LINEAR] = linear_host()
    code, _, p = _run(
        fake,
        net,
        tmp_path,
        "--allow-writes",
        sandbox=True,
        arguments={"archive_issue": {"id": "ISS-1"}},
    )
    assert code == 0
    assert "archive_issue" not in fake.calls(LINEAR)
    assert ("archive_issue", "not_in_manifest") in _skips(p)


def test_live_annotations_never_widen(fake, net, tmp_path):
    tools = live_tools()
    tools[0]["annotations"] = {
        **tools[0]["annotations"],
        "readOnlyHint": False,
    }  # list_issues now says it writes
    fake.hosts[LINEAR] = linear_host(tools=tools)
    code, _, p = _run(fake, net, tmp_path)
    assert code == 1
    assert fake.calls(LINEAR) == []
    assert ("list_issues", "live_annotations_widen") in _skips(p, "verify")
    assert ("list_issues", "live_annotations_widen") in _skips(p)
    assert checks(p, "annotations")[0] == {
        "check": "annotations",
        "status": "drift",
        "widened": ["list_issues"],
    }


def test_tool_missing_live_is_not_called(fake, net, tmp_path):
    fake.hosts[LINEAR] = linear_host(
        tools=[t for t in live_tools() if t["name"] != "list_issues"]
    )
    code, _, p = _run(fake, net, tmp_path)
    assert code == 1  # the tools hash drifted too
    assert fake.calls(LINEAR) == []
    assert ("list_issues", "not_in_live_list") in _skips(p, "verify")


def test_entry_arguments_replace_scrubbed_and_recorded_ones(fake, net, tmp_path):
    fake.hosts[LINEAR] = linear_host()
    code, _, p = _run(fake, net, tmp_path, arguments={"list_issues": {"team": "OPS"}})
    assert code == 0
    assert [s.arguments for s in fake.requests(LINEAR) if s.rpc == "tools/call"] == [
        {"team": "OPS"}
    ] * 3
    assert not any(
        c["reason"] == "arguments_scrubbed"
        for c in checks(p, "call")
        if c["status"] == "skipped"
    )


@pytest.mark.parametrize(
    "live, widens",
    [
        (None, False),
        ({}, False),
        ({"readOnlyHint": True}, False),
        ({"readOnlyHint": True, "destructiveHint": True}, False),
        ({"readOnlyHint": False}, True),
        ({"destructiveHint": True}, True),
        ({"destructiveHint": False}, False),
    ],
)
def test_live_widens(live, widens):
    tool = {"name": "t"} if live is None else {"name": "t", "annotations": live}
    assert policy.live_widens(tool) is widens


def test_manifest_decides_read_only():
    assert policy.manifest_read_only(
        {"annotations": {"readOnlyHint": True, "destructiveHint": False}}
    )
    assert not policy.manifest_read_only(
        {"annotations": {"readOnlyHint": False, "destructiveHint": False}}
    )
    assert not policy.manifest_read_only(
        {"annotations": {"readOnlyHint": True, "destructiveHint": True}}
    )
    assert not policy.manifest_read_only({})


def test_live_calls_are_capped(fake, net, tmp_path, monkeypatch):
    from connector_smoke import cli, smoke

    real = smoke.Options
    monkeypatch.setattr(cli.smoke, "Options", lambda **kw: real(**kw, max_calls=1))
    fake.hosts[LINEAR] = linear_host()
    code, _, p = _run(fake, net, tmp_path)
    assert code == 0
    assert fake.calls(LINEAR) == [
        "list_issues",
        "list_issues",
    ]  # verify, then exactly one call
    assert [c["reason"] for c in checks(p, "call") if c["status"] == "skipped"].count(
        "max_calls_reached"
    ) == 0
    monkeypatch.setattr(cli.smoke, "Options", lambda **kw: real(**kw, max_calls=0))
    fake.reset()
    fake.hosts[LINEAR] = linear_host()
    code, _, p = _run(fake, net, tmp_path)
    assert fake.calls(LINEAR) == ["list_issues"]  # verify only
    assert ("list_issues", "max_calls_reached") in _skips(p)


def test_annotations_compare_nothing_when_no_read_only_tool_is_live(
    fake, net, tmp_path
):
    """The manifest's read-only tools are all missing live: no annotation was
    compared, so the check is skipped, not passed."""
    fake.hosts[LINEAR] = linear_host(
        tools=[t for t in live_tools() if not t["annotations"].get("readOnlyHint")]
    )
    _, _, p = _run(fake, net, tmp_path)
    assert checks(p, "annotations") == [
        {
            "check": "annotations",
            "status": "skipped",
            "reason": "no_read_only_tools_live",
        }
    ]
