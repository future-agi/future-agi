"""SIEM export's scalar sanitizer and org redactor against the gateway's.

siem_scalar ports the gateway's span scalar sanitizer (telemetry.SanitizeText)
and siem_redactor the org privacy redactor it applies (tenant.Store.Redactor).
Their decisions may redact more than the gateway's, never less. The parity
test reads the gateway's own decisions, dumped by the Go test
TestSIEMScalarCorpusExport (AGENTCC_SIEM_CORPUS_OUT) over the G16 frozen
corpus, its carriers and a seeded fuzz set; it runs where that dump is given
(AGENTCC_SIEM_CORPUS) and writes only counts and names
(AGENTCC_SIEM_PARITY_OUT).
"""

import json
import os

import pytest

from agentcc.services import siem_payloads, siem_redactor, siem_scalar
from agentcc.tests.siem_privacy_corpus import OPAQUE

CORPUS = os.environ.get("AGENTCC_SIEM_CORPUS", "")
PARITY_OUT = os.environ.get("AGENTCC_SIEM_PARITY_OUT", "")
REDACTED = siem_scalar.REDACTED


def _redactor(privacy):
    return siem_redactor.from_privacy(privacy)


def test_org_redactor_follows_the_gateway_store():
    cust = [{"name": "cust", "pattern": "CUST-[0-9]+"}]
    assert _redactor(None).redacts("CUST-1") is False
    assert _redactor({}).redacts("CUST-1") is False
    assert _redactor({"mode": "full"}).redacts("anything") is False  # not enabled
    assert _redactor({"enabled": False, "mode": "full"}).redacts("x") is False
    assert _redactor({"enabled": True, "mode": "full"}).redacts("researcher") is True
    assert _redactor({"enabled": True, "mode": "none"}).redacts("CUST-1") is False
    patterns = _redactor({"enabled": True, "mode": "patterns", "redact_patterns": cust})
    assert patterns.redacts("CUST-424242") and not patterns.redacts("researcher")
    # An empty mode is patterns, as the gateway's store builds it.
    assert _redactor({"enabled": True, "redact_patterns": cust}).redacts("CUST-9")
    assert _redactor({"Enabled": True, "Mode": "full"}).redacts("x")
    assert _redactor({"enabled": True, "redactPatterns": cust}).redacts("CUST-9")
    skipped = [None, {"name": "empty", "pattern": ""}, {"name": "none"}]
    assert not _redactor({"enabled": True, "redact_patterns": skipped}).redacts(
        "researcher"
    )


@pytest.mark.parametrize(
    "privacy",
    [
        {"enabled": True, "mode": "strict"},
        {"enabled": True, "mode": 3},
        {"enabled": "yes", "mode": "patterns", "redact_patterns": "CUST"},
        {"enabled": True, "redact_patterns": [{"name": "bad", "pattern": "(unclosed"}]},
        {
            "enabled": True,
            "redact_patterns": [{"name": "posix", "pattern": "[[:alpha:]]"}],
        },
        {"enabled": True, "redact_patterns": [{"name": "class", "pattern": "\\pL+"}]},
        {"enabled": True, "redact_patterns": ["CUST-[0-9]+"]},
        {"enabled": True, "redact_patterns": [{"pattern": 7}]},
        ["enabled"],
    ],
)
def test_org_redactor_fails_closed_where_python_cannot_match_re2(privacy):
    assert _redactor(privacy).redacts("researcher") is True


def test_sanitizer_matches_the_gateway_scalar_fixtures():
    for value in (
        "tool_call_01",
        "researcher",
        "run_R1",
        "up",
        "tools/call",
        "2025-11-25",
        "-32603",
        "agentcc-gateway",
        "alpha.beta:read",
        "42",
    ):
        assert siem_scalar.sanitize(value) == value
    unapproved = (
        "researcher\n",
        "a" * 257,
        "name with spaces",
        "password:" + OPAQUE,
        "clientSecret:" + OPAQUE,
        "X_Auth_42:" + OPAQUE,
        "https://service.invalid/path",
    )
    # Indexes only: a failure message never carries a synthetic credential.
    kept = [i for i, v in enumerate(unapproved) if siem_scalar.sanitize(v) != REDACTED]
    assert kept == []


def test_sanitizer_applies_the_redactor_last():
    full = _redactor({"enabled": True, "mode": "full"})
    assert siem_scalar.sanitize("researcher", full) == REDACTED
    assert siem_scalar.sanitize("", full) == ""
    assert siem_payloads.scalar("researcher", full) == REDACTED


@pytest.mark.skipif(not CORPUS, reason="needs the gateway's dump (AGENTCC_SIEM_CORPUS)")
def test_never_keeps_what_the_gateway_redacts():
    with open(CORPUS) as fh:
        dump = json.load(fh)
    redactors = {
        name: _redactor(privacy) for name, privacy in dump["redactors"].items()
    }
    missed, over = [], {}
    for fixture in dump["fixtures"]:
        value = fixture["value"]
        for name, gateway in fixture["gateway"].items():
            ours = siem_scalar.sanitize(value, redactors.get(name))
            if gateway != value and ours == value:
                missed.append(f"{fixture['group']}/{fixture['name']}/{name}")
            elif gateway == value and ours != value:
                over.setdefault(name, []).append(
                    f"{fixture['group']}/{fixture['name']}"
                )
    summary = {
        "fixtures": len(dump["fixtures"]),
        "redactors": sorted(redactors),
        "gateway_redacts_siem_keeps": missed,
        "siem_redacts_gateway_keeps_counts": {k: len(v) for k, v in over.items()},
        "siem_redacts_gateway_keeps": {k: v[:50] for k, v in over.items()},
    }
    if PARITY_OUT:
        with open(PARITY_OUT, "w") as fh:
            json.dump(summary, fh, indent=2, sort_keys=True)
    assert len(dump["fixtures"]) > 1000
    assert missed == []
