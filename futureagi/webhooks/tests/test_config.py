"""Install settings (DESIGN.md sections 6.1 and 7.3): the FI_WEBHOOKS_TIME_SCALE guard (R10, WT-34), the hosted
profile ignoring operator escapes, and the real Public Suffix List agreeing with every URL vector. No database."""

from __future__ import annotations

import json
from pathlib import Path

from webhooks import config
from webhooks.core.addresses import url_decision
from webhooks.core.ipclass import Profile

CONF = Path(__file__).resolve().parents[1] / "core" / "conformance"


def test_time_scale_applies_only_off_hosted_with_debug(monkeypatch, caplog):
    monkeypatch.setenv("FI_WEBHOOKS_TIME_SCALE", "0.001")
    assert config.time_scale(hosted=False, debug=True) == 0.001
    assert config.time_scale(hosted=True, debug=True) == 1.0
    assert config.time_scale(hosted=False, debug=False) == 1.0
    assert "ignored" in caplog.text
    monkeypatch.setenv("FI_WEBHOOKS_TIME_SCALE", "7")
    assert config.time_scale(hosted=False, debug=True) == 1.0
    monkeypatch.delenv("FI_WEBHOOKS_TIME_SCALE")
    assert config.time_scale(hosted=False, debug=True) == 1.0


def test_hosted_profile_ignores_operator_escapes(monkeypatch):
    monkeypatch.setenv("FI_WEBHOOKS_ALLOW_HTTP", "true")
    monkeypatch.setenv("FI_WEBHOOKS_PRIVATE_CIDRS", "10.0.0.0/8")
    monkeypatch.setenv("FI_WEBHOOKS_ALLOWED_PRIVATE_HOSTS", "host.docker.internal")
    hosted = config.profile(hosted=True)
    assert (
        hosted.mode,
        hosted.allow_http,
        hosted.private_cidrs,
        hosted.allowed_private_hosts,
    ) == ("hosted", False, (), ())
    sh = config.profile(hosted=False)
    assert (sh.allow_http, sh.private_cidrs, sh.allowed_private_hosts) == (
        True,
        ("10.0.0.0/8",),
        ("host.docker.internal",),
    )
    assert config.extra_ca_file(hosted=True) is None


def test_real_public_suffix_list_agrees_with_the_url_vectors():
    data = json.loads((CONF / "addresses.json").read_text())
    profiles = {k: Profile.from_mapping(v) for k, v in data["profiles"].items()}
    assert config.public_suffix("hooks.acme.ai") == "ai"
    assert config.public_suffix("hooks.acme.co.uk") == "co.uk"
    assert config.public_suffix("svc.cluster.corp") is None
    for case in data["url"]:
        got = url_decision(
            case["url"],
            profiles[case["profile"]],
            config.public_suffix,
            case.get("policy"),
        )
        assert got.as_dict() == case["expected"], case["id"]


def test_hosted_refuses_to_send_until_its_gates_are_met(monkeypatch):
    monkeypatch.delenv("WEBHOOKS_RESOLVERS", raising=False)
    monkeypatch.delenv("WEBHOOKS_DENY_HOSTS", raising=False)
    monkeypatch.delenv("FI_WEBHOOKS_ENABLED", raising=False)
    assert config.send_blockers(hosted=False) == []
    gates = config.hosted_send_blockers(hosted=True)
    assert len(gates) == 3 and "WEBHOOKS_RESOLVERS" in gates[0]
    monkeypatch.setenv("WEBHOOKS_RESOLVERS", "1.1.1.1,8.8.8.8")
    monkeypatch.setenv("WEBHOOKS_DENY_HOSTS", "internal.futureagi.com")
    # The resolver that would use WEBHOOKS_RESOLVERS is WH-1c's: until it exists, hosted still refuses.
    assert config.hosted_send_blockers(hosted=True) == [
        "resolution through WEBHOOKS_RESOLVERS is not built yet (WH-1c)"
    ]
    monkeypatch.setattr(config, "HOSTED_RESOLVER_BUILT", True)
    assert config.send_blockers(hosted=True) == []
    monkeypatch.setenv("FI_WEBHOOKS_ENABLED", "false")
    assert config.send_blockers(hosted=False) == ["FI_WEBHOOKS_ENABLED=false"]
