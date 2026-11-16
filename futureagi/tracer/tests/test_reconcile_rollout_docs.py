"""The legacy PG trace-tag handoff requires a reviewed cold writer cutover."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _in_order(text, steps):
    position = 0
    for step in steps:
        position = text.index(step, position) + len(step)


def _section(path, heading, end):
    return (ROOT / path).read_text().split(heading, 1)[1].split(end, 1)[0]


def _separate_reconciliation_actions(section, command, start):
    """Review must be prose between executable dry-run and apply blocks."""
    blocks = re.findall(r"```(?:bash|sh)\n(.*?)```", section, re.DOTALL)
    actions = []
    for block in blocks:
        subprocess.run(["bash", "-n"], input=block, text=True, check=True)
        assert "STOP for human review" not in block
        if command not in block:
            continue
        assert "&&" not in block
        assert start not in block
        calls = re.findall(
            rf"{re.escape(command)}((?:[ \t][^\n]*)?)$", block, re.MULTILINE
        )
        assert len(calls) == 1
        actions.append(calls[0])
    # Both dry-runs are full scope; neither a cursor nor an inline apply is safe.
    assert actions == [" --dry-run", "", " --dry-run"]
    _in_order(
        section,
        (
            command + " --dry-run\n",
            "STOP for human review",
            command + "\n",
            command + " --dry-run\n",
            start,
        ),
    )


def test_helm_chart_and_runbook_reconcile_before_cold_rollout():
    job = (ROOT / "deploy/helm/futureagi/templates/bootstrap/job.yaml").read_text()
    assert '"SERVICE_TYPE" "bootstrap"' in job
    assert '"STARTUP_DB_MUTATION_MODE" "operator"' in job
    assert 'command: ["python", "manage.py", "bootstrap_install"]' in job
    chart = _section(
        "deploy/helm/futureagi/README.md",
        "## Upgrading",
        "### Moving to the first published chart",
    )
    _in_order(
        chart,
        (
            "[cold writer barrier](UPGRADING.md#legacy-tag-cutover-barrier)",
            "suspend controllers/autoscalers",
            "drain requests",
            "reconcile_legacy_trace_tags --dry-run",
            "**stop for human count review**",
            "`python manage.py reconcile_legacy_trace_tags` only after approval",
            "Verify a fresh full dry-run",
            "Only after final reconciliation is complete and reviewed",
            "require reload/refetch",
            "helm upgrade",
        ),
    )
    section = _section(
        "deploy/helm/futureagi/UPGRADING.md", "## Direct Helm", "## Standalone"
    )
    assert (
        'container["command"] = ["python", "manage.py", "reconcile_legacy_trace_tags"]'
        in section
    )
    assert 'container["args"] = sys.argv[1:]' in section
    assert "--show-only templates/bootstrap/job.yaml" in section
    assert 'job["metadata"].pop("annotations", None)' in section
    assert 'job["spec"].pop("activeDeadlineSeconds", None)' in section
    assert 'job["spec"]["backoffLimit"] = 0' in section
    # A short inherited TTL could delete the Job before either wait sees it
    # finish, losing its logs and looping forever.
    assert 'job["spec"].pop("ttlSecondsAfterFinished", None)' in section
    assert "--for=condition=complete" in section
    assert "--for=condition=failed" in section
    assert section.index("logs job/futureagi-reconcile-legacy-tags") < section.index(
        'if [ "$complete" != true ]; then'
    )
    assert "\nset -e\n" not in section
    assert "delete job futureagi-reconcile-legacy-tags" in section
    assert "--after-id <cursor>" in section
    assert "SERVICE_TYPE=bootstrap" in section
    assert "STARTUP_DB_MUTATION_MODE=operator" in section
    _in_order(
        section,
        (
            "suspend Argo/Flux/other reconcilers **in their source of truth**",
            "external admission\nalready closed",
            "delete hpa -l app.kubernetes.io/instance=futureagi",
            "scale deployment -l app.kubernetes.io/instance=futureagi --replicas=0",
            "**STOP and verify**",
            "reconcile_tags --dry-run\n",
            "helm upgrade futureagi",
            "Keep external admission closed during the rollout",
            "sign-off before reopening",
        ),
    )
    _separate_reconciliation_actions(
        section, "reconcile_tags", "helm upgrade futureagi"
    )


def test_upgrade_entry_points_explain_the_manual_handoff():
    installation = _section(
        "INSTALLATION.md", "**One-time trace/span tag-editor upgrade:", "On Helm:"
    )
    standalone = _section(
        "deploy/update/README.md",
        "**One-time trace/span tag-editor transition:",
        "`check` changes nothing.",
    )
    production = _section(
        "deploy/update/README.md", "## Update a production install", "## Roll back"
    )
    _in_order(
        installation,
        (
            "[cold writer barrier](deploy/helm/futureagi/UPGRADING.md#legacy-tag-cutover-barrier)",
            "[Standalone handoff](deploy/helm/futureagi/UPGRADING.md#standalone)",
            "[Distributed handoff](deploy/README.md#upgrading-a-production-install)",
            "Stop **all legacy and new editor/request writers**",
            "reconcile_legacy_trace_tags --dry-run",
            "**stop for human count\nreview**",
            "then apply separately",
            "**before any new editor exposure**",
            "Neither `./bin/install` nor `./bin/update apply` performs this handoff",
            "Pause\nautomatic updates **before**",
        ),
    )
    _in_order(
        standalone,
        (
            "Neither `./bin/update check` nor `./bin/update apply` runs",
            "`reconcile_legacy_trace_tags`",
            "[Standalone operator handoff](../helm/futureagi/UPGRADING.md#standalone)",
            "`./bin/update apply --stage`",
            "stop **all legacy and new editor/request writers**",
            "Run dry-run, **stop for human count review**, then apply as a separate action",
            "Verify a new full dry-run",
            "Only after final reconciliation is complete and reviewed may `./bin/update apply`",
            "Pause automatic updates **before**",
        ),
    )
    _in_order(
        production,
        (
            "--production apply --stage",
            "[production handoff](../README.md#upgrading-a-production-install)",
            "close admission",
            "stop all writer services",
            "dry-run, pause for human review, apply, and verify from the beginning",
            "reconciliation is complete and reviewed",
            "--production apply           # restart only after reconciliation review",
            "Keep external admission closed",
            "owner sign-off to expose the editor",
        ),
    )


def test_production_compose_handoff_reconciles_under_closed_writer_barrier():
    section = _section(
        "deploy/README.md", "### Upgrading a production install", "### Signed updates"
    )
    _in_order(
        section,
        (
            "close all external/internal application admission",
            "$COMPOSE --profile '*' stop $WRITERS",
            "**STOP and verify**",
            "--entrypoint python backend manage.py reconcile_legacy_trace_tags --dry-run",
            "STOP for human review",
            "--entrypoint python backend manage.py reconcile_legacy_trace_tags\n",
            "--entrypoint python backend manage.py reconcile_legacy_trace_tags --dry-run",
            "Only after final reconciliation is complete",
            "$COMPOSE up -d",
            "Keep external admission closed during startup",
            "discard queued pre-maintenance tag saves",
            "reopening",
        ),
    )
    _separate_reconciliation_actions(
        section, "reconcile_legacy_trace_tags", "$COMPOSE up -d"
    )


def test_rollout_guides_require_external_barrier_and_fresh_clients_not_feature_gate():
    for path in (
        "deploy/README.md",
        "deploy/helm/futureagi/README.md",
        "deploy/helm/futureagi/UPGRADING.md",
    ):
        guide = (ROOT / path).read_text()
        assert "legacy-tag-cutover-barrier" in guide
        assert "run the second apply immediately" not in guide.lower()
        assert not re.search(r"reconcile_(?:tags|legacy_trace_tags)[^\n]*&&", guide)
        assert "reload" in guide
    barrier = _section(
        "deploy/helm/futureagi/UPGRADING.md",
        "## Legacy tag cutover barrier",
        "## Direct Helm",
    )
    normalized = " ".join(barrier.split())
    for requirement in (
        "Pause unattended updates and all competing operators/controllers first",
        "Close admission to **all** application traffic",
        "direct backend ports, API clients, shared links, internal callers and ingestion",
        "external maintenance/access controls",
        "no application feature flag that supplies this barrier",
        "Stop **all legacy and new editor/request writers**",
        "workers, collectors, gateway and any external ingestion/mirror writers",
        "Drain in-flight work",
        "Keep the new UI and API inaccessible",
        "Keep external admission closed during startup/validation",
        "Discard pre-cutover grid/drawer selections",
        "invalidate any response caches serving tag state",
        "close old tabs and reload before editing",
        "API clients must discard cached tag lists and refetch",
        "Do not replay queued pre-maintenance tag requests",
        "If you cannot ensure fresh clients, keep editing unavailable",
        "Reopen only after the owner signs off",
    ):
        assert requirement in normalized


def test_helm_failed_dry_run_resumes_as_dry_run_and_wait_is_bounded():
    section = _section(
        "deploy/helm/futureagi/UPGRADING.md", "## Direct Helm", "## Standalone"
    )
    assert "resumes with `reconcile_tags --dry-run --after-id <cursor>`" in section
    assert "`reconcile_tags --after-id <cursor>`" in section
    assert "A failed dry-run" in section
    assert "A failed apply" in section
    assert "before separately starting apply from the beginning" in section
    assert "and rerun `reconcile_tags --after-id <cursor>`" not in section
    assert not re.search(r"reconcile_tags[^\n]*&&", section)
    assert "every segment" in section and "without `--after-id`" in section
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
    installation = _section(
        "INSTALLATION.md", "**One-time trace/span tag-editor upgrade:", "On Helm:"
    )
    update = _section(
        "deploy/update/README.md",
        "**One-time trace/span tag-editor transition:",
        "`check` changes nothing.",
    )
    assert "COMPOSE_FILE=docker-compose.distributed.yml" in installation
    assert "docker-compose.override.yml" in installation
    assert "distributed file and any installer override" in update
    for section in (installation, update):
        assert "--distributed" in section
        assert "COMPOSE_FILE" in section
        assert "#upgrading-a-production-install" in section
        assert 'COMPOSE="docker compose"' in section
        assert "`backend`" in section and "`app`" in section
        assert "docker compose -f docker-compose.distributed.yml" not in section
        assert "docker compose pull" not in section
    # Entrypoints now link the central recipe rather than duplicating commands.
    recipe = _section(
        "deploy/README.md", "### Upgrading a production install", "### Signed updates"
    )
    assert (
        "-f docker-compose.distributed.yml -f deploy/docker-compose.production.yml"
        in recipe
    )
    assert recipe.count("$COMPOSE run --rm --no-deps") == 3
    assert (
        recipe.count(
            "--entrypoint python backend manage.py reconcile_legacy_trace_tags"
        )
        == 3
    )
    assert (
        recipe.count("-e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator")
        == 3
    )
    assert "Staging is not a writer barrier" in installation
    assert "Staging alone does not stop writes" in update


def test_changed_during_scan_after_resume_requires_full_pass():
    for path, heading, end in (
        (
            "deploy/README.md",
            "Retain every segment's logs.",
            "The new trace/span tag PATCH",
        ),
        (
            "deploy/helm/futureagi/UPGRADING.md",
            "## Legacy tag cutover barrier",
            "## Direct Helm",
        ),
    ):
        section = _section(path, heading, end)
        normalized = " ".join(section.split()).replace("**", "")
        assert "same pass/scope" in normalized
        assert "any" in normalized and "segment" in normalized
        assert "changed during scan" in normalized
        assert "investigate" in normalized and "writer barrier" in normalized
        assert "whole pass from the beginning" in normalized
        assert "without `--after-id`" in normalized
        assert "`would mirror 0`" in normalized
        assert "`changed during scan 0`" in normalized
        assert "missing" in normalized.lower() and "inaccessible" in normalized
        assert "owner-approved" in normalized
    helm = _section(
        "deploy/helm/futureagi/UPGRADING.md", "## Direct Helm", "## Standalone"
    )
    assert "Inspect every segment's `changed during scan` count" in helm
    assert "Any nonzero count requires" in helm
    assert (
        "investigating the writer barrier and a whole repeat without `--after-id`"
        in helm
    )
    assert "verify from\nthe beginning" in helm


def test_standalone_runbook_reconciles_before_starting_new_app():
    chart = _section(
        "deploy/helm/futureagi/README.md",
        "## Upgrading",
        "### Moving to the first published chart",
    )
    _in_order(
        chart,
        (
            "[Standalone](UPGRADING.md#standalone)",
            "requires stopping",
            "`app` and completing reconciliation before starting the new editor",
            "script does not perform this handoff",
        ),
    )
    section = _section(
        "deploy/helm/futureagi/UPGRADING.md", "## Standalone", "## Argo CD"
    )
    _in_order(
        section,
        (
            "[barrier and recovery requirements](#legacy-tag-cutover-barrier)",
            "Pause automatic updates",
            "Close external admission, drain requests",
            "docker compose stop app",
            "--entrypoint python app manage.py migrate --noinput",
            "reconcile_legacy_trace_tags --dry-run",
            "STOP for human review",
            "reconcile_legacy_trace_tags\n",
            "full scan from the beginning while `app` remains stopped",
            "reconcile_legacy_trace_tags --dry-run",
            "Only after final reconciliation is complete and reviewed",
            "docker compose up -d",
            "Keep admission closed during startup",
            "discard\nstale tabs/caches and requests",
            "sign-off before reopening",
        ),
    )
    assert (
        section.count("-e SERVICE_TYPE=bootstrap -e STARTUP_DB_MUTATION_MODE=operator")
        == 4
    )
    assert section.count("docker compose run --rm --no-deps") == 4
    _separate_reconciliation_actions(
        section, "reconcile_legacy_trace_tags", "docker compose up -d"
    )
