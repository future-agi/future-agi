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
            assert 'job["spec"].pop("activeDeadlineSeconds", None)' in section
            assert 'job["spec"]["backoffLimit"] = 0' in section
            assert "--for=condition=complete" in section
            assert "--for=condition=failed" in section
            assert "logs job/futureagi-reconcile-legacy-tags" in section
            assert section.index(
                "logs job/futureagi-reconcile-legacy-tags"
            ) < section.index('if [ "$complete" != true ]; then')
            assert "\nset -e\n" not in section
            assert "delete job futureagi-reconcile-legacy-tags" in section
            assert "--after-id <cursor>" in section
            assert "SERVICE_TYPE=bootstrap" in section
            assert "STARTUP_DB_MUTATION_MODE=operator" in section
            steps = (
                "reconcile_tags --dry-run &&",
                "reconcile_tags  # first apply",
                "helm upgrade",
                "rollout status deployment/futureagi-backend",
                "reconcile_tags            # second apply",
                "The new editor stores",
            )
        _in_order(section, steps)


def test_upgrade_entry_points_explain_the_manual_handoff():
    installation = (ROOT / "INSTALLATION.md").read_text().split("## Upgrading", 1)[1]
    update = (ROOT / "deploy/update/README.md").read_text()
    standalone = update.split("## Update a production install", 1)[0]
    production = update.split("## Update a production install", 1)[1].split(
        "## Roll back", 1
    )[0]
    for guide in (installation, standalone):
        assert "reconcile_legacy_trace_tags" in guide
        assert "./bin/update apply" in guide
        assert "automatic" in guide.lower()
        assert "UPGRADING.md#standalone" in guide
    _in_order(
        production,
        (
            "--production apply --stage",
            "reconcile_legacy_trace_tags --dry-run",
            "reconcile_legacy_trace_tags",
            "--production apply",
            "reconcile_legacy_trace_tags",
        ),
    )
    assert "../README.md#upgrading-a-production-install" in production


def test_rollout_guides_do_not_claim_the_editor_is_gated():
    for path in (
        "deploy/README.md",
        "deploy/helm/futureagi/README.md",
        "deploy/helm/futureagi/UPGRADING.md",
    ):
        guide = (ROOT / path).read_text()
        assert "keep the editor unavailable" not in guide
        assert "keep the new tag editor unavailable" not in guide
        assert "Do not expose the new" not in guide
        assert "before releasing the editor" not in guide
        assert "run the second apply immediately" in guide.lower()


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
            "Rerun the apply",
        ),
    )
