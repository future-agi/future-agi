"""The gates: no key skips cleanly; keys alone never start a live run; an
entry without a named test account records nothing."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from conftest import (
    CLOSE,
    LINEAR,
    LINEAR_BEARER,
    argv,
    close_host,
    entry,
    env_with,
    keys_doc,
    linear_host,
    report_of,
    run,
)

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _nothing_happened(fake, route, out):
    assert fake.requests() == [], "a request reached the fake vendor"
    assert route.refused == [], "a host was dialled"
    assert not out.exists(), "something was written"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"CONNECTOR_SMOKE_LIVE": "1"},
        {"CONNECTOR_SMOKE_KEYS": ""},
        {"CONNECTOR_SMOKE_KEYS": "  \n", "CONNECTOR_SMOKE_LIVE": "1"},
    ],
    ids=["nothing", "gate-only", "empty-keys", "blank-keys-with-gate"],
)
def test_skips_without_key(env, fake, net, route, tmp_path):
    fake.hosts[LINEAR] = linear_host()
    out = tmp_path / "out"
    code, text = run(argv(out), env, net)
    assert code == 0
    assert "skipped: no keys" in text
    assert "nothing contacted, nothing written" in text
    _nothing_happened(fake, route, out)


def test_skips_without_key_as_a_process(tmp_path):
    """The real entry point, in a process whose environment has no keys."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CONNECTOR_SMOKE_")}
    out = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, "-m", "connector_smoke", "--out", str(out)],
        cwd=PKG_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "skipped: no keys" in proc.stdout
    assert not out.exists()


def test_provider_without_key_is_skipped_and_never_contacted(
    fake, net, route, tmp_path
):
    fake.hosts[LINEAR] = linear_host()
    fake.hosts[CLOSE] = close_host()
    out = tmp_path / "out"
    code, _ = run(
        argv(out), env_with({"linear/linear-mcp": entry(bearer=LINEAR_BEARER)}), net
    )
    assert code == 0
    assert fake.requests(CLOSE) == []
    providers = {(p["app"], p["provider"]): p for p in report_of(out)["providers"]}
    assert providers[("close", "close-mcp")] == {
        "app": "close",
        "provider": "close-mcp",
        "kind": "vendor_mcp",
        "status": "skipped",
        "reason": "no_key",
    }
    assert providers[("gmail", "futureagi-rest")]["reason"] == "no_key"
    assert providers[("linear", "linear-mcp")]["status"] == "pass"


@pytest.mark.parametrize("live", [None, "", "0", "true", "yes", " 1"])
def test_refuses_without_the_live_gate(live, fake, net, route, tmp_path):
    fake.hosts[LINEAR] = linear_host()
    env = env_with({"linear/linear-mcp": entry(bearer=LINEAR_BEARER)}, live=False)
    if live is not None:
        env["CONNECTOR_SMOKE_LIVE"] = live
    out = tmp_path / "out"
    code, text = run(argv(out), env, net)
    assert code == 3
    assert "refused" in text and "CONNECTOR_SMOKE_LIVE is not 1" in text
    assert LINEAR_BEARER not in text
    _nothing_happened(fake, route, out)


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"testAccount": ""},
        {"testAccount": "x"},
        {"testAccount": "someone@vendor.example"},
        {"testAccount": "ghp_" + "a" * 36},
        {"testAccount": LINEAR_BEARER},
        {"testAccount": None},
    ],
    ids=[
        "absent",
        "empty",
        "too-short",
        "an-email",
        "credential-shaped",
        "the-credential",
        "null",
    ],
)
def test_records_nothing_without_a_test_account(bad, fake, net, route, tmp_path):
    fake.hosts[LINEAR] = linear_host()
    out = tmp_path / "out"
    code, text = run(
        argv(out),
        env_with({"linear/linear-mcp": {**bad, "bearer": LINEAR_BEARER}}),
        net,
    )
    assert code == 2
    assert "testAccount" in text
    assert LINEAR_BEARER not in text
    _nothing_happened(fake, route, out)


@pytest.mark.parametrize(
    "doc",
    [
        "not json",
        '{"format": "connector-smoke-keys/v2", "providers": {}}',
        '{"format": "connector-smoke-keys/v1", "providers": {"linear/linear-mcp": {"testAccount": "fa-ci", "token": "x"}}}',
        '{"format": "connector-smoke-keys/v1", "providers": {"Linear": {"testAccount": "fa-ci"}}}',
        '{"format": "connector-smoke-keys/v1", "providers": {"linear/linear-mcp": {"testAccount": "fa-ci", "sandbox": "yes"}}}',
    ],
)
def test_malformed_keys_are_a_configuration_error(doc, fake, net, route, tmp_path):
    out = tmp_path / "out"
    code, text = run(
        argv(out), {"CONNECTOR_SMOKE_KEYS": doc, "CONNECTOR_SMOKE_LIVE": "1"}, net
    )
    assert code == 2
    assert "configuration error" in text
    _nothing_happened(fake, route, out)


def test_nothing_written_when_nothing_is_smoked(fake, net, route, tmp_path):
    out = tmp_path / "out"
    code, text = run(
        argv(out), env_with({"notion/notion-mcp": entry(bearer=LINEAR_BEARER)}), net
    )
    assert code == 0
    assert "nothing smoked" in text
    _nothing_happened(fake, route, out)


def test_unknown_provider_filter_is_a_configuration_error(fake, net, route, tmp_path):
    out = tmp_path / "out"
    code, text = run(
        argv(out, "--provider", "notion/notion-mcp"),
        {"CONNECTOR_SMOKE_KEYS": keys_doc({}), "CONNECTOR_SMOKE_LIVE": "1"},
        net,
    )
    assert code == 2
    assert "--provider" in text
    _nothing_happened(fake, route, out)


def test_provider_filter_limits_the_run(fake, net, route, tmp_path):
    fake.hosts[LINEAR] = linear_host()
    fake.hosts[CLOSE] = close_host()
    out = tmp_path / "out"
    code, _ = run(
        argv(out, "--provider", "linear/linear-mcp"),
        env_with(
            {
                "linear/linear-mcp": entry(bearer=LINEAR_BEARER),
                "close/close-mcp": entry(
                    "fa-ci-close", parts={"Close-API-Key": "fixture-close-key"}
                ),
            }
        ),
        net,
    )
    assert code == 0
    assert fake.requests(CLOSE) == []
    rep = report_of(out)
    assert [p["provider"] for p in rep["providers"]] == ["linear-mcp"]
    assert rep["unusedKeys"] == 1
