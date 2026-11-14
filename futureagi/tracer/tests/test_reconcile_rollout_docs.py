"""The legacy PG trace-tag handoff must straddle every documented rollout."""

import subprocess
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
            # A short inherited TTL could delete the Job before either wait
            # sees it finish, losing its logs and looping forever.
            assert 'job["spec"].pop("ttlSecondsAfterFinished", None)' in section
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
    installation = (
        (ROOT / "INSTALLATION.md")
        .read_text()
        .split("**One-time trace/span tag-editor upgrade:**", 1)[1]
        .split("On Helm:", 1)[0]
    )
    update = (ROOT / "deploy/update/README.md").read_text()
    standalone = update.split("**One-time trace/span tag-editor transition:**", 1)[
        1
    ].split("`check` changes nothing.", 1)[0]
    production = update.split("## Update a production install", 1)[1].split(
        "## Roll back", 1
    )[0]
    _in_order(
        installation,
        (
            "[Standalone handoff](deploy/helm/futureagi/UPGRADING.md#standalone)",
            "or [Distributed production handoff](deploy/README.md#upgrading-a-production-install)",
            "Run `reconcile_legacy_trace_tags --dry-run`, review the count, then apply",
            "run the second apply\nimmediately after rollout",
            "Neither\n`./bin/install` nor `./bin/update apply` performs this handoff automatically.",
            "If automatic updates are enabled, pause them **before**",
        ),
    )
    _in_order(
        standalone,
        (
            "`./bin/update check` does not\nreconcile old PG trace tags",
            "`./bin/update apply` does not run\n`reconcile_legacy_trace_tags`",
            "[Standalone operator handoff](../helm/futureagi/UPGRADING.md#standalone)",
            "`./bin/update apply --stage`",
            "dry-run and first\napply while the old app still serves",
            "Finish with `./bin/update apply`, then\nrun the second apply immediately after update rollout.",
            "Automatic updates (apply mode) do not run this\nhandoff either: pause them **before**",
        ),
    )
    _in_order(
        production,
        (
            "--production apply --stage",
            "--entrypoint python backend manage.py reconcile_legacy_trace_tags --dry-run",
            "--entrypoint python backend manage.py reconcile_legacy_trace_tags\n",
            "--production apply           # restart",
            "--entrypoint python backend manage.py reconcile_legacy_trace_tags\n",
        ),
    )
    assert "../README.md#upgrading-a-production-install" in production


def test_production_compose_handoff_orders_both_applies_around_rollout():
    section = (
        (ROOT / "deploy/README.md")
        .read_text()
        .split("### Upgrading a production install", 1)[1]
        .split("### ", 1)[0]
    )
    _in_order(
        section,
        (
            "--entrypoint python backend manage.py reconcile_legacy_trace_tags --dry-run",
            "--entrypoint python backend manage.py reconcile_legacy_trace_tags\n",
            "$COMPOSE up -d",
            "--entrypoint python backend manage.py reconcile_legacy_trace_tags\n",
        ),
    )


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


def test_helm_failed_dry_run_resumes_as_dry_run_and_wait_is_bounded():
    section = (
        (ROOT / "deploy/helm/futureagi/UPGRADING.md")
        .read_text()
        .split("## Direct Helm", 1)[1]
        .split("## Standalone", 1)[0]
    )
    assert "reconcile_tags --dry-run --after-id <cursor> && reconcile_tags" in section
    assert "reconcile_tags --after-id <cursor>" in section
    assert "A failed dry-run" in section
    assert "A failed apply" in section
    assert "and rerun `reconcile_tags --after-id <cursor>`" not in section
    assert "each run" in section and "without `--after-id`" in section
    bash = section.split("```bash\n", 1)[1].split("\n```", 1)[0]
    subprocess.run(["bash", "-n"], input=bash, text=True, check=True)
    assert "RECONCILE_WAIT_TIMEOUT_SECONDS" in bash
    assert "SECONDS >= deadline" in bash
    assert "last cursor:" in bash
    assert "return 1" in bash
    # A failed logs call must not suppress the timeout diagnostic.
    assert "2>&1) || logs_status=$?" in bash
    _in_order(
        bash,
        (
            "logs=$(kubectl",
            'if [ "$timed_out" = true ]; then',
            "if (( logs_status != 0 )); then",
        ),
    )
    assert "\nset -e\n" not in bash


def test_distributed_handoff_uses_the_installer_compose_file_and_backend():
    compose = (ROOT / "docker-compose.distributed.yml").read_text()
    assert "  backend:" in compose
    assert "  app:" not in compose
    installer = (ROOT / "bin/install").read_text()
    assert (
        "docker-compose.distributed.yml:docker-compose.override.yml" in installer
        or ('distributed_setting+=":docker-compose.override.yml"' in installer)
    )
    installation = (
        (ROOT / "INSTALLATION.md")
        .read_text()
        .split("**One-time trace/span tag-editor upgrade:**", 1)[1]
        .split("On Helm:", 1)[0]
    )
    update = (
        (ROOT / "deploy/update/README.md")
        .read_text()
        .split("**One-time trace/span tag-editor transition:**", 1)[1]
        .split("`check` changes nothing.", 1)[0]
    )
    for section, rollout in (
        (installation, "./bin/update apply    # complete the staged update"),
        (update, "./bin/update apply    # complete the staged update"),
    ):
        assert "--distributed" in section
        assert "COMPOSE_FILE=docker-compose.distributed.yml" in section
        assert "docker-compose.override.yml" in section
        assert "docker compose -f docker-compose.distributed.yml" not in section
        assert "docker compose pull" not in section
        _in_order(
            section,
            (
                "--entrypoint python backend manage.py reconcile_legacy_trace_tags --dry-run",
                "--entrypoint python backend manage.py reconcile_legacy_trace_tags\n",
                rollout,
                "--entrypoint python backend manage.py reconcile_legacy_trace_tags\n",
            ),
        )
        assert section.count("docker compose run --rm --no-deps") == 3
        assert (
            "-e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator" in section
        )
        assert "--rm --no-deps" in section
    assert "could not write after the first apply" in " ".join(update.split())
    assert "stopped before its last write" not in update


def test_changed_during_scan_after_resume_requires_full_pass():
    for path, heading in (
        ("deploy/README.md", "Check the final `changed during scan` count"),
        (
            "deploy/helm/futureagi/UPGRADING.md",
            "Inspect the final `changed during scan` count",
        ),
    ):
        section = (ROOT / path).read_text().split(heading, 1)[1].split("\n\n", 1)[0]
        assert "--after-id" in section
        assert "each run" in section
        assert "any run" in section
        assert "without `--after-id`" in section


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
