"""Shipped docs and examples describe the zero-egress telemetry opt-out.

Opting out of deployment telemetry sends nothing (test_optout_zero_egress.py).
Operator-facing text that still promises a registration ping, a remaining
registration attempt, or an offline retry contradicts that. Standard library
only: no Django, services, or network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
CHART = ROOT / "deploy/helm/futureagi"
TELEMETRY_PAGE = "docs.futureagi.com/docs/self-hosting/configuration/telemetry"

SHIPPED = sorted(
    [
        "README.md",
        "INSTALLATION.md",
        "TESTING.md",
        "deploy/README.md",
        "deploy/helm/futureagi/README.md",
        "deploy/helm/futureagi/values.yaml",
        "deploy/helm/futureagi/values.schema.json",
        "deploy/helm/futureagi/templates/NOTES.txt",
        ".env.example",
        "futureagi/.env.example",
        "deploy/.env.production.example",
        "deploy/env-reference.toml",
        "bin/install",
        "bin/install.ps1",
        "deploy/standalone/bin/bootstrap.py",
        "futureagi/tfc/deployment_telemetry/wire_reference.toml",
    ]
    + [
        path.relative_to(ROOT).as_posix()
        for path in (CHART / "examples").rglob("*.yaml")
    ]
)

# Wording the minimal-ping exception used before opt-out became zero egress.
OBSOLETE = re.compile(
    r"minimal ping"
    r"|registration ping"
    r"|census ping"
    r"|minimal (?:telemetry )?registration (?:attempt|ping|remains|still)"
    r"|registration (?:attempt )?remains"
    r"|still sends? one registration"
    r"|single minimal"
    r"|minimal opt-out"
    r"|opt-out registration"
    r"|harmlessly"
)


def prose(text: str) -> str:
    """Comment markers removed and wrapped lines joined, lowercased."""
    text = re.sub(r"(?m)^[ \t]*(?:#|//|;)+[ \t]?", "", text)
    return " ".join(text.split()).lower()


def comment_block_above(path: Path, key: str) -> str:
    """The contiguous comment lines directly above the first `key=` line."""
    lines = path.read_text().splitlines()
    index = next(
        i
        for i, line in enumerate(lines)
        if re.match(rf"#?\s*{re.escape(key)}=", line.strip())
    )
    block = []
    for line in reversed(lines[:index]):
        if not line.lstrip().startswith("#"):
            break
        block.append(line)
    return prose("\n".join(reversed(block)))


def test_the_scan_covers_the_airgap_example():
    assert "deploy/helm/futureagi/examples/airgap.yaml" in SHIPPED
    assert all((ROOT / path).is_file() for path in SHIPPED)


@pytest.mark.parametrize("path", SHIPPED)
def test_no_shipped_text_promises_an_opt_out_ping(path):
    text = prose((ROOT / path).read_text())
    found = [
        text[max(0, match.start() - 50) : match.end() + 50]
        for match in OBSOLETE.finditer(text)
    ]
    assert not found, f"{path} still describes an opt-out request: {found}"


def test_airgap_example_says_telemetry_sends_nothing():
    lines = (CHART / "examples/airgap.yaml").read_text().splitlines()
    header = []
    for line in lines:
        if not line.startswith("#"):
            break
        header.append(line)
    text = prose("\n".join(header))
    assert "sends nothing to future agi" in text
    assert "no registration attempt or retry" in text


@pytest.mark.parametrize(
    "path",
    [".env.example", "futureagi/.env.example", "deploy/.env.production.example"],
)
def test_env_examples_say_opting_out_sends_nothing(path):
    block = comment_block_above(ROOT / path, "FUTURE_AGI_TELEMETRY_DISABLED")
    assert "sends nothing to future agi" in block


def test_backend_env_example_covers_previously_registered_instances():
    block = comment_block_above(
        ROOT / "futureagi/.env.example", "FUTURE_AGI_TELEMETRY_DISABLED"
    )
    for fact in ("no registration", "re-registration", "previously registered"):
        assert fact in block


@pytest.mark.parametrize("path", ["README.md", "INSTALLATION.md"])
def test_external_page_is_not_cited_for_the_opt_out(path):
    # The external telemetry page still describes the old exception until the
    # docs site is regenerated; the repository is the opt-out reference.
    for paragraph in re.split(r"\n\s*\n", (ROOT / path).read_text()):
        if TELEMETRY_PAGE in paragraph:
            text = prose(paragraph)
            assert not re.search(r"opt[- ]out|zero-egress", text), text
