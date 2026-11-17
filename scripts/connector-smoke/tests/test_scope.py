"""What the runner can never do: push, open a PR, run a program, or reach
the network outside transport.py; and the workflow that runs it live only on
a schedule or by hand, never on a pull request."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from conftest import HERE
from connector_smoke import smoke

PACKAGE = HERE.parent / "connector_smoke"
WORKFLOW = HERE.parents[2] / ".github" / "workflows" / "connector-smoke.yml"
SOURCES = sorted(PACKAGE.glob("*.py"))


def _imports(path: Path) -> set:
    text = path.read_text()
    return set(
        re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_.]*)", text, re.M)
    )


def test_never_pushes_or_opens_a_pr():
    assert SOURCES
    for path in SOURCES:
        text = path.read_text()
        mods = _imports(path)
        assert not {"subprocess", "pty", "multiprocessing", "shutil"} & mods, path.name
        for call in (
            r"os\.system",
            r"os\.exec",
            r"os\.spawn",
            r"os\.popen",
            r"api\.github\.com",
            r"\bgh\s+pr\b",
            r"git\s+(push|commit|checkout)",
        ):
            assert not re.search(call, text), (path.name, call)


def test_only_transport_reaches_the_network():
    network = {
        "socket",
        "ssl",
        "http.client",
        "urllib.request",
        "urllib3",
        "requests",
        "httpx",
        "http.server",
    }
    for path in SOURCES:
        if path.name == "transport.py":
            continue
        assert not network & _imports(path), path.name


def test_the_only_file_the_runner_writes_is_the_report():
    writers = [
        p.name
        for p in SOURCES
        if re.search(
            r"\bopen\([^)]*['\"][wa]|write_text|write_bytes|os\.replace", p.read_text()
        )
    ]
    assert writers == ["report.py"]


@pytest.mark.parametrize(
    "provider, reason",
    [
        ({"kind": "partner", "transport": {"kind": "http"}}, "partner_not_smoked"),
        (
            {"kind": "vendor_local", "transport": {"kind": "stdio"}},
            "transport_stdio_not_run",
        ),
        (
            {"kind": "vendor_mcp", "transport": {"kind": "http", "variant": "sse"}},
            "transport_sse_not_smoked",
        ),
        (
            {
                "kind": "vendor_mcp",
                "transport": {"kind": "http", "variant": "streamable"},
            },
            None,
        ),
        ({"kind": "rest", "transport": {"kind": "http"}}, None),
    ],
)
def test_kinds_the_runner_does_not_smoke_are_named(provider, reason):
    from connector_smoke.catalog import Provider

    p = Provider(app="a", id="p", path=Path("p.yaml"), record=provider)
    assert smoke.unsupported(p) == reason


def _workflow() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text())
    doc["on"] = doc.pop(True, doc.get("on"))  # YAML 1.1 reads a bare `on` as true
    return doc


def test_workflow_runs_live_only_on_a_schedule_or_by_hand():
    doc = _workflow()
    assert "pull_request_target" not in doc["on"]
    assert doc["permissions"] == {"contents": "read"}
    smoke_job = doc["jobs"]["smoke"]
    assert (
        "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
        in smoke_job["if"]
    )
    assert "vars.CONNECTOR_SMOKE_ENABLED == 'true'" in smoke_job["if"]
    # only from main, in the environment that holds the keys
    assert "github.ref == 'refs/heads/main'" in smoke_job["if"]
    assert smoke_job["environment"] == "connector-smoke"
    text = WORKFLOW.read_text()
    for job_id, job in doc["jobs"].items():
        uses_secrets = "secrets." in yaml.safe_dump(job)
        assert uses_secrets == (job_id == "smoke"), job_id
    assert "--allow-writes" not in text  # the scheduled smoke is read-only
    assert (
        "git push" not in text
        and "pull-requests: write" not in text
        and "contents: write" not in text
    )


def test_workflow_actions_are_pinned_and_hold_no_git_credential():
    """Every action is pinned by a full commit SHA, so a moved tag cannot
    change what runs beside the keys; checkouts keep no git credential."""
    doc = _workflow()
    steps = [step for job in doc["jobs"].values() for step in job["steps"]]
    uses = [step["uses"] for step in steps if "uses" in step]
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[a-z0-9-]+/[a-z0-9-]+@[0-9a-f]{40}", ref), ref
    checkouts = [s for s in steps if s.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 2
    assert all(s["with"]["persist-credentials"] is False for s in checkouts)
