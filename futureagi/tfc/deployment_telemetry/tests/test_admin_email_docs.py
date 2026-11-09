"""Shipped text says admin emails leave the install only on opt-in.

By default the registration carries the instance id, version and deployment
type and no email addresses; FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS=true (Helm
``config.telemetryShareAdminEmails=true``) adds the emails and email domains of
owner, admin and staff/superuser accounts (test_admin_email_opt_in.py). Text
that still says emails or domains go out by default, or that calls the
enabled telemetry anonymous, contradicts that. Standard library only: no
Django, services or network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SHARE = "future_agi_telemetry_share_admin_emails"
HELM_SHARE = "telemetryshareadminemails"

# Every surface that describes what the enabled registration sends.
SURFACES = [
    "README.md",
    "INSTALLATION.md",
    ".env.example",
    "futureagi/.env.example",
    "bin/install",
    "bin/install.ps1",
    "deploy/helm/futureagi/values.yaml",
    "deploy/helm/futureagi/values.schema.json",
    "deploy/helm/futureagi/README.md",
    "deploy/helm/futureagi/templates/NOTES.txt",
    "deploy/env-reference.toml",
    "deploy/standalone/bin/bootstrap.py",
    "futureagi/tfc/deployment_telemetry/wire_reference.toml",
    "futureagi/tfc/deployment_telemetry/sender.py",
    "futureagi/tfc/deployment_telemetry/payloads.py",
]
HELM = {
    "deploy/helm/futureagi/values.yaml",
    "deploy/helm/futureagi/values.schema.json",
    "deploy/helm/futureagi/README.md",
    "deploy/helm/futureagi/templates/NOTES.txt",
}

# How these surfaces described the registration while it sent the emails of
# owner and admin accounts by default.
EMAILS_BY_DEFAULT = re.compile(
    r"registration with the \**email addresses"
    r"|registers once, with the email addresses"
    r"|owner/admin emails and domains"
    r"|registration also sends the email addresses"
    r"|deployment type, and the emails and domains"
    r"|an instance id, the version, admin emails"
    r"|an instance id, the version, the emails of"
    r"|owner, admin and staff emails once"
    r"|first account's email is part of the registration"
    r"|registration \(active admin user emails"
    r"|enabled registration \(with admin user emails\)"
)


def prose(text: str) -> str:
    """Comment markers removed and wrapped lines joined, lowercased."""
    text = re.sub(r"(?m)^[ \t]*(?:#|//|;)+[ \t]?", "", text)
    return " ".join(text.split()).lower()


def read(path: str) -> str:
    return prose((ROOT / path).read_text(encoding="utf-8"))


def test_every_surface_exists():
    assert all((ROOT / path).is_file() for path in SURFACES)


@pytest.mark.parametrize("path", SURFACES)
def test_no_surface_says_emails_are_sent_by_default(path):
    text = read(path)
    found = [
        text[max(0, match.start() - 60) : match.end() + 60]
        for match in EMAILS_BY_DEFAULT.finditer(text)
    ]
    assert not found, f"{path} still says admin emails are sent by default: {found}"


@pytest.mark.parametrize(
    "path", sorted(set(SURFACES) - {"futureagi/tfc/deployment_telemetry/payloads.py"})
)
def test_every_surface_names_the_opt_in(path):
    text = read(path)
    names = (HELM_SHARE,) if path in HELM else (SHARE,)
    if path == "deploy/helm/futureagi/README.md":
        names = (HELM_SHARE, SHARE)
    for name in names:
        assert name in text, f"{path} does not name {name}"


@pytest.mark.parametrize("path", SURFACES)
def test_enabled_telemetry_is_not_called_anonymous(path):
    text = read(path)
    for match in re.finditer(r"anonymi[sz]ed|anonymous", text):
        window = text[max(0, match.start() - 300) : match.end() + 300]
        assert not re.search(r"telemetry|registration|registers", window), (
            f"{path} calls deployment telemetry anonymous: {window}"
        )


@pytest.mark.parametrize(
    "path",
    ["README.md", "INSTALLATION.md", "deploy/helm/futureagi/README.md"],
)
def test_the_default_registration_is_described(path):
    """Instance id, version and deployment type, and no emails without the opt-in."""
    text = read(path)
    assert re.search(r"instance id, (the )?version,? and (the )?deployment type", text)
    assert re.search(r"no (email addresses|emails)", text)


# The fields of a registration sent without the opt-in (site gate DH A rows).
DEFAULT_REGISTRATION_FIELDS = (
    "schema_version",
    "instance_id",
    "version",
    "deployment_type",
    "timestamp",
    "telemetry_disabled",
)


@pytest.mark.parametrize("path", [p for p in SURFACES if not p.endswith(".py")])
def test_every_surface_says_when_registration_runs_and_what_it_carries(path):
    """Python surfaces are pinned by what they print or log instead:
    test_the_disclosure_says_why_when_and_which_fields and
    deploy/tests/test_standalone_first_run.py."""
    text = read(path)
    assert "owner/admin account exists" in text, (
        f"{path} does not say the registration waits for an owner/admin account"
    )
    missing = [name for name in DEFAULT_REGISTRATION_FIELDS if name not in text]
    assert not missing, f"{path} does not name the registration fields {missing}"


@pytest.mark.parametrize(
    "path", ["bin/install.ps1", "deploy/helm/futureagi/templates/NOTES.txt"]
)
def test_the_enabled_notice_says_why_telemetry_runs(path):
    """Static, as pwsh and a cluster for NOTES.txt are not on every host;
    test_oss_install_hardening.py runs bin/install's notice."""
    assert (
        "deployment telemetry is on so future agi can count installs and plan "
        "release testing." in read(path)
    )


def test_the_crm_sentence_applies_only_when_opted_in():
    text = read("futureagi/.env.example")
    sentences = [s for s in re.split(r"(?<=\.)\s", text) if "crm" in s]
    assert sentences, "futureagi/.env.example no longer mentions the CRM"
    for sentence in sentences:
        assert SHARE in sentence or "opted in" in sentence or "only when" in sentence, (
            sentence
        )


def test_the_root_env_example_offers_the_opt_in_commented_out():
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "# FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS=true" in lines
    assert not any(
        line.startswith("FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS=") for line in lines
    )


@pytest.mark.parametrize(
    "path", ["docker-compose.yml", "docker-compose.distributed.yml"]
)
def test_compose_passes_the_opt_in_beside_the_opt_out(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    disabled = text.count(
        "FUTURE_AGI_TELEMETRY_DISABLED: ${FUTURE_AGI_TELEMETRY_DISABLED:-false}"
    )
    shared = text.count(
        "FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS: "
        "${FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS:-false}"
    )
    assert disabled >= 1
    assert shared == disabled


def test_helm_renders_the_opt_in_beside_the_opt_out():
    chart = ROOT / "deploy/helm/futureagi"
    env = (chart / "templates/_env.tpl").read_text(encoding="utf-8")
    assert '"FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS"' in env
    assert "$v.config.telemetryShareAdminEmails" in env
    values = (chart / "values.yaml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^  telemetryShareAdminEmails: false$", values)
