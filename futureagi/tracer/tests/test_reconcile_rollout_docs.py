"""The legacy PG trace-tag handoff must straddle every documented rollout."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _in_order(text, steps):
    position = 0
    for step in steps:
        position = text.index(step, position) + len(step)


def test_helm_chart_and_runbook_reconcile_on_both_sides_of_rollout():
    job = (ROOT / "deploy/helm/futureagi/templates/bootstrap/job.yaml").read_text()
    assert '"SERVICE_TYPE" "bootstrap"' in job
    assert '"STARTUP_DB_MUTATION_MODE" "operator"' in job
    assert 'command: ["python", "manage.py", "bootstrap_install"]' in job
    for path, end in (
        ("deploy/helm/futureagi/README.md", "### Moving to the first published chart"),
        ("deploy/helm/futureagi/UPGRADING.md", "## Argo CD"),
    ):
        guide = (ROOT / path).read_text()
        section = (
            guide.split("## Upgrading", 1)[1]
            if path.endswith("README.md")
            else guide.split("## Direct Helm", 1)[1]
        )
        section = section.split(end, 1)[0]
        if path.endswith("README.md"):
            steps = (
                "reconcile_legacy_trace_tags --dry-run",
                "reconcile_legacy_trace_tags` before",
                "helm upgrade",
                "reconcile_legacy_trace_tags` again",
                "tag editor",
            )
        else:
            assert (
                'container["command"] = ["python", "manage.py", "reconcile_legacy_trace_tags"]'
                in section
            )
            assert 'container["args"] = sys.argv[1:]' in section
            assert "--show-only templates/bootstrap/job.yaml" in section
            assert "job/futureagi-reconcile-legacy-tags --timeout=20m" in section
            assert "SERVICE_TYPE=bootstrap" in section
            assert "STARTUP_DB_MUTATION_MODE=operator" in section
            steps = (
                "reconcile_tags --dry-run",
                "reconcile_tags            # first apply",
                "helm upgrade",
                "rollout status deployment/futureagi-backend",
                "reconcile_tags            # second apply",
                "tag editor",
            )
        _in_order(section, steps)


def test_standalone_runbook_reconciles_on_both_sides_of_rollout():
    chart = (
        (ROOT / "deploy/helm/futureagi/README.md")
        .read_text()
        .split("## Upgrading", 1)[1]
    )
    _in_order(
        chart,
        (
            "[Standalone](UPGRADING.md#standalone)",
            "reconcile_legacy_trace_tags --dry-run",
            "then apply,",
            "docker compose up -d",
            "then apply again",
            "tag editor",
        ),
    )
    guide = (ROOT / "deploy/helm/futureagi/UPGRADING.md").read_text()
    section = guide.split("## Standalone", 1)[1].split("## Argo CD", 1)[0]
    _in_order(
        section,
        (
            "reconcile_legacy_trace_tags --dry-run",
            "reconcile_legacy_trace_tags\n",
            "docker compose up -d",
            "reconcile_legacy_trace_tags\n",
            "tag editor",
        ),
    )
